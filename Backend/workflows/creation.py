"""The one service that creates a workflow and registers its triggers.

Both the agent handoff and the chat workflow builder go through here, so a
scheduled workflow always ends with a registered trigger or a clear failure.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from asgiref.sync import sync_to_async
from celery.schedules import ParseException, crontab
from django.db import transaction

from .models import UserWorkflow, WorkflowTrigger
from .temporal_integration import create_schedule_for_trigger, delete_trigger_schedule

logger = logging.getLogger(__name__)


class WorkflowCreationError(Exception):
    """A workflow could not be created, or a required trigger could not be registered."""


def _column_length(field: str) -> int:
    return WorkflowTrigger._meta.get_field(field).max_length


def _check_cron(cron: Any) -> None:
    """Refuse a cron the scheduler would reject only after the rows exist."""
    text = str(cron or "")
    fields = text.split()
    if len(fields) != 5:
        raise WorkflowCreationError(
            "A schedule trigger needs a cron expression with five fields."
        )
    minute, hour, day_of_month, month_of_year, day_of_week = fields
    try:
        if len(text) > _column_length("schedule_cron"):
            raise ValueError("cron expression too long")
        crontab(
            minute=minute, hour=hour, day_of_month=day_of_month,
            month_of_year=month_of_year, day_of_week=day_of_week,
        )
    except (ParseException, ValueError) as exc:
        raise WorkflowCreationError(
            "The cron expression is not valid. Its five fields are minute, "
            "hour, day of month, month and day of week."
        ) from exc


def _check_timezone(timezone: Any) -> None:
    name = str(timezone)
    try:
        if len(name) > _column_length("schedule_timezone"):
            raise ValueError("timezone name too long")
        ZoneInfo(name)
    # A name that is a folder of zones ("Africa") raises OSError, not a lookup error.
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise WorkflowCreationError(
            "The timezone is not a known IANA name such as Africa/Nairobi."
        ) from exc


def _classify_trigger(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise and validate one trigger definition before anything is written."""
    if not isinstance(raw, dict):
        raise WorkflowCreationError("Each workflow trigger must be an object.")

    service = str(raw.get("service") or "")
    event = str(raw.get("event") or "")
    config = raw.get("config") or {}
    if not isinstance(config, dict):
        config = {}

    trigger_type = raw.get("trigger_type")
    if not trigger_type:
        if service == "schedule" or event == "cron":
            trigger_type = "schedule"
        elif service and event:
            trigger_type = "webhook"
        else:
            trigger_type = "manual"
    trigger_type = str(trigger_type)

    cron = raw.get("cron") or config.get("cron")
    timezone = raw.get("timezone") or config.get("timezone") or "UTC"

    if trigger_type == "schedule":
        _check_cron(cron)
        _check_timezone(timezone)
    elif trigger_type == "webhook":
        if not service or not event:
            raise WorkflowCreationError(
                "A webhook trigger needs a service and an event."
            )

    return {
        "trigger_type": trigger_type,
        "service": service,
        "event": event,
        "config": config,
        "cron": cron,
        "timezone": timezone,
    }


def _normalize_triggers(definition: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_triggers = definition.get("triggers") or []
    if not isinstance(raw_triggers, list):
        raise WorkflowCreationError("The workflow triggers must be a list.")
    return [_classify_trigger(raw) for raw in raw_triggers]


async def create_workflow_with_triggers(
    *,
    user_id: Optional[int],
    room_id: Optional[int],
    definition: Dict[str, Any],
    draft=None,
) -> Tuple[UserWorkflow, List[WorkflowTrigger]]:
    """Create the workflow and its trigger rows, then register schedules.

    Every trigger is validated before a single row is written. The workflow
    and its trigger rows are created in one transaction. A schedule that
    cannot be registered leaves the workflow ``failed`` and raises, so a
    workflow never looks ready while its schedule will not fire.
    """
    definition = definition or {}
    triggers = _normalize_triggers(definition)

    def _create_rows():
        with transaction.atomic():
            workflow = UserWorkflow.objects.create(
                user_id=user_id,
                name=definition.get("workflow_name", "Untitled Workflow"),
                description=definition.get("workflow_description", ""),
                definition=definition,
                status="active",
                created_from_room_id=room_id,
                created_from_draft=draft,
            )
            rows = [
                WorkflowTrigger.objects.create(
                    workflow=workflow,
                    trigger_type=trigger["trigger_type"],
                    service=trigger["service"],
                    event=trigger["event"],
                    config=trigger["config"],
                    schedule_cron=trigger["cron"],
                    schedule_timezone=trigger["timezone"],
                )
                for trigger in triggers
            ]
            return workflow, rows

    workflow, rows = await sync_to_async(_create_rows)()

    registered: List[WorkflowTrigger] = []
    for trigger in rows:
        if trigger.trigger_type != "schedule":
            continue
        try:
            await create_schedule_for_trigger(trigger)
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            # A failed workflow must not leave an earlier schedule live.
            for done in registered:
                try:
                    await delete_trigger_schedule(done)
                except Exception as cleanup_exc:
                    logger.warning(
                        "Could not remove schedule for trigger %s: %s",
                        done.id, cleanup_exc,
                    )

            def _mark_failed():
                workflow.status = "failed"
                workflow.save(update_fields=["status", "updated_at"])

            await sync_to_async(_mark_failed)()
            raise WorkflowCreationError(
                f"Could not register the schedule: {reason}"
            ) from exc
        registered.append(trigger)

    def _reload():
        workflow.refresh_from_db()
        return workflow, list(WorkflowTrigger.objects.filter(workflow=workflow))

    return await sync_to_async(_reload)()


def describe_schedule(trigger) -> str:
    """The stored schedule in words, built from the trigger row."""
    return f"cron `{trigger.schedule_cron}`, timezone {trigger.schedule_timezone}"
