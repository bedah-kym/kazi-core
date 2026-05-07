"""Render a human-readable timeline for a single workflow execution.

Usage:
    python Backend/manage.py kazi_trace <execution_id>
    python Backend/manage.py kazi_trace <execution_id> --json

Reads:
- WorkflowExecution row (status, current/last step, receipts, attempts,
  recovery hints)
- WorkflowApprovalRecord rows associated with the execution (every
  pause-and-decide cycle)
- The workflow definition (so steps appear in declared order even when
  execution skipped or stalled)

Writes:
- A pretty timeline to stdout (default), one line per event, grouped
  by phase. Designed to be copyable into incident notes.
- A JSON document with the same data when --json is set, for piping
  into other tools.

This is the v0.4 M4-2 "debug an agent run" surface — the brief's
"developer can debug an agent run from the trace alone" SLA is held
against this command.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from django.core.management.base import BaseCommand, CommandError

from workflows.models import (
    DeferredWorkflowExecution,
    WorkflowApprovalRecord,
    WorkflowExecution,
)


def _iso(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _step_status(
    step_id: str,
    *,
    last_completed: Optional[str],
    current: Optional[str],
    waiting_on: Optional[str],
    overall_status: str,
    step_order: List[str],
) -> str:
    """Best-effort per-step status derivation from the execution row.

    The execution row tells us last_completed_step + current_step;
    everything before last_completed is "completed", current is either
    "running" or "waiting" depending on overall status, everything after
    is "pending" (or "skipped" for a failed/cancelled run).
    """
    if last_completed and step_id in step_order and current and current in step_order:
        last_idx = step_order.index(last_completed) if last_completed in step_order else -1
        current_idx = step_order.index(current)
        my_idx = step_order.index(step_id)
        if my_idx <= last_idx:
            return "completed"
        if my_idx == current_idx:
            if overall_status == "waiting":
                return f"waiting ({waiting_on or 'unknown'})"
            if overall_status == "running":
                return "running"
            if overall_status in {"failed", "cancelled"}:
                return "failed"
            return overall_status
        if overall_status in {"failed", "cancelled", "rejected"}:
            return "skipped"
        return "pending"
    # Fall back: just say what the execution row says.
    return overall_status


def _build_trace(execution: WorkflowExecution) -> Dict[str, Any]:
    """Assemble the trace structure (the same shape used for both the
    pretty render and the --json output).
    """
    workflow = execution.workflow
    definition = workflow.definition if workflow else {}
    declared_steps = [
        s for s in (definition.get("steps") or []) if isinstance(s, dict)
    ]
    step_order = [s.get("id") or s.get("action") or f"step_{i + 1}"
                  for i, s in enumerate(declared_steps)]

    approval_rows = list(
        WorkflowApprovalRecord.objects.filter(execution=execution).order_by("created_at")
    ) if hasattr(WorkflowApprovalRecord, "execution") else []

    if not approval_rows and execution.pending_approval_id:
        try:
            approval_rows = [WorkflowApprovalRecord.objects.get(id=execution.pending_approval_id)]
        except WorkflowApprovalRecord.DoesNotExist:
            approval_rows = []

    deferred_rows = list(
        DeferredWorkflowExecution.objects.filter(execution=execution).order_by("created_at")
    )

    steps_render: List[Dict[str, Any]] = []
    for step_def in declared_steps:
        step_id = step_def.get("id") or step_def.get("action") or "?"
        steps_render.append({
            "id": step_id,
            "action": step_def.get("action"),
            "service": step_def.get("service"),
            "requires_approval": bool(step_def.get("requires_approval")),
            "safe_to_replay": bool(step_def.get("safe_to_replay")),
            "status": _step_status(
                step_id,
                last_completed=execution.last_completed_step,
                current=execution.current_step,
                waiting_on=execution.waiting_on,
                overall_status=execution.status,
                step_order=step_order,
            ),
            "attempts": (execution.attempts or {}).get(step_id, 0),
        })

    approvals_render: List[Dict[str, Any]] = []
    for ar in approval_rows:
        approvals_render.append({
            "id": ar.id,
            "step_id": getattr(ar, "step_id", None),
            "step_action": getattr(ar, "action", None),
            "status": ar.status,
            "summary": getattr(ar, "approval_message", None),
            "decided_by": getattr(getattr(ar, "reviewed_by", None), "username", None),
            "decision_note": getattr(ar, "review_comment", None),
            "requested_at": _iso(getattr(ar, "created_at", None)),
            "deadline_at": _iso(getattr(ar, "expires_at", None)),
            "decided_at": _iso(getattr(ar, "reviewed_at", None)),
        })

    deferred_render: List[Dict[str, Any]] = []
    for dr in deferred_rows:
        deferred_render.append({
            "id": dr.id,
            "status": dr.status,
            "attempts": dr.attempts,
            "next_attempt_at": _iso(dr.next_attempt_at),
            "last_error": dr.last_error,
            "dead_letter_reason": dr.dead_letter_reason,
        })

    return {
        "execution": {
            "id": execution.id,
            "workflow_id": workflow.id if workflow else None,
            "workflow_name": workflow.name if workflow else None,
            "trigger_type": execution.trigger_type,
            "status": execution.status,
            "current_step": execution.current_step,
            "last_completed_step": execution.last_completed_step,
            "waiting_on": execution.waiting_on,
            "started_at": _iso(execution.started_at),
            "completed_at": _iso(execution.completed_at),
            "error_message": execution.error_message,
            "failure_summary": execution.failure_summary,
            "recovery_suggestion": execution.recovery_suggestion,
            "result_summary": execution.result_summary,
            "receipt_ids": list(execution.receipt_ids or []),
        },
        "steps": steps_render,
        "approvals": approvals_render,
        "deferred": deferred_render,
    }


def _render_pretty(trace: Dict[str, Any]) -> str:
    e = trace["execution"]
    out: List[str] = []
    out.append("=" * 72)
    out.append(f" Execution #{e['id']}  workflow={e['workflow_name']!r} (id={e['workflow_id']})")
    out.append("=" * 72)
    out.append(f"  trigger:        {e['trigger_type']}")
    out.append(f"  status:         {e['status']}")
    if e.get("current_step"):
        out.append(f"  current step:   {e['current_step']}")
    if e.get("waiting_on"):
        out.append(f"  waiting on:     {e['waiting_on']}")
    out.append(f"  started:        {e['started_at']}")
    out.append(f"  completed:      {e['completed_at']}")
    if e.get("result_summary"):
        out.append("")
        out.append("  result summary:")
        for line in str(e["result_summary"]).splitlines():
            out.append(f"    {line}")
    if e.get("failure_summary") or e.get("error_message"):
        out.append("")
        out.append("  failure:")
        if e.get("failure_summary"):
            out.append(f"    {e['failure_summary']}")
        if e.get("error_message"):
            out.append(f"    error: {e['error_message']}")
        if e.get("recovery_suggestion"):
            out.append(f"    suggestion: {e['recovery_suggestion']}")

    out.append("")
    out.append("Steps (in declared order):")
    if not trace["steps"]:
        out.append("  (no steps in workflow definition)")
    for idx, step in enumerate(trace["steps"], start=1):
        marks = []
        if step["requires_approval"]:
            marks.append("approval")
        if step["safe_to_replay"]:
            marks.append("replay-safe")
        marks_str = f"  [{', '.join(marks)}]" if marks else ""
        attempts_str = f"  (attempts={step['attempts']})" if step["attempts"] else ""
        out.append(
            f"  {idx:>2}. {step['id']:<28} {step['status']:<24} "
            f"action={step['action']!r}{marks_str}{attempts_str}"
        )

    if trace["approvals"]:
        out.append("")
        out.append("Approvals:")
        for ap in trace["approvals"]:
            out.append(
                f"  - step={ap['step_id']!r}  status={ap['status']}  "
                f"requested={ap['requested_at']}  decided={ap['decided_at']}"
            )
            if ap.get("summary"):
                out.append(f"      summary: {ap['summary']}")
            if ap.get("decided_by"):
                out.append(
                    f"      by: {ap['decided_by']}"
                    + (f"  note: {ap['decision_note']}" if ap.get("decision_note") else "")
                )

    if trace["deferred"]:
        out.append("")
        out.append("Deferred-run history:")
        for dr in trace["deferred"]:
            out.append(
                f"  - id={dr['id']}  status={dr['status']}  "
                f"attempts={dr['attempts']}  next_attempt_at={dr['next_attempt_at']}"
            )
            if dr.get("dead_letter_reason"):
                out.append(f"      dead_letter: {dr['dead_letter_reason']}")
            elif dr.get("last_error"):
                out.append(f"      last_error: {dr['last_error']}")

    if e.get("receipt_ids"):
        out.append("")
        out.append(f"Receipts: {len(e['receipt_ids'])} record(s) — ids={e['receipt_ids']}")

    out.append("")
    return "\n".join(out)


class Command(BaseCommand):
    help = "Render a human-readable trace for a workflow execution."

    def add_arguments(self, parser):
        parser.add_argument(
            "execution_id",
            type=int,
            help="ID of the WorkflowExecution to trace.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Output the trace as JSON instead of pretty text.",
        )

    def handle(self, *args, **options):
        execution_id = options["execution_id"]
        as_json = options["json"]

        try:
            execution = (
                WorkflowExecution.objects
                .select_related("workflow")
                .get(id=execution_id)
            )
        except WorkflowExecution.DoesNotExist:
            raise CommandError(f"Execution {execution_id} not found.")

        trace = _build_trace(execution)
        if as_json:
            self.stdout.write(json.dumps(trace, indent=2, default=str))
        else:
            self.stdout.write(_render_pretty(trace))
