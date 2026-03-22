"""
Base connector class for Kazi plugins.

All connectors (built-in and community) extend BaseConnector.
This is the primary extension point for teaching Kazi new skills.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from orchestration.connectors.connector_error import ConnectorError

logger = logging.getLogger(__name__)


class BaseConnector:
    """
    Base class for all Kazi connectors.

    Subclass this to create a new connector. At minimum, implement:
    - ``name``, ``actions`` class attributes
    - ``get_action_catalog_entries()``
    - ``execute()``

    Example::

        class WeatherConnector(BaseConnector):
            name = "weather"
            version = "1.0.0"
            actions = ["get_weather"]
            required_credentials = ["OPENWEATHER_API_KEY"]

            def get_action_catalog_entries(self):
                return [{
                    "action": "get_weather",
                    "service": "weather",
                    "description": "Get current weather for a city",
                    "params": {
                        "city": {"type": "string", "required": True,
                                 "description": "City name"},
                    },
                    "risk_level": "low",
                }]

            async def execute(self, parameters, context):
                city = parameters.get("city")
                # ... call weather API ...
                return {"status": "success", "data": {...}}
    """

    # Override in subclass
    name: str = ""
    version: str = "0.1.0"
    actions: List[str] = []
    required_credentials: List[str] = []

    def validate_config(self) -> Tuple[bool, str]:
        """
        Check whether all required credentials are available.

        Returns:
            (ok, message) — True if ready, False with explanation if not.
        """
        missing = [
            key for key in self.required_credentials
            if not self.get_credential(key)
        ]
        if missing:
            return False, f"{self.name}: missing credentials: {', '.join(missing)}"
        return True, ""

    def get_credential(self, key: str) -> Optional[str]:
        """
        Look up a credential by name.

        Checks environment variables first, then Django settings.
        Returns None if not found.
        """
        value = os.environ.get(key)
        if value:
            return value
        try:
            from django.conf import settings
            return getattr(settings, key, None)
        except Exception:
            return None

    def get_action_catalog_entries(self) -> List[Dict[str, Any]]:
        """
        Return ACTION_CATALOG-format entries for this connector's actions.

        Each entry should be a dict with at least:
        - action (str): unique action name
        - service (str): service group name
        - description (str): what this action does
        - params (dict): parameter definitions
        - risk_level (str): "low", "medium", or "high"

        Optional fields:
        - aliases (list[str]): alternative names
        - confirmation_policy (str): "always", "high_risk", or "never"
        - capability_gate (str): permission required
        """
        return []

    async def execute(self, parameters: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute an action.

        Args:
            parameters: Dict with "action" key plus action-specific params.
            context: Dict with "user_id", "room_id", and other context.

        Returns:
            Dict with at least a "status" key ("success" or "error").
            On success, include "data" and/or "message".
            On error, include "message" with a user-friendly explanation.

        Raises:
            ConnectorError: For structured errors with retry semantics.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement execute()"
        )

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r} v{self.version}>"
