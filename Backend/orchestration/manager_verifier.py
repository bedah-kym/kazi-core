"""
Deterministic manager verifier — stub module.

The full verifier (dependency ordering, missing-param detection, plan fixing)
is available in Kazi Pro. This stub passes steps through unchanged.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional


class ManagerVerifier:
    """
    Stub verifier that approves all plans without rewriting them.

    The full ManagerVerifier rewrites step ordering, resolves dependencies,
    normalises parameter aliases, and catches missing required params.
    Available in Kazi Pro.
    """

    def __init__(self, capabilities: Optional[Dict[str, Any]] = None):
        self.capabilities = capabilities or {}

    def review_steps(
        self,
        steps: List[Dict[str, Any]],
        message: str,
        preferences: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not isinstance(steps, list) or not steps:
            return {
                "verdict": "ask_user",
                "assistant_message": "I need a bit more detail to proceed.",
                "steps": [],
                "missing_fields": [],
            }
        return {
            "verdict": "approve",
            "reason": "approved",
            "assistant_message": "",
            "steps": copy.deepcopy(steps),
            "missing_fields": [],
        }

    def review_execution_result(
        self,
        execution_result: Dict[str, Any],
        workflow_definition: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        if not isinstance(execution_result, dict):
            return None
        errors = []
        for key, value in execution_result.items():
            if not isinstance(value, dict):
                continue
            if value.get("status") == "error":
                error_text = value.get("error") or "Unknown error"
                errors.append(f"{key}: {error_text}")
        if errors:
            joined = "; ".join(errors[:3])
            return f"I hit a snag while running the workflow. {joined}"
        return None
