from __future__ import annotations

import asyncio
from unittest.mock import patch

import httpx
from django.test import SimpleTestCase, override_settings

from orchestration.connectors.shell_connector import ShellConnector
from orchestration.tool_executor import get_tool_risk_info

_TOKEN = "fixture-value"  # nosec B105 test fixture, not a credential
_BLANK = ""


def _run(coro):
    return asyncio.run(coro)


def _ok_response(payload=None):
    return httpx.Response(200, json=payload or {
        "stdout": "hi\n", "stderr": "", "exit_code": 0, "duration_ms": 5, "truncated": False,
    })


class ShellConnectorTests(SimpleTestCase):
    def setUp(self):
        self.connector = ShellConnector()

    def _execute(self, parameters, context=None):
        return _run(self.connector.execute(parameters, context or {"room_id": "r"}))

    @override_settings(
        SHELL_EXEC_TOKEN=_TOKEN, SHELL_EXEC_HOST="127.0.0.1",
        SHELL_EXEC_PORT=8765, SHELL_EXEC_PROFILE="standard",
    )
    def test_happy_path_forwards_to_sidecar(self):
        captured = {}

        async def fake_post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _ok_response()

        with patch.object(httpx.AsyncClient, "post", new=fake_post):
            result = self._execute({"action": "run_command", "command": "echo hi"})
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"]["exit_code"], 0)
        self.assertEqual(captured["url"], "http://127.0.0.1:8765/exec")
        self.assertEqual(captured["headers"]["x-shell-exec-token"], _TOKEN)
        self.assertEqual(captured["json"]["network"], "none")
        self.assertEqual(captured["json"]["profile"], "standard")

    @override_settings(SHELL_EXEC_TOKEN=_TOKEN, SHELL_EXEC_PROFILE="standard")
    def test_empty_command_is_rejected(self):
        self.assertEqual(self._execute({"action": "run_command", "command": "  "})["status"], "error")

    @override_settings(SHELL_EXEC_TOKEN=_TOKEN, SHELL_EXEC_PROFILE="standard")
    def test_denied_command_refused_even_if_invoked_directly(self):
        result = self._execute({"action": "run_command", "command": "sudo rm -rf /"})
        self.assertEqual(result["status"], "error")

    @override_settings(SHELL_EXEC_TOKEN=_BLANK, SHELL_EXEC_PROFILE="standard")
    def test_unconfigured_shell_is_an_error(self):
        result = self._execute({"action": "run_command", "command": "echo hi"})
        self.assertEqual(result["status"], "error")

    @override_settings(SHELL_EXEC_TOKEN=_TOKEN, SHELL_EXEC_PROFILE="standard")
    def test_sidecar_down_is_normalized(self):
        async def boom(self, url, json=None, headers=None):
            raise httpx.ConnectError("down")

        with patch.object(httpx.AsyncClient, "post", new=boom):
            result = self._execute({"action": "run_command", "command": "echo hi"})
        self.assertEqual(result["status"], "error")

    @override_settings(SHELL_EXEC_TOKEN=_TOKEN, SHELL_EXEC_PROFILE="standard")
    def test_sidecar_401_is_normalized(self):
        async def unauth(self, url, json=None, headers=None):
            return httpx.Response(401, json={"error": "unauthorized"})

        with patch.object(httpx.AsyncClient, "post", new=unauth):
            result = self._execute({"action": "run_command", "command": "echo hi"})
        self.assertEqual(result["status"], "error")


class DynamicRiskGateTests(SimpleTestCase):
    @override_settings(SHELL_EXEC_PROFILE="standard")
    def test_run_command_is_always_gated_in_phase1(self):
        info = get_tool_risk_info("run_command", None, {"command": "echo hi"})
        self.assertTrue(info["requires_confirmation"])
        self.assertTrue(info["is_high_risk"])
        self.assertEqual(info["shell_tier"], "safe")

    @override_settings(SHELL_EXEC_PROFILE="standard")
    def test_destructive_command_reports_destructive_tier(self):
        info = get_tool_risk_info("run_command", None, {"command": "rm -rf /"})
        self.assertEqual(info["shell_tier"], "destructive")
        self.assertTrue(info["requires_confirmation"])

    @override_settings(SHELL_EXEC_PROFILE="standard")
    def test_network_command_reports_bounded_tier(self):
        info = get_tool_risk_info("run_command", None, {"command": "ping -c 1 1.1.1.1"})
        self.assertEqual(info["shell_tier"], "bounded")

    def test_risk_gate_without_tool_input_still_gates(self):
        info = get_tool_risk_info("run_command")
        self.assertTrue(info["requires_confirmation"])

    def test_existing_high_risk_action_is_unchanged(self):
        info = get_tool_risk_info("send_email", None, {"to": "a@b.c"})
        self.assertTrue(info["requires_confirmation"])
        self.assertNotIn("shell_tier", info)

    def test_low_risk_action_is_unchanged(self):
        info = get_tool_risk_info("get_weather", None, {"city": "Nairobi"})
        self.assertFalse(info["requires_confirmation"])
