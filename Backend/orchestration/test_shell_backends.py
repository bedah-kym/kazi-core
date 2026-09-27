from __future__ import annotations

import asyncio
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


class _FakeProc:
    def __init__(self, out: bytes = b"", err: bytes = b"", returncode: int = 0):
        self._out, self._err, self.returncode = out, err, returncode
        self.killed = False

    async def communicate(self):
        return self._out, self._err

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


class _TimeoutProc(_FakeProc):
    async def communicate(self):
        raise asyncio.TimeoutError


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
            result = asyncio.run(backend.execute("echo hi", room_id="r"))
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "hi\n")
        self.assertFalse(result.truncated)
        self.assertEqual(mocked.call_args.args[0], "docker")

    def test_missing_docker_maps_to_error(self):
        backend = DockerBackend(_config())
        with patch(_SUBPROCESS, side_effect=FileNotFoundError):
            result = asyncio.run(backend.execute("echo hi"))
        self.assertEqual(result.exit_code, 127)
        self.assertIn("not found", result.stderr)

    def test_timeout_kills_and_reports_124(self):
        backend = DockerBackend(_config())
        proc = _TimeoutProc()
        with patch(_SUBPROCESS, new=AsyncMock(return_value=proc)):
            result = asyncio.run(backend.execute("sleep 100"))
        self.assertEqual(result.exit_code, 124)
        self.assertTrue(proc.killed)

    def test_output_is_truncated(self):
        backend = DockerBackend(_config(output_bytes_max=3))
        with patch(_SUBPROCESS, new=AsyncMock(return_value=_FakeProc(out=b"abcdef"))):
            result = asyncio.run(backend.execute("x"))
        self.assertEqual(result.stdout, "abc")
        self.assertTrue(result.truncated)


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
