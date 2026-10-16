from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from orchestration.shell_exec.backends import (
    DockerBackend,
    LocalBackend,
    ShellExecConfig,
    get_backend,
)

_SUBPROCESS = "orchestration.shell_exec.backends.asyncio.create_subprocess_exec"

# Every secret-bearing variable named in .env.example, with a distinct fake
# value per variable. A command must never see any of them (GHSA-ppxm-j4p7-9cw2).
_SECRET_ENV_VARS = {
    "DJANGO_SECRET_KEY": "fake-django-secret-value",  # nosec B105 - fake test fixture
    "ENCRYPTION_KEY": "fake-encryption-key-value",
    "ANTHROPIC_API_KEY": "fake-anthropic-key-value",
    "DEEPSEEK_API_KEY": "fake-deepseek-key-value",
    "HF_API_TOKEN": "fake-hf-token-value",  # nosec B105 - fake test fixture
    "OPENWEATHER_API_KEY": "fake-openweather-key-value",
    "GIPHY_API_KEY": "fake-giphy-key-value",
    "EXCHANGE_RATE_API_KEY": "fake-exchange-key-value",
    "SHELL_EXEC_TOKEN": "fake-shell-exec-token-value",  # nosec B105 - fake test fixture
    "POSTGRES_PASSWORD": "fake-postgres-password-value",  # nosec B105 - fake test fixture
    "DATABASE_URL": "postgres://u:fake-db-password-value@db:5432/x",
    "REDIS_URL": "redis://:fake-redis-password-value@redis:6379/0",
    "CELERY_BROKER_URL": "redis://:fake-broker-password-value@redis:6379/1",
    "CELERY_RESULT_BACKEND": "redis://:fake-result-password-value@redis:6379/2",
}

# A secret that is in the sidecar's environment but not in .env.example: an
# operator may add keys of their own. The allowlist must drop these too.
_UNKNOWN_OPERATOR_VAR = "KAZI_OPERATOR_PRIVATE_TOKEN"
_UNKNOWN_OPERATOR_VALUE = "fake-operator-secret-value"

_ENV_DUMP = "import os,json;print(json.dumps(dict(os.environ)))"
_ENV_DUMP_AND_TEMP = (
    "import os,json,tempfile;"
    "print(json.dumps(dict(os.environ)));"
    "print(tempfile.gettempdir())"
)


def _run(coro):
    """Run a coroutine on a subprocess-capable loop (Proactor on Windows)."""
    if sys.platform == "win32" and hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
        policy = asyncio.WindowsProactorEventLoopPolicy()
        with asyncio.Runner(loop_factory=policy.new_event_loop) as runner:
            return runner.run(coro)
    return asyncio.run(coro)


def _config(**overrides) -> ShellExecConfig:
    root = overrides.pop("root", None) or Path(tempfile.mkdtemp(prefix="kazi-shell-"))
    defaults = dict(
        root=Path(root),
        token="t",  # nosec B106 test fixture
        timeout_default=5,
        timeout_max=10,
        output_bytes_max=1000,
    )
    defaults.update(overrides)
    return ShellExecConfig(**defaults)


class _FakeStream:
    def __init__(self, data: bytes = b""):
        self._data = data

    async def read(self, n: int = -1) -> bytes:
        if not self._data:
            return b""
        if n is None or n < 0:
            out, self._data = self._data, b""
        else:
            out, self._data = self._data[:n], self._data[n:]
        return out


class _HangingStream:
    async def read(self, n: int = -1) -> bytes:
        raise asyncio.TimeoutError


class _FakeProc:
    def __init__(self, out: bytes = b"", err: bytes = b"", returncode: int = 0):
        self.stdout = _FakeStream(out)
        self.stderr = _FakeStream(err)
        self.returncode = returncode
        self.killed = False

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


class WorkspaceTests(unittest.TestCase):
    def test_workspace_is_created(self):
        config = _config()
        path = config.workspace("room-1")
        self.assertTrue(path.is_dir())
        self.assertEqual(path.name, "room-1")

    def test_workspace_sanitizes_path_separators(self):
        config = _config()
        path = config.workspace("../../etc")
        self.assertEqual(path.name, ".._.._etc")

    def test_dotdot_room_falls_back_to_default(self):
        config = _config()
        path = config.workspace("..")
        self.assertEqual(path.name, "default")


