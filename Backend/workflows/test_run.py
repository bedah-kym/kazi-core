"""Routine test runs and dry definition walks (W-F #204, W-D2 #169).

``execute_definition_dry`` walks a definition with side-effect steps stubbed:
replay-safe read-only steps execute for real, everything else is stubbed.
``run_test_run`` records the evidence gate in front of a standing grant;
``shadow_replay.replay_candidate`` feeds the same engine recorded inputs so a
reviewer suggestion must not regress a step that previously succeeded.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from django.utils import timezone

from .capability_manifest import manifest_from_definition
from .models import WorkflowTestRun
from .runtime import get_step_id, is_step_safe_to_replay, step_requires_approval

logger = logging.getLogger(__name__)

_SEED_RESERVED_KEYS = {"trigger", "workflow", "user_id", "execution_id", "preferences"}


async def execute_definition_dry(
    definition: Dict[str, Any],
    *,
    workflow,
    context_seed: Optional[Dict[str, Any]] = None,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Walk ``definition`` with side effects stubbed. Returns outcome dict."""
    from .activity_executors import execute_workflow_step

    context: Dict[str, Any] = {
        "trigger": (context_seed or {}).get("trigger") or {},
        "workflow": {
            "id": workflow.id,
            "policy": definition.get("policy") or {},
            "capabilities": manifest_from_definition(definition),
        },
        "user_id": user_id or workflow.user_id,
        "test_run": True,
    }
    if context_seed:
        for key, value in context_seed.items():
            if key not in _SEED_RESERVED_KEYS:
                context[key] = value

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

    return {
        "status": status,
        "audit": audit,
        "approval_stop_point": approval_stop_point,
        "failure_states": failure_states,
        "context": context,
    }


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

    definition = workflow.definition or {}
    version = workflow.definition_version or 1
    outcome = await execute_definition_dry(definition, workflow=workflow, user_id=user_id)

    completed_at = timezone.now()
    return await sync_to_async(WorkflowTestRun.objects.create)(
        workflow=workflow,
        definition_version=version,
        status=outcome["status"],
        inputs=_selected_inputs(definition),
        output_preview=_output_preview(definition),
        audit_trail=outcome["audit"],
        approval_stop_point=outcome["approval_stop_point"],
        failure_states=outcome["failure_states"],
        summary=(
            "Test run passed with side effects stubbed."
            if outcome["status"] == "passed"
            else f"Test run failed at {outcome['failure_states'][0]['step']}."
        ),
        completed_at=completed_at,
    )
