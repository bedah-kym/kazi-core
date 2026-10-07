"""Phase 2/3 — unified orchestration: workflow handoff + lazy Celery load."""
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from workflows.models import UserWorkflow, WorkflowTrigger

User = get_user_model()


class WorkflowHandoffTests(SimpleTestCase):
    """_create_workflow_handoff routes through execute_adhoc_workflow with
    the validated definition shape (Phase 3)."""

    @patch(
        "orchestration.workflow_planner.execute_adhoc_workflow",
        new_callable=AsyncMock,
    )
    def test_handoff_manual_routes_through_adhoc_workflow(self, mock_exec):
        mock_exec.return_value = {
            "status": "completed",
            "message": "done",
            "workflow": MagicMock(id=5),
        }

        from orchestration.agent_loop import _create_workflow_handoff

        result = async_to_sync(_create_workflow_handoff)(
            {
                "description": "Do a thing",
                "steps": [
                    {
                        "id": "s1",
                        "service": "weather",
                        "action": "get_weather",
                        "params": {},
                    }
                ],
                "trigger_type": "manual",
            },
            {"user_id": 1, "room_id": 2},
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["workflow_id"], 5)
        definition = mock_exec.await_args[0][0]
        self.assertEqual(definition["workflow_name"], "Do a thing")
        self.assertIn("workflow_description", definition)
        self.assertEqual(definition["triggers"], [{"trigger_type": "manual"}])
        self.assertEqual(len(definition["steps"]), 1)

    def test_handoff_rejects_invalid_steps_json(self):
        from orchestration.agent_loop import _create_workflow_handoff

        result = async_to_sync(_create_workflow_handoff)(
            {"description": "Do a thing", "steps": "not-json", "trigger_type": "manual"},
            {"user_id": 1},
        )
        self.assertEqual(result["status"], "error")


class ScheduledHandoffTests(TestCase):
    """A scheduled handoff registers a real trigger and reports the stored row."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="handoff-owner", email="example@example.com",
            password="fake-token",  # nosec B106 — test fixture — fake credential
        )
        self.context = {"user_id": self.user.id, "room_id": None}

    def _tool_input(self, **overrides):
        payload = {
            "description": "Morning weather check",
            "steps": [
                {"id": "s1", "service": "weather", "action": "get_weather", "params": {}},
            ],
            "trigger_type": "schedule",
            "schedule": "0 9 * * *",
            "timezone": "Africa/Nairobi",
        }
        payload.update(overrides)
        return payload

    @patch("workflows.creation.create_schedule_for_trigger", new_callable=AsyncMock)
    def test_scheduled_handoff_registers_trigger_and_reports_it(self, mock_schedule):
        from orchestration.agent_loop import _create_workflow_handoff

        result = async_to_sync(_create_workflow_handoff)(self._tool_input(), self.context)

        self.assertEqual(result["status"], "success")
        self.assertEqual(UserWorkflow.objects.count(), 1)
        trigger = WorkflowTrigger.objects.get()
        self.assertEqual(trigger.schedule_cron, "0 9 * * *")
        self.assertEqual(trigger.schedule_timezone, "Africa/Nairobi")
        self.assertEqual(trigger.workflow_id, result["workflow_id"])
        mock_schedule.assert_awaited_once()
        self.assertIn("0 9 * * *", result["message"])
        self.assertIn("Africa/Nairobi", result["message"])

    @patch("workflows.creation.create_schedule_for_trigger", new_callable=AsyncMock)
    def test_schedule_registration_failure_is_an_error(self, mock_schedule):
        from orchestration.agent_loop import _create_workflow_handoff

        mock_schedule.side_effect = RuntimeError("temporal down")

        result = async_to_sync(_create_workflow_handoff)(self._tool_input(), self.context)

        self.assertEqual(result["status"], "error")
        workflow = UserWorkflow.objects.get()
        self.assertEqual(workflow.status, "failed")
        self.assertNotIn("created", result["message"].lower())
        self.assertNotIn("all set", result["message"].lower())

    def test_schedule_without_cron_writes_nothing(self):
        from orchestration.agent_loop import _create_workflow_handoff

        result = async_to_sync(_create_workflow_handoff)(
            self._tool_input(schedule=""), self.context,
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(UserWorkflow.objects.count(), 0)
        self.assertEqual(WorkflowTrigger.objects.count(), 0)

    @patch("workflows.creation.create_schedule_for_trigger", new_callable=AsyncMock)
    def test_malformed_cron_writes_nothing(self, mock_schedule):
        from orchestration.agent_loop import _create_workflow_handoff

        result = async_to_sync(_create_workflow_handoff)(
            self._tool_input(schedule="whenever"), self.context,
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(UserWorkflow.objects.count(), 0)
        self.assertEqual(WorkflowTrigger.objects.count(), 0)
        mock_schedule.assert_not_awaited()

    def test_webhook_handoff_is_refused(self):
        from orchestration.agent_loop import _create_workflow_handoff

        result = async_to_sync(_create_workflow_handoff)(
            self._tool_input(trigger_type="webhook"), self.context,
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(UserWorkflow.objects.count(), 0)
        self.assertEqual(WorkflowTrigger.objects.count(), 0)


class BackendCeleryLazyLoadTests(SimpleTestCase):
    """Backend/__init__.py lazy-loads celery_app (the manage.py check fix)."""

    def test_unknown_attribute_raises(self):
        import Backend

        with self.assertRaises(AttributeError):
            Backend.__getattr__("nonexistent_attribute_xyz")

    def test_celery_app_attribute_is_lazy_imported(self):
        from Backend import celery_app

        self.assertIsNotNone(celery_app)
