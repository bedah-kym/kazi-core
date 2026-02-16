from typing import Dict
from celery import shared_task
from django.db import models
from datetime import timedelta
from django.utils import timezone
from django.db import transaction
from asgiref.sync import async_to_sync
import logging

from .models import DeferredWorkflowExecution
from .temporal_integration import start_workflow_execution

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 6
BASE_BACKOFF_SECONDS = 30
MAX_BACKOFF_SECONDS = 10 * 60


def _compute_backoff(attempts: int) -> int:
    delay = BASE_BACKOFF_SECONDS * (2 ** max(attempts - 1, 0))
    return min(delay, MAX_BACKOFF_SECONDS)


@shared_task
def replay_deferred_workflows(limit: int = 10) -> Dict[str, int]:
    """
    Attempt to start queued workflows when Temporal is back up.
    """
    now = timezone.now()
    processed = 0
    started = 0
    failed = 0

    # Select due items in small batches to avoid long locks.
    due_ids = list(
        DeferredWorkflowExecution.objects.filter(
            status='queued',
        ).filter(
            models.Q(next_attempt_at__lte=now) | models.Q(next_attempt_at__isnull=True)
        ).order_by('next_attempt_at').values_list('id', flat=True)[:limit]
    )

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

    return {"processed": processed, "started": started, "failed": failed}