class DockerBackendTests(unittest.TestCase):
    def test_build_argv_carries_the_sandbox(self):
        backend = DockerBackend(
            _config(user="1000:1000", memory="128m", pids_limit=64, image="alpine:3.20")
        )
        workspace = backend.config.workspace("r")
        argv = backend.build_argv("echo hi", workspace, network="none")
        for flag in ("--read-only", "--cap-drop=ALL", "--network=none", "--memory=128m",
                     "--pids-limit=64"):
            self.assertIn(flag, argv)
        self.assertIn("no-new-privileges", argv)
        self.assertIn("1000:1000", argv)
        self.assertEqual(argv[-3:], ["sh", "-c", "echo hi"])

    def test_build_argv_network_bridge_only_when_asked(self):
        backend = DockerBackend(_config())
        workspace = backend.config.workspace("r")
        self.assertIn("--network=bridge", backend.build_argv("curl x", workspace, network="bridge"))
        self.assertIn("--network=none", backend.build_argv("curl x", workspace, network="weird"))

    def test_cwd_outside_workspace_is_ignored(self):
        backend = DockerBackend(_config())
        workspace = backend.config.workspace("r")
        argv = backend.build_argv("ls", workspace, cwd="../../etc")
        self.assertEqual(argv[argv.index("-w") + 1], "/workspace")

    def test_execute_returns_result(self):
        backend = DockerBackend(_config())
        proc = _FakeProc(out=b"hi\n", returncode=0)
        with patch(_SUBPROCESS, new=AsyncMock(return_value=proc)) as mocked:
            result = _run(backend.execute("echo hi", room_id="r"))
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "hi\n")
        self.assertFalse(result.truncated)
        self.assertEqual(mocked.call_args.args[0], "docker")

    def test_execute_names_container(self):
        backend = DockerBackend(_config())
        proc = _FakeProc(out=b"hi\n")
        with patch(_SUBPROCESS, new=AsyncMock(return_value=proc)) as mocked:
            _run(backend.execute("echo hi", room_id="r"))
        argv = mocked.call_args.args
        self.assertIn("--name", argv)
        self.assertTrue(argv[argv.index("--name") + 1].startswith("kazi-exec-"))

    def test_missing_docker_maps_to_error(self):
        backend = DockerBackend(_config())
        with patch(_SUBPROCESS, side_effect=FileNotFoundError):
            result = _run(backend.execute("echo hi"))
        self.assertEqual(result.exit_code, 127)
        self.assertIn("not found", result.stderr)

    def test_timeout_removes_container(self):
        backend = DockerBackend(_config())
        proc = _FakeProc()
        proc.stdout = _HangingStream()
        proc.stderr = _HangingStream()
        with patch(_SUBPROCESS, new=AsyncMock(return_value=proc)), \
                patch.object(DockerBackend, "_remove_container", new=AsyncMock()) as remover:
            result = _run(backend.execute("sleep 100", room_id="r"))
        self.assertEqual(result.exit_code, 124)
        self.assertTrue(proc.killed)
        self.assertGreaterEqual(remover.await_count, 1)

    def test_output_is_truncated(self):
        backend = DockerBackend(_config(output_bytes_max=3))
        with patch(_SUBPROCESS, new=AsyncMock(return_value=_FakeProc(out=b"abcdef"))):
            result = _run(backend.execute("x"))
        self.assertEqual(result.stdout, "abc")
        self.assertTrue(result.truncated)


class TimeoutResolutionTests(unittest.TestCase):
    def test_non_numeric_timeout_is_value_error(self):
        backend = DockerBackend(_config())
        with self.assertRaises(ValueError):
            backend.resolve_timeout(["nope"])

    def test_timeout_clamped_to_max(self):
        backend = DockerBackend(_config(timeout_max=30))
        self.assertEqual(backend.resolve_timeout(999), 30)

    def test_non_positive_uses_default(self):
        backend = DockerBackend(_config(timeout_default=7))
        self.assertEqual(backend.resolve_timeout(0), 7)


class LocalBackendTests(unittest.TestCase):
    def test_runs_command_in_workspace(self):
        config = _config()
        backend = LocalBackend(config)
        command = f'"{sys.executable}" -c "print(42)"'
        result = _run(backend.execute(command, room_id="r"))
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertIn("42", result.stdout)
        self.assertTrue(config.workspace("r").is_dir())

    def test_get_backend_selects_by_profile(self):
        self.assertEqual(get_backend("open", _config()).name, "local")
        self.assertEqual(get_backend("standard", _config()).name, "docker")
        self.assertEqual(get_backend("locked", _config()).name, "docker")


