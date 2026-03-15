"""
Generate Claude-compatible tool definitions from ACTION_CATALOG.

This is the bridge between Mathia's action catalog (single source of truth)
and the Anthropic tool_use API format. The agent loop sends these definitions
to the LLM so it can natively call tools.
"""
from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any, Dict, List, Optional

from orchestration.action_catalog import (
    ACTION_CATALOG,
    get_action_definition,
    get_capability_gate,
)

logger = logging.getLogger(__name__)


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
) -> List[Dict[str, Any]]:
    """
    Build the full list of tool definitions for the agent loop.

    Args:
        user_capabilities: Dict of capability flags (allow_email, allow_travel, etc.)
            If None, all tools are included.
        exclude_actions: List of action names to exclude.

    Returns:
        List of Claude-compatible tool definitions.
    """
    capabilities = user_capabilities or {}
    excluded = set(exclude_actions or [])
    tools: List[Dict[str, Any]] = []

    for action_def in ACTION_CATALOG:
        action_name = action_def["action"]

        # Skip excluded actions
        if action_name in excluded:
            continue

        # Check capability gate
        gate = action_def.get("capability_gate")
        if gate and capabilities:
            if not capabilities.get(gate, True):
                continue

        tools.append(build_tool_definition(action_def))

    # Append internal memory tools
    from orchestration.memory_tools import MEMORY_TOOL_DEFINITIONS
    tools.extend(MEMORY_TOOL_DEFINITIONS)

    return tools


def get_tool_names(
    user_capabilities: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Return just the action names available to a user."""
    capabilities = user_capabilities or {}
    names: List[str] = []
    for action_def in ACTION_CATALOG:
        gate = action_def.get("capability_gate")
        if gate and capabilities:
            if not capabilities.get(gate, True):
                continue
        names.append(action_def["action"])
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
