"""Reviewer agent (v0.7 W-D, issue #158).

A scheduled, metric-citing reviewer: reads the definition vN plus the last K
execution summaries (typed metrics, no raw tool output), and proposes vN+1
changes. A proposal only reaches the human after it passes shadow replay
(W-D2 #169) — a regressing suggestion is auto-rejected and logged. The model
never executes anything and never edits a live definition: acceptance routes
through the versioned write path (W-A #155).
"""
from __future__ import annotations

import logging
from statistics import median
from typing import Any, Dict, List, Optional

from django.conf import settings

from orchestration.telemetry import record_event

from .capabilities import validate_workflow_definition
from .capability_manifest import (
    capability_delta_for_suggestion,
    proposed_definition_from_changes,
)
from .models import UserWorkflow, WorkflowImprovementSuggestion
from .shadow_replay import replay_candidate
from .versioning import create_workflow_version

logger = logging.getLogger(__name__)

CITED_METRICS = {"duration", "step_count", "cost"}
_MAX_SUGGESTIONS = 5
_SUMMARIES_LIMIT = 20

REVIEWER_SYSTEM_PROMPT = (
    "You are a workflow reviewer. Read a workflow definition and recent run summaries, "
    "then propose concrete vN+1 changes. Each proposal must cite ONE metric it is expected "
    "to move: duration, step_count, or cost. Never execute anything; never touch live "
    "definitions. Return JSON only in the shape: "
    '{"suggestions": [{"title": "...", "summary": "...", "cited_metric": "duration", '
    '"proposed_changes": {"definition": {...full workflow definition...}}}]}. '
    "Follow the trust doctrine: sends, purchases, deletes, publishes and production "
    "changes stay behind approval."
)


def _window_days() -> int:
    try:
        return max(1, int(getattr(settings, "WORKFLOW_REVIEWER_WINDOW_DAYS", 30) or 30))
    except (TypeError, ValueError):
        return 30


def _execution_summaries(workflow: UserWorkflow, limit: int = _SUMMARIES_LIMIT) -> List[Dict[str, Any]]:
    from .routine import routine_run_history

    return routine_run_history(workflow, limit=limit)


def _metric_value(workflow: UserWorkflow, metric: str, summaries: List[Dict[str, Any]]) -> Optional[float]:
    executions = workflow.executions.filter(status="completed").order_by("-started_at")[:50]
    if metric == "step_count":
        return float(len(workflow.get_steps()))
    if metric == "duration":
        durations = [
            (run.completed_at - run.started_at).total_seconds()
            for run in executions
            if run.completed_at
        ]
        return round(median(durations), 1) if durations else None
    if metric == "cost":
        receipt_counts = [len(run.receipt_ids or []) for run in executions]
        return round(sum(receipt_counts) / len(receipt_counts), 2) if receipt_counts else None
    return None


def _review_prompt(workflow: UserWorkflow, summaries: List[Dict[str, Any]]) -> str:
    import json

    return "\n".join([
        f"Workflow: {workflow.name} (version {workflow.definition_version})",
        "Definition:",
        json.dumps(workflow.definition, indent=2, default=str),
        "",
        "Recent run summaries (typed metrics only):",
        json.dumps(summaries, indent=2, default=str),
        "",
        "Propose improvements that cite a metric. Return JSON only.",
    ])


async def review_workflow(workflow: UserWorkflow, *, llm=None) -> List[WorkflowImprovementSuggestion]:
    """Draft metric-citing suggestions, gated by shadow replay."""
    from asgiref.sync import sync_to_async

    if llm is None:
        from orchestration.llm_client import get_llm_client

        llm = get_llm_client()

    summaries = await sync_to_async(_execution_summaries)(workflow)
    response = await llm.generate_text(
        system_prompt=REVIEWER_SYSTEM_PROMPT,
        user_prompt=_review_prompt(workflow, summaries),
        temperature=0.2,
        max_tokens=1500,
        json_mode=True,
        model_role="reviewer",
    )
    parsed = llm.extract_json(response) or {}
    raw_suggestions = parsed.get("suggestions")
    if not isinstance(raw_suggestions, list):
        return []

    created: List[WorkflowImprovementSuggestion] = []
    for raw in raw_suggestions[: _MAX_SUGGESTIONS]:
        if not isinstance(raw, dict):
            continue
        suggested = await sync_to_async(_create_suggestion)(workflow, raw, summaries)
        if suggested is not None:
            created.append(suggested)
    return created


