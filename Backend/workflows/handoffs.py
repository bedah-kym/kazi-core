"""Internal specialist handoffs (v0.7 W-G2, issue #137).

A named specialist (#203) receives a scoped task, works under budget caps, and
returns a result with artifacts and receipts — built on the existing
``delegate_task`` sub-agent machinery. One owner per stage; the originating
conversation sees the handoff and its result, so handoffs are never hidden.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.utils import timezone

from orchestration.telemetry import record_event

from .models import Handoff, Persona

logger = logging.getLogger(__name__)


def _default_budget() -> int:
    try:
        return max(1, int(getattr(settings, "HANDOFF_DEFAULT_BUDGET", 20) or 20))
    except (TypeError, ValueError):
        return 20


def _max_budget() -> int:
    try:
        return max(1, int(getattr(settings, "HANDOFF_MAX_BUDGET", 50) or 50))
    except (TypeError, ValueError):
        return 50


def _granted_budget(task: Dict[str, Any]) -> int:
    """The stored budget, clamped again at run time: the task is editable in the admin."""
    try:
        value = int(task.get("budget") or _default_budget())
    except (TypeError, ValueError, OverflowError):
        value = _default_budget()
    return min(max(1, value), _max_budget())


def create_handoff(
    *,
    to_persona_name: str,
    requested_by,
    task: str,
    context_refs: Optional[List[str]] = None,
    working_scope: Optional[List[str]] = None,
    budget: Optional[int] = None,
    room_id: Optional[int] = None,
    from_persona: Optional[Persona] = None,
) -> Handoff:
    """Create a handoff. Fails closed for unknown/inactive or foreign personas.

    The requesting user's access is the ceiling: the target persona must be
    owned by the requester, and the working scope can never exceed the
    persona's tool scope.
    """
    persona = Persona.objects.filter(
        name=to_persona_name, user_id=requested_by.id, status="active",
    ).first()
    if persona is None:
        raise ValueError(f"No active persona named '{to_persona_name}' owned by you.")

    persona_scope = {str(item).strip().lower() for item in (persona.tool_scope or []) if item}
    if not persona_scope:
        raise ValueError("The persona has no tool scope to hand off.")
    scope = sorted({str(item).strip().lower() for item in (working_scope or []) if item}) or sorted(persona_scope)
    if not set(scope) <= persona_scope:
        raise ValueError("Working scope exceeds the persona's tool scope.")

    configured_budget = int(budget or _default_budget())
    configured_budget = min(max(1, configured_budget), _max_budget())

    return Handoff.objects.create(
        from_persona=from_persona,
        to_persona=persona,
        requested_by=requested_by,
        room_id=room_id,
        task={
            "task": str(task or ""),
            "context_refs": list(context_refs or []),
            "working_scope": scope,
            "budget": configured_budget,
        },
        status="pending",
    )


def _record_receipt(handoff: Handoff, outcome: str, *, tools_used: List[str], reason: str = "") -> Dict[str, Any]:
    receipt = {
        "status": outcome,
        "tools_used": tools_used,
        "reason": reason,
        "at": timezone.now().isoformat(),
    }
    receipts = list(handoff.receipts or [])
    receipts.append(receipt)
    handoff.receipts = receipts
    record_event("handoff_receipt", {
        "handoff_id": handoff.id,
        "to_persona": handoff.to_persona.name,
        "requested_by": handoff.requested_by_id,
        "room_id": handoff.room_id,
        "outcome": outcome,
        "reason": reason,
        "tools_used": tools_used,
    })
    return receipt


async def run_handoff(
    handoff_id: int,
    *,
    context: Optional[Dict[str, Any]] = None,
    preferences: Optional[Dict[str, Any]] = None,
    parent_system: str = "",
    parent_tools: Optional[List[Dict[str, Any]]] = None,
    executor=None,
) -> Handoff:
    """Run a pending handoff through the sub-agent machinery.

    ``executor`` is injectable for tests; the real path is
    ``orchestration.agent_loop._run_sub_agent``. Out-of-scope tool use in the
    result is denied deterministically; budget exhaustion returns partial work
    plus the reason — never a silent failure.
    """
    from asgiref.sync import sync_to_async

    def _claim() -> bool:
        updated = Handoff.objects.filter(id=handoff_id, status="pending").update(status="in_progress")
        return updated == 1

    claimed = await sync_to_async(_claim)()
    if not claimed:
        # Already running or finished: concurrent callers and retries must
        # never re-run the sub-agent.
        return await sync_to_async(Handoff.objects.select_related("to_persona").get)(id=handoff_id)

    handoff = await sync_to_async(Handoff.objects.select_related("to_persona").get)(id=handoff_id)
    if (
        handoff.to_persona.status != "active"
        or handoff.to_persona.user_id != handoff.requested_by_id
    ):
        # Re-verified at execution time, not just creation: a handoff can
        # never run under an archived or foreign persona.
        await sync_to_async(_fail_handoff)(
            handoff, "Target persona is inactive or not owned by the requester.",
        )
        return handoff

    task = handoff.task or {}
    scope = {str(item).strip().lower() for item in (task.get("working_scope") or []) if item}
    budget = _granted_budget(task)
    tool_input = {
        "task": task.get("task") or "",
        "tools": ",".join(sorted(scope)),
    }
    # The budget is granted by the harness through the context; the sub-agent
    # ignores any cap that arrives in tool input, which the model can write.
    run_context = dict(context or {})
    run_context["sub_agent_tool_call_cap"] = budget
    # Receipts and persona scope are keyed on whose run this is: the handoff
    # row decides that, not the caller.
    run_context["user_id"] = handoff.requested_by_id
    if handoff.room_id:
        run_context["room_id"] = handoff.room_id

    if executor is None:
        from orchestration.agent_loop import _run_sub_agent

        executor = _run_sub_agent

    try:
        result = await executor(
            tool_input, run_context, preferences, parent_system, list(parent_tools or []),
        )
    except Exception as exc:
        logger.warning("Handoff %s executor failed: %s", handoff.id, exc)
        await sync_to_async(_fail_handoff)(handoff, str(exc))
        return handoff

    result = result if isinstance(result, dict) else {"status": "error", "summary": str(result)}
    if result.get("status") != "success":
        await sync_to_async(_fail_handoff)(
            handoff, str(result.get("message") or result.get("summary") or "executor_error"),
        )
        return handoff

    tools_used = [str(tool).strip().lower() for tool in (result.get("tools_used") or []) if tool]
    stopped_reason = str(result.get("stopped_reason") or "")

    outcome = "completed"
    reason = ""
    if scope and any(tool not in scope for tool in tools_used):
        outcome = "denied"
        reason = "out_of_scope_tool_use"
    elif len(tools_used) > budget:
        outcome = "partial"
        reason = "budget_exhausted"
    elif stopped_reason:
        outcome = "partial"
        reason = stopped_reason

    await sync_to_async(_complete_handoff)(
        handoff, result, tools_used, outcome, reason,
    )
    _record_receipt(handoff, outcome, tools_used=tools_used, reason=reason)
    await sync_to_async(lambda: handoff.save(update_fields=["receipts", "updated_at"]))()
    return handoff


def _publish_room_note(handoff: Handoff, text: str) -> None:
    """Make the handoff visible in the originating conversation."""
    if not handoff.room_id:
        return
    try:
        from chatbot.context_manager import ContextManager
        from chatbot.models import Chatroom

        room = Chatroom.objects.filter(id=handoff.room_id).first()
        if room is None:
            return
        ContextManager.add_note(
            room,
            "handoff",
            text,
            created_by=handoff.requested_by,
            priority="medium",
        )
    except Exception as exc:
        logger.warning("Handoff room note failed: %s", exc)


def _complete_handoff(handoff: Handoff, result: Dict[str, Any], tools_used: List[str], outcome: str, reason: str) -> None:
    handoff.status = "completed" if outcome in ("completed", "partial") else "failed"
    handoff.outcome = outcome
    handoff.result = {
        "summary": result.get("summary") or "",
        "stopped_reason": result.get("stopped_reason") or reason,
        "tokens_used": result.get("tokens_used", 0),
    }
    handoff.artifacts = list(result.get("artifacts") or [])
    handoff.budget_used = len(tools_used)
    handoff.stopped_reason = reason
    handoff.completed_at = timezone.now()
    handoff.save(update_fields=[
        "status", "outcome", "result", "artifacts", "budget_used",
        "stopped_reason", "completed_at", "updated_at",
    ])
    _publish_room_note(
        handoff,
        f"Handoff #{handoff.id} to '{handoff.to_persona.name}' {outcome}"
        + (f" ({reason})" if reason else "")
        + f": {handoff.result['summary'][:300]}",
    )


def _fail_handoff(handoff: Handoff, error: str) -> None:
    handoff.status = "failed"
    handoff.outcome = "failed"
    handoff.result = {"error": error}
    handoff.stopped_reason = "executor_error"
    handoff.completed_at = timezone.now()
    handoff.save(update_fields=[
        "status", "outcome", "result", "stopped_reason", "completed_at", "updated_at",
    ])
    _record_receipt(handoff, "failed", tools_used=[], reason=error)
    handoff.save(update_fields=["receipts", "updated_at"])
    _publish_room_note(
        handoff,
        f"Handoff #{handoff.id} to '{handoff.to_persona.name}' failed: {error[:300]}",
    )
