"""Workflow runtime helpers for approval, replay, and operator summaries."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from orchestration.action_catalog import is_high_risk_action, resolve_action_alias
from orchestration.action_receipts import requires_confirmation

from .utils import get_context_value


APPROVAL_TIMEOUT_POLICIES = {"cancel", "continue", "fail"}


def get_step_id(step: Dict[str, Any], index: int = 0) -> str:
    return str(step.get("id") or step.get("action") or f"step_{index + 1}")


def step_requires_approval(step: Dict[str, Any]) -> bool:
    action = resolve_action_alias(step.get("action"))
    return bool(step.get("requires_approval")) or is_high_risk_action(action) or requires_confirmation(action)


def get_approval_timeout_minutes(step: Dict[str, Any], default: int = 60) -> int:
    value = step.get("approval_timeout_minutes", default)
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, value)


def get_timeout_policy(step: Dict[str, Any], default: str = "cancel") -> str:
    policy = str(step.get("on_timeout") or default).strip().lower()
    return policy if policy in APPROVAL_TIMEOUT_POLICIES else default


def get_step_timeout_seconds(step: Dict[str, Any], default: int = 300) -> int:
    value = step.get("timeout_seconds", default)
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(5, value)


def get_step_max_attempts(step: Dict[str, Any], default: int = 3) -> int:
    value = step.get("max_attempts", default)
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, value)


def is_step_safe_to_replay(step: Dict[str, Any]) -> bool:
    # Honor explicit step-level overrides — both True AND False. The previous
    # `if bool(step.get("safe_to_replay")): return True` only honored True,
    # silently ignoring `safe_to_replay: false` (v0.4.1 Bug #2A).
    explicit = step.get("safe_to_replay")
    if explicit is not None:
        return bool(explicit)
    action = resolve_action_alias(step.get("action"))
    return not is_high_risk_action(action) and not requires_confirmation(action)


def get_replayable_slice(
    workflow_definition: Dict[str, Any],
    *,
    from_step_id: Optional[str] = None,
) -> Tuple[bool, Optional[str], List[Dict[str, Any]]]:
    steps = list(workflow_definition.get("steps") or [])
    if not steps:
        return False, "Workflow has no steps to replay.", []

    start_index = 0
    if from_step_id:
        for idx, step in enumerate(steps):
            if get_step_id(step, idx) == from_step_id:
                start_index = idx
                break
        else:
            return False, f"Unknown replay step '{from_step_id}'.", []

    replay_steps = steps[start_index:]
    unsafe = [get_step_id(step, idx + start_index) for idx, step in enumerate(replay_steps) if not is_step_safe_to_replay(step)]
    if unsafe:
        joined = ", ".join(unsafe[:3])
        return False, f"Replay is blocked because these steps are not safe to replay: {joined}.", []

    return True, None, replay_steps


def resolve_step_idempotency_key(step: Dict[str, Any], context: Dict[str, Any]) -> Optional[str]:
    source = str(step.get("idempotency_key_source") or "").strip()
    if not source:
        return None

    if source in {"workflow", "workflow.id"}:
        raw_value = get_context_value("workflow.id", context)
    elif source == "trigger":
        raw_value = context.get("trigger")
    elif source == "execution":
        raw_value = context.get("execution_id")
    else:
        raw_value = get_context_value(source, context)

    if raw_value is None:
        return None

    payload = {
        "step_id": get_step_id(step),
        "source": source,
        "value": raw_value,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return digest[:32]


def collect_receipt_ids(result: Optional[Dict[str, Any]]) -> List[int]:
    if not isinstance(result, dict):
        return []
    receipt = result.get("receipt") or {}
    receipt_id = receipt.get("id")
    if isinstance(receipt_id, int):
        return [receipt_id]
    return []


def build_result_summary(result: Optional[Dict[str, Any]], max_steps: int = 4) -> str:
    if not isinstance(result, dict):
        return "No workflow output was recorded."

    lines: List[str] = []
    for key, value in result.items():
        if key in {"trigger", "workflow", "user_id", "preferences", "execution_id"}:
            continue
        if not isinstance(value, dict):
            continue
        status = value.get("status")
        if isinstance(value.get("results"), list):
            count = len(value.get("results") or [])
            lines.append(f"{key}: returned {count} result(s)")
        elif status == "success":
            message = value.get("message") or "completed successfully"
            lines.append(f"{key}: {message}")
        elif status in {"error", "failed", "timed_out", "rejected"}:
            message = value.get("error") or value.get("message") or status
            lines.append(f"{key}: {message}")
        elif status:
            lines.append(f"{key}: {status}")
        if len(lines) >= max_steps:
            break

    return "\n".join(lines) if lines else "Workflow completed without structured step output."


def build_failure_summary(
    *,
    step_id: Optional[str],
    error_message: str,
    waiting_on: str = "",
) -> Tuple[str, str]:
    label = step_id or "workflow"
    error_message = (error_message or "").strip() or "Unknown failure"
    summary = f"{label} failed: {error_message}"
    if waiting_on == "approval":
        return summary, "Review the approval request, then rerun from the blocked step if the step is replay-safe."
    lowered = error_message.lower()
    if "timeout" in lowered:
        return summary, "Increase the step timeout or rerun from the failed step after confirming the external service is healthy."
    if "blocked" in lowered or "policy" in lowered:
        return summary, "Update the workflow policy or step parameters before rerunning."
    if "option" in lowered or "search first" in lowered:
        return summary, "Add a discovery step before this action so the workflow has the required context."
    return summary, "Inspect the execution detail, adjust the step parameters, and rerun if the step is replay-safe."


def build_suggestions_for_step(step: Dict[str, Any], outcome: str) -> List[Dict[str, Any]]:
    action = resolve_action_alias(step.get("action"))
    step_id = get_step_id(step)
    suggestions: List[Dict[str, Any]] = []

    if action == "withdraw" and not bool(step.get("requires_approval")):
        suggestions.append({
            "suggestion_type": "approval_rule",
            "title": "Always ask before withdraw",
            "summary": "Require a durable human approval before this withdrawal step runs.",
            "proposed_changes": {"step_id": step_id, "requires_approval": True},
        })

    if action == "send_email":
        suggestions.append({
            "suggestion_type": "summary_before_email",
            "title": "Send summary before email",
            "summary": "Add or reuse a summary step so emails contain clearer operator-facing context.",
            "proposed_changes": {"step_id": step_id, "hint": "prepend_summary"},
        })

    if outcome in {"rejected", "failed"}:
        suggestions.append({
            "suggestion_type": "skip_step",
            "title": "Skip this step next time",
            "summary": "Convert this step to a conditional or remove it from future workflow revisions.",
            "proposed_changes": {"step_id": step_id, "hint": "review_or_remove"},
        })

    return suggestions
