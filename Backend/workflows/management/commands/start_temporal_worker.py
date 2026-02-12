import asyncio
from django.core.management.base import BaseCommand
from django.conf import settings
from temporalio.worker import Worker, UnsandboxedWorkflowRunner

from workflows.temporal_integration import (
    DynamicUserWorkflow,
    get_temporal_client,
    run_step_activity,
    create_execution_record,
    update_execution_record,
)


class Command(BaseCommand):
    help = 'Start Temporal worker for workflow execution'

    def handle(self, *args, **options):
        asyncio.run(self._run_worker())

    async def _run_worker(self):
        client = await get_temporal_client()
        worker = Worker(
            client,
            task_queue=settings.TEMPORAL_TASK_QUEUE,
            workflows=[DynamicUserWorkflow],
            activities=[run_step_activity, create_execution_record, update_execution_record],
            workflow_runner=UnsandboxedWorkflowRunner(),
        )
        await worker.run()
