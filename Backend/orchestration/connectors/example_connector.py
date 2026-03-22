"""
Example connector - a minimal template for building Kazi plugins.

This connector implements a simple "echo" action that returns whatever
input it receives. Use it as a starting point for your own connectors.

To create your own connector:
1. Copy this file and rename it (e.g., slack_connector.py)
2. Update name, version, actions, and required_credentials
3. Implement get_action_catalog_entries() with your action definitions
4. Implement execute() with your logic
5. Drop it in Backend/orchestration/connectors/ - it auto-registers

By default this example connector is disabled in discovery.
Set KAZI_ENABLE_EXAMPLE_CONNECTOR=true to enable it for local testing.

See docs/writing-a-connector.md for the full guide.
"""
from __future__ import annotations

from typing import Any, Dict, List

from orchestration.base_connector import BaseConnector


class ExampleConnector(BaseConnector):
    """A minimal example connector that echoes input back."""

    name = "example"
    version = "1.0.0"
    actions = ["echo"]
    required_credentials = []  # No API keys needed

    def get_action_catalog_entries(self) -> List[Dict[str, Any]]:
        return [
            {
                "action": "echo",
                "service": "example",
                "description": "Echo back the input (for testing and development)",
                "params": {
                    "message": {
                        "type": "string",
                        "required": True,
                        "description": "The message to echo back",
                    },
                },
                "risk_level": "low",
            },
        ]

    async def execute(self, parameters: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        action = parameters.get("action", "echo")
        message = parameters.get("message", "")

        if action != "echo":
            return {
                "status": "error",
                "message": f"Unknown action: {action}",
            }

        return {
            "status": "success",
            "message": f"Echo: {message}",
            "data": {
                "input": message,
                "user_id": context.get("user_id"),
                "connector": self.name,
            },
        }
