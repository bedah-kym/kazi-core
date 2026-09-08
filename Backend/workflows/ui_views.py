"""HTML views for the human-gated workflow operations surface.

The JSON API in ``workflows/views.py`` stays the machine surface; these
views render the operator-facing pages (workflow list, operations inbox,
execution list/detail) and reuse the same runtime helpers so approval,
replay and cancellation semantics stay identical.
"""
from __future__ import annotations

import json

from asgiref.sync import async_to_sync
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render

from orchestration.security_policy import sanitize_parameters, user_has_room_access

from .models import (
    DeferredWorkflowExecution,
    WorkflowApprovalRecord,
    WorkflowExecution,
    WorkflowImprovementSuggestion,
    UserWorkflow,
)
from .temporal_integration import (
    build_replay_request,
    fetch_execution_runtime_state,
    pause_trigger_schedule,
    request_execution_cancel,
    resume_trigger_schedule,
    start_workflow_execution,
    submit_execution_approval,
)


def _own_workflow(request, workflow_id):
    return get_object_or_404(
        UserWorkflow, id=workflow_id, user=request.user
    )


def _own_execution(request, execution_id):
    return get_object_or_404(
        WorkflowExecution,
        id=execution_id,
        workflow__user=request.user,
    )


def _parse_trigger_data(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return sanitize_parameters(parsed)


def _temporal_error_message(exc: Exception, action: str) -> str:
    detail = str(exc) or exc.__class__.__name__
    return (
        f"The workflow run is no longer reachable in Temporal, so the "
        f"{action} could not be delivered. ({detail})"
    )


@login_required
def workflows_list(request):
    workflows = (
        UserWorkflow.objects.filter(user=request.user)
        .prefetch_related("registered_triggers")
        .order_by("-created_at")
    )
    return render(request, "workflows/workflows_list.html", {"workflows": workflows})


@login_required
def operations_inbox(request):
    approvals = WorkflowApprovalRecord.objects.filter(
        workflow__user=request.user,
        status="pending",
    ).select_related("execution", "workflow")
    attention_executions = WorkflowExecution.objects.filter(
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

    return render(
        request,
        "workflows/operations_inbox.html",
        {
            "approvals": approvals,
            "attention_executions": attention_executions,
            "deferred_runs": deferred,
            "suggestions": suggestions,
            "needs_attention": bool(
                approvals or attention_executions or deferred or suggestions
            ),
        },
    )


@login_required
def workflow_executions(request, workflow_id):
    workflow = _own_workflow(request, workflow_id)
    executions = workflow.executions.select_related("pending_approval")
    status_filter = request.GET.get("status", "")
    if status_filter:
        executions = executions.filter(status=status_filter)
    return render(
        request,
        "workflows/workflow_executions.html",
        {
            "workflow": workflow,
            "executions": executions,
            "status_filter": status_filter,
            "status_choices": WorkflowExecution.STATUS_CHOICES,
        },
    )


@login_required
def execution_detail(request, execution_id):
    execution = _own_execution(request, execution_id)
    workflow = execution.workflow

    runtime_state = None
    try:
        runtime_state = async_to_sync(fetch_execution_runtime_state)(execution)
    except Exception:
        runtime_state = None

    steps = workflow.get_steps()
    step_options = [
        {"id": str(step.get("id") or step.get("action") or ""), "label": str(step.get("id") or step.get("action") or "step")}
        for step in steps
    ]
    step_options = [option for option in step_options if option["id"]]

    return render(
        request,
        "workflows/workflow_execution_detail.html",
        {
            "workflow": workflow,
            "execution": execution,
            "runtime_state": runtime_state,
            "step_options": step_options,
            "approval": execution.pending_approval,
        },
    )


@login_required
def run_workflow_ui(request, workflow_id):
    workflow = _own_workflow(request, workflow_id)
    if workflow.status != "active":
        messages.error(request, "This workflow is not active, so it cannot run right now.")
        return redirect("workflows:workflows_list")

    trigger_data = _parse_trigger_data(request.POST.get("trigger_data", ""))
    if trigger_data is None:
        messages.error(request, "Trigger data must be a valid JSON object.")
        return redirect("workflows:workflows_list")

    room_id = trigger_data.get("room_id")
    if room_id is not None:
        try:
            room_id = int(room_id)
        except (TypeError, ValueError):
            messages.error(request, "Invalid room_id in trigger data.")
            return redirect("workflows:workflows_list")
        allowed = async_to_sync(user_has_room_access)(request.user.id, room_id)
        if not allowed:
            messages.error(request, "You do not have access to that room_id.")
            return redirect("workflows:workflows_list")
        trigger_data["room_id"] = room_id

    try:
        execution = async_to_sync(start_workflow_execution)(
            workflow,
            trigger_data=trigger_data,
            trigger_type="manual",
        )
    except Exception as exc:
        messages.error(request, f"Could not start the workflow. ({str(exc) or exc.__class__.__name__})")
        return redirect("workflows:workflows_list")

    messages.success(request, f"Started a new run of \"{workflow.name}\".")
    return redirect("workflows:execution_detail", execution_id=execution.id)


def _approval_decision_ui(request, execution_id: int, decision: str, label: str):
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
        messages.error(request, "That execution is no longer waiting for a decision.")
        return redirect("workflows:operations_inbox")
    if not execution.pending_approval_id:
        messages.error(request, "This execution has no pending approval.")
        return redirect("workflows:execution_detail", execution_id=execution.id)

    comment = (request.POST.get("comment") or "").strip()
    try:
        async_to_sync(submit_execution_approval)(
            execution,
            approval_id=execution.pending_approval_id,
            reviewer_id=request.user.id,
            decision=decision,
            comment=comment,
        )
    except Exception as exc:
        messages.error(request, _temporal_error_message(exc, "decision"))
        return redirect("workflows:execution_detail", execution_id=execution.id)

    messages.success(request, f"Step \"{execution.pending_approval.step_id}\" {label}.")
    return redirect("workflows:operations_inbox")


@login_required
def approve_execution_ui(request, execution_id):
    return _approval_decision_ui(request, execution_id, "approved", "approved")


@login_required
def reject_execution_ui(request, execution_id):
    return _approval_decision_ui(request, execution_id, "rejected", "rejected")


@login_required
def cancel_execution_ui(request, execution_id):
    execution = _own_execution(request, execution_id)
    if execution.status in {"completed", "failed", "cancelled"}:
        messages.error(request, "That execution is already finished.")
        return redirect("workflows:execution_detail", execution_id=execution.id)

    reason = (request.POST.get("reason") or "").strip()
    try:
        async_to_sync(request_execution_cancel)(execution, reason=reason)
    except Exception as exc:
        messages.error(request, _temporal_error_message(exc, "cancel request"))
        return redirect("workflows:execution_detail", execution_id=execution.id)

    messages.success(request, "Cancellation requested. The run will stop at the next safe point.")
    return redirect("workflows:execution_detail", execution_id=execution.id)


@login_required
def rerun_execution_ui(request, execution_id):
    execution = _own_execution(request, execution_id)

    raw_from_step = (request.POST.get("from_step") or "").strip() or None
    force = request.POST.get("force") == "on"
    from_failed_step = request.POST.get("from_failed_step") == "on"

    try:
        replay_request = async_to_sync(build_replay_request)(
            execution,
            from_failed_step=from_failed_step,
            from_step_id=raw_from_step,
            force=force,
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("workflows:execution_detail", execution_id=execution.id)

    try:
        new_execution = async_to_sync(start_workflow_execution)(
            execution.workflow,
            trigger_data=replay_request["trigger_data"],
            trigger_type="rerun",
        )
    except Exception as exc:
        messages.error(request, f"Could not start the rerun. ({str(exc) or exc.__class__.__name__})")
        return redirect("workflows:execution_detail", execution_id=execution.id)

    messages.success(request, "Rerun started from a fresh execution.")
    return redirect("workflows:execution_detail", execution_id=new_execution.id)


@login_required
def toggle_trigger_ui(request, trigger_id: int):
    from .models import WorkflowTrigger

    trigger = get_object_or_404(
        WorkflowTrigger, id=trigger_id, workflow__user=request.user
    )
    if request.POST.get("action") == "pause":
        async_to_sync(pause_trigger_schedule)(trigger)
        messages.success(request, "Schedule paused.")
    elif request.POST.get("action") == "resume":
        async_to_sync(resume_trigger_schedule)(trigger)
        messages.success(request, "Schedule resumed.")
    else:
        return HttpResponseBadRequest("Unknown trigger action")
    return redirect("workflows:workflows_list")


__all__ = [
    "approve_execution_ui",
    "cancel_execution_ui",
    "execution_detail",
    "operations_inbox",
    "reject_execution_ui",
    "rerun_execution_ui",
    "run_workflow_ui",
    "toggle_trigger_ui",
    "workflow_executions",
    "workflows_list",
]
