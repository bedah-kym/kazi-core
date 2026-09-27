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
from typing import Awaitable, Callable, List, Optional, Tuple

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

    def as_dict(self) -> dict:
        return {
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "truncated": self.truncated,
        }


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
        )

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
    ) -> List[str]:
        net = "bridge" if str(network) == "bridge" else "none"
        argv = ["docker", "run", "--rm"]
        if name:
            argv += ["--name", name]
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

    async def execute(
        self,
        command: str,
        *,
        room_id: str = "default",
        cwd: Optional[str] = None,
        timeout_s: Optional[int] = None,
        network: str = "none",
    ) -> ExecResult:
        workspace = self.config.workspace(room_id)
        name = f"kazi-exec-{uuid.uuid4().hex[:16]}"
        argv = self.build_argv(command, workspace, network=network, cwd=cwd, name=name)
        timeout = self.resolve_timeout(timeout_s)
        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(  # nosec B603,B607 - fixed argv, no shell
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return ExecResult("", "docker executable not found on the sidecar host", 127, _elapsed_ms(start))
        return await _gather(proc, timeout, start, self, cleanup=lambda: self._remove_container(name))


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
