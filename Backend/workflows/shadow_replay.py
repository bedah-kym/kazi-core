"""Shadow replay (v0.7 W-D2, issue #169).

Replays a proposed definition against the recorded inputs of the last K
replay-safe executions, with side-effect steps stubbed. A suggestion that
regresses a previously succeeding step — or any assertion — never reaches the
human: the reviewer auto-rejects it and logs the evidence.

K is explicit (``SHADOW_REPLAY_WINDOW``, default 10) and can be overridden
per definition via ``definition.config.shadow_replay_window``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from django.conf import settings

from .test_run import execute_definition_dry

logger = logging.getLogger(__name__)


def shadow_replay_window(definition: Optional[Dict[str, Any]]) -> int:
    config = (definition or {}).get("config") if isinstance((definition or {}).get("config"), dict) else {}
    try:
        configured = int(config.get("shadow_replay_window") or getattr(settings, "SHADOW_REPLAY_WINDOW", 10) or 10)
    except (TypeError, ValueError):
        configured = 10
    return max(1, configured)


def collect_replay_inputs(workflow, k: Optional[int] = None) -> List[Dict[str, Any]]:
    """Recorded inputs of the last K completed executions (replay context)."""
    k = k or shadow_replay_window(workflow.definition or {})
    runs = (
        workflow.executions.filter(status="completed")
        .exclude(result=None)
        .order_by("-started_at")[:k]
    )
    inputs: List[Dict[str, Any]] = []
    for run in runs:
        seed: Dict[str, Any] = dict(run.trigger_data or {})
        if isinstance(run.result, dict):
            for key, value in run.result.items():
                if key not in {"trigger", "workflow", "user_id", "execution_id", "preferences"}:
                    seed[key] = value
        inputs.append({
            "execution_id": run.id,
            "definition_version": run.definition_version,
            "seed": seed,
            "recorded": run.result or {},
        })
    return inputs


def _regressed_steps(recorded: Dict[str, Any], replayed_context: Dict[str, Any]) -> List[str]:
    regressed = []
    for step_id, value in (recorded or {}).items():
        if not isinstance(value, dict) or str(value.get("status")) != "success":
            continue
        replayed = replayed_context.get(step_id)
        if not isinstance(replayed, dict) or str(replayed.get("status")) != "success":
            regressed.append(step_id)
    return regressed


async def replay_candidate(workflow, proposed_definition: Dict[str, Any], *, k: Optional[int] = None) -> Dict[str, Any]:
    """Replay a proposed definition over recorded inputs.

    Returns ``passed=False`` when any replayed run fails or a previously
    succeeding step regresses. No recorded inputs means nothing to regress
    against; the caller decides whether that is enough evidence.
    """
    from asgiref.sync import sync_to_async

    inputs = await sync_to_async(collect_replay_inputs)(workflow, k=k)
    if not inputs:
        return {"passed": True, "replays": 0, "regressions": [], "audit": []}

    regressions: List[Dict[str, Any]] = []
    audit: List[Dict[str, Any]] = []
    for record in inputs:
        outcome = await execute_definition_dry(
            proposed_definition,
            workflow=workflow,
            context_seed=record["seed"],
            user_id=workflow.user_id,
        )
        regressed = _regressed_steps(record["recorded"], outcome["context"])
        entry = {
            "execution_id": record["execution_id"],
            "status": outcome["status"],
            "regressed_steps": regressed,
        }
        audit.append(entry)
        if outcome["status"] != "passed" or regressed:
            regressions.append({
                "execution_id": record["execution_id"],
                "steps": regressed,
                "failures": outcome["failure_states"],
            })

    return {
        "passed": not regressions,
        "replays": len(inputs),
        "regressions": regressions,
        "audit": audit,
    }
