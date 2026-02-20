from typing import Dict
from celery import shared_task
from django.db import models
from datetime import timedelta
from django.utils import timezone
from django.db import transaction
from django.core.cache import cache
from asgiref.sync import async_to_sync
import logging
import os

from .models import DeferredWorkflowExecution
from .temporal_integration import start_workflow_execution

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = int(os.environ.get('WORKFLOW_REPLAY_MAX_ATTEMPTS', 6))
BASE_BACKOFF_SECONDS = int(os.environ.get('WORKFLOW_REPLAY_BACKOFF_BASE', 30))
MAX_BACKOFF_SECONDS = int(os.environ.get('WORKFLOW_REPLAY_BACKOFF_MAX', 10 * 60))
REPLAY_BATCH_LIMIT = int(os.environ.get('WORKFLOW_REPLAY_BATCH_LIMIT', 10))
TEMPORAL_GUARD_SECONDS = int(os.environ.get('WORKFLOW_REPLAY_GUARD_SECONDS', 120))
TEMPORAL_GUARD_KEY = 'temporal:unavailable'


def _compute_backoff(attempts: int) -> int:
    delay = BASE_BACKOFF_SECONDS * (2 ** max(attempts - 1, 0))
    return min(delay, MAX_BACKOFF_SECONDS)

def _temporal_guard_active() -> bool:
    return bool(cache.get(TEMPORAL_GUARD_KEY))

def _set_temporal_guard() -> None:
    if TEMPORAL_GUARD_SECONDS <= 0:
        return
    cache.set(TEMPORAL_GUARD_KEY, True, timeout=TEMPORAL_GUARD_SECONDS)

def _should_guard_temporal(exc: Exception) -> bool:
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    text = str(exc).lower()
    return any(
        token in text
        for token in (
            'temporal',
            'connection refused',
            'connection',
            'timeout',
            'unavailable',
            'grpc',
        )
    )


@shared_task(ignore_result=True)
def replay_deferred_workflows(limit: int = None) -> Dict[str, int]:
    """
    Attempt to start queued workflows when Temporal is back up.
    """
    now = timezone.now()
    processed = 0
    started = 0
    failed = 0
    skipped = 0
    batch_limit = limit or REPLAY_BATCH_LIMIT

    if _temporal_guard_active():
        logger.warning("Temporal guard active; skipping deferred workflow replay.")
        return {"processed": 0, "started": 0, "failed": 0, "skipped": 1}

    # Select due items in small batches to avoid long locks.
    due_ids = list(
        DeferredWorkflowExecution.objects.filter(
            status='queued',
        ).filter(
            models.Q(next_attempt_at__lte=now) | models.Q(next_attempt_at__isnull=True)
        ).order_by('next_attempt_at').values_list('id', flat=True)[:batch_limit]
    )

    if not due_ids:
        return {"processed": 0, "started": 0, "failed": 0, "skipped": 0}

    for deferred_id in due_ids:
        processed += 1
        with transaction.atomic():
            updated = DeferredWorkflowExecution.objects.filter(
                id=deferred_id,
                status='queued'
            ).update(
                status='processing',
                last_attempt_at=now
            )
            if not updated:
                continue

        deferred = DeferredWorkflowExecution.objects.filter(id=deferred_id).select_related('workflow').first()
        if not deferred:
            continue

        workflow_obj = deferred.workflow
        if not workflow_obj or workflow_obj.status != 'active':
            deferred.status = 'abandoned'
            deferred.last_error = 'Workflow missing or inactive'
            deferred.save(update_fields=['status', 'last_error', 'updated_at'])
            failed += 1
            continue

        try:
            execution = async_to_sync(start_workflow_execution)(
                workflow_obj,
                deferred.trigger_data or {},
                'manual',
            )
            deferred.status = 'started'
            deferred.execution = execution
            deferred.last_error = None
            deferred.save(update_fields=['status', 'execution', 'last_error', 'updated_at'])
            started += 1
        except Exception as exc:
            deferred.attempts += 1
            deferred.last_error = str(exc)
            if _should_guard_temporal(exc):
                _set_temporal_guard()
            if deferred.attempts >= MAX_ATTEMPTS:
                deferred.status = 'abandoned'
            else:
                deferred.status = 'queued'
                backoff = _compute_backoff(deferred.attempts)
                deferred.next_attempt_at = now + timedelta(seconds=backoff)
            deferred.save(update_fields=[
                'status',
                'attempts',
                'last_error',
                'next_attempt_at',
                'updated_at'
            ])
            failed += 1
            if _should_guard_temporal(exc):
                skipped += 1
                break

    return {"processed": processed, "started": started, "failed": failed, "skipped": skipped}
