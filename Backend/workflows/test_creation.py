"""Tests for the one workflow creation service (issue #222)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TestCase

from workflows.models import UserWorkflow, WorkflowTrigger

User = get_user_model()


def _definition(**trigger_overrides):
    trigger = {
        "trigger_type": "schedule",
        "cron": "0 9 * * *",
        "timezone": "Africa/Nairobi",
    }
    trigger.update(trigger_overrides)
    return {
        "workflow_name": "Morning check",
        "workflow_description": "Check the weather",
        "triggers": [trigger],
        "steps": [
            {"id": "s1", "service": "weather", "action": "get_weather", "params": {}},
        ],
    }


class CreateWorkflowWithTriggersTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="wf-owner", email="example@example.com",
            password="fake-token",  # nosec B106 — test fixture — fake credential
        )

    @patch("workflows.creation.create_schedule_for_trigger", new_callable=AsyncMock)
    def test_schedule_creates_workflow_and_trigger(self, mock_schedule):
        from workflows.creation import create_workflow_with_triggers

        workflow, triggers = async_to_sync(create_workflow_with_triggers)(
            user_id=self.user.id, room_id=None, definition=_definition(),
        )

        self.assertEqual(UserWorkflow.objects.count(), 1)
        self.assertEqual(WorkflowTrigger.objects.count(), 1)
        trigger = triggers[0]
        self.assertEqual(trigger.workflow_id, workflow.id)
        self.assertEqual(trigger.schedule_cron, "0 9 * * *")
        self.assertEqual(trigger.schedule_timezone, "Africa/Nairobi")
        mock_schedule.assert_awaited_once()

    @patch("workflows.creation.create_schedule_for_trigger", new_callable=AsyncMock)
    def test_schedule_registration_failure_marks_workflow_failed(self, mock_schedule):
        from workflows.creation import (
            WorkflowCreationError,
            create_workflow_with_triggers,
        )

        mock_schedule.side_effect = RuntimeError("temporal down")

        with self.assertRaises(WorkflowCreationError):
            async_to_sync(create_workflow_with_triggers)(
                user_id=self.user.id, room_id=None, definition=_definition(),
            )

        workflow = UserWorkflow.objects.get()
        self.assertEqual(workflow.status, "failed")
        self.assertEqual(WorkflowTrigger.objects.count(), 1)

    def test_schedule_without_cron_writes_nothing(self):
        from workflows.creation import (
            WorkflowCreationError,
            create_workflow_with_triggers,
        )

        with self.assertRaises(WorkflowCreationError):
            async_to_sync(create_workflow_with_triggers)(
                user_id=self.user.id, room_id=None, definition=_definition(cron=""),
            )

        self.assertEqual(UserWorkflow.objects.count(), 0)
        self.assertEqual(WorkflowTrigger.objects.count(), 0)

    def test_malformed_cron_writes_nothing(self):
        from workflows.creation import (
            WorkflowCreationError,
            create_workflow_with_triggers,
        )

        with self.assertRaises(WorkflowCreationError):
            async_to_sync(create_workflow_with_triggers)(
                user_id=self.user.id, room_id=None,
                definition=_definition(cron="0 9 * *"),
            )

        self.assertEqual(UserWorkflow.objects.count(), 0)
        self.assertEqual(WorkflowTrigger.objects.count(), 0)

    def test_webhook_without_event_writes_nothing(self):
        from workflows.creation import (
            WorkflowCreationError,
            create_workflow_with_triggers,
        )

        with self.assertRaises(WorkflowCreationError):
            async_to_sync(create_workflow_with_triggers)(
                user_id=self.user.id, room_id=None,
                definition=_definition(trigger_type="webhook", service="calendly"),
            )

        self.assertEqual(UserWorkflow.objects.count(), 0)
        self.assertEqual(WorkflowTrigger.objects.count(), 0)

    def test_describe_schedule_matches_row(self):
        from workflows.creation import describe_schedule

        trigger = SimpleNamespace(
            schedule_cron="0 9 * * *", schedule_timezone="Africa/Nairobi",
        )
        self.assertEqual(
            describe_schedule(trigger),
            "cron `0 9 * * *`, timezone Africa/Nairobi",
        )
