"""Execution backends for the shell-exec sidecar.

Two backends behind one ``execute()`` interface:

- ``DockerBackend`` — ``standard`` / ``locked``: non-root, read-only rootfs,
  ``--cap-drop=ALL``, no-new-privileges, network off by default, memory/PID caps.
- ``LocalBackend`` — ``open`` only: a subprocess in the workspace, full trust.
  ``open`` is never enabled by default (see ``ShellExecConfig.allowed_profiles``).

The sandbox is the security boundary; this module contains no classifier and no
policy. See ``docs/contracts/credential-scoping.md``.
"""
from __future__ import annotations

import asyncio
import datetime
import ipaddress
import logging
import os
import re
import shutil
import signal
import sys
import tarfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from orchestration.shell_exec import squid

logger = logging.getLogger(__name__)

_ROOM_SAFE = re.compile(r"[^A-Za-z0-9._-]")
_TIMEOUT_EXIT_CODE = 124
_CAP_CHUNK = 65536

#: Profiles the sidecar understands. Which ones it will actually serve is
#: controlled by ``ShellExecConfig.allowed_profiles``.
SUPPORTED_PROFILES = ("open", "standard", "locked")


@dataclass
class ExecResult:
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    truncated: bool = False
    blocked_hosts: Tuple[str, ...] = ()

    def as_dict(self) -> dict:
        payload = {
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "truncated": self.truncated,
        }
        if self.blocked_hosts:
            payload["blocked_hosts"] = list(self.blocked_hosts)
        return payload


@dataclass(frozen=True)
class ShellExecConfig:
    """Sidecar configuration, built from Django settings or the environment."""

    root: Path
    token: str = field(default="", repr=False)
    host: str = "127.0.0.1"
    port: int = 8765
    profile: str = "standard"
    allowed_profiles: Tuple[str, ...] = ("standard",)
    image: str = "alpine:3.20"
    user: str = "65534:65534"
    memory: str = "256m"
    pids_limit: int = 128
    timeout_default: int = 120
    timeout_max: int = 600
    output_bytes_max: int = 65536
    network_allowlist: Tuple[str, ...] = ()
    egress_proxy: bool = False
    egress_image: str = "kazi-egress-squid:1"
    egress_port: int = 3128

    @classmethod
    def from_settings(cls) -> "ShellExecConfig":
        from django.conf import settings

        default_root = Path(__file__).resolve().parents[3] / "shell_workspaces"
        profile = str(getattr(settings, "SHELL_EXEC_PROFILE", "standard") or "standard")
        raw_profiles = getattr(settings, "SHELL_EXEC_PROFILES", None)
        if raw_profiles is None:
            raw_profiles = os.environ.get("SHELL_EXEC_PROFILES", "")
        if isinstance(raw_profiles, str):
            allowed = tuple(part.strip() for part in raw_profiles.split(",") if part.strip())
        else:
            allowed = tuple(str(part).strip() for part in (raw_profiles or ()) if str(part).strip())
        if not allowed:
            allowed = (profile,) if profile in SUPPORTED_PROFILES else ("standard",)
        return cls(
            root=Path(
                getattr(settings, "SHELL_EXEC_ROOT", None)
                or os.environ.get("SHELL_EXEC_ROOT")
                or default_root
            ),
            token=getattr(settings, "SHELL_EXEC_TOKEN", "")
            or os.environ.get("SHELL_EXEC_TOKEN", ""),
            host=getattr(settings, "SHELL_EXEC_HOST", "127.0.0.1"),
            port=int(getattr(settings, "SHELL_EXEC_PORT", 8765)),
            profile=profile,
            allowed_profiles=allowed,
            image=getattr(settings, "SHELL_EXEC_IMAGE", "alpine:3.20"),
            user=getattr(settings, "SHELL_EXEC_USER", "65534:65534"),
            memory=getattr(settings, "SHELL_EXEC_MEMORY", "256m"),
            pids_limit=int(getattr(settings, "SHELL_EXEC_PIDS_LIMIT", 128)),
            timeout_default=int(getattr(settings, "SHELL_EXEC_TIMEOUT_DEFAULT", 120)),
            timeout_max=int(getattr(settings, "SHELL_EXEC_TIMEOUT_MAX", 600)),
            output_bytes_max=int(getattr(settings, "SHELL_EXEC_OUTPUT_BYTES_MAX", 65536)),
            network_allowlist=tuple(getattr(settings, "SHELL_EXEC_NETWORK_ALLOWLIST", ()) or ()),
            egress_proxy=bool(getattr(settings, "SHELL_EGRESS_PROXY", False)),
            egress_image=getattr(settings, "SHELL_EGRESS_PROXY_IMAGE", "kazi-egress-squid:1"),
        )

    def egress_dir(self) -> Path:
        """Per-command proxy config and logs. Never mounted into an exec container."""
        path = (self.root / "egress").resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def workspace(self, room_id: str) -> Path:
        """Return (creating if needed) the per-room workspace directory.

        The room id is sanitized and the resolved path is re-checked against the
        workspaces root so a hostile room id can never escape it.
        """
        safe = _ROOM_SAFE.sub("_", str(room_id or "default"))
        if safe in {"", ".", ".."}:
            safe = "default"
        workspaces_root = (self.root / "workspaces").resolve()
        path = (workspaces_root / safe).resolve()
        if path != workspaces_root and workspaces_root not in path.parents:
            raise ValueError("invalid room id")
        path.mkdir(parents=True, exist_ok=True)
        try:
            # The workspace must be writable by an arbitrary non-root container
            # uid, so it is intentionally group/world-writable.
            os.chmod(path, 0o777)  # nosec B103 - deliberate: container uid is not the sidecar uid
        except OSError:
            pass
        return path