class LocalBackendEnvironmentTests(unittest.TestCase):
    """GHSA-ppxm-j4p7-9cw2 — an ``open`` command must not inherit server secrets."""

    def _big_config(self) -> ShellExecConfig:
        return _config(output_bytes_max=2_000_000, timeout_default=30)

    def _child_env(self, backend: LocalBackend) -> dict:
        command = f'"{sys.executable}" -c "{_ENV_DUMP}"'
        result = _run(backend.execute(command, room_id="r"))
        self.assertEqual(result.exit_code, 0, result.stderr)
        return json.loads(result.stdout)

    def test_open_does_not_leak_env_example_secrets(self):
        backend = LocalBackend(self._big_config())
        with patch.dict(os.environ, _SECRET_ENV_VARS, clear=False):
            child = self._child_env(backend)
        leaked = sorted(name for name in _SECRET_ENV_VARS if name in child)
        self.assertEqual(leaked, [], f"server secrets reached an open command: {leaked}")

    def test_open_drops_a_secret_only_the_operator_added(self):
        backend = LocalBackend(self._big_config())
        with patch.dict(os.environ, {_UNKNOWN_OPERATOR_VAR: _UNKNOWN_OPERATOR_VALUE}):
            child = self._child_env(backend)
        self.assertNotIn(_UNKNOWN_OPERATOR_VAR, child)

    def test_open_keeps_programs_home_and_temp(self):
        backend = LocalBackend(self._big_config())
        with patch.dict(os.environ, {_UNKNOWN_OPERATOR_VAR: _UNKNOWN_OPERATOR_VALUE}):
            command = f'"{sys.executable}" -c "{_ENV_DUMP_AND_TEMP}"'
            result = _run(backend.execute(command, room_id="r"))
        self.assertEqual(result.exit_code, 0, result.stderr)
        first, second = result.stdout.splitlines()[:2]
        child = json.loads(first)
        self.assertTrue(child.get("PATH"), "PATH must reach the command")
        self.assertTrue(child.get("HOME") or child.get("USERPROFILE"), "home must reach the command")
        self.assertTrue(Path(second.strip()).is_dir(), "the temp directory must exist")
        self.assertNotIn(_UNKNOWN_OPERATOR_VAR, child)

    @unittest.skipUnless(sys.platform == "win32", "cmd.exe only starts on Windows")
    def test_open_starts_cmd_exe(self):
        backend = LocalBackend(self._big_config())
        result = _run(backend.execute("echo open-works", room_id="r"))
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertIn("open-works", result.stdout)

    def test_local_environment_is_an_allowlist(self):
        from orchestration.shell_exec.backends import LOCAL_ENV_ALLOWLIST, local_environment

        with patch.dict(os.environ, {_UNKNOWN_OPERATOR_VAR: _UNKNOWN_OPERATOR_VALUE}):
            env = local_environment()
        self.assertTrue(set(env) <= set(LOCAL_ENV_ALLOWLIST))
        self.assertNotIn(_UNKNOWN_OPERATOR_VAR, env)


class ProfileWorkspaceMountTests(unittest.TestCase):
    """GHSA-ppxm-j4p7-9cw2 — the ``locked`` workspace must be read-only."""

    def _argv(self, backend, command: str = "ls") -> list:
        workspace = backend.config.workspace("r")
        return backend.build_argv(command, workspace, network="none")

    def _workspace(self, backend) -> Path:
        return backend.config.workspace("r")

    def test_locked_mounts_the_workspace_read_only(self):
        backend = get_backend("locked", _config())
        self.assertEqual(backend.name, "docker")
        self.assertIn(f"{self._workspace(backend)}:/workspace:ro", self._argv(backend))

    def test_standard_keeps_a_writable_workspace(self):
        backend = get_backend("standard", _config())
        workspace = self._workspace(backend)
        argv = self._argv(backend)
        self.assertIn(f"{workspace}:/workspace", argv)
        self.assertNotIn(f"{workspace}:/workspace:ro", argv)
        for flag in ("--read-only", "--cap-drop=ALL", "--network=none"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[-3:], ["sh", "-c", "ls"])

    def test_locked_loosens_nothing_else(self):
        argv = self._argv(get_backend("locked", _config()), "touch x")
        self.assertIn("--read-only", argv)
        self.assertIn("--cap-drop=ALL", argv)
        self.assertIn("--network=none", argv)
        self.assertIn("no-new-privileges", argv)
        self.assertEqual(argv[-3:], ["sh", "-c", "touch x"])

    def test_standard_and_locked_differ_only_in_workspace_writability(self):
        standard = get_backend("standard", _config())
        locked = get_backend("locked", standard.config)
        std_argv = self._argv(standard)
        locked_argv = self._argv(locked)

        def mount_of(argv: list) -> str:
            return argv[argv.index("-v") + 1]

        self.assertEqual(mount_of(std_argv) + ":ro", mount_of(locked_argv))
        self.assertEqual(
            [a for a in std_argv if a != mount_of(std_argv)],
            [a for a in locked_argv if a != mount_of(locked_argv)],
        )
