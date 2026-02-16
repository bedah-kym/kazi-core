"""Temporal workflow definitions and helpers."""
import asyncio
import uuid
from datetime import timedelta
from typing import Any, Dict, Optional

from django.conf import settings
from django.utils import timezone
from asgiref.sync import sync_to_async

from temporalio import workflow, activity
from temporalio.client import Client, Schedule, ScheduleActionStartWorkflow, ScheduleSpec, SchedulePolicy, ScheduleOverlapPolicy
from temporalio.common import RetryPolicy

from .activity_executors import execute_workflow_step
from .utils import safe_eval_condition, compact_context
from .models import WorkflowExecution


@activity.defn
async def create_execution_record(
    workflow_id: int,
    temporal_workflow_id: str,
    temporal_run_id: str,
    trigger_type: str,
    trigger_data: Dict[str, Any],
) -> int:
    def _create():
        return WorkflowExecution.objects.create(
            workflow_id=workflow_id,
            temporal_workflow_id=temporal_workflow_id,
            temporal_run_id=temporal_run_id,
            trigger_type=trigger_type,
            trigger_data=trigger_data,
            status='running'
        ).id

    return await sync_to_async(_create)()


@activity.defn
async def update_execution_record(
    execution_id: int,
    status: str,
    result: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
) -> None:
    def _update():
        execution = WorkflowExecution.objects.filter(id=execution_id).first()
        if not execution:
            return
        execution.status = status
        execution.result = result
        execution.error_message = error_message
        execution.completed_at = timezone.now() if status in ('completed', 'failed', 'cancelled') else None
        execution.save()

    await sync_to_async(_update)()


@activity.defn
async def run_step_activity(step: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    return await execute_workflow_step(step, context)


@workflow.defn
class DynamicUserWorkflow:
    @workflow.run
    async def run(
        self,
        workflow_id: int,
        workflow_definition: Dict[str, Any],
        trigger_data: Dict[str, Any],
        trigger_type: str,
        execution_id: Optional[int],
        user_id: Optional[int],
    ) -> Dict[str, Any]:
        context: Dict[str, Any] = {
            'trigger': trigger_data,
            'workflow': {
                'id': workflow_id,
                'policy': workflow_definition.get('policy') or {},
            },
            'user_id': user_id,
        }

        if not execution_id:
            execution_id = await workflow.execute_activity(
                create_execution_record,
                args=[workflow_id, workflow.info().workflow_id, workflow.info().run_id, trigger_type, trigger_data],
                schedule_to_close_timeout=timedelta(seconds=30),
            )

        try:
            for step in workflow_definition.get('steps', []):
                condition = step.get('condition')
                if condition and not safe_eval_condition(condition, context):
                    continue

                step_id = step.get('id') or step.get('action') or f"step_{len(context)}"
                on_error = str(step.get('on_error') or 'stop').lower()

                try:
                    result = await workflow.execute_activity(
                        run_step_activity,
                        args=[step, context],
                        schedule_to_close_timeout=timedelta(minutes=5),
                        retry_policy=RetryPolicy(
                            initial_interval=timedelta(seconds=2),
                            maximum_interval=timedelta(seconds=30),
                            maximum_attempts=3,
                        ),
                    )
                    context[step_id] = result
                except Exception as exc:
                    context[step_id] = {"status": "error", "error": str(exc)}
                    if on_error == 'continue':
                        continue
                    raise

            stored_context = compact_context(context)
            await workflow.execute_activity(
                update_execution_record,
                args=[execution_id, 'completed', stored_context, None],
                schedule_to_close_timeout=timedelta(seconds=30),
            )
            return context

        except Exception as exc:
            stored_context = compact_context(context)
            await workflow.execute_activity(
                update_execution_record,
                args=[execution_id, 'failed', stored_context, str(exc)],
                schedule_to_close_timeout=timedelta(seconds=30),
            )
            raise


async def get_temporal_client() -> Client:
    return await Client.connect(settings.TEMPORAL_HOST, namespace=settings.TEMPORAL_NAMESPACE)


async def start_workflow_execution(
    workflow_obj,
    trigger_data: Dict[str, Any],
    trigger_type: str,
    execution_id: Optional[int] = None,
) -> WorkflowExecution:
    client = await get_temporal_client()
    workflow_run_id = f"workflow-{workflow_obj.id}-{uuid.uuid4()}"

    if execution_id is None:
        def _create_execution():
            return WorkflowExecution.objects.create(
                workflow=workflow_obj,
                temporal_workflow_id=workflow_run_id,
                trigger_type=trigger_type,
                trigger_data=trigger_data,
                status='running'
            )
        execution = await sync_to_async(_create_execution)()
        execution_id = execution.id
    else:
        execution = await sync_to_async(lambda: WorkflowExecution.objects.filter(id=execution_id).first())()

    handle = await client.start_workflow(
        DynamicUserWorkflow.run,
        args=[workflow_obj.id, workflow_obj.definition, trigger_data, trigger_type, execution_id, workflow_obj.user_id],
        id=workflow_run_id,
        task_queue=settings.TEMPORAL_TASK_QUEUE,
    )

    def _update_execution():
        if not execution:
            return
        execution.temporal_workflow_id = handle.id
        execution.temporal_run_id = handle.run_id
        execution.save(update_fields=['temporal_workflow_id', 'temporal_run_id'])

    await sync_to_async(_update_execution)()
    return execution


async def create_schedule_for_trigger(trigger_obj) -> None:
    if not trigger_obj.schedule_cron:
        return

    client = await get_temporal_client()
    schedule_id = f"workflow-{trigger_obj.workflow_id}-trigger-{trigger_obj.id}"
    trigger_data = {
        'schedule': {
            'cron': trigger_obj.schedule_cron,
            'timezone': trigger_obj.schedule_timezone
        }
    }

    action = ScheduleActionStartWorkflow(
        DynamicUserWorkflow.run,
        args=[
            trigger_obj.workflow_id,
            trigger_obj.workflow.definition,
            trigger_data,
            'schedule',
            None,
            trigger_obj.workflow.user_id,
        ],
        task_queue=settings.TEMPORAL_TASK_QUEUE,
    )

    schedule = Schedule(
        action=action,
        spec=ScheduleSpec(cron_expressions=[trigger_obj.schedule_cron], time_zone_name=trigger_obj.schedule_timezone),
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
    )

    await client.create_schedule(schedule_id, schedule)

    def _save_schedule():
        trigger_obj.temporal_schedule_id = schedule_id
        trigger_obj.save(update_fields=['temporal_schedule_id'])

    await sync_to_async(_save_schedule)()
