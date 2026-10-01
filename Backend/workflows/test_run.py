"""Routine test runs: real work with side-effect steps stubbed (W-F, #204).

The test run is the evidence gate in front of a standing grant. It walks the
current version's steps, executes the replay-safe read-only ones, stubs every
side-effect/approval step, and records inputs, output preview, audit trail,
approval stop point and explicit failure states on ``WorkflowTestRun``.

W-D2 (#169) upgrades the input side with recorded shadow-replay inputs; the
record shape and gate stay the same.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from django.utils import timezone

from .capability_manifest import manifest_from_definition
from .models import WorkflowTestRun
from .runtime import get_step_id, is_step_safe_to_replay, step_requires_approval

logger = logging.getLogger(__name__)


def _selected_inputs(definition: Dict[str, Any]) -> Dict[str, Any]:
    selected = {}
    for index, step in enumerate(definition.get("steps") or []):
        step_id = get_step_id(step, index)
        selected[step_id] = sorted(str(key) for key in (step.get("params") or {}))
    return {"steps": selected}


def _output_preview(definition: Dict[str, Any]) -> Dict[str, Any]:
    preview = {}
    for index, step in enumerate(definition.get("steps") or []):
        step_id = get_step_id(step, index)
        preview[step_id] = f"{step.get('service')}.{step.get('action')}"
    return preview


async def run_test_run(workflow, *, user_id=None) -> WorkflowTestRun:
    """Execute a routine's read-only steps and stub the side-effecting ones."""
    from asgiref.sync import sync_to_async

    from .activity_executors import execute_workflow_step

    definition = workflow.definition or {}
    version = workflow.definition_version or 1
    context: Dict[str, Any] = {
        "trigger": {},
        "workflow": {
            "id": workflow.id,
            "policy": definition.get("policy") or {},
            "capabilities": manifest_from_definition(definition),
        },
        "user_id": user_id or workflow.user_id,
        "test_run": True,
    }

    audit = []
    failure_states = []
    approval_stop_point = ""
    status = "passed"

    for index, step in enumerate(definition.get("steps") or []):
        step_id = get_step_id(step, index)
        if step_requires_approval(step) or not is_step_safe_to_replay(step):
            if not approval_stop_point:
                approval_stop_point = step_id
            context[step_id] = {"status": "stubbed"}
            audit.append({
                "step": step_id,
                "outcome": "stubbed",
                "reason": "side-effect step is stubbed in a test run",
            })
            continue

        try:
            result = await execute_workflow_step(step, context)
        except Exception as exc:
            result = {"status": "error", "error": str(exc)}
        context[step_id] = result

        outcome = "success" if str(result.get("status")) == "success" else "error"
        audit.append({"step": step_id, "outcome": outcome, "status": result.get("status")})
        if outcome == "error":
            status = "failed"
            failure_states.append({
                "step": step_id,
                "error": result.get("error") or result.get("status") or "unknown failure",
            })

    completed_at = timezone.now()
    return await sync_to_async(WorkflowTestRun.objects.create)(
        workflow=workflow,
        definition_version=version,
        status=status,
        inputs=_selected_inputs(definition),
        output_preview=_output_preview(definition),
        audit_trail=audit,
        approval_stop_point=approval_stop_point,
        failure_states=failure_states,
        summary=(
            "Test run passed with side effects stubbed."
            if status == "passed"
            else f"Test run failed at {failure_states[0]['step']}."
        ),
        completed_at=completed_at,
    )
