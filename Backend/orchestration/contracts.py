"""Shared result/event contracts for orchestration flows."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional


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
