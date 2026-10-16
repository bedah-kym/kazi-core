"""
Generate Claude-compatible tool definitions from ACTION_CATALOG.

This is the bridge between Kazi's action catalog (single source of truth)
and the Anthropic tool_use API format. The agent loop sends these definitions
to the LLM so it can natively call tools.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from orchestration.action_catalog import (
    get_action_definition,
    iter_action_definitions,
    resolve_action_alias,
)

logger = logging.getLogger(__name__)


def _ensure_dynamic_connector_actions() -> Dict[str, Any]:
    """
    Ensure plugin connectors are discovered before building tool schemas.
    This allows dynamically registered connector actions to be visible to the LLM.

    Returns the discovered action -> connector map (empty on failure).
    """
    try:
        from orchestration.connector_registry import discover_connectors
        return discover_connectors()
    except Exception as exc:
        logger.debug("Dynamic connector discovery skipped: %s", exc)
        return {}


def _configured_actions(connectors: Dict[str, Any]) -> set:
    """Action names whose connector actually loaded (configured at boot).

    A connector skipped by ``validate_config`` (missing key, missing library)
    never lands in the map, so its actions are not offered. See T-F2.
    """
    return set(connectors.keys())


def _gate_allows(gate: str, capabilities: Dict[str, Any], capabilities_known: bool) -> bool:
    """Whether a capability gate permits offering an action.

    When no capabilities were supplied (``None``), nothing is filtered — the
    documented behaviour of ``get_tool_definitions`` for catalog callers. When a
    dict was supplied (even an empty one, which is what a failed or empty
    preference lookup becomes), the sensitive gates fail closed and the rest
    keep today's default (missing means on). Matches execution's gates.
    """
    if not capabilities_known:
        return True
    from orchestration.security_policy import SENSITIVE_CAPABILITY_GATES

    if gate in SENSITIVE_CAPABILITY_GATES:
        return bool(capabilities.get(gate, False))
    return bool(capabilities.get(gate, True))


def _normalize_scope(tool_scope: Optional[List[str]]) -> Optional[set]:
    """Canonical, lower-cased action names a persona scope allows (or None)."""
    if not tool_scope:
        return None
    return {resolve_action_alias(str(item)) for item in tool_scope if item}


def _build_input_schema(action_def: Dict[str, Any]) -> Dict[str, Any]:
    """Convert ACTION_CATALOG params into JSON Schema for Claude tool_use."""
    params = action_def.get("params") or {}
    properties: Dict[str, Any] = {}
    required: List[str] = []

    for param_name, spec in params.items():
        prop: Dict[str, Any] = {}
        param_type = spec.get("type", "string")
        if param_type == "number":
            prop["type"] = "number"
        elif param_type == "integer":
            prop["type"] = "integer"
        elif param_type == "boolean":
            prop["type"] = "boolean"
        else:
            prop["type"] = "string"

        if spec.get("description"):
            prop["description"] = spec["description"]

        if spec.get("enum"):
            prop["enum"] = spec["enum"]

        properties[param_name] = prop

        if spec.get("required"):
            required.append(param_name)

    schema: Dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required

    return schema


def build_tool_definition(action_def: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a single ACTION_CATALOG entry into a Claude tool definition."""
    return {
        "name": action_def["action"],
        "description": action_def.get("description") or action_def["action"].replace("_", " ").title(),
        "input_schema": _build_input_schema(action_def),
    }


def get_tool_definitions(
    user_capabilities: Optional[Dict[str, Any]] = None,
    exclude_actions: Optional[List[str]] = None,
    tool_scope: Optional[List[str]] = None,
    available_actions: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """
    Build the tool list offered to the model for one turn.

    A tool is offered only when all of these hold:
    * its connector loaded (configured at boot — no missing key or library);
    * its capability gate allows it (sensitive gates fail closed when a
      capabilities dict is supplied; passing ``None`` filters nothing);
    * it is inside the room persona's ``tool_scope``, when one is set.

    Nothing here inspects the message text (T-F2 R4/S10).

    Args:
        user_capabilities: Capability flags. ``None`` means "filter nothing"
            (catalog callers); any dict — even an empty one from an empty or
            failed preference lookup — applies the gates, sensitive ones closed.
        exclude_actions: Action names to drop regardless (e.g. ``search_info``
            when no search backend is available).
        tool_scope: A persona's allowed action names. Empty/None means no scope.
        available_actions: Override the loaded-connector set (tests). ``None``
            asks the connector registry.

    Returns:
        List of Claude-compatible tool definitions.
    """
    capabilities_known = user_capabilities is not None
    capabilities = user_capabilities or {}
    excluded = set(exclude_actions or [])
    scope = _normalize_scope(tool_scope)
    tools: List[Dict[str, Any]] = []

    connectors = _ensure_dynamic_connector_actions()
    if available_actions is None:
        available = _configured_actions(connectors)
    else:
        available = set(available_actions)

    for action_def in iter_action_definitions():
        action_name = action_def["action"]

        # Only tools whose connector can actually work here.
        if action_name not in available:
            continue

        # Skip excluded actions
        if action_name in excluded:
            continue

        # Persona scope is a ceiling on what may be offered.
        if scope is not None and action_name not in scope:
            continue

        # Check capability gate
        gate = action_def.get("capability_gate")
        if gate and not _gate_allows(gate, capabilities, capabilities_known):
            continue

        tools.append(build_tool_definition(action_def))

    # Append internal memory tools
    from orchestration.memory_tools import MEMORY_TOOL_DEFINITIONS
    tools.extend(MEMORY_TOOL_DEFINITIONS)

    # Append internal contact tools
    from orchestration.contact_tools import CONTACT_TOOL_DEFINITIONS
    tools.extend(CONTACT_TOOL_DEFINITIONS)

    # The internal tools carry no connector; a persona scope still bounds them.
    if scope is not None:
        tools = [tool for tool in tools if resolve_action_alias(tool["name"]) in scope]

    return tools


def get_tool_names(
    user_capabilities: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Return the action names a user can be offered (connector + gate aware)."""
    capabilities_known = user_capabilities is not None
    capabilities = user_capabilities or {}
    available = _configured_actions(_ensure_dynamic_connector_actions())
    names: List[str] = []
    for action_def in iter_action_definitions():
        action_name = action_def["action"]
        if action_name not in available:
            continue
        gate = action_def.get("capability_gate")
        if gate and not _gate_allows(gate, capabilities, capabilities_known):
            continue
        names.append(action_name)
    return names


def get_tool_metadata(action: str) -> Optional[Dict[str, Any]]:
    """Return risk_level, confirmation_policy, and capability_gate for an action."""
    action_def = get_action_definition(action)
    if not action_def:
        return None
    return {
        "risk_level": action_def.get("risk_level", "low"),
        "confirmation_policy": action_def.get("confirmation_policy", "never"),
        "capability_gate": action_def.get("capability_gate"),
        "service": action_def.get("service", ""),
    }