def _create_suggestion(workflow, raw, summaries) -> Optional[WorkflowImprovementSuggestion]:
    proposed_changes = raw.get("proposed_changes") or {}
    cited_metric = str(raw.get("cited_metric") or "duration")
    if cited_metric not in CITED_METRICS:
        cited_metric = "duration"
    title = str(raw.get("title") or "Reviewer suggestion")[:200]
    summary = str(raw.get("summary") or "")

    metadata: Dict[str, Any] = {
        "cited_metric": cited_metric,
        "metric_before": _metric_value(workflow, cited_metric, summaries),
        "shadow": {"replays": 0},
        "auto_rejected_reason": "",
    }
    status = "proposed"

    proposed_definition = proposed_definition_from_changes(workflow.definition or {}, proposed_changes)
    if proposed_definition is None:
        status = "dismissed"
        metadata["auto_rejected_reason"] = "proposal could not be resolved to a definition"
    else:
        valid, error = validate_workflow_definition(proposed_definition)
        if not valid:
            status = "dismissed"
            metadata["auto_rejected_reason"] = f"invalid proposed definition: {error}"
        else:
            from asgiref.sync import async_to_sync

            replay = async_to_sync(replay_candidate)(workflow, proposed_definition)
            metadata["shadow"] = replay
            if not replay["passed"]:
                status = "dismissed"
                metadata["auto_rejected_reason"] = "shadow replay regressed a previously succeeding step"

    suggestion = WorkflowImprovementSuggestion.objects.create(
        workflow=workflow,
        user_id=workflow.user_id,
        suggestion_type="reviewer",
        title=title,
        summary=summary,
        proposed_changes=proposed_changes,
        capability_delta=capability_delta_for_suggestion(workflow.definition or {}, proposed_changes),
        status=status,
        metadata=metadata,
    )

    if status == "dismissed":
        record_event("reviewer_suggestion_rejected", {
            "workflow_id": workflow.id,
            "suggestion_id": suggestion.id,
            "reason": metadata["auto_rejected_reason"],
        })
        logger.info(
            "Reviewer suggestion %s auto-rejected: %s",
            suggestion.id, metadata["auto_rejected_reason"],
        )
        return suggestion
    return suggestion


def accept_suggestion(suggestion: WorkflowImprovementSuggestion, *, by_user=None):
    """Human accept: create vN+1 through the versioned write path."""
    workflow = suggestion.workflow
    proposed = proposed_definition_from_changes(workflow.definition or {}, suggestion.proposed_changes)
    if proposed is None:
        raise ValueError("The proposed changes cannot be resolved to a definition.")
    valid, error = validate_workflow_definition(proposed)
    if not valid:
        raise ValueError(f"The proposed definition is invalid: {error}")

    version = create_workflow_version(
        workflow,
        proposed,
        created_by=by_user,
        change_summary=suggestion.title,
    )
    suggestion.status = "accepted"
    metadata = dict(suggestion.metadata or {})
    metadata["accepted_version"] = version.version
    suggestion.metadata = metadata
    suggestion.save(update_fields=["status", "metadata"])
    record_event("reviewer_suggestion_accepted", {
        "workflow_id": workflow.id,
        "suggestion_id": suggestion.id,
        "new_version": version.version,
    })
    return version


def dismiss_suggestion(suggestion: WorkflowImprovementSuggestion) -> None:
    suggestion.status = "dismissed"
    suggestion.save(update_fields=["status"])


def report_suggestion_outcomes(workflow: UserWorkflow) -> List[Dict[str, Any]]:
    """Compare the cited metric after acceptance, and log the result."""
    summaries = _execution_summaries(workflow)
    outcomes = []
    for suggestion in workflow.improvement_suggestions.filter(status="accepted"):
        metadata = suggestion.metadata or {}
        metric = metadata.get("cited_metric") or "duration"
        before = metadata.get("metric_before")
        after = _metric_value(workflow, metric, summaries)
        outcome = {
            "suggestion_id": suggestion.id,
            "metric": metric,
            "before": before,
            "after": after,
        }
        record_event("reviewer_metric_check", {
            "workflow_id": workflow.id,
            **outcome,
        })
        outcomes.append(outcome)
    return outcomes


def run_workflow_reviewer(workflow_id: Optional[int] = None) -> Dict[str, int]:
    """Scheduled reviewer sweep: outcomes first, then fresh suggestions."""
    from asgiref.sync import async_to_sync
    from django.utils import timezone

    if not bool(getattr(settings, "WORKFLOW_REVIEWER_ENABLED", True)):
        return {"enabled": False}

    reviewed = 0
    suggested = 0
    rejected = 0
    cutoff = timezone.now() - timezone.timedelta(days=_window_days())

    if workflow_id:
        workflows = list(UserWorkflow.objects.filter(id=workflow_id))
    else:
        workflows = list(
            UserWorkflow.objects.filter(status="active")
            .filter(executions__started_at__gte=cutoff)
            .distinct()
        )

    for workflow in workflows:
        report_suggestion_outcomes(workflow)
        try:
            suggestions = async_to_sync(review_workflow)(workflow)
        except Exception as exc:
            logger.warning("Reviewer failed for workflow %s: %s", workflow.id, exc)
            continue
        reviewed += 1
        suggested += sum(1 for suggestion in suggestions if suggestion.status == "proposed")
        rejected += sum(1 for suggestion in suggestions if suggestion.status == "dismissed")

    return {"reviewed": reviewed, "suggested": suggested, "rejected": rejected}
