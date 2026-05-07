"""
Echo connector — the minimal Kazi connector.

One action. No credentials. Returns whatever input it receives.

Use this as the starting point when building your own connector:
copy the directory, rename the class, change the `actions` list, and
implement `execute()`.

The runtime auto-discovers any `*_connector.py` file under
`examples/connectors/*/` and registers any `BaseConnector` subclass it
finds. No manifest required.

See `docs/writing-a-connector.md` for the full guide.
"""
from __future__ import annotations

from typing import Any, Dict, List

from orchestration.base_connector import BaseConnector


class EchoConnector(BaseConnector):
    name = "echo"
    version = "1.0.0"
    actions = ["echo"]
    required_credentials: List[str] = []

    def get_action_catalog_entries(self) -> List[Dict[str, Any]]:
        return [
            {
                "action": "echo",
                "service": "echo",
                "description": "Echo the input back. For testing and demos.",
                "params": {
                    "message": {
                        "type": "string",
                        "required": True,
                        "description": "The message to echo back.",
                    },
                },
                "risk_level": "low",
            },
        ]

    async def execute(
        self, parameters: Dict[str, Any], context: Dict[str, Any]
    ) -> Dict[str, Any]:
        action = parameters.get("action", "echo")
        if action != "echo":
            return {"status": "error", "message": f"Unknown action: {action}"}

        message = parameters.get("message", "")
        return {
            "status": "success",
            "message": f"Echo: {message}",
            "data": {
                "input": message,
                "user_id": context.get("user_id"),
                "connector": self.name,
            },
        }