_DOCKER_TIMEOUT = 60.0
_MIN_ENGINE_MAJOR = 28
_EGRESS_LABEL = "kazi.egress=1"
_ACCESS_LOG_MAX_BYTES = 262144
_engine_checked = False


async def _docker(*args: str, timeout: float = _DOCKER_TIMEOUT) -> Tuple[int, str]:
    """Run one fixed-argv docker command and return ``(exit_code, output)``.

    Time-boxed: a hung docker call must not hang a request forever.
    """
    try:
        proc = await asyncio.create_subprocess_exec(  # nosec B603,B607 - fixed argv, no shell
            "docker", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        return 127, "docker executable not found on the sidecar host"
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return 124, f"docker {args[0] if args else ''} timed out"
    return int(proc.returncode or 0), out.decode("utf-8", errors="replace").strip()


async def _engine_error() -> Optional[str]:
    """Refuse engines that cannot isolate the sandbox network from the host.

    Older engines silently accept the isolated-gateway option and create a
    plain internal network, from which services the host binds on 0.0.0.0 are
    reachable. They also resolve external DNS on internal networks
    (CVE-2024-29018). Only a passing check is cached.
    """
    global _engine_checked
    if _engine_checked:
        return None
    code, out = await _docker("version", "--format", "{{.Server.Version}}")
    if code != 0:
        return out if code == 127 else "the docker daemon is not reachable"
    digits = out.strip().split(".", 1)[0]
    if not digits.isdigit() or int(digits) < _MIN_ENGINE_MAJOR:
        return (
            f"Docker Engine {out.strip() or 'unknown'} cannot isolate the sandbox network from "
            f"the host; the egress proxy needs Engine {_MIN_ENGINE_MAJOR} or newer"
        )
    _engine_checked = True
    return None


def _egress_build_context() -> Path:
    return Path(__file__).resolve().parent / "egress"


async def sweep_egress_leftovers(config: "ShellExecConfig") -> int:
    """Remove proxy containers, networks and files a crashed sidecar left behind.

    Call only when no command is running (sidecar start): live commands use
    the same label.
    """
    removed = 0
    _, containers = await _docker("ps", "-aq", "--filter", f"label={_EGRESS_LABEL}")
    for container_id in containers.split():
        await _docker("rm", "-f", container_id)
        removed += 1
    _, networks = await _docker("network", "ls", "-q", "--filter", f"label={_EGRESS_LABEL}")
    for network_id in networks.split():
        await _docker("network", "rm", network_id)
        removed += 1
    for child in config.egress_dir().iterdir():
        shutil.rmtree(child, ignore_errors=True)
    return removed


async def prepare_egress(config: "ShellExecConfig") -> Optional[str]:
    """Sidecar start-up: check the engine, build the proxy image, sweep leftovers.

    Returns an error string (the sidecar should refuse to start with the proxy
    on) or ``None``. The image is built here, never inside a request.
    """
    error = await _engine_error()
    if error:
        return error
    code, _ = await _docker("image", "inspect", "-f", "{{.Id}}", config.egress_image)
    if code != 0:
        code, out = await _docker(
            "build", "-q", "-t", config.egress_image, str(_egress_build_context()), timeout=900,
        )
        if code != 0:
            return f"could not build the egress proxy image: {out[-300:]}"
    await sweep_egress_leftovers(config)
    return None


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


class ShellBackend:
    """Base backend. Subclasses implement ``execute``."""

    name = "base"

    def __init__(self, config: Optional[ShellExecConfig] = None):
        self.config = config or ShellExecConfig.from_settings()

    def resolve_timeout(self, timeout_s: Optional[int]) -> int:
        """Clamp ``timeout_s`` to the configured bounds. Raises ``ValueError``
        for a non-integer value so the daemon can answer 400, not 500."""
        if timeout_s is None:
            requested = self.config.timeout_default
        else:
            try:
                requested = int(timeout_s)
            except (TypeError, ValueError):
                raise ValueError("timeout_s must be an integer")
        if requested <= 0:
            requested = self.config.timeout_default
        return min(requested, self.config.timeout_max)

    async def execute(
        self,
        command: str,
        *,
        room_id: str = "default",
        cwd: Optional[str] = None,
        timeout_s: Optional[int] = None,
        network: str = "none",
        allowed_hosts: Optional[List[str]] = None,
    ) -> ExecResult:
        raise NotImplementedError


class DockerBackend(ShellBackend):
    """Run the command in a fresh, bounded, non-root container."""

    name = "docker"

    def _container_cwd(self, workspace: Path, cwd: Optional[str]) -> str:
        if not cwd:
            return "/workspace"
        base = workspace.resolve()
        target = (base / cwd).resolve()
        if target != base and base not in target.parents:
            return "/workspace"
        rel = target.relative_to(base).as_posix()
        return "/workspace" if rel in {"", "."} else f"/workspace/{rel}"

    def build_argv(
        self,
        command: str,
        workspace: Path,
        *,
        network: str = "none",
        cwd: Optional[str] = None,
        name: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        egress_network: Optional[str] = None,
    ) -> List[str]:
        # Anything unrecognised means no network at all. A proxied command is
        # attached to its own internal network by the caller (``egress_network``).
        net = "bridge" if str(network) == "bridge" else "none"
        argv = ["docker", "run", "--rm"]
        if egress_network:
            net = egress_network
            argv += ["--label", _EGRESS_LABEL]
        if name:
            argv += ["--name", name]
        for key, value in (env or {}).items():
            argv += ["-e", f"{key}={value}"]
        argv += [
            "--user",
            self.config.user,
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt",
            "no-new-privileges",
            f"--network={net}",
            f"--memory={self.config.memory}",
            f"--pids-limit={self.config.pids_limit}",
            "-v",
            f"{workspace}:/workspace",
            "-w",
            self._container_cwd(workspace, cwd),
            self.config.image,
            "sh",
            "-c",
            command,
        ]
        return argv

    async def _remove_container(self, name: str) -> None:
        """Force-remove a container left behind when the client was killed."""
        try:
            remover = await asyncio.create_subprocess_exec(  # nosec B603,B607 - fixed argv, no shell
                "docker", "rm", "-f", name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await remover.wait()
        except Exception:
            logger.warning("failed to remove container %s", name)

    async def _open_egress(self, exec_id: str, hosts: List[str]) -> Tuple[Optional["_EgressSession"], str]:
        """Give one command its own internal network and its own Squid.

        Any failure returns ``(None, reason)`` and leaves nothing behind. The
        caller then fails the command; it never falls back to the open bridge.
        """
        cfg = self.config
        error = await _engine_error()
        if error:
            return None, error
        code, _ = await _docker("image", "inspect", "-f", "{{.Id}}", cfg.egress_image)
        if code != 0:
            return None, (
                f"the egress proxy image {cfg.egress_image} is not built; restart the sidecar "
                f"(it builds it at start) or run: docker build -t {cfg.egress_image} {_egress_build_context()}"
            )
        session = _EgressSession(
            network=f"kazi-egress-{exec_id[:16]}",
            proxy=f"kazi-egress-proxy-{exec_id[:16]}",
            workdir=cfg.egress_dir() / exec_id,
            hosts=tuple(squid.clean_hosts(hosts)),
        )
        try:
            error = await self._start_egress(session)
        except BaseException:
            await asyncio.shield(self._close_egress(session))
            raise
        if error:
            await self._close_egress(session)
            return None, error
        return session, ""

    async def _start_egress(self, session: "_EgressSession") -> str:
        cfg = self.config
        log_dir = session.workdir / "log"
        log_dir.mkdir(parents=True, exist_ok=True)
        try:
            # Squid runs as its own non-root user and must be able to write its logs.
            os.chmod(log_dir, 0o777)  # nosec B103 - log-only directory, removed with the command
        except OSError:
            pass
        (session.workdir / "squid.conf").write_text(squid.render_config(cfg.egress_port), encoding="utf-8")
        (session.workdir / "hosts.txt").write_text(squid.render_hosts(session.hosts), encoding="utf-8")

        # --internal: no route out. Isolated gateway mode: the bridge gets no
        # host address, so the sandbox cannot reach services the host binds on
        # 0.0.0.0 (Kazi's own Redis/Postgres).
        code, out = await _docker(
            "network", "create", "--internal", "--ipv6=false", "--label", _EGRESS_LABEL,
            "-o", "com.docker.network.bridge.gateway_mode_ipv4=isolated",
            session.network,
        )
        if code != 0:
            return f"could not create an isolated sandbox network: {out[-200:]}"
        # Docker accepts network options it does not know, so check the effect,
        # not the request: internal, and no gateway address on the host.
        code, out = await _docker(
            "network", "inspect", "-f", "{{.Internal}}|{{range .IPAM.Config}}{{.Gateway}}{{end}}", session.network,
        )
        internal, _, gateway = out.partition("|")
        if code != 0 or internal.strip() != "true" or _is_ip(gateway):
            return "the sandbox network is not isolated from the Docker host; refusing to run"
        code, out = await _docker(
            "run", "-d", "--pull=never", "--name", session.proxy, "--label", _EGRESS_LABEL,
            "--user", "proxy", "--read-only", "--cap-drop=ALL", "--security-opt", "no-new-privileges",
            "--memory=256m", "--pids-limit=128", "--network=bridge",
            "--tmpfs", "/tmp", "--tmpfs", "/run", "--tmpfs", "/var/spool/squid",  # nosec B108 - tmpfs inside the proxy container
            "-v", f"{session.workdir / 'squid.conf'}:{squid.CONFIG_PATH}:ro",
            "-v", f"{session.workdir / 'hosts.txt'}:{squid.HOSTS_PATH}:ro",
            "-v", f"{log_dir}:{squid.LOG_DIR}",
            cfg.egress_image,
        )
        if code != 0:
            return f"could not start the egress proxy: {out[-200:]}"
        code, out = await _docker(
            "network", "connect", "--alias", squid.PROXY_ALIAS, session.network, session.proxy,
        )
        if code != 0:
            return f"could not attach the egress proxy: {out[-200:]}"
        if not await _wait_for_marker(log_dir / "cache.log", squid.READY_MARKER, _EGRESS_READY_SECONDS):
            return "the egress proxy did not become ready"
        return ""

    async def _close_egress(self, session: "_EgressSession") -> Tuple[str, ...]:
        """Tear the command's proxy and network down; return the hosts it refused.

        Never raises, and the teardown runs whatever happens to the log.
        """
        blocked: Tuple[str, ...] = ()
        try:
            with open(session.workdir / "log" / "access.log", "rb") as handle:
                log_text = handle.read(_ACCESS_LOG_MAX_BYTES).decode("utf-8", errors="replace")
            blocked = squid.parse_denied(log_text, session.hosts)
        except Exception:
            blocked = ()
        finally:
            for args in (("rm", "-f", session.proxy), ("network", "rm", session.network)):
                try:
                    code, out = await _docker(*args)
                    if code != 0 and "No such" not in out and "not found" not in out:
                        logger.warning("egress teardown `docker %s` failed: %s", " ".join(args[:2]), out[-200:])
                except Exception:
                    logger.warning("egress teardown `docker %s` raised", " ".join(args[:2]), exc_info=True)
            shutil.rmtree(session.workdir, ignore_errors=True)
        return blocked

    def _proxy_env(self) -> Dict[str, str]:
        url = f"http://{squid.PROXY_ALIAS}:{self.config.egress_port}"
        return {
            "HTTP_PROXY": url, "HTTPS_PROXY": url, "http_proxy": url, "https_proxy": url,
            "NO_PROXY": "", "no_proxy": "",
        }

    async def execute(
        self,
        command: str,
        *,
        room_id: str = "default",
        cwd: Optional[str] = None,
        timeout_s: Optional[int] = None,
        network: str = "none",
        allowed_hosts: Optional[List[str]] = None,
    ) -> ExecResult:
        workspace = self.config.workspace(room_id)
        exec_id = uuid.uuid4().hex
        name = f"kazi-exec-{exec_id[:16]}"
        timeout = self.resolve_timeout(timeout_s)
        start = time.monotonic()
        session: Optional[_EgressSession] = None
        if str(network) == "proxy":
            if not self.config.egress_proxy:
                return ExecResult("", "the egress proxy is not enabled on this sidecar", 125, _elapsed_ms(start))
            session, error = await self._open_egress(exec_id, list(allowed_hosts or []))
            if session is None:
                return ExecResult("", error, 125, _elapsed_ms(start))
        try:
            argv = self.build_argv(
                command, workspace, network=network, cwd=cwd, name=name,
                env=self._proxy_env() if session else None,
                egress_network=session.network if session else None,
            )
            try:
                proc = await asyncio.create_subprocess_exec(  # nosec B603,B607 - fixed argv, no shell
                    *argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError:
                return ExecResult("", "docker executable not found on the sidecar host", 127, _elapsed_ms(start))
            result = await _gather(proc, timeout, start, self, cleanup=lambda: self._remove_container(name))
        finally:
            # The proxy and its network die with the command, even if this
            # request is cancelled part-way through.
            blocked = await asyncio.shield(self._close_egress(session)) if session else ()
        result.blocked_hosts = blocked
        return result


class LocalBackend(ShellBackend):
    """Run the command as a subprocess in the workspace (``open`` profile only)."""

    name = "local"

    async def execute(
        self,
        command: str,
        *,
        room_id: str = "default",
        cwd: Optional[str] = None,
        timeout_s: Optional[int] = None,
        network: str = "none",
        allowed_hosts: Optional[List[str]] = None,
    ) -> ExecResult:
        workspace = self.config.workspace(room_id)
        workdir = workspace
        if cwd:
            target = (workspace / cwd).resolve()
            if target == workspace or workspace in target.parents:
                workdir = target
        timeout = self.resolve_timeout(timeout_s)
        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_shell(  # nosec B602 - open profile is full trust by design
                command,
                cwd=str(workdir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=(sys.platform != "win32"),
            )
        except FileNotFoundError:
            return ExecResult("", "shell not available on the sidecar host", 127, _elapsed_ms(start))
        return await _gather(proc, timeout, start, self, cleanup=lambda: _kill_process_group(proc))


@dataclass
class _EgressSession:
    """One command's private network and proxy."""

    network: str
    proxy: str
    workdir: Path
    hosts: Tuple[str, ...] = ()


_EGRESS_READY_SECONDS = 20.0


def _is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text.strip())
        return True
    except ValueError:
        return False


async def _wait_for_marker(path: Path, marker: str, timeout: float) -> bool:
    """Poll a log file until ``marker`` appears (the proxy is accepting connections)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if marker in path.read_text(encoding="utf-8", errors="replace"):
                return True
        except OSError:
            pass
        await asyncio.sleep(0.2)
    return False


async def _drain_capped(stream, limit: int) -> Tuple[bytes, bool]:
    """Read a stream to EOF, keeping at most ``limit`` bytes."""
    kept = bytearray()
    truncated = False
    while True:
        chunk = await stream.read(_CAP_CHUNK)
        if not chunk:
            break
        room = limit - len(kept)
        if room > 0:
            kept.extend(chunk[:room])
        if len(chunk) > max(room, 0):
            truncated = True
    return bytes(kept), truncated


async def _kill_process_group(proc) -> None:
    if sys.platform == "win32":
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        logger.warning("failed to kill process group", exc_info=True)


async def _abort(proc, cleanup: Optional[Callable[[], Awaitable[None]]]) -> None:
    if cleanup is not None:
        try:
            await cleanup()
        except Exception:
            logger.warning("shell cleanup failed", exc_info=True)
    try:
        proc.kill()
    except Exception:
        pass
    try:
        await proc.wait()
    except Exception:  # pragma: no cover - best-effort reap
        pass


async def _gather(
    proc,
    timeout: int,
    start: float,
    backend: ShellBackend,
    cleanup: Optional[Callable[[], Awaitable[None]]] = None,
) -> ExecResult:
    limit = backend.config.output_bytes_max
    out_task = asyncio.ensure_future(_drain_capped(proc.stdout, limit))
    err_task = asyncio.ensure_future(_drain_capped(proc.stderr, limit))
    try:
        (out, out_truncated), (err, err_truncated) = await asyncio.wait_for(
            asyncio.gather(out_task, err_task), timeout=timeout
        )
        await proc.wait()
    except asyncio.TimeoutError:
        await _abort(proc, cleanup)
        return ExecResult("", f"command timed out after {timeout}s", _TIMEOUT_EXIT_CODE, _elapsed_ms(start))
    except Exception as exc:
        await _abort(proc, cleanup)
        return ExecResult("", f"execution failed: {exc}", 1, _elapsed_ms(start))
    except BaseException:
        # Cancelled: still remove the container, then let the cancellation go on.
        await asyncio.shield(_abort(proc, cleanup))
        raise
    return ExecResult(
        stdout=out.decode("utf-8", errors="replace"),
        stderr=err.decode("utf-8", errors="replace"),
        exit_code=int(proc.returncode or 0),
        duration_ms=_elapsed_ms(start),
        truncated=out_truncated or err_truncated,
    )


def get_backend(profile: str = "standard", config: Optional[ShellExecConfig] = None) -> ShellBackend:
    """``open`` uses the local subprocess; everything else is containerized."""
    if str(profile) == "open":
        return LocalBackend(config)
    return DockerBackend(config)


# --------------------------------------------------------------------------- #
#  Workspace snapshots (tar): snapshot before destructive, restore on rollback #
# --------------------------------------------------------------------------- #

def _snapshots_dir(config: ShellExecConfig, room_id: str) -> Path:
    safe = _ROOM_SAFE.sub("_", str(room_id or "default"))
    if safe in {"", ".", ".."}:
        safe = "default"
    snapshots_root = (config.root / "snapshots").resolve()
    path = (snapshots_root / safe).resolve()
    if path != snapshots_root and snapshots_root not in path.parents:
        raise ValueError("invalid room id")
    path.mkdir(parents=True, exist_ok=True)
    return path


def snapshot_workspace(config: ShellExecConfig, room_id: str) -> str:
    """Tar the room workspace and return the snapshot id (a filename)."""
    workspace = config.workspace(room_id)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    name = f"{stamp}.tar.gz"
    with tarfile.open(_snapshots_dir(config, room_id) / name, "w:gz") as tar:
        tar.add(workspace, arcname=".")
    return name


def restore_snapshot(config: ShellExecConfig, room_id: str, snapshot_id: str) -> None:
    """Replace the room workspace with the contents of a snapshot."""
    safe_name = Path(str(snapshot_id)).name
    target = _snapshots_dir(config, room_id) / safe_name
    if not target.is_file():
        raise ValueError("snapshot not found")
    workspace = config.workspace(room_id)
    for child in workspace.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    with tarfile.open(target, "r:gz") as tar:
        # Snapshots are sidecar-created; filter guards member paths anyway.
        tar.extractall(workspace, filter="data")  # nosec B202
