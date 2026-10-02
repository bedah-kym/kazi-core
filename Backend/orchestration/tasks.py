"""Celery tasks for orchestration background work."""
from __future__ import annotations

import logging
from typing import Any, Dict

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(ignore_result=True)
def roll_telemetry() -> Dict[str, Any]:
    """Nightly rollup of JSONL telemetry into entity facts + derived watches.

    See orchestration.telemetry_rollups for the rotation / offset-bookmark /
    idempotency design (issue #153).
    """
    from orchestration.telemetry_rollups import run_rollup

    try:
        return run_rollup()
    except Exception:
        logger.exception("Telemetry rollup task failed")
        return {"noop": False, "error": "rollup_failed"}


@shared_task(ignore_result=True)
def mine_approval_preferences() -> Dict[str, Any]:
    """Nightly mining of approve/deny history into learned overrides (#152)."""
    from orchestration.preference_mining import mine_approval_overrides

    try:
        return mine_approval_overrides()
    except Exception:
        logger.exception("Preference mining task failed")
        return {"error": "mining_failed"}


@shared_task(ignore_result=True)
def roll_approval_telemetry() -> Dict[str, Any]:
    """Nightly approval telemetry rollup + rule candidates (#166)."""
    from orchestration.approval_telemetry import rollup_approval_telemetry

    try:
        return rollup_approval_telemetry()
    except Exception:
        logger.exception("Approval telemetry rollup failed")
        return {"error": "rollup_failed"}


@shared_task(ignore_result=True)
def curate_skill_lifecycle() -> Dict[str, Any]:
    """Weekly skill curator pass: demote unused skills, never promote (#138)."""
    from orchestration.skill_registry import curate_skills

    try:
        return curate_skills()
    except Exception:
        logger.exception("Skill curation task failed")
        return {"error": "curation_failed"}


@shared_task(ignore_result=True)
def send_daily_digest() -> Dict[str, Any]:
    """Rung 2: deliver the daily initiative digest (#154)."""
    from orchestration.initiative import send_digests

    try:
        return send_digests()
    except Exception:
        # Re-raise so Celery records a failed run (ignore_result hides returns).
        logger.exception("Daily digest task failed")
        raise
