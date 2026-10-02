"""Workflow health watches and auto-pause (v0.7 W-C, issue #157).

Measure first, act second. The only automatic self-action is pause — never
edit. Health is computed from execution rows: failure rate, duration drift,
approval rate. A failure spike pauses the workflow and notifies with the
failing execution summaries; the weekly digest reports healthy / degraded /
paused per routine. No-data policy violations surface as failed executions
and therefore count as failures.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.utils import timezone

from .models import UserWorkflow, WorkflowApprovalRecord

logger = logging.getLogger(__name__)

STATE_HEALTHY = "healthy"
STATE_DEGRADED = "degraded"
STATE_PAUSED = "paused"

_CANCEL_COUNTS_AS_FAILURE = {"failed", "cancelled"}


def _window_hours() -> int:
    try:
        return max(1, int(getattr(settings, "WORKFLOW_HEALTH_WINDOW_HOURS", 24) or 24))
    except (TypeError, ValueError):
        return 24


def _failure_spike_count() -> int:
    try:
        return max(1, int(getattr(settings, "WORKFLOW_HEALTH_FAILURE_SPIKE", 3) or 3))
    except (TypeError, ValueError):
        return 3


def _degraded_rate() -> float:
    try:
        return float(getattr(settings, "WORKFLOW_HEALTH_DEGRADED_RATE", 0.3) or 0.3)
    except (TypeError, ValueError):
        return 0.3


def _mean(values: List[float]) -> Optional[float]:
    return round(sum(values) / len(values), 1) if values else None


def compute_workflow_health(workflow: UserWorkflow, window_hours: Optional[int] = None) -> Dict[str, Any]:
    """Deterministic health snapshot for one workflow over the window."""
    now = timezone.now()
    window = int(window_hours or _window_hours())
    since = now - timedelta(hours=window)
    # Failures from before the most recent reactivation must not re-pause a
    # manually recovered workflow: the window starts no earlier than that.
    if workflow.reactivated_at and workflow.reactivated_at > since:
        since = workflow.reactivated_at

    executions = list(workflow.executions.filter(started_at__gte=since))
    total = len(executions)
    failed = sum(1 for run in executions if run.status in _CANCEL_COUNTS_AS_FAILURE)
    failure_rate = round(failed / total, 4) if total else 0.0

    durations = [
        (run.completed_at - run.started_at).total_seconds()
        for run in executions
        if run.status == "completed" and run.completed_at
    ]
    mean_duration = _mean(durations)

    earlier = list(workflow.executions.filter(
        started_at__gte=since - timedelta(hours=window),
        started_at__lt=since,
    ))
    earlier_durations = [
        (run.completed_at - run.started_at).total_seconds()
        for run in earlier
        if run.status == "completed" and run.completed_at
    ]
    earlier_mean = _mean(earlier_durations)
    duration_drift_pct = (
        round((mean_duration - earlier_mean) / earlier_mean * 100, 1)
        if mean_duration is not None and earlier_mean
        else None
    )

    decisions = WorkflowApprovalRecord.objects.filter(
        workflow=workflow,
        created_at__gte=since,
        status__in=["approved", "rejected"],
    )
    approved = sum(1 for record in decisions if record.status == "approved")
    rejected = sum(1 for record in decisions if record.status == "rejected")
    decided = approved + rejected
    approval_rate = round(approved / decided, 4) if decided else None

    spike = failed >= _failure_spike_count() and failure_rate >= 0.5
    state = STATE_PAUSED if workflow.status == "paused" else (
        STATE_DEGRADED if total >= 5 and failure_rate >= _degraded_rate() else STATE_HEALTHY
    )

    return {
        "workflow_id": workflow.id,
        "name": workflow.name,
        "runs": total,
        "failed": failed,
        "failure_rate": failure_rate,
        "mean_duration_seconds": mean_duration,
        "duration_drift_pct": duration_drift_pct,
        "approval_rate": approval_rate,
        "state": state,
        "spike": spike,
    }


def _failing_summaries(workflow: UserWorkflow, limit: int = 3) -> List[str]:
    since = timezone.now() - timedelta(hours=_window_hours())
    summaries = []
    for run in workflow.executions.filter(started_at__gte=since, status__in=_CANCEL_COUNTS_AS_FAILURE):
        summaries.append(
            run.failure_summary or run.error_message or f"Run #{run.id} failed."
        )
        if len(summaries) >= limit:
            break
    return summaries


def _notify_health(user, event_type: str, title: str, body: str) -> None:
    try:
        from notifications.services import NotificationService

        NotificationService.notify(user=user, event_type=event_type, title=title, body=body)
    except Exception as exc:
        logger.warning("Workflow health notification failed: %s", exc)


def pause_workflow_after_spike(workflow: UserWorkflow, health: Dict[str, Any]) -> None:
    """Pause only — never mutate the definition. A spike also lapses grants."""
    from .grants import lapse_grants_for_workflow
    from .routine import pause_workflow

    pause_workflow(workflow)
    lapse_grants_for_workflow(workflow, "failure_spike")
    _notify_health(
        workflow.user,
        "workflow.health",
        f"Paused '{workflow.name}' after a failure spike",
        (
            f"{health['failed']} failures in the last {_window_hours()}h. Failing runs:\n"
            + "\n".join(f"- {summary}" for summary in _failing_summaries(workflow))
            + "\nStanding grants for this routine have lapsed and will ask again."
        ),
    )


def reactivate_workflow(workflow: UserWorkflow) -> UserWorkflow:
    """Manual recovery: mark active and reset the health window."""
    workflow.status = "active"
    workflow.reactivated_at = timezone.now()
    workflow.save(update_fields=["status", "reactivated_at", "updated_at"])
    for trigger in workflow.registered_triggers.filter(trigger_type="webhook"):
        trigger.is_active = True
        trigger.save(update_fields=["is_active", "updated_at"])
    return workflow


def check_workflow_health(window_hours: Optional[int] = None) -> Dict[str, int]:
    """Spike detection pauses failing workflows; recovery is always manual."""
    checked = 0
    paused = 0
    degraded = 0
    for workflow in UserWorkflow.objects.filter(status="active"):
        health = compute_workflow_health(workflow, window_hours=window_hours)
        checked += 1
        if health["spike"]:
            pause_workflow_after_spike(workflow, health)
            paused += 1
        elif health["state"] == STATE_DEGRADED:
            degraded += 1
    return {"checked": checked, "paused": paused, "degraded": degraded}


def build_health_digest(user_id: int) -> Dict[str, Any]:
    """Per-user weekly digest: healthy / degraded / paused routine states."""
    states = {STATE_HEALTHY: [], STATE_DEGRADED: [], STATE_PAUSED: []}
    for workflow in UserWorkflow.objects.filter(user_id=user_id).exclude(status="deleted"):
        health = compute_workflow_health(workflow)
        states[health["state"]].append(health)

    lines: List[str] = []
    for state in (STATE_PAUSED, STATE_DEGRADED, STATE_HEALTHY):
        for health in states[state]:
            suffix = ""
            if health["failure_rate"]:
                suffix = f" (failure rate {health['failure_rate']:.0%})"
            lines.append(f"- {state}: {health['name']}{suffix}")
    return {"lines": lines, "states": states, "empty": not lines}


def send_workflow_health_digest() -> Dict[str, int]:
    from django.contrib.auth import get_user_model

    sent = 0
    for user in get_user_model().objects.all():
        digest = build_health_digest(user.id)
        if digest["empty"]:
            continue
        _notify_health(
            user,
            "workflow.health",
            "Weekly routine health",
            "\n".join(digest["lines"]),
        )
        sent += 1
    return {"sent": sent}
