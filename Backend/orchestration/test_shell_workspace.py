from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from orchestration.shell_exec.backends import (
    LocalBackend,
    ShellExecConfig,
    restore_snapshot,
    snapshot_workspace,
)


def _config(**overrides) -> ShellExecConfig:
    root = overrides.pop("root", None) or Path(tempfile.mkdtemp(prefix="kazi-ws-"))
    defaults = dict(root=Path(root), token="t", timeout_default=5, timeout_max=10, output_bytes_max=1000)  # nosec B106 test fixture
    defaults.update(overrides)
    return ShellExecConfig(**defaults)


def _run(coro):
    if sys.platform == "win32" and hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
        policy = asyncio.WindowsProactorEventLoopPolicy()
        with asyncio.Runner(loop_factory=policy.new_event_loop) as runner:
            return runner.run(coro)
    return asyncio.run(coro)


class SnapshotTests(unittest.TestCase):
    def test_snapshot_and_restore_round_trip(self):
        config = _config()
        workspace = config.workspace("r")
        (workspace / "note.txt").write_text("v1", encoding="utf-8")
        snapshot = snapshot_workspace(config, "r")
        self.assertTrue(snapshot.endswith(".tar.gz"))
        (workspace / "note.txt").write_text("v2", encoding="utf-8")
        (workspace / "extra.txt").write_text("x", encoding="utf-8")

        restore_snapshot(config, "r", snapshot)

        self.assertEqual((workspace / "note.txt").read_text(encoding="utf-8"), "v1")
        self.assertFalse((workspace / "extra.txt").exists())

    def test_restore_unknown_snapshot_raises(self):
        config = _config()
        with self.assertRaises(ValueError):
            restore_snapshot(config, "r", "missing.tar.gz")

    def test_workspace_persists_across_executions(self):
        config = _config()
        backend = LocalBackend(config)
        write = f'"{sys.executable}" -c "import pathlib;pathlib.Path(\'persist.txt\').write_text(\'hello\')"'
        _run(backend.execute(write, room_id="r"))
        self.assertEqual(
            (config.workspace("r") / "persist.txt").read_text(encoding="utf-8"),
            "hello",
        )
        read = f'"{sys.executable}" -c "import pathlib;print(pathlib.Path(\'persist.txt\').read_text())"'
        result = _run(backend.execute(read, room_id="r"))
        self.assertIn("hello", result.stdout)


class ShellRollbackCommandTests(SimpleTestCase):
    @override_settings(SHELL_EXEC_ROOT=tempfile.mkdtemp(prefix="kazi-rb-"))
    def test_unknown_snapshot_raises_command_error(self):
        with self.assertRaises(CommandError):
            call_command("shell_rollback", room="r", snapshot="missing.tar.gz")
