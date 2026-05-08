"""Temporal workflow definitions and helpers."""
from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any, Dict, List, Optional

from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone
from temporalio import activity, workflow
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleOverlapPolicy,
)
from temporalio.common import RetryPolicy

from orchestration.security_policy import sanitize_parameters, user_has_room_access

from .activity_executors import execute_workflow_step
from .models import (
    WorkflowApprovalRecord,
    WorkflowExecution,
    WorkflowImprovementSuggestion,
    WorkflowTrigger,
)
from .runtime import (
    build_failure_summary,
    build_result_summary,
    build_suggestions_for_step,
    collect_receipt_ids,
    get_approval_timeout_minutes,
    get_replayable_slice,
    get_step_id,
    get_step_max_attempts,
    get_step_timeout_seconds,
    get_timeout_policy,
    step_requires_approval,
)
from .utils import compact_context, resolve_parameters, safe_eval_condition


FINAL_STATUSES = {"completed", "failed", "cancelled"}
RUNTIME_STATE_FIELDS = {
    "current_step",
    "last_completed_step",
    "waiting_on",
    "attempts",
    "receipt_ids",
    "result_summary",
    "failure_summary",
    "recovery_suggestion",
    "pending_approval_id",
}


def _approval_title(workflow_name: str, step_id: str) -> str:
    return f"Approval required for {workflow_name}: {step_id}"


def _approval_body(action: str, approval_message: str) -> str:
    if approval_message:
        return approval_message
    return f"The workflow is waiting for approval before running '{action}'."


@activity.defn
async def create_execution_record(
    workflow_id: int,
    temporal_workflow_id: str,
    temporal_run_id: str,
    trigger_type: str,
    trigger_data: Dict[str, Any],
) -> int:
    def _create():
        return WorkflowExecution.objects.create(
            workflow_id=workflow_id,
            temporal_workflow_id=temporal_workflow_id,
            temporal_run_id=temporal_run_id,
            trigger_type=trigger_type,
            trigger_data=trigger_data,
            status="running",
        ).id

    return await sync_to_async(_create)()


@activity.defn
async def update_execution_record(
    execution_id: int,
    status: str,
    result: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
    runtime_state: Optional[Dict[str, Any]] = None,
) -> None:
    # Capture into a stable local — the inner closure assigns to a name with
    # the same identifier, which makes it look-before-it-writes inside _update.
    runtime_state_arg = runtime_state or {}

    def _update():
        execution = WorkflowExecution.objects.filter(id=execution_id).first()
        if not execution:
            return None

        update_fields: List[str] = ["status"]
        execution.status = status

        if result is not None:
            execution.result = result
            execution.result_summary = build_result_summary(result)
            update_fields.extend(["result", "result_summary"])

        if error_message is not None:
            execution.error_message = error_message
            update_fields.append("error_message")

        local_runtime_state = runtime_state_arg
        pending_approval_id = local_runtime_state.get("pending_approval_id")
        for field in RUNTIME_STATE_FIELDS - {"pending_approval_id"}:
            if field in local_runtime_state:
                setattr(execution, field, local_runtime_state.get(field))
                update_fields.append(field)

        if "pending_approval_id" in local_runtime_state:
            if pending_approval_id:
                execution.pending_approval = WorkflowApprovalRecord.objects.filter(id=pending_approval_id).first()
            else:
                execution.pending_approval = None
            update_fields.append("pending_approval")

        if status in FINAL_STATUSES:
            execution.completed_at = timezone.now()
            if result is not None and not execution.result_summary:
                execution.result_summary = build_result_summary(result)
                update_fields.append("result_summary")
            update_fields.append("completed_at")
        else:
            execution.completed_at = None
            update_fields.append("completed_at")

        execution.save(update_fields=sorted(set(update_fields)))
        return execution.status

    saved_status = await sync_to_async(_update)()
    if saved_status:
        try:
            from django_redis import get_redis_connection

            redis = get_redis_connection("default")
            redis.publish(f"wf_exec:{execution_id}", saved_status)
        except Exception:
            pass


