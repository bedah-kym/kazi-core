"""Routine contract and pause-on-absence (v0.7 W-F, #204).

A *skill* says how to do a task; a routine says when, on what, and what to do
when the source is missing. Every draft carrying a schedule or event trigger
must answer the routine questions before it can be enabled. Test runs and
grant readiness live here too; the grant gate itself is W-E (#159).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.db.models import Max
from django.utils import timezone

logger = logging.getLogger(__name__)

EVENT_TRIGGER_TYPES = {"schedule", "webhook"}
DEFAULT_APPROVAL_BOUNDARY = ["send", "purchase", "delete", "publish", "production_change"]
REQUIRED_ROUTINE_FIELDS = (
    "owner",
    "inputs",
    "output",
    "no_data_policy",
    "partial_completion",
    "idempotency",
)


def trigger_types(definition: Dict[str, Any]) -> set:
    """Trigger types present in a definition (schedule/webhook/manual)."""
    found = set()
    for trigger in (definition or {}).get("triggers", []) or []:
        if not isinstance(trigger, dict):
            continue
        trigger_type = str(trigger.get("trigger_type") or "").strip().lower()
        service = str(trigger.get("service") or "").strip().lower()
        event = str(trigger.get("event") or "").strip().lower()
        if trigger_type:
            found.add(trigger_type)
        elif service == "schedule" or event == "cron":
            found.add("schedule")
        elif service and event:
            found.add("webhook")
        else:
            found.add("manual")
    return found


def has_event_trigger(definition: Dict[str, Any]) -> bool:
    return bool(trigger_types(definition) & EVENT_TRIGGER_TYPES)


def normalize_routine(definition: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy with the trust-doctrine approval boundary defaulted."""
    result = dict(definition or {})
    routine = dict(result.get("routine") or {})
    routine.setdefault("approval_boundary", list(DEFAULT_APPROVAL_BOUNDARY))
    result["routine"] = routine
    return result


