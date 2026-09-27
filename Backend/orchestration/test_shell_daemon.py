from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from orchestration.shell_exec.backends import ExecResult, ShellExecConfig
from orchestration.shell_exec.daemon import create_app

_GOOD = "token-fixture"
_BAD = "wrong-fixture"
_EMPTY = ""


class _FakeBackend:
    name = "fake"

    def __init__(self, result=None, error=None):
        self.result = result or ExecResult("hi\n", "", 0, 1)
        self.error = error
        self.calls = []

    async def execute(self, command, *, room_id="default", cwd=None, timeout_s=None, network="none"):
        self.calls.append((command, room_id, cwd, timeout_s, network))
        if self.error:
            raise self.error
        return self.result


def _config(token: str = _GOOD) -> ShellExecConfig:
    return ShellExecConfig(root=Path(tempfile.mkdtemp(prefix="kazi-daemon-")), token=token)


async def _call(app, method, path, *, token=None, body=None, raw_body=None):
    raw = raw_body if raw_body is not None else (b"" if body is None else json.dumps(body).encode())
    delivered = False
    sent = []

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": raw, "more_body": False}

    async def send(message):
        sent.append(message)

    headers = []
    if token is not None:
        headers.append((b"x-shell-exec-token", token.encode()))
    scope = {"type": "http", "method": method, "path": path, "headers": headers}
    await app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    payload = next(m for m in sent if m["type"] == "http.response.body")["body"]
    return start["status"], json.loads(payload)


class DaemonTests(unittest.TestCase):
    def setUp(self):
        self.backend = _FakeBackend()
        self.app = create_app(_config(), backend=self.backend)

    def test_health(self):
        status, payload = asyncio.run(_call(self.app, "GET", "/health"))
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")

    def test_exec_requires_token(self):
        status, _ = asyncio.run(_call(self.app, "POST", "/exec", body={"command": "echo hi"}))
        self.assertEqual(status, 401)

    def test_exec_rejects_wrong_token(self):
        status, _ = asyncio.run(
            _call(self.app, "POST", "/exec", token=_BAD, body={"command": "echo hi"})
        )
        self.assertEqual(status, 401)

    def test_exec_runs_command(self):
        status, payload = asyncio.run(
            _call(self.app, "POST", "/exec", token=_GOOD, body={"command": "echo hi", "room_id": "r"})
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["stdout"], "hi\n")
        self.assertEqual(self.backend.calls[0][0], "echo hi")

    def test_profile_must_be_enabled(self):
        status, _ = asyncio.run(
            _call(self.app, "POST", "/exec", token=_GOOD,
                  body={"command": "echo hi", "profile": "open"})
        )
        self.assertEqual(status, 400)

    def test_bridge_network_is_accepted(self):
        status, _ = asyncio.run(
            _call(self.app, "POST", "/exec", token=_GOOD,
                  body={"command": "ping 1.1.1.1", "network": "bridge"})
        )
        self.assertEqual(status, 200)

    def test_unsupported_network_is_rejected(self):
        status, _ = asyncio.run(
            _call(self.app, "POST", "/exec", token=_GOOD,
                  body={"command": "ping 1.1.1.1", "network": "host"})
        )
        self.assertEqual(status, 400)

    def test_exec_missing_command(self):
        status, _ = asyncio.run(_call(self.app, "POST", "/exec", token=_GOOD, body={}))
        self.assertEqual(status, 400)

    def test_exec_invalid_json(self):
        status, _ = asyncio.run(
            _call(self.app, "POST", "/exec", token=_GOOD, raw_body=b"not json")
        )
        self.assertEqual(status, 400)

    def test_exec_backend_error_is_500(self):
        app = create_app(_config(), backend=_FakeBackend(error=RuntimeError("boom")))
        status, _ = asyncio.run(_call(app, "POST", "/exec", token=_GOOD, body={"command": "x"}))
        self.assertEqual(status, 500)

    def test_unknown_path(self):
        status, _ = asyncio.run(_call(self.app, "GET", "/nope"))
        self.assertEqual(status, 404)

    def test_empty_token_refuses_everything(self):
        app = create_app(_config(token=_EMPTY), backend=self.backend)
        status, _ = asyncio.run(_call(app, "POST", "/exec", token=_EMPTY, body={"command": "x"}))
        self.assertEqual(status, 401)


class RunShellExecCommandTests(SimpleTestCase):
    @override_settings(SHELL_EXEC_TOKEN=_EMPTY)
    def test_refuses_without_token(self):
        with self.assertRaises(CommandError):
            call_command("run_shell_exec")
