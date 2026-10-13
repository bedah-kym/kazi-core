"""
Start and execute an ad-hoc multi-step workflow.
"""
import hashlib
import json
import logging
import time
from datetime import timedelta
from typing import Any, Dict, List, Optional

from asgiref.sync import sync_to_async
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.conf import settings

from orchestration.action_catalog import is_high_risk_action
from orchestration.action_receipts import requires_confirmation
from workflows.temporal_integration import start_workflow_execution

logger = logging.getLogger(__name__)


MAX_WAIT_SECONDS = 20
IDEMPOTENCY_TTL_SECONDS = 90


def _has_high_risk_step(steps: List[Dict[str, Any]]) -> bool:
    for step in steps:
        action = str(step.get("action") or "").lower()
        if is_high_risk_action(action) or requires_confirmation(action):
            return True
    return False


def _requires_durable_runtime(steps: List[Dict[str, Any]]) -> bool:
    if _has_high_risk_step(steps):
        return True
    return any(bool(step.get("requires_approval")) for step in steps)


def _idempotency_key(definition: Dict[str, Any], trigger_data: Dict[str, Any]) -> str:
    payload = json.dumps(
        {"definition": definition, "trigger": trigger_data},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


async def _enqueue_deferred_execution(
    workflow_obj,
    user_id: int,
    room_id: Optional[int],
    trigger_data: Dict[str, Any],
) -> Optional[int]:
    try:
        from workflows.models import DeferredWorkflowExecution

        def _create():
            return DeferredWorkflowExecution.objects.create(
                workflow=workflow_obj,
                user_id=user_id,
                room_id=room_id,
                trigger_data=trigger_data,
                status='queued',
                attempts=0,
                next_attempt_at=timezone.now(),
            ).id

        return await sync_to_async(_create)()
    except Exception as exc:
        logger.error("Failed to enqueue deferred workflow: %s", exc)
        return None


async def _create_adhoc_workflow(
    user_id: int,
    room_id: Optional[int],
    definition: Dict[str, Any],
    idempotency_key: Optional[str] = None,
):
    """
    Create the UserWorkflow row for an ad-hoc execution.

    The (user, idempotency_key) unique constraint — not the cache — is what
    blocks duplicate executions, so a Redis flush or a retry after the cache
    TTL can no longer double-fire. Returns (workflow, False) on success and
    (None, True) when an identical request is already inside the dedupe
    window. A colliding row older than the window is released so deliberate
    retries of the same definition still work.
    """
    from workflows.models import UserWorkflow

    def _create():
        try:
            with transaction.atomic():
                return UserWorkflow.objects.create(
                    user_id=user_id,
                    name=definition.get("workflow_name", "Ad hoc request"),
                    description=definition.get("workflow_description", "Ad hoc execution"),
                    definition=definition,
                    status='active',
                    created_from_room_id=room_id,
                    idempotency_key=idempotency_key,
                ), False
        except IntegrityError:
            stale = UserWorkflow.objects.filter(
                user_id=user_id,
                idempotency_key=idempotency_key,
                created_at__lt=timezone.now() - timedelta(seconds=IDEMPOTENCY_TTL_SECONDS),
            ).first()
            if not stale:
                return None, True
            stale.idempotency_key = None
            stale.save(update_fields=["idempotency_key"])
            try:
                with transaction.atomic():
                    return UserWorkflow.objects.create(
                        user_id=user_id,
                        name=definition.get("workflow_name", "Ad hoc request"),
                        description=definition.get("workflow_description", "Ad hoc execution"),
                        definition=definition,
                        status='active',
                        created_from_room_id=room_id,
                        idempotency_key=idempotency_key,
                    ), False
            except IntegrityError:
                return None, True

    return await sync_to_async(_create)()


async def execute_adhoc_workflow(
    definition: Dict[str, Any],
    user_id: int,
    room_id: Optional[int],
    trigger_data: Optional[Dict[str, Any]] = None,
    wait_seconds: int = 12,
) -> Dict[str, Any]:
    """
    Execute an ad-hoc workflow via Temporal when possible, falling back to inline
    execution if Temporal is unavailable.
    """
    trigger_data = trigger_data or {}
    wait_seconds = min(max(wait_seconds, 1), MAX_WAIT_SECONDS)
    steps = definition.get("steps") or []
    workflow_risk = "high" if _has_high_risk_step(steps) else "low"
    requires_durable_runtime = _requires_durable_runtime(steps)

    def _enrich(payload: Dict[str, Any]) -> Dict[str, Any]:
        enriched = dict(payload)
        enriched.setdefault("action", "adhoc_workflow")
        enriched.setdefault("risk_level", workflow_risk)
        enriched.setdefault("requires_confirmation", False)
        enriched.setdefault("clarification_prompt", "")
        enriched.setdefault("data", enriched.get("result") or {})
        enriched.setdefault("receipt", None)
        return enriched

    idempotency_key = _idempotency_key(definition, trigger_data)
    workflow_obj, is_duplicate = await _create_adhoc_workflow(user_id, room_id, definition, idempotency_key)
    if is_duplicate or workflow_obj is None:
        return _enrich({
            "status": "duplicate",
            "mode": "noop",
            "workflow": None,
            "execution": None,
            "result": {},
            "message": "I already started that request. Please wait a moment.",
        })

    if settings.TEMPORAL_DISABLED:
        if requires_durable_runtime:
            deferred_id = await _enqueue_deferred_execution(workflow_obj, user_id, room_id, trigger_data)
            if deferred_id:
                return _enrich({
                    "status": "queued",
                    "mode": "deferred",
                    "workflow": workflow_obj,
                    "execution": None,
                    "result": {},
                    "message": "Temporal is disabled. This approval-gated workflow was queued and will run when durable execution is back.",
                })
            return _enrich({
                "status": "error",
                "mode": "deferred",
                "workflow": workflow_obj,
                "execution": None,
                "result": {},
                "message": "Temporal is disabled and this workflow requires a durable approval flow.",
            })
        logger.info("Temporal disabled; using inline execution.")
        result = await _run_inline(definition, user_id, trigger_data)
        return _enrich({
            "status": "completed",
            "mode": "inline",
            "workflow": workflow_obj,
            "execution": None,
            "result": result,
        })

    try:
        execution = await start_workflow_execution(workflow_obj, trigger_data, "manual")
    except Exception as exc:
        logger.error("Temporal start failed, falling back to inline execution: %s", exc)
        deferred_id = await _enqueue_deferred_execution(workflow_obj, user_id, room_id, trigger_data)
        if deferred_id:
            return _enrich({
                "status": "queued",
                "mode": "deferred",
                "workflow": workflow_obj,
                "execution": None,
                "result": {},
                "message": "Temporal is unavailable. Your request is queued and will run when it is back up.",
            })
        if requires_durable_runtime:
            return _enrich({
                "status": "error",
                "mode": "deferred",
                "workflow": workflow_obj,
                "execution": None,
                "result": {},
                "message": "Temporal is unavailable and this workflow requires durable approvals, so it was not run inline.",
            })
        result = await _run_inline(definition, user_id, trigger_data)
        return _enrich({
            "status": "completed",
            "mode": "inline",
            "workflow": workflow_obj,
            "execution": None,
            "result": result,
        })

    completed = await _wait_for_execution(execution.id, wait_seconds)
    if completed and completed.status == "completed":
        return _enrich({
            "status": "completed",
            "mode": "temporal",
            "workflow": workflow_obj,
            "execution": completed,
            "result": completed.result or {},
        })

    if completed and completed.status in ("failed", "cancelled"):
        return _enrich({
            "status": completed.status,
            "mode": "temporal",
            "workflow": workflow_obj,
            "execution": completed,
            "result": completed.result or {},
            "error": completed.error_message,
        })

    if completed and completed.status == "waiting":
        return _enrich({
            "status": "waiting",
            "mode": "temporal",
            "workflow": workflow_obj,
            "execution": completed,
            "result": completed.result or {},
            "message": "The workflow is waiting for human approval before it can continue.",
        })

    return _enrich({
        "status": "running",
        "mode": "temporal",
        "workflow": workflow_obj,
        "execution": completed or execution,
        "result": completed.result if completed else {},
    })


async def _wait_for_execution(execution_id: int, wait_seconds: int):
    """Wait for a workflow execution to complete using Redis pub/sub (no DB polling)."""
    from workflows.models import WorkflowExecution

    # First, check if it's already done (fast path)
    def _fetch():
        return WorkflowExecution.objects.filter(id=execution_id).first()
    execution = await sync_to_async(_fetch)()
    if execution and execution.status in ("completed", "failed", "cancelled", "waiting"):
        return execution

    # Subscribe to Redis channel for this execution
    channel = f"wf_exec:{execution_id}"
    try:
        from django_redis import get_redis_connection
        redis = get_redis_connection("default")
        pubsub = redis.pubsub()
        # pubsub.subscribe is synchronous in redis-py, but we use it in sync_to_async
        await sync_to_async(pubsub.subscribe)(channel)

        deadline = time.monotonic() + max(wait_seconds, 1)
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            # Wait for message with timeout (non-blocking in terms of CPU, but blocks thread)
            # Since we are in an async function, we must use a non-blocking way to check
            # but standard django-redis (redis-py) pubsub.get_message(timeout=...) blocks the thread.
            # However, for a short wait it's better than rapid DB polling.
            timeout = min(remaining, 1.0)
            msg = await sync_to_async(pubsub.get_message)(ignore_subscribe_messages=True, timeout=timeout)
            if msg and msg.get("type") == "message":
                # Got a completion signal; fetch the final state.
                execution = await sync_to_async(_fetch)()
                if execution and execution.status in ("completed", "failed", "cancelled", "waiting"):
                    return execution

        await sync_to_async(pubsub.unsubscribe)(channel)
        await sync_to_async(pubsub.close)()
    except Exception as e:
        logger.warning("Redis pub/sub wait failed, falling back to single DB check: %s", e)

    # Final fallback: one last DB hit
    return await sync_to_async(_fetch)()


async def _run_inline(definition: Dict[str, Any], user_id: int, trigger_data: Dict[str, Any]) -> Dict[str, Any]:
    from workflows.activity_executors import execute_workflow_step
    from workflows.utils import safe_eval_condition, compact_context

    context: Dict[str, Any] = {
        "trigger": trigger_data,
        "workflow": {"id": 0, "policy": definition.get("policy") or {}},
        "user_id": user_id,
    }
    if isinstance(trigger_data, dict) and trigger_data.get("room_id"):
        context["room_id"] = trigger_data.get("room_id")

    for step in definition.get("steps", []):
        condition = step.get("condition")
        if condition and not safe_eval_condition(condition, context):
            continue
        step_id = step.get("id") or step.get("action") or f"step_{len(context)}"
        on_error = str(step.get("on_error") or "stop").lower()
        try:
            result = await execute_workflow_step(step, context)
            context[step_id] = result
        except Exception as exc:
            context[step_id] = {"status": "error", "error": str(exc)}
            if on_error == "continue":
                continue
            raise

    return compact_context(context)
