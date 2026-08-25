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

from .models import DeferredWorkflowExecution, WorkflowApprovalRecord, WorkflowExecution
from .runtime import build_failure_summary
from .temporal_integration import start_workflow_execution

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = int(os.environ.get('WORKFLOW_REPLAY_MAX_ATTEMPTS', 6))
BASE_BACKOFF_SECONDS = int(os.environ.get('WORKFLOW_REPLAY_BACKOFF_BASE', 30))
MAX_BACKOFF_SECONDS = int(os.environ.get('WORKFLOW_REPLAY_BACKOFF_MAX', 10 * 60))
REPLAY_BATCH_LIMIT = int(os.environ.get('WORKFLOW_REPLAY_BATCH_LIMIT', 25))
TEMPORAL_GUARD_SECONDS = int(os.environ.get('WORKFLOW_REPLAY_GUARD_SECONDS', 120))
TEMPORAL_GUARD_KEY = 'temporal:unavailable'
APPROVAL_SWEEP_BATCH_LIMIT = int(os.environ.get('WORKFLOW_APPROVAL_SWEEP_BATCH_LIMIT', 100))
APPROVAL_MAX_PENDING_AGE_SECONDS = int(os.environ.get('WORKFLOW_APPROVAL_MAX_PENDING_AGE_SECONDS', 24 * 60 * 60))


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
            deferred.dead_letter_reason = 'workflow_inactive'
            deferred.recovery_hint = 'Reactivate or restore the workflow, then create a new run.'
            deferred.save(update_fields=['status', 'last_error', 'dead_letter_reason', 'recovery_hint', 'updated_at'])
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
            deferred.recovery_hint = ''
            deferred.dead_letter_reason = ''
            deferred.save(update_fields=['status', 'execution', 'last_error', 'recovery_hint', 'dead_letter_reason', 'updated_at'])
            started += 1
        except Exception as exc:
            deferred.attempts += 1
            deferred.last_error = str(exc)
            summary, recovery = build_failure_summary(step_id=None, error_message=str(exc))
            deferred.recovery_hint = recovery
            if _should_guard_temporal(exc):
                _set_temporal_guard()
            if deferred.attempts >= MAX_ATTEMPTS:
                deferred.status = 'abandoned'
                deferred.dead_letter_reason = summary
            else:
                deferred.status = 'queued'
                backoff = _compute_backoff(deferred.attempts)
                deferred.next_attempt_at = now + timedelta(seconds=backoff)
            deferred.save(update_fields=[
                'status',
                'attempts',
                'last_error',
                'next_attempt_at',
                'recovery_hint',
                'dead_letter_reason',
                'updated_at'
            ])
            failed += 1
            if _should_guard_temporal(exc):
                skipped += 1
                break

    return {"processed": processed, "started": started, "failed": failed, "skipped": skipped}


@shared_task(ignore_result=True)
def sweep_stuck_approvals(limit: int = None) -> Dict[str, int]:
    """
    Time out approvals stuck in ``pending`` past their expiry.

    Covers rows orphaned when the agent loop or a Temporal worker dies before
    the workflow's own wait-condition timeout can fire. Pending rows without
    an ``expires_at`` are swept once they exceed APPROVAL_MAX_PENDING_AGE_SECONDS.
    Workflow-kind sweeps also fail executions still parked in ``waiting``.
    """
    now = timezone.now()
    batch_limit = limit or APPROVAL_SWEEP_BATCH_LIMIT
    age_cutoff = now - timedelta(seconds=APPROVAL_MAX_PENDING_AGE_SECONDS)

    stuck_ids = list(
        WorkflowApprovalRecord.objects.filter(status='pending').filter(
            models.Q(expires_at__lte=now)
            | models.Q(expires_at__isnull=True, created_at__lt=age_cutoff)
        ).order_by('expires_at', 'created_at').values_list('id', flat=True)[:batch_limit]
    )
    if not stuck_ids:
        return {"swept": 0, "failed_executions": 0}

    swept = WorkflowApprovalRecord.objects.filter(
        id__in=stuck_ids,
        status='pending',
    ).update(
        status='timed_out',
        reviewed_at=now,
        review_comment='Swept: approval expired with no reviewer.',
    )

    execution_ids = list(
        WorkflowApprovalRecord.objects.filter(
            id__in=stuck_ids,
            kind='workflow',
            execution_id__isnull=False,
        ).values_list('execution_id', flat=True)
    )

    failed_executions = 0
    if execution_ids:
        summary, recovery = build_failure_summary(
            step_id=None,
            error_message='Approval expired while the run was waiting on a human review.',
            waiting_on='approval',
        )
        failed_executions = WorkflowExecution.objects.filter(
            id__in=execution_ids,
            status='waiting',
        ).update(
            status='failed',
            waiting_on='',
            error_message='Stuck approval swept: pending approval timed out with no reviewer.',
            failure_summary=summary,
            recovery_suggestion=recovery,
        )
        WorkflowExecution.objects.filter(
            id__in=execution_ids,
            pending_approval_id__in=stuck_ids,
        ).update(pending_approval=None)

    return {"swept": swept, "failed_executions": failed_executions}