@activity.defn
async def create_approval_record(
    execution_id: int,
    workflow_id: int,
    requested_by_id: int,
    step_id: str,
    service: str,
    action: str,
    approval_message: str,
    sanitized_params: Dict[str, Any],
    expires_at_iso: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> int:
    def _create():
        expires_at = None
        if expires_at_iso:
            expires_at = timezone.datetime.fromisoformat(expires_at_iso)
            if timezone.is_naive(expires_at):
                expires_at = timezone.make_aware(expires_at)

        approval = WorkflowApprovalRecord.objects.create(
            workflow_id=workflow_id,
            execution_id=execution_id,
            requested_by_id=requested_by_id,
            step_id=step_id,
            service=service,
            action=action,
            approval_message=approval_message,
            sanitized_params=sanitized_params,
            expires_at=expires_at,
            metadata=metadata or {},
        )
        WorkflowExecution.objects.filter(id=execution_id).update(pending_approval=approval)
        return approval.id

    return await sync_to_async(_create)()


@activity.defn
async def resolve_approval_record(
    approval_id: int,
    status: str,
    reviewed_by_id: Optional[int] = None,
    review_comment: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    def _update():
        approval = WorkflowApprovalRecord.objects.filter(id=approval_id).first()
        if not approval:
            return
        approval.status = status
        approval.review_comment = review_comment or approval.review_comment
        approval.reviewed_by_id = reviewed_by_id or approval.reviewed_by_id
        approval.reviewed_at = timezone.now()
        if metadata:
            merged = dict(approval.metadata or {})
            merged.update(metadata)
            approval.metadata = merged
        approval.save(update_fields=["status", "review_comment", "reviewed_by", "reviewed_at", "metadata"])
        if approval.execution_id:
            WorkflowExecution.objects.filter(id=approval.execution_id, pending_approval_id=approval.id).update(pending_approval=None)

    await sync_to_async(_update)()


@activity.defn
async def notify_workflow_event(
    user_id: int,
    event_type: str,
    title: str,
    body: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    def _notify():
        from django.contrib.auth import get_user_model
        from notifications.services import NotificationService

        user_model = get_user_model()
        user = user_model.objects.filter(id=user_id).first()
        if not user:
            return
        NotificationService.notify(
            user=user,
            event_type=event_type,
            title=title,
            body=body,
            severity="warning" if event_type != "workflow.approval" else "info",
            metadata=metadata or {},
        )

    await sync_to_async(_notify)()


@activity.defn
async def create_improvement_suggestions(
    workflow_id: int,
    execution_id: int,
    user_id: int,
    suggestions: List[Dict[str, Any]],
) -> None:
    if not suggestions:
        return

    def _create():
        for suggestion in suggestions:
            WorkflowImprovementSuggestion.objects.get_or_create(
                workflow_id=workflow_id,
                execution_id=execution_id,
                user_id=user_id,
                suggestion_type=suggestion.get("suggestion_type") or "workflow_hint",
                title=suggestion.get("title") or "Workflow suggestion",
                defaults={
                    "summary": suggestion.get("summary") or "",
                    "proposed_changes": suggestion.get("proposed_changes") or {},
                },
            )

    await sync_to_async(_create)()


@activity.defn
async def run_step_activity(step: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    return await execute_workflow_step(step, context)


@workflow.defn
class DynamicUserWorkflow:
    def __init__(self) -> None:
        self._state: Dict[str, Any] = {
            "status": "pending",
            "current_step": "",
            "last_completed_step": "",
            "waiting_on": "",
            "attempts": {},
            "receipt_ids": [],
            "pending_approval_id": None,
        }
        self._approval_response: Optional[Dict[str, Any]] = None
        self._cancel_requested = False
        self._cancel_reason = ""

    @workflow.signal
    def submit_approval(self, payload: Dict[str, Any]) -> None:
        self._approval_response = dict(payload or {})

    @workflow.signal
    def cancel_run(self, reason: str = "") -> None:
        self._cancel_requested = True
        self._cancel_reason = reason or "Cancelled by operator"

    @workflow.query
    def get_runtime_state(self) -> Dict[str, Any]:
        return dict(self._state)

    async def _update_state(
        self,
        execution_id: int,
        status: str,
        *,
        result: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
        **runtime_state: Any,
    ) -> None:
        self._state["status"] = status
        self._state.update(runtime_state)
        await workflow.execute_activity(
            update_execution_record,
            args=[execution_id, status, result, error_message, runtime_state],
            schedule_to_close_timeout=timedelta(seconds=30),
        )

    async def _cancel_execution(self, execution_id: int, context: Dict[str, Any]) -> Dict[str, Any]:
        message = self._cancel_reason or "Cancelled by operator"
        stored_context = compact_context(context)
        summary, recovery = build_failure_summary(
            step_id=self._state.get("current_step"),
            error_message=message,
            waiting_on=self._state.get("waiting_on") or "",
        )
        await self._update_state(
            execution_id,
            "cancelled",
            result=stored_context,
            error_message=message,
            current_step=self._state.get("current_step") or "",
            waiting_on="",
            pending_approval_id=None,
            failure_summary=summary,
            recovery_suggestion=recovery,
            result_summary=build_result_summary(stored_context),
        )
        return context

    @workflow.run
    async def run(
        self,
        workflow_id: int,
        workflow_definition: Dict[str, Any],
        trigger_data: Dict[str, Any],
        trigger_type: str,
        execution_id: Optional[int],
        user_id: Optional[int],
    ) -> Dict[str, Any]:
        context: Dict[str, Any] = {
            "trigger": trigger_data,
            "workflow": {
                "id": workflow_id,
                "policy": workflow_definition.get("policy") or {},
            },
            "user_id": user_id,
        }
        if execution_id:
            context["execution_id"] = execution_id
        if isinstance(trigger_data, dict) and trigger_data.get("room_id"):
            context["room_id"] = trigger_data.get("room_id")

        replay_context = trigger_data.get("replay_context")
        if isinstance(replay_context, dict):
            for key, value in replay_context.items():
                if key not in {"trigger", "workflow", "user_id", "execution_id"}:
                    context[key] = value

        if not execution_id:
            execution_id = await workflow.execute_activity(
                create_execution_record,
                args=[workflow_id, workflow.info().workflow_id, workflow.info().run_id, trigger_type, trigger_data],
                schedule_to_close_timeout=timedelta(seconds=30),
            )
            context["execution_id"] = execution_id

        replay_from_step_id = trigger_data.get("replay_from_step_id")
        replay_mode = not bool(replay_from_step_id)

        try:
            for index, step in enumerate(workflow_definition.get("steps", [])):
                step_id = get_step_id(step, index)
                if replay_from_step_id and not replay_mode:
                    replay_mode = step_id == replay_from_step_id
                    if not replay_mode:
                        continue

                if self._cancel_requested:
                    return await self._cancel_execution(execution_id, context)

                condition = step.get("condition")
                if condition and not safe_eval_condition(condition, context):
                    continue

                self._state["current_step"] = step_id
                attempts = dict(self._state.get("attempts") or {})
                attempts[step_id] = int(attempts.get(step_id) or 0) + 1

                if step_requires_approval(step):
                    timeout_minutes = get_approval_timeout_minutes(step)
                    approval_message = str(step.get("approval_message") or "").strip()
                    approval_id = await workflow.execute_activity(
                        create_approval_record,
                        args=[
                            execution_id,
                            workflow_id,
                            user_id or 0,
                            step_id,
                            str(step.get("service") or ""),
                            str(step.get("action") or ""),
                            approval_message,
                            sanitize_parameters(resolve_parameters(step.get("params") or {}, context)),
                            (workflow.now() + timedelta(minutes=timeout_minutes)).isoformat(),
                            {"trigger_type": trigger_type},
                        ],
                        schedule_to_close_timeout=timedelta(seconds=30),
                    )
                    self._approval_response = None
                    await self._update_state(
                        execution_id,
                        "waiting",
                        current_step=step_id,
                        waiting_on="approval",
                        attempts=attempts,
                        pending_approval_id=approval_id,
                    )
                    await workflow.execute_activity(
                        notify_workflow_event,
                        args=[
                            user_id or 0,
                            "workflow.approval",
                            _approval_title(workflow_definition.get("workflow_name") or "Workflow", step_id),
                            _approval_body(str(step.get("action") or step_id), approval_message),
                            {"execution_id": execution_id, "approval_id": approval_id, "step_id": step_id},
                        ],
                        schedule_to_close_timeout=timedelta(seconds=30),
                    )

                    try:
                        await workflow.wait_condition(
                            lambda: self._approval_response is not None or self._cancel_requested,
                            timeout=timedelta(minutes=timeout_minutes),
                        )
                    except asyncio.TimeoutError:
                        timeout_policy = get_timeout_policy(step)
                        context[step_id] = {"status": "timed_out", "message": "Approval timed out."}
                        await workflow.execute_activity(
                            resolve_approval_record,
                            args=[approval_id, "timed_out", None, "Approval timed out."],
                            schedule_to_close_timeout=timedelta(seconds=30),
                        )
                        if timeout_policy == "continue":
                            await self._update_state(
                                execution_id,
                                "running",
                                current_step=step_id,
                                waiting_on="",
                                attempts=attempts,
                                pending_approval_id=None,
                            )
                            continue
                        if timeout_policy == "cancel":
                            self._cancel_requested = True
                            self._cancel_reason = "Approval timed out"
                            return await self._cancel_execution(execution_id, context)
                        raise RuntimeError("Approval timed out")

                    if self._cancel_requested:
                        await workflow.execute_activity(
                            resolve_approval_record,
                            args=[approval_id, "cancelled", None, self._cancel_reason or "Cancelled by operator"],
                            schedule_to_close_timeout=timedelta(seconds=30),
                        )
                        return await self._cancel_execution(execution_id, context)

                    response = dict(self._approval_response or {})
                    self._approval_response = None
                    decision = str(response.get("decision") or "").lower()
                    reviewer_id = response.get("reviewed_by_id")
                    review_comment = str(response.get("comment") or "").strip()

                    if decision != "approved":
                        context[step_id] = {"status": "rejected", "message": review_comment or "Approval rejected."}
                        await workflow.execute_activity(
                            resolve_approval_record,
                            args=[approval_id, "rejected", reviewer_id, review_comment or "Rejected by operator"],
                            schedule_to_close_timeout=timedelta(seconds=30),
                        )
                        suggestions = build_suggestions_for_step(step, "rejected")
                        if suggestions:
                            await workflow.execute_activity(
                                create_improvement_suggestions,
                                args=[workflow_id, execution_id, user_id or 0, suggestions],
                                schedule_to_close_timeout=timedelta(seconds=30),
                            )
                        self._cancel_requested = True
                        self._cancel_reason = review_comment or f"Approval rejected for {step_id}"
                        return await self._cancel_execution(execution_id, context)

                    await workflow.execute_activity(
                        resolve_approval_record,
                        args=[approval_id, "approved", reviewer_id, review_comment],
                        schedule_to_close_timeout=timedelta(seconds=30),
                    )
                    await self._update_state(
                        execution_id,
                        "running",
                        current_step=step_id,
                        waiting_on="",
                        attempts=attempts,
                        pending_approval_id=None,
                    )

                on_error = str(step.get("on_error") or "stop").lower()

                try:
                    await self._update_state(
                        execution_id,
                        "running",
                        current_step=step_id,
                        waiting_on="",
                        attempts=attempts,
                    )
                    result = await workflow.execute_activity(
                        run_step_activity,
                        args=[step, context],
                        schedule_to_close_timeout=timedelta(seconds=get_step_timeout_seconds(step)),
                        retry_policy=RetryPolicy(
                            initial_interval=timedelta(seconds=2),
                            maximum_interval=timedelta(seconds=30),
                            maximum_attempts=get_step_max_attempts(step),
                        ),
                    )
                    context[step_id] = result
                    receipt_ids = list(self._state.get("receipt_ids") or [])
                    for receipt_id in collect_receipt_ids(result):
                        if receipt_id not in receipt_ids:
                            receipt_ids.append(receipt_id)
                    self._state["receipt_ids"] = receipt_ids
                    self._state["last_completed_step"] = step_id
                    await self._update_state(
                        execution_id,
                        "running",
                        current_step=step_id,
                        last_completed_step=step_id,
                        waiting_on="",
                        attempts=attempts,
                        receipt_ids=receipt_ids,
                        result_summary=build_result_summary(compact_context(context)),
                    )
                except Exception as exc:
                    context[step_id] = {"status": "error", "error": str(exc)}
                    suggestions = build_suggestions_for_step(step, "failed")
                    if suggestions:
                        await workflow.execute_activity(
                            create_improvement_suggestions,
                            args=[workflow_id, execution_id, user_id or 0, suggestions],
                            schedule_to_close_timeout=timedelta(seconds=30),
                        )
                    if on_error == "continue":
                        summary, recovery = build_failure_summary(step_id=step_id, error_message=str(exc))
                        await self._update_state(
                            execution_id,
                            "running",
                            current_step=step_id,
                            attempts=attempts,
                            failure_summary=summary,
                            recovery_suggestion=recovery,
                        )
                        continue
                    raise

            stored_context = compact_context(context)
            await self._update_state(
                execution_id,
                "completed",
                result=stored_context,
                current_step="",
                waiting_on="",
                pending_approval_id=None,
                receipt_ids=list(self._state.get("receipt_ids") or []),
                result_summary=build_result_summary(stored_context),
                failure_summary="",
                recovery_suggestion="",
            )
            return context

        except Exception as exc:
            stored_context = compact_context(context)
            current_step = self._state.get("current_step") or ""
            summary, recovery = build_failure_summary(
                step_id=current_step,
                error_message=str(exc),
                waiting_on=self._state.get("waiting_on") or "",
            )
            await self._update_state(
                execution_id,
                "failed",
                result=stored_context,
                error_message=str(exc),
                current_step=current_step,
                waiting_on=self._state.get("waiting_on") or "",
                pending_approval_id=None,
                receipt_ids=list(self._state.get("receipt_ids") or []),
                failure_summary=summary,
                recovery_suggestion=recovery,
                result_summary=build_result_summary(stored_context),
            )
            await workflow.execute_activity(
                notify_workflow_event,
                args=[
                    user_id or 0,
                    "workflow.failed",
                    f"Workflow failed: {workflow_definition.get('workflow_name') or workflow_id}",
                    summary,
                    {"execution_id": execution_id, "step_id": current_step},
                ],
                schedule_to_close_timeout=timedelta(seconds=30),
            )
            raise


async def get_temporal_client() -> Client:
    return await Client.connect(settings.TEMPORAL_HOST, namespace=settings.TEMPORAL_NAMESPACE)


async def _update_workflow_run_stats(workflow_obj, trigger_id: Optional[int] = None) -> None:
    now = timezone.now()

    def _update():
        from django.db.models import F

        workflow_obj.execution_count = (workflow_obj.execution_count or 0) + 1
        workflow_obj.last_executed_at = now
        workflow_obj.save(update_fields=["execution_count", "last_executed_at", "updated_at"])
        if trigger_id:
            WorkflowTrigger.objects.filter(id=trigger_id, workflow=workflow_obj).update(
                last_triggered_at=now,
                trigger_count=F("trigger_count") + 1,
            )

    await sync_to_async(_update)()


async def start_workflow_execution(
    workflow_obj,
    trigger_data: Dict[str, Any],
    trigger_type: str,
    execution_id: Optional[int] = None,
) -> WorkflowExecution:
    client = await get_temporal_client()
    workflow_run_id = f"workflow-{workflow_obj.id}-{uuid.uuid4()}"

    if not isinstance(trigger_data, dict):
        raise ValueError("trigger_data must be a dictionary")

    room_id = trigger_data.get("room_id")
    if room_id is not None:
        try:
            room_id = int(room_id)
        except (TypeError, ValueError):
            raise ValueError("Invalid room_id in trigger_data")
        allowed = await user_has_room_access(workflow_obj.user_id, room_id)
        if not allowed:
            raise PermissionError("Workflow user does not have access to trigger room_id")
        trigger_data = dict(trigger_data)
        trigger_data["room_id"] = room_id

    trigger_id = trigger_data.get("trigger_id")
    if trigger_id is not None:
        try:
            trigger_id = int(trigger_id)
        except (TypeError, ValueError):
            trigger_id = None

    await _update_workflow_run_stats(workflow_obj, trigger_id=trigger_id)

    execution = None
    if execution_id is None:
        def _create_execution():
            return WorkflowExecution.objects.create(
                workflow=workflow_obj,
                temporal_workflow_id=workflow_run_id,
                trigger_type=trigger_type,
                trigger_data=trigger_data,
                status="running",
            )

        execution = await sync_to_async(_create_execution)()
        execution_id = execution.id
    else:
        execution = await sync_to_async(lambda: WorkflowExecution.objects.filter(id=execution_id).first())()

    try:
        handle = await client.start_workflow(
            DynamicUserWorkflow.run,
            args=[workflow_obj.id, workflow_obj.definition, trigger_data, trigger_type, execution_id, workflow_obj.user_id],
            id=workflow_run_id,
            task_queue=settings.TEMPORAL_TASK_QUEUE,
        )
    except Exception as exc:
        error_message = str(exc)

        def _mark_failed():
            if not execution:
                return
            summary, recovery = build_failure_summary(step_id=None, error_message=error_message)
            execution.status = "failed"
            execution.error_message = error_message
            execution.failure_summary = summary
            execution.recovery_suggestion = recovery
            execution.completed_at = timezone.now()
            execution.save(
                update_fields=[
                    "status",
                    "error_message",
                    "failure_summary",
                    "recovery_suggestion",
                    "completed_at",
                ]
            )

        await sync_to_async(_mark_failed)()
        raise

    def _update_execution():
        if not execution:
            return
        execution.temporal_workflow_id = handle.id
        execution.temporal_run_id = handle.run_id
        execution.save(update_fields=["temporal_workflow_id", "temporal_run_id"])

    await sync_to_async(_update_execution)()
    return execution


async def fetch_execution_runtime_state(execution: WorkflowExecution) -> Dict[str, Any]:
    state = {
        "status": execution.status,
        "current_step": execution.current_step or "",
        "last_completed_step": execution.last_completed_step or "",
        "waiting_on": execution.waiting_on or "",
        "attempts": execution.attempts or {},
        "receipt_ids": execution.receipt_ids or [],
        "pending_approval_id": execution.pending_approval_id,
    }
    if getattr(settings, "TEMPORAL_DISABLED", False) or not execution.temporal_workflow_id:
        return state

    try:
        client = await get_temporal_client()
        handle = client.get_workflow_handle(execution.temporal_workflow_id, run_id=execution.temporal_run_id or None)
        runtime_state = await handle.query(DynamicUserWorkflow.get_runtime_state)
        if isinstance(runtime_state, dict):
            state.update(runtime_state)
    except Exception:
        pass
    return state


async def submit_execution_approval(
    execution: WorkflowExecution,
    *,
    approval_id: int,
    reviewer_id: int,
    decision: str,
    comment: str = "",
) -> None:
    client = await get_temporal_client()
    handle = client.get_workflow_handle(execution.temporal_workflow_id, run_id=execution.temporal_run_id or None)
    await handle.signal(
        DynamicUserWorkflow.submit_approval,
        {
            "approval_id": approval_id,
            "reviewed_by_id": reviewer_id,
            "decision": decision,
            "comment": comment,
        },
    )


async def request_execution_cancel(execution: WorkflowExecution, *, reason: str = "") -> None:
    client = await get_temporal_client()
    handle = client.get_workflow_handle(execution.temporal_workflow_id, run_id=execution.temporal_run_id or None)
    await handle.signal(DynamicUserWorkflow.cancel_run, reason or "Cancelled by operator")


async def create_schedule_for_trigger(trigger_obj) -> None:
    if not trigger_obj.schedule_cron:
        return

    client = await get_temporal_client()
    schedule_id = f"workflow-{trigger_obj.workflow_id}-trigger-{trigger_obj.id}"
    trigger_data = {
        "trigger_id": trigger_obj.id,
        "schedule": {
            "cron": trigger_obj.schedule_cron,
            "timezone": trigger_obj.schedule_timezone,
        },
    }

    action = ScheduleActionStartWorkflow(
        DynamicUserWorkflow.run,
        args=[
            trigger_obj.workflow_id,
            trigger_obj.workflow.definition,
            trigger_data,
            "schedule",
            None,
            trigger_obj.workflow.user_id,
        ],
        task_queue=settings.TEMPORAL_TASK_QUEUE,
    )

    schedule = Schedule(
        action=action,
        spec=ScheduleSpec(
            cron_expressions=[trigger_obj.schedule_cron],
            time_zone_name=trigger_obj.schedule_timezone,
        ),
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
    )

    try:
        await client.create_schedule(schedule_id, schedule)
    except Exception as exc:
        # Capture into a local before defining the closure — Python clears the
        # `exc` binding when the except block exits, so the closure would see
        # NameError. Same pattern as the v0.3.0 fix elsewhere in this file.
        error_message = str(exc)

        def _mark_unavailable():
            trigger_obj.schedule_status = "unavailable"
            trigger_obj.schedule_last_error = error_message
            trigger_obj.save(update_fields=["schedule_status", "schedule_last_error", "updated_at"])

        await sync_to_async(_mark_unavailable)()
        raise

    def _save_schedule():
        trigger_obj.temporal_schedule_id = schedule_id
        trigger_obj.schedule_status = "active"
        trigger_obj.schedule_last_error = ""
        trigger_obj.is_active = True
        trigger_obj.save(update_fields=["temporal_schedule_id", "schedule_status", "schedule_last_error", "is_active"])

    await sync_to_async(_save_schedule)()


async def pause_trigger_schedule(trigger_obj) -> None:
    if trigger_obj.trigger_type != "schedule":
        return

    if trigger_obj.temporal_schedule_id:
        client = await get_temporal_client()
        handle = client.get_schedule_handle(trigger_obj.temporal_schedule_id)
        await handle.pause(note="Paused from Kazi workflow controls")

    def _pause():
        trigger_obj.schedule_status = "paused"
        trigger_obj.is_active = False
        trigger_obj.save(update_fields=["schedule_status", "is_active", "updated_at"])

    await sync_to_async(_pause)()


async def resume_trigger_schedule(trigger_obj) -> None:
    if trigger_obj.trigger_type != "schedule":
        return

    if trigger_obj.temporal_schedule_id:
        client = await get_temporal_client()
        handle = client.get_schedule_handle(trigger_obj.temporal_schedule_id)
        await handle.unpause(note="Resumed from Kazi workflow controls")
    else:
        await create_schedule_for_trigger(trigger_obj)

    def _resume():
        trigger_obj.schedule_status = "active"
        trigger_obj.schedule_last_error = ""
        trigger_obj.is_active = True
        trigger_obj.save(update_fields=["schedule_status", "schedule_last_error", "is_active", "updated_at"])

    await sync_to_async(_resume)()


async def delete_trigger_schedule(trigger_obj) -> None:
    if not trigger_obj.temporal_schedule_id:
        return
    client = await get_temporal_client()
    handle = client.get_schedule_handle(trigger_obj.temporal_schedule_id)
    await handle.delete()

    def _delete():
        trigger_obj.schedule_status = "deleted"
        trigger_obj.is_active = False
        trigger_obj.save(update_fields=["schedule_status", "is_active", "updated_at"])

    await sync_to_async(_delete)()


async def build_replay_request(
    execution: WorkflowExecution,
    *,
    from_failed_step: bool = False,
    from_step_id: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Build the replay payload, honoring per-step safety per
    docs/contracts/replay-safety.md v1.0.

    v0.4.1: accepts explicit `from_step_id` (string, documented contract)
    in addition to the legacy `from_failed_step` (bool). When `force=True`
    the safety check is bypassed and the override is recorded on
    trigger_data so the receipt + trace remain auditable.
    """
    definition = dict(execution.workflow.definition or {})
    # Explicit from_step_id wins over the legacy boolean.
    target_step = from_step_id or (execution.current_step if from_failed_step else None)
    if from_failed_step and not target_step:
        raise ValueError("This execution does not have a failed step to replay from.")

    allowed, error, replay_steps = get_replayable_slice(definition, from_step_id=target_step)
    if not allowed and not force:
        raise ValueError(error or "Replay is not allowed for this execution.")

    if not allowed and force:
        # Operator opted in to the unsafe replay. Manually compute the slice
        # since get_replayable_slice returned []. The override is recorded
        # in trigger_data below.
        all_steps = list(definition.get("steps") or [])
        replay_steps = list(all_steps)
        if target_step:
            from .runtime import get_step_id as _get_step_id
            for idx, step in enumerate(all_steps):
                if _get_step_id(step, idx) == target_step:
                    replay_steps = all_steps[idx:]
                    break

    replay_definition = dict(definition)
    if target_step or from_failed_step:
        replay_definition["steps"] = replay_steps

    trigger_data = dict(execution.trigger_data or {})
    trigger_data["replay_context"] = execution.result or {}
    if target_step:
        trigger_data["replay_from_step_id"] = target_step
    if force:
        trigger_data["replay_forced"] = True
        trigger_data["replay_safety_bypass_reason"] = error or "operator override"
    return {"definition": replay_definition, "trigger_data": trigger_data}
