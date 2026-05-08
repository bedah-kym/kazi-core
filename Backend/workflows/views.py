from asgiref.sync import async_to_sync
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from orchestration.security_policy import sanitize_parameters, user_has_room_access

from .models import (
    DeferredWorkflowExecution,
    WorkflowApprovalRecord,
    WorkflowExecution,
    WorkflowImprovementSuggestion,
    WorkflowTrigger,
    UserWorkflow,
)
from .runtime import build_result_summary
from .temporal_integration import (
    build_replay_request,
    fetch_execution_runtime_state,
    pause_trigger_schedule,
    request_execution_cancel,
    resume_trigger_schedule,
    start_workflow_execution,
    submit_execution_approval,
)


def _serialize_approval(approval: WorkflowApprovalRecord | None):
    if not approval:
        return None
    return {
        "id": approval.id,
        "workflow_id": approval.workflow_id,
        "execution_id": approval.execution_id,
        "step_id": approval.step_id,
        "action": approval.action,
        "service": approval.service,
        "status": approval.status,
        "approval_message": approval.approval_message,
        "sanitized_params": approval.sanitized_params,
        "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
        "review_comment": approval.review_comment,
        "reviewed_by_id": approval.reviewed_by_id,
        "created_at": approval.created_at.isoformat(),
    }


def _serialize_execution(execution: WorkflowExecution, runtime_state=None):
    runtime_state = runtime_state or {}
    pending_approval = execution.pending_approval
    return {
        "id": execution.id,
        "workflow_id": execution.workflow_id,
        "status": runtime_state.get("status") or execution.status,
        "current_step": runtime_state.get("current_step") or execution.current_step or "",
        "last_completed_step": runtime_state.get("last_completed_step") or execution.last_completed_step or "",
        "waiting_on": runtime_state.get("waiting_on") or execution.waiting_on or "",
        "attempts": runtime_state.get("attempts") or execution.attempts or {},
        "trigger_type": execution.trigger_type,
        "trigger_data": execution.trigger_data or {},
        "result_summary": execution.result_summary or build_result_summary(execution.result),
        "receipt_ids": runtime_state.get("receipt_ids") or execution.receipt_ids or [],
        "pending_approval": _serialize_approval(pending_approval),
        "temporal_ids": {
            "workflow_id": execution.temporal_workflow_id,
            "run_id": execution.temporal_run_id,
        },
        "failure_summary": execution.failure_summary,
        "recovery_suggestion": execution.recovery_suggestion,
        "result": execution.result,
        "error_message": execution.error_message,
        "started_at": execution.started_at.isoformat(),
        "completed_at": execution.completed_at.isoformat() if execution.completed_at else None,
    }


def _serialize_workflow(workflow: UserWorkflow):
    triggers = list(workflow.registered_triggers.all())
    return {
        "id": workflow.id,
        "name": workflow.name,
        "description": workflow.description,
        "status": workflow.status,
        "created_at": workflow.created_at.isoformat(),
        "updated_at": workflow.updated_at.isoformat(),
        "execution_count": workflow.execution_count,
        "last_executed_at": workflow.last_executed_at.isoformat() if workflow.last_executed_at else None,
        "schedule_health": [trigger.schedule_status for trigger in triggers if trigger.trigger_type == "schedule"],
        "triggers": [
            {
                "id": trigger.id,
                "trigger_type": trigger.trigger_type,
                "service": trigger.service,
                "event": trigger.event,
                "is_active": trigger.is_active,
                "schedule_status": trigger.schedule_status,
            }
            for trigger in triggers
        ],
    }


def _serialize_deferred(item: DeferredWorkflowExecution):
    return {
        "id": item.id,
        "workflow_id": item.workflow_id,
        "status": item.status,
        "attempts": item.attempts,
        "next_attempt_at": item.next_attempt_at.isoformat() if item.next_attempt_at else None,
        "last_attempt_at": item.last_attempt_at.isoformat() if item.last_attempt_at else None,
        "last_error": item.last_error,
        "dead_letter_reason": item.dead_letter_reason,
        "recovery_hint": item.recovery_hint,
        "execution_id": item.execution_id,
    }


