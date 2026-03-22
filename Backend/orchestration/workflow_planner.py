"""
Multi-step workflow planning — stub module.

The full workflow planner (plan decomposition, execution, and synthesis)
is available in Kazi Pro. This stub exports the same public API so that
the rest of the codebase degrades gracefully.
"""
from __future__ import annotations

import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

from orchestration.user_preferences import get_user_preferences  # noqa: F401 (re-export)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  Confidence thresholds (constants used by consumers.py)
# ---------------------------------------------------------------------------

LLM_CONFIDENCE_AUTO_EXECUTE = 0.85
LLM_CONFIDENCE_ASK_ONCE = 0.60
LLM_CONFIDENCE_ASK_ALL = 0.40
LLM_CONFIDENCE_REJECT = 0.20

# Backwards-compatible aliases
LLM_CONFIDENCE_EXECUTE = LLM_CONFIDENCE_AUTO_EXECUTE
LLM_CONFIDENCE_CONFIRM = LLM_CONFIDENCE_ASK_ONCE

_CONFIRM_WORDS = {
    "yes", "approve", "approved", "confirm", "confirmed", "go ahead", "proceed",
}


# ---------------------------------------------------------------------------
#  Public API — stub implementations
# ---------------------------------------------------------------------------

def looks_like_confirmation(message: str) -> bool:
    """Check if a message looks like a user confirmation."""
    lowered = message.strip().lower()
    return any(word in lowered for word in _CONFIRM_WORDS)


async def plan_user_request(
    message: str,
    history_text: str = "",
    user_id: Optional[int] = None,
    preferences: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Stub: always falls back to single-action routing.

    The full planner decomposes multi-step requests into ordered workflows.
    Available in Kazi Pro.
    """
    return {
        "mode": "single_action",
        "assistant_message": "",
        "workflow_definition": None,
        "confidence": 0.0,
    }


async def execute_adhoc_workflow(
    definition: Dict[str, Any],
    user_id: int,
    room_id: Optional[int],
    trigger_data: Optional[Dict[str, Any]] = None,
    wait_seconds: int = 12,
) -> Dict[str, Any]:
    """
    Stub: returns unavailable status.

    The full executor runs multi-step workflows via Temporal or inline.
    Available in Kazi Pro.
    """
    return {
        "status": "unavailable",
        "mode": "noop",
        "action": "adhoc_workflow",
        "message": "Multi-step workflow execution requires Kazi Pro.",
        "result": {},
        "data": {},
        "risk_level": "low",
        "requires_confirmation": False,
        "clarification_prompt": "",
        "receipt": None,
    }


async def synthesize_workflow_response_stream(
    user_message: str,
    workflow_definition: Dict[str, Any],
    execution_result: Dict[str, Any],
    status: str,
    error: Optional[str] = None,
    preferences: Optional[Dict[str, Any]] = None,
) -> AsyncGenerator[str, None]:
    """
    Stub: yields a single unavailability message.

    The full synthesizer streams natural-language summaries of workflow runs.
    Available in Kazi Pro.
    """
    yield "Multi-step workflow synthesis requires Kazi Pro."
