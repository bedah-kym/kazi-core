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