def _serialize_suggestion(item: WorkflowImprovementSuggestion):
    return {
        "id": item.id,
        "workflow_id": item.workflow_id,
        "execution_id": item.execution_id,
        "suggestion_type": item.suggestion_type,
        "title": item.title,
        "summary": item.summary,
        "proposed_changes": item.proposed_changes,
        "status": item.status,
        "created_at": item.created_at.isoformat(),
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_workflows(request):
    workflows = (
        UserWorkflow.objects.filter(user=request.user)
        .prefetch_related("registered_triggers")
        .order_by("-created_at")
    )
    return Response({"workflows": [_serialize_workflow(workflow) for workflow in workflows]})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_workflow_executions(request, workflow_id):
    workflow = UserWorkflow.objects.filter(id=workflow_id, user=request.user).first()
    if not workflow:
        return Response({"error": "Workflow not found"}, status=404)

    executions = workflow.executions.select_related("pending_approval").order_by("-started_at")
    return Response(
        {
            "workflow": _serialize_workflow(workflow),
            "executions": [_serialize_execution(execution) for execution in executions],
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def execution_detail(request, execution_id):
    execution = (
        WorkflowExecution.objects.filter(id=execution_id, workflow__user=request.user)
        .select_related("workflow", "pending_approval")
        .first()
    )
    if not execution:
        return Response({"error": "Execution not found"}, status=404)

    runtime_state = async_to_sync(fetch_execution_runtime_state)(execution)
    return Response({"execution": _serialize_execution(execution, runtime_state=runtime_state)})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def operations_inbox(request):
    approvals = WorkflowApprovalRecord.objects.filter(
        workflow__user=request.user,
        status="pending",
    ).select_related("execution", "workflow")
    failed_executions = WorkflowExecution.objects.filter(
        workflow__user=request.user,
        status__in=["waiting", "failed", "cancelled"],
    ).select_related("workflow", "pending_approval")
    deferred = DeferredWorkflowExecution.objects.filter(
        user=request.user,
        status__in=["queued", "processing", "abandoned"],
    ).select_related("workflow")
    suggestions = WorkflowImprovementSuggestion.objects.filter(
        user=request.user,
        status="proposed",
    ).select_related("workflow", "execution")

    return Response(
        {
            "pending_approvals": [_serialize_approval(item) for item in approvals],
            "attention_executions": [_serialize_execution(item) for item in failed_executions],
            "deferred_runs": [_serialize_deferred(item) for item in deferred],
            "suggestions": [_serialize_suggestion(item) for item in suggestions],
        }
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def run_workflow(request, workflow_id):
    workflow = UserWorkflow.objects.filter(id=workflow_id, user=request.user).first()
    if not workflow:
        return Response({"error": "Workflow not found"}, status=404)
    if workflow.status != "active":
        return Response({"error": "Workflow is not active"}, status=400)

    trigger_data = request.data.get("trigger_data", {})
    if trigger_data is None:
        trigger_data = {}
    if not isinstance(trigger_data, dict):
        return Response({"error": "trigger_data must be an object"}, status=400)
    trigger_data = sanitize_parameters(trigger_data)

    room_id = trigger_data.get("room_id")
    if room_id is not None:
        try:
            room_id = int(room_id)
        except (TypeError, ValueError):
            return Response({"error": "Invalid room_id in trigger_data"}, status=400)
        allowed = async_to_sync(user_has_room_access)(request.user.id, room_id)
        if not allowed:
            return Response({"error": "You do not have access to that room_id"}, status=403)
        trigger_data["room_id"] = room_id

    execution = async_to_sync(start_workflow_execution)(
        workflow,
        trigger_data=trigger_data,
        trigger_type="manual",
    )

    return Response(
        {
            "status": "started",
            "workflow_id": workflow.id,
            "execution_id": execution.id,
        }
    )


def _approval_action(request, execution_id: int, decision: str):
    execution = (
        WorkflowExecution.objects.filter(
            id=execution_id,
            workflow__user=request.user,
            status="waiting",
        )
        .select_related("pending_approval", "workflow")
        .first()
    )
    if not execution:
        return Response({"error": "Waiting execution not found"}, status=404)
    if not execution.pending_approval_id:
        return Response({"error": "This execution has no pending approval"}, status=400)

    comment = str(request.data.get("comment") or "").strip()
    async_to_sync(submit_execution_approval)(
        execution,
        approval_id=execution.pending_approval_id,
        reviewer_id=request.user.id,
        decision=decision,
        comment=comment,
    )
    return Response(
        {
            "status": "signalled",
            "decision": decision,
            "execution_id": execution.id,
            "approval_id": execution.pending_approval_id,
        }
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def approve_execution(request, execution_id):
    return _approval_action(request, execution_id, "approved")


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def reject_execution(request, execution_id):
    return _approval_action(request, execution_id, "rejected")


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def cancel_execution(request, execution_id):
    execution = (
        WorkflowExecution.objects.filter(id=execution_id, workflow__user=request.user)
        .select_related("workflow")
        .first()
    )
    if not execution:
        return Response({"error": "Execution not found"}, status=404)
    if execution.status in {"completed", "failed", "cancelled"}:
        return Response({"error": "Execution is already finished"}, status=400)

    reason = str(request.data.get("reason") or "").strip()
    async_to_sync(request_execution_cancel)(execution, reason=reason)
    return Response({"status": "signalled", "execution_id": execution.id})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def rerun_execution(request, execution_id):
    """Rerun an execution from a chosen step, honoring replay-safety per
    docs/contracts/replay-safety.md.

    Body params (all optional):
      from_step (str)      — step id to rerun from (preferred, v0.4.1)
      force (bool)         — bypass safety check; recorded in receipt
      from_failed_step (bool) — legacy: rerun from execution.current_step
    """
    execution = (
        WorkflowExecution.objects.filter(id=execution_id, workflow__user=request.user)
        .select_related("workflow")
        .first()
    )
    if not execution:
        return Response({"error": "Execution not found"}, status=404)

    raw_from_step = request.data.get("from_step")
    from_step_id = str(raw_from_step).strip() if raw_from_step else None
    if from_step_id == "":
        from_step_id = None
    force = bool(request.data.get("force"))
    from_failed_step = bool(request.data.get("from_failed_step"))

    try:
        replay_request = async_to_sync(build_replay_request)(
            execution,
            from_failed_step=from_failed_step,
            from_step_id=from_step_id,
            force=force,
        )
    except ValueError as exc:
        return Response({"error": str(exc)}, status=400)

    new_execution = async_to_sync(start_workflow_execution)(
        execution.workflow,
        trigger_data=replay_request["trigger_data"],
        trigger_type="rerun",
    )

    if force:
        mode = "forced"
    elif from_step_id:
        mode = "from_step"
    elif from_failed_step:
        mode = "from_failed_step"
    else:
        mode = "full"
    return Response(
        {
            "status": "started",
            "execution_id": new_execution.id,
            "workflow_id": execution.workflow_id,
            "mode": mode,
            "from_step": from_step_id,
            "forced": force,
        }
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def pause_trigger(request, trigger_id):
    trigger = WorkflowTrigger.objects.filter(id=trigger_id, workflow__user=request.user).first()
    if not trigger:
        return Response({"error": "Trigger not found"}, status=404)

    async_to_sync(pause_trigger_schedule)(trigger)
    return Response({"status": "paused", "trigger_id": trigger.id})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def resume_trigger(request, trigger_id):
    trigger = WorkflowTrigger.objects.filter(id=trigger_id, workflow__user=request.user).first()
    if not trigger:
        return Response({"error": "Trigger not found"}, status=404)

    async_to_sync(resume_trigger_schedule)(trigger)
    return Response({"status": "active", "trigger_id": trigger.id})
