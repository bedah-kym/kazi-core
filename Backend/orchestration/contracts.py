"""Shared result/event contracts for orchestration flows.

Stable runtime contracts are documented under `docs/contracts/`. This
module ships the in-tree builders + validators that back those docs.
Each contract carries a Major.Minor version constant so consumers can
pin against the surface they were tested against.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


# Contract versions — bump on any change documented in docs/contracts/.
CONNECTOR_EXECUTION_CONTRACT_VERSION = "1.1"
TOOL_SCHEMA_CONTRACT_VERSION = "1.0"
APPROVAL_CONTRACT_VERSION = "1.0"
EXECUTION_DETAIL_CONTRACT_VERSION = "1.0"
REPLAY_SAFETY_CONTRACT_VERSION = "1.0"


_VALID_RISK_LEVELS = {"low", "medium", "high"}
_VALID_PARAM_TYPES = {"string", "number", "integer", "boolean", "array", "object"}
_VALID_CONFIRMATION_POLICIES = {"always", "high_risk", "never"}
_ACTION_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def build_orchestration_result(
    *,
    status: str,
    action: Optional[str] = None,
    risk_level: str = "low",
    requires_confirmation: bool = False,
    clarification_prompt: str = "",
    data: Optional[Dict[str, Any]] = None,
    receipt: Optional[Dict[str, Any]] = None,
    reason: str = "",
    next_step: str = "",
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "status": status,
        "action": action or "",
        "risk_level": risk_level or "low",
        "requires_confirmation": bool(requires_confirmation),
        "clarification_prompt": clarification_prompt or "",
        "data": data if isinstance(data, dict) else (data or {}),
        "receipt": receipt if isinstance(receipt, dict) else None,
    }
    if clarification_prompt:
        payload["message"] = clarification_prompt
    elif isinstance(data, dict) and data.get("message"):
        payload["message"] = data.get("message")
    if reason:
        payload["reason"] = reason
    if next_step:
        payload["next_step"] = next_step
    return payload


def validate_catalog_entry(entry: Any) -> Tuple[bool, List[str]]:
    """Validate a single tool-schema catalog entry against contract v1.0.

    Returns (ok, errors). On any error the entry should be skipped by
    the consumer (the connector registry logs the warning and moves on
    so a single bad entry doesn't break boot). See
    docs/contracts/tool-schema.md for the canonical shape.
    """
    errors: List[str] = []

    if not isinstance(entry, dict):
        return False, [f"entry is not a dict: {type(entry).__name__}"]

    action = entry.get("action")
    if not isinstance(action, str) or not action:
        errors.append("missing or non-string 'action'")
    elif not _ACTION_NAME_RE.match(action):
        errors.append(f"action {action!r} must be snake_case (^[a-z][a-z0-9_]*$)")

    service = entry.get("service")
    if not isinstance(service, str) or not service:
        errors.append("missing or non-string 'service'")

    description = entry.get("description")
    if not isinstance(description, str) or not description:
        errors.append("missing or non-string 'description'")

    risk_level = entry.get("risk_level")
    if risk_level not in _VALID_RISK_LEVELS:
        errors.append(f"risk_level {risk_level!r} not in {sorted(_VALID_RISK_LEVELS)}")

    params = entry.get("params")
    if not isinstance(params, dict):
        errors.append("missing or non-dict 'params' (use {} for no-param actions)")
    else:
        for param_name, param_schema in params.items():
            if not isinstance(param_schema, dict):
                errors.append(f"params[{param_name!r}] is not a dict")
                continue
            ptype = param_schema.get("type")
            if ptype is not None and ptype not in _VALID_PARAM_TYPES:
                errors.append(
                    f"params[{param_name!r}].type {ptype!r} not in {sorted(_VALID_PARAM_TYPES)}"
                )
            pdesc = param_schema.get("description")
            if not isinstance(pdesc, str) or not pdesc:
                errors.append(f"params[{param_name!r}] missing 'description'")

    aliases = entry.get("aliases")
    if aliases is not None and not (
        isinstance(aliases, list) and all(isinstance(a, str) for a in aliases)
    ):
        errors.append("'aliases' must be a list of strings if provided")

    confirmation_policy = entry.get("confirmation_policy")
    if (
        confirmation_policy is not None
        and confirmation_policy not in _VALID_CONFIRMATION_POLICIES
    ):
        errors.append(
            f"confirmation_policy {confirmation_policy!r} not in "
            f"{sorted(_VALID_CONFIRMATION_POLICIES)}"
        )

    capability_gate = entry.get("capability_gate")
    if capability_gate is not None and not isinstance(capability_gate, str):
        errors.append("'capability_gate' must be a string if provided")

    replay_safe = entry.get("replay_safe")
    if replay_safe is not None and not isinstance(replay_safe, bool):
        errors.append("'replay_safe' must be a bool if provided")

    return (not errors), errors


def build_step_event(
    *,
    step_id: str,
    phase: str,
    state: str,
    message: str = "",
    timestamp: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "step_id": step_id,
        "phase": phase,
        "state": state,
        "message": message,
        "timestamp": timestamp or datetime.utcnow().isoformat(),
    }
