"""Shell connector — routes ``run_command`` to the shell-exec sidecar.

Thin by design: it forwards the command and normalizes the result. No policy
lives here; the dynamic risk gate (``get_tool_risk_info``) decides the tier and
the sandbox on the sidecar host is the boundary.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

import httpx

from orchestration.base_connector import BaseConnector
from orchestration.shell.classifier import classify_command

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 120
_HTTP_SLACK_SECONDS = 10


def _setting(name: str, default: Any = None) -> Any:
    try:
        from django.conf import settings
        return getattr(settings, name, default)
    except Exception:
        return default


class ShellConnector(BaseConnector):
    name = "shell"
    version = "1.0.0"
    actions = ["run_command"]
    required_credentials = ["SHELL_EXEC_TOKEN"]

    def get_action_catalog_entries(self) -> List[Dict[str, Any]]:
        return [
            {
                "action": "run_command",
                "aliases": ["run_shell", "shell_command"],
                "service": "shell",
                "description": (
                    "Run a shell command on the shell-exec host inside a sandboxed "
                    "container (non-root, read-only, network off by default). Use for "
                    "diagnostics and system tasks: ping, dig, nslookup, ps, df, cat, etc."
                ),
                "params": {
                    "command": {
                        "type": "string",
                        "required": True,
                        "description": "The command line to run.",
                    },
                    "cwd": {
                        "type": "string",
                        "required": False,
                        "description": "Working directory relative to /workspace.",
                    },
                    "timeout_s": {
                        "type": "integer",
                        "required": False,
                        "description": "Timeout in seconds (capped by the sidecar).",
                    },
                },
                "return_description": "Returns stdout, stderr, and exit_code.",
                "risk_level": "high",
                "confirmation_policy": "always",
                "capability_gate": None,
            }
        ]

    async def execute(self, parameters: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        action = parameters.get("action", "run_command")
        if action != "run_command":
            return {"status": "error", "message": f"Unknown action: {action}"}

        command = str(parameters.get("command") or "").strip()
        if not command:
            return {"status": "error", "message": "run_command requires a non-empty 'command'."}

        token = self.get_credential("SHELL_EXEC_TOKEN")
        if not token:
            return {
                "status": "error",
                "message": "The shell is not configured (SHELL_EXEC_TOKEN is unset).",
            }

        profile = str(_setting("SHELL_EXEC_PROFILE", "standard") or "standard")
        classification = classify_command(command, profile=profile)
        if classification["tier"] == "denied":
            return {
                "status": "error",
                "message": f"This command is not allowed under the {profile} profile.",
            }

        host = str(_setting("SHELL_EXEC_HOST", "127.0.0.1") or "127.0.0.1")
        port = int(_setting("SHELL_EXEC_PORT", 8765) or 8765)
        timeout_max = int(_setting("SHELL_EXEC_TIMEOUT_MAX", 600) or 600)

        payload: Dict[str, Any] = {
            "command": command,
            "room_id": str(context.get("room_id") or context.get("user_id") or "default"),
            # Phase 1 standard profile: network off. #134 adds the toggle.
            "network": "none",
        }
        if parameters.get("cwd"):
            payload["cwd"] = str(parameters["cwd"])

        request_timeout = _DEFAULT_TIMEOUT
        raw_timeout = parameters.get("timeout_s")
        if raw_timeout:
            try:
                request_timeout = max(1, min(int(raw_timeout), timeout_max))
            except (TypeError, ValueError):
                request_timeout = _DEFAULT_TIMEOUT
            payload["timeout_s"] = request_timeout

        url = f"http://{host}:{port}/exec"
        try:
            async with httpx.AsyncClient(timeout=request_timeout + _HTTP_SLACK_SECONDS) as client:
                response = await client.post(
                    url, json=payload, headers={"x-shell-exec-token": token}
                )
        except httpx.HTTPError as exc:
            logger.warning("Shell sidecar unreachable: %s", exc)
            return {"status": "error", "message": "The shell sidecar is unreachable."}

        if response.status_code == 401:
            return {"status": "error", "message": "The shell sidecar rejected the request token."}
        if response.status_code != 200:
            return {
                "status": "error",
                "message": f"The shell sidecar returned status {response.status_code}.",
            }
        try:
            data = response.json()
        except ValueError:
            return {"status": "error", "message": "The shell sidecar returned an invalid response."}

        return {
            "status": "success",
            "message": f"Command exited with code {data.get('exit_code')}.",
            "data": data,
        }
