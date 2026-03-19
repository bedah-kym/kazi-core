"""
Tool Executor for the Agentic Loop.

Single entry point to execute any tool call from the agent loop.
Reuses the existing MCPRouter connector map, applies safety checks,
and returns a standardized result dict.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional

from orchestration.action_catalog import (
    get_action_definition,
    is_high_risk_action,
    requires_confirmation,
    resolve_action_alias,
)
from orchestration.security_policy import is_prompt_injection, sanitize_parameters, should_block_action

logger = logging.getLogger(__name__)

# Lazy-loaded singleton for the connector map
_connector_map: Optional[Dict[str, Any]] = None


def _get_connector_map() -> Dict[str, Any]:
    """Lazy-load the connector map from MCPRouter (avoids circular imports)."""
    global _connector_map
    if _connector_map is not None:
        return _connector_map

    from orchestration.connectors.whatsapp_connector import WhatsAppConnector
    from orchestration.connectors.intersend_connector import IntersendPayConnector
    from orchestration.connectors.gmail_connector import GmailConnector
    from orchestration.connectors.quota_connector import QuotaConnector
    from orchestration.connectors.payment_connector import ReadOnlyPaymentConnector
    from orchestration.connectors.invoice_connector import InvoiceConnector
    from orchestration.connectors.travel_buses_connector import TravelBusesConnector
    from orchestration.connectors.travel_hotels_connector import TravelHotelsConnector
    from orchestration.connectors.travel_flights_connector import TravelFlightsConnector
    from orchestration.connectors.travel_transfers_connector import TravelTransfersConnector
    from orchestration.connectors.travel_events_connector import TravelEventsConnector
    from orchestration.connectors.itinerary_connector import ItineraryConnector

    # Import inline connectors from mcp_router
    from orchestration.mcp_router import (
        CalendarConnector,
        SearchConnector,
        WeatherConnector,
        GiphyConnector,
        CurrencyConnector,
        ReminderConnector,
    )

    _connector_map = {
        "schedule_meeting": CalendarConnector(),
        "check_availability": CalendarConnector(),
        "check_payments": ReadOnlyPaymentConnector(),
        "search_info": SearchConnector(),
        "get_weather": WeatherConnector(),
        "search_gif": GiphyConnector(),
        "convert_currency": CurrencyConnector(),
        "send_message": WhatsAppConnector(),
        "send_whatsapp": WhatsAppConnector(),
        "send_email": GmailConnector(),
        "set_reminder": ReminderConnector(),
        "check_quotas": QuotaConnector(),
        "check_balance": ReadOnlyPaymentConnector(),
        "list_transactions": ReadOnlyPaymentConnector(),
        "check_invoice_status": ReadOnlyPaymentConnector(),
        "create_invoice": InvoiceConnector(),
        "create_payment_link": IntersendPayConnector(),
        "withdraw": IntersendPayConnector(),
        "check_status": IntersendPayConnector(),
        "search_buses": TravelBusesConnector(),
        "search_hotels": TravelHotelsConnector(),
        "search_flights": TravelFlightsConnector(),
        "search_transfers": TravelTransfersConnector(),
        "search_events": TravelEventsConnector(),
        "create_itinerary": ItineraryConnector(),
        "view_itinerary": ItineraryConnector(),
        "add_to_itinerary": ItineraryConnector(),
        "book_travel_item": ItineraryConnector(),
    }
    return _connector_map


async def execute_tool(
    tool_name: str,
    tool_input: Dict[str, Any],
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Execute a tool call from the agent loop.

    Args:
        tool_name: The action name (e.g., 'search_flights', 'send_email').
        tool_input: The parameters the LLM provided for the tool.
        context: User context dict with user_id, room_id, username, preferences.

    Returns:
        Dict with at minimum {"status": "success"|"error", ...} plus tool-specific data.
    """
    start_time = time.monotonic()
    action = resolve_action_alias(tool_name)

    # 0a. Internal contact tools (no connector needed)
    from orchestration.contact_tools import _CONTACT_TOOL_MAP
    if action in _CONTACT_TOOL_MAP:
        try:
            result = await _CONTACT_TOOL_MAP[action](tool_input, context)
        except Exception as exc:
            logger.error("Contact tool error %s: %s", action, exc, exc_info=True)
            return {"status": "error", "message": f"Contact tool failed: {str(exc)}"}
        elapsed = round(time.monotonic() - start_time, 2)
        logger.info("Contact tool %s executed in %ss", action, elapsed)
        if not isinstance(result, dict):
            result = {"status": "success", "data": result}
        if "status" not in result:
            result["status"] = "success"
        return result

    # 0b. Internal memory tools (no connector needed)
    from orchestration.memory_tools import _MEMORY_TOOL_MAP
    if action in _MEMORY_TOOL_MAP:
        try:
            result = await _MEMORY_TOOL_MAP[action](tool_input, context)
        except Exception as exc:
            logger.error("Memory tool error %s: %s", action, exc, exc_info=True)
            return {"status": "error", "message": f"Memory tool failed: {str(exc)}"}
        elapsed = round(time.monotonic() - start_time, 2)
        logger.info("Memory tool %s executed in %ss", action, elapsed)
        if not isinstance(result, dict):
            result = {"status": "success", "data": result}
        if "status" not in result:
            result["status"] = "success"
        return result

    # 1. Validate action exists
    action_def = get_action_definition(action)
    if not action_def:
        return {
            "status": "error",
            "message": f"Unknown tool: {tool_name}",
        }

    # 2. Check capability gate
    gate = action_def.get("capability_gate")
    if gate:
        preferences = context.get("preferences") or {}
        if not preferences.get(gate, True):
            return {
                "status": "error",
                "message": f"The {action.replace('_', ' ')} capability is disabled in your settings.",
            }

    # 3. Security check — block prompt injection for sensitive actions.
    raw_query = context.get("raw_query") or context.get("user_message") or ""
    if should_block_action(raw_query, action):
        return {
            "status": "error",
            "message": "This request was blocked by the safety policy.",
        }
    # Additional guard when suspicious instructions are embedded directly in tool params.
    try:
        params_text = json.dumps(tool_input, default=str)
    except Exception:
        params_text = str(tool_input)
    if is_prompt_injection(params_text) and is_high_risk_action(action):
        return {
            "status": "error",
            "message": "This request was blocked by the safety policy.",
        }

    # 4. Sanitize parameters
    parameters = sanitize_parameters(dict(tool_input))
    parameters["action"] = action

    # 5. Get connector
    connectors = _get_connector_map()
    connector = connectors.get(action)
    if not connector:
        return {
            "status": "error",
            "message": f"No connector available for: {action}",
        }

    # 6. Execute
    try:
        result = await connector.execute(parameters, context)
    except Exception as exc:
        logger.error("Tool execution error for %s: %s", action, exc, exc_info=True)
        return {
            "status": "error",
            "message": f"Tool execution failed: {str(exc)}",
        }

    elapsed = round(time.monotonic() - start_time, 2)
    logger.info("Tool %s executed in %ss", action, elapsed)

    # 7. Normalize result format
    if not isinstance(result, dict):
        result = {"status": "success", "data": result}
    if "status" not in result:
        result["status"] = "success"

    return result


def get_tool_risk_info(
    tool_name: str,
    user_preferences: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Return risk metadata for a tool call. Used by the agent loop
    to decide whether to pause for confirmation.

    Supports user-configurable approval overrides via preferences:
        preferences.approval_overrides = {"send_email": "auto", "withdraw": "always"}
        "auto"   → skip confirmation
        "always" → force confirmation (default for high-risk)
    """
    action = resolve_action_alias(tool_name)
    catalog_requires = requires_confirmation(action)

    # Check user overrides
    if user_preferences:
        overrides = user_preferences.get("approval_overrides") or {}
        override = overrides.get(action)
        if override == "auto":
            catalog_requires = False
        elif override == "always":
            catalog_requires = True

    return {
        "is_high_risk": is_high_risk_action(action),
        "requires_confirmation": catalog_requires,
        "risk_level": (get_action_definition(action) or {}).get("risk_level", "low"),
    }