def _is_answered(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def validate_routine_contract(definition: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Every event/schedule definition must answer the seven routine questions."""
    if not has_event_trigger(definition):
        return True, None

    routine = (definition or {}).get("routine")
    if not isinstance(routine, dict):
        return False, "A schedule or event workflow needs a 'routine' contract before it can be enabled."

    for field in REQUIRED_ROUTINE_FIELDS:
        if not _is_answered(routine.get(field)):
            return False, f"Routine contract is missing '{field}'."

    boundary = routine.get("approval_boundary")
    if boundary is not None and not isinstance(boundary, list):
        return False, "Routine approval_boundary must be a list."

    return True, None


def routine_ready_for_grant(workflow) -> Tuple[bool, Optional[str]]:
    """A routine can only run under a standing grant after a passing test run."""
    from .models import WorkflowTestRun

    valid, error = validate_routine_contract(workflow.definition or {})
    if not valid:
        return False, error

    passing = WorkflowTestRun.objects.filter(
        workflow=workflow,
        definition_version=workflow.definition_version or 1,
        status="passed",
    ).exists()
    if not passing:
        return False, "No passing test run for the current definition version."
    return True, None


def routine_history_limit() -> int:
    try:
        return max(1, int(getattr(settings, "ROUTINE_HISTORY_LIMIT", 20) or 20))
    except (TypeError, ValueError):
        return 20


def routine_run_history(workflow, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Last K runs, compact, without raw telemetry."""
    limit = limit or routine_history_limit()
    runs = workflow.executions.order_by("-started_at")[:limit]
    return [
        {
            "id": run.id,
            "status": run.status,
            "trigger_type": run.trigger_type,
            "started_at": run.started_at,
            "completed_at": run.completed_at,
            "definition_version": run.definition_version,
            "summary": run.result_summary or run.failure_summary or "",
        }
        for run in runs
    ]


def user_last_seen(user_id: int):
    """Best-effort last activity: chat presence or last message."""
    from chatbot.models import Member, Message

    presence = Member.objects.filter(User_id=user_id).aggregate(last=Max("last_seen"))["last"]
    message = Message.objects.filter(member__User_id=user_id).aggregate(last=Max("timestamp"))["last"]
    candidates = [value for value in (presence, message) if value is not None]
    return max(candidates) if candidates else None


def _absence_settings() -> Tuple[int, int]:
    idle_days = int(getattr(settings, "ROUTINE_ABSENCE_IDLE_DAYS", 14) or 14)
    prompt_window_days = int(getattr(settings, "ROUTINE_ABSENCE_PROMPT_WINDOW_DAYS", 3) or 3)
    return idle_days, prompt_window_days


def active_routine_workflows(user_id: int):
    from .models import UserWorkflow

    return list(
        UserWorkflow.objects.filter(
            user_id=user_id,
            status="active",
            registered_triggers__trigger_type__in=list(EVENT_TRIGGER_TYPES),
        ).distinct()
    )


def _notify(user, event_type: str, title: str, body: str) -> None:
    try:
        from notifications.services import NotificationService

        NotificationService.notify(user=user, event_type=event_type, title=title, body=body)
    except Exception as exc:
        logger.warning("Routine check-in notification failed: %s", exc)


def _pause_workflow(workflow) -> None:
    from asgiref.sync import async_to_sync

    from .temporal_integration import pause_trigger_schedule

    workflow.status = "paused"
    workflow.save(update_fields=["status", "updated_at"])
    for trigger in workflow.registered_triggers.all():
        if trigger.trigger_type == "schedule":
            try:
                # Pause the remote Temporal schedule too, or it keeps firing
                # runs even though the DB row says paused.
                async_to_sync(pause_trigger_schedule)(trigger)
                continue
            except Exception as exc:
                logger.warning("Remote schedule pause failed for trigger %s: %s", trigger.id, exc)
        trigger.is_active = False
        trigger.schedule_status = "paused"
        trigger.save(update_fields=["is_active", "schedule_status", "updated_at"])


def acknowledge_check_in(user, *, keep_running: bool = True):
    """Answer the *"keep routines running?"* prompt.

    ``keep_running=True`` closes the check-in; ``False`` pauses the routines
    immediately, same as an unanswered prompt eventually would.
    """
    from .models import RoutineCheckIn

    check_in = RoutineCheckIn.objects.filter(user=user, status="prompted").order_by("-prompted_at").first()
    if check_in is None:
        return None
    check_in.answered_at = timezone.now()
    if keep_running:
        check_in.status = "answered"
        check_in.save(update_fields=["status", "answered_at"])
        return check_in

    pause_routines_for_user(user)
    check_in.status = "paused"
    check_in.save(update_fields=["status", "answered_at"])
    return check_in


def pause_routines_for_user(user) -> List[int]:
    workflows = active_routine_workflows(user.id)
    for workflow in workflows:
        _pause_workflow(workflow)
    return [workflow.id for workflow in workflows]


def check_routine_absence(now=None, *, idle_days: Optional[int] = None, prompt_window_days: Optional[int] = None) -> Dict[str, int]:
    """Prompt idle owners once; pause their routines after the prompt window."""
    from django.contrib.auth import get_user_model

    from .models import RoutineCheckIn

    now = now or timezone.now()
    configured_idle, configured_window = _absence_settings()
    idle_days = configured_idle if idle_days is None else idle_days
    prompt_window_days = configured_window if prompt_window_days is None else prompt_window_days
    idle_cutoff = now - timezone.timedelta(days=idle_days)
    prompt_cutoff = now - timezone.timedelta(days=prompt_window_days)

    User = get_user_model()
    prompted = 0
    paused = 0
    for user_id in User.objects.values_list("id", flat=True).iterator():
        workflows = active_routine_workflows(user_id)
        if not workflows:
            continue
        last_seen = user_last_seen(user_id)
        if last_seen is None or last_seen > idle_cutoff:
            continue

        pending = RoutineCheckIn.objects.filter(user_id=user_id, status="prompted").order_by("-prompted_at").first()
        if pending is None:
            user = User.objects.filter(id=user_id).first()
            if user is None:
                continue
            RoutineCheckIn.objects.create(user=user, status="prompted", prompted_at=now)
            _notify(
                user,
                "workflow.routine_checkin",
                "Keep your routines running?",
                (
                    "You've been away for a while. Reply to keep your routines running; "
                    "if I don't hear back, they'll pause rather than run unattended."
                ),
            )
            prompted += 1
        elif pending.prompted_at <= prompt_cutoff:
            user = User.objects.filter(id=user_id).first()
            paused_ids = pause_routines_for_user(user)
            pending.status = "paused"
            pending.paused_workflow_ids = paused_ids
            pending.save(update_fields=["status", "paused_workflow_ids"])
            _notify(
                user,
                "workflow.routine_paused",
                "Routines paused while you were away",
                "No answer to the check-in, so I paused your routines. Re-activate them when you're back.",
            )
            paused += len(paused_ids)
    return {"prompted": prompted, "paused": paused}
