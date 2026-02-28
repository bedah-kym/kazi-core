"""Lightweight telemetry logging for orchestration events."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict, Optional

from django.conf import settings
import sys

_LOCK = Lock()


def _telemetry_enabled() -> bool:
    return getattr(settings, "ORCHESTRATION_TELEMETRY_ENABLED", True)


def _telemetry_path() -> str:
    configured = getattr(settings, "ORCHESTRATION_TELEMETRY_PATH", None)
    if configured:
        return str(configured)
    base_dir = getattr(settings, "BASE_DIR", os.getcwd())
    return os.path.join(base_dir, "telemetry", "orchestration.jsonl")


def _ensure_dir(path: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)


def record_event(event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
    if not _telemetry_enabled() or not event_type:
        return
    data = {
        "event": event_type,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    if isinstance(payload, dict):
        data.update(payload)
    line = json.dumps(data, ensure_ascii=True)
    path = _telemetry_path()
    try:
        _ensure_dir(path)
        with _LOCK:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        print(line, file=sys.stdout, flush=True)
    except Exception:
        # Telemetry should never block core flows.
        return


# Phase 3C: Correction Recording Functions

async def record_correction_signal(
    user_id: int,
    workspace_id: int,
    intent_action: str,
    correction_type: str,
    data: Dict[str, Any],
    original_ai_reasoning: Optional[str] = None,
    user_explanation: Optional[str] = None,
    confidence: int = 5,
) -> None:
    """
    Phase 3C: Record a user correction for learning.

    This is called when user corrects or adjusts AI behavior, enabling the system
    to learn from user feedback and personalize future responses.

    Args:
        user_id: User who made the correction
        workspace_id: Workspace context
        intent_action: The action being corrected (e.g., "search_flights")
        correction_type: Type of correction:
            - "parameter": User modified a parameter (e.g., "4 passengers not 3")
            - "result_selection": User picked different result (e.g., "not that one")
            - "preference": User stated preference (e.g., "I prefer aisle seats")
            - "workflow": User adjusted workflow (e.g., "skip the email")
            - "confirmation": User rejected confirmation (e.g., "That's wrong")
        data: Correction details, e.g., {param: "passengers", old: 3, new: 4}
        original_ai_reasoning: What the AI said/did before correction
        user_explanation: Why user is correcting (optional)
        confidence: User's confidence in the correction (1-10)

    Returns:
        None (logs asynchronously)

    Example:
        await record_correction_signal(
            user_id=123,
            workspace_id=456,
            intent_action="search_flights",
            correction_type="parameter",
            data={"param": "passengers", "old": 3, "new": 4},
            original_ai_reasoning="I assumed 3 based on last search",
            user_explanation="I always travel with my family of 4",
            confidence=9
        )
    """
    # Record to telemetry (always)
    record_event(
        "correction_signal",
        {
            "user_id": user_id,
            "workspace_id": workspace_id,
            "intent_action": intent_action,
            "correction_type": correction_type,
            "data": data,
            "confidence": confidence,
        }
    )

    # Also save to database for learning (async)
    try:
        from asgiref.sync import sync_to_async
        from users.models import CorrectionSignal, User, Workspace

        @sync_to_async
        def _save_correction():
            try:
                user = User.objects.get(id=user_id)
                workspace = Workspace.objects.get(id=workspace_id)
                CorrectionSignal.objects.create(
                    user=user,
                    workspace=workspace,
                    intent_action=intent_action,
                    correction_type=correction_type,
                    data=data,
                    original_ai_reasoning=original_ai_reasoning or "",
                    user_explanation=user_explanation or "",
                    confidence=confidence,
                )
            except Exception as e:
                # Log but don't fail - corrections shouldn't break the flow
                record_event("correction_signal_error", {"error": str(e)})

        await _save_correction()
    except Exception as e:
        # If async save fails, just log it
        record_event("correction_signal_save_failed", {"error": str(e)})


def load_user_correction_patterns(user_id: int, max_patterns: int = 5) -> Dict[str, Any]:
    """
    Phase 3C: Load correction patterns for a user to personalize future responses.

    Queries CorrectionSignal table to find common patterns in user corrections,
    which are then used to personalize LLM system prompts.

    Args:
        user_id: User ID to load patterns for
        max_patterns: Maximum number of patterns to return (default: 5)

    Returns:
        Dict with pattern structure:
        {
            "corrections_found": 5,
            "parameter_patterns": {
                "search_flights": {
                    "passengers": {"common_value": 4, "frequency": 3},
                    "class": {"common_value": "business", "frequency": 2}
                }
            },
            "preference_patterns": {
                "seat_preference": "aisle",
                "travel_time": "early_morning"
            },
            "workflow_patterns": {
                "send_email_after_booking": True,
                "prefer_quick_option": True
            }
        }

    Example:
        patterns = load_user_correction_patterns(user_id=123)
        if patterns["parameter_patterns"].get("search_flights", {}).get("passengers"):
            # User has consistent passenger count preference
            lm_prompt += "\nUser typically travels with {} passengers.".format(...)
    """
    try:
        from users.models import CorrectionSignal
        from django.db.models import Count

        # Find corrections for this user
        corrections = CorrectionSignal.objects.filter(user_id=user_id).order_by("-created_at")[:100]

        if not corrections:
            return {"corrections_found": 0}

        patterns = {
            "corrections_found": corrections.count(),
            "parameter_patterns": {},
            "preference_patterns": {},
            "workflow_patterns": {},
            "result_selection_patterns": {},
        }

        # Analyze parameter corrections
        for correction in corrections:
            if correction.correction_type == "parameter":
                action = correction.intent_action
                if action not in patterns["parameter_patterns"]:
                    patterns["parameter_patterns"][action] = {}

                data = correction.data or {}
                param_name = data.get("param")
                new_value = data.get("new")

                if param_name and new_value:
                    if param_name not in patterns["parameter_patterns"][action]:
                        patterns["parameter_patterns"][action][param_name] = {
                            "common_value": new_value,
                            "frequency": 1
                        }
                    else:
                        patterns["parameter_patterns"][action][param_name]["frequency"] += 1
                        # Update common value if this one appears more often
                        if patterns["parameter_patterns"][action][param_name]["frequency"] >= 2:
                            patterns["parameter_patterns"][action][param_name]["common_value"] = new_value

            elif correction.correction_type == "preference":
                data = correction.data or {}
                preference_key = data.get("key") or "unknown"
                preference_value = data.get("value")
                if preference_value:
                    patterns["preference_patterns"][preference_key] = preference_value

            elif correction.correction_type == "workflow":
                data = correction.data or {}
                workflow_key = data.get("workflow_step") or "unknown"
                action_value = data.get("action")
                if action_value:
                    patterns["workflow_patterns"][workflow_key] = action_value

        return patterns

    except Exception as e:
        # Return empty patterns on error - don't break the flow
        record_event("load_patterns_error", {"user_id": user_id, "error": str(e)})
        return {"corrections_found": 0, "error": str(e)}
