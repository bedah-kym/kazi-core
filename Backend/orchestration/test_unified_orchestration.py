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


class HandoffGateTests(SimpleTestCase):
    """A schedule handoff is persistence: it always pauses (#222 addendum)."""

    def _bucket(self, trigger_type, preferences=None, tainted=False):
        from orchestration.agent_loop import _bucket_tool_calls

        tool_call = {
            "name": "handoff_to_workflow",
            "input": {"trigger_type": trigger_type, "schedule": "0 9 * * *"},
        }
        auto, pause, denied = _bucket_tool_calls(
            [tool_call], preferences, tainted=tainted,
        )
        return auto, pause, denied, tool_call

    def test_scheduled_handoff_pauses(self):
        auto, pause, denied, _ = self._bucket("schedule")
        self.assertEqual(auto, [])
        self.assertEqual(denied, [])
        self.assertEqual(len(pause), 1)

    def test_tainted_scheduled_handoff_still_pauses(self):
        auto, pause, _denied, _tc = self._bucket("schedule", tainted=True)
        self.assertEqual(auto, [])
        self.assertEqual(len(pause), 1)

    def test_auto_override_does_not_skip_the_pause(self):
        auto, pause, _denied, _tc = self._bucket(
            "schedule",
            preferences={"approval_overrides": {"handoff_to_workflow": "auto"}},
        )
        self.assertEqual(auto, [])
        self.assertEqual(len(pause), 1)

    def test_manual_handoff_does_not_pause(self):
        auto, pause, _denied, _tc = self._bucket("manual")
        self.assertEqual(len(auto), 1)
        self.assertEqual(pause, [])

    def test_timezone_is_resolved_before_the_prompt(self):
        _auto, _pause, _denied, tool_call = self._bucket(
            "schedule", preferences={"timezone": "Africa/Nairobi"},
        )
        self.assertEqual(tool_call["input"]["timezone"], "Africa/Nairobi")

    def test_timezone_defaults_to_utc_without_preferences(self):
        _auto, _pause, _denied, tool_call = self._bucket("schedule")
        self.assertEqual(tool_call["input"]["timezone"], "UTC")


def _end_turn_response(text="ok"):
    return {
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {},
    }


class HandoffResumeTests(TestCase):
    """Confirming a paused handoff runs the creator; declining runs nothing."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="handoff-resume", email="example@example.com",
            password="fake-token",  # nosec B106 — test fixture — fake credential
        )
        self.context = {"user_id": self.user.id, "room_id": None}

    def _pending_handoff(self):
        return {
            "id": "t1",
            "name": "handoff_to_workflow",
            "input": {
                "description": "Morning weather check",
                "steps": [
                    {"id": "s1", "service": "weather", "action": "get_weather", "params": {}},
                ],
                "trigger_type": "schedule",
                "schedule": "0 9 * * *",
                "timezone": "Africa/Nairobi",
            },
        }

    @patch("workflows.creation.create_schedule_for_trigger", new_callable=AsyncMock)
    @patch("orchestration.agent_loop.get_llm_client")
    def test_confirming_a_scheduled_handoff_creates_the_rows(
        self, mock_get_llm, mock_schedule,
    ):
        from orchestration.agent_loop import LoopState, run_agent_loop

        mock_llm = MagicMock()
        mock_llm.create_message = AsyncMock(return_value=_end_turn_response())
        mock_get_llm.return_value = mock_llm

        state = LoopState(
            messages=[], pending_tool=self._pending_handoff(), pending_tier=None,
        )

        async def _collect():
            return [
                event async for event in run_agent_loop(
                    user_message="",
                    context=self.context,
                    resumed_state=state,
                    confirmed_tool=True,
                )
            ]

        async_to_sync(_collect)()

        self.assertEqual(UserWorkflow.objects.count(), 1)
        trigger = WorkflowTrigger.objects.get()
        self.assertEqual(trigger.schedule_cron, "0 9 * * *")
        mock_schedule.assert_awaited_once()

    def test_declining_a_scheduled_handoff_creates_nothing(self):
        from orchestration.agent_loop import (
            LoopState,
            cancel_pending_action,
            save_loop_state,
        )

        save_loop_state(
            7, self.user.id,
            LoopState(messages=[], pending_tool=self._pending_handoff()),
        )
        with patch("orchestration.agent_loop.clear_loop_state", new=MagicMock()):
            async_to_sync(cancel_pending_action)(7, self.user.id)

        self.assertEqual(UserWorkflow.objects.count(), 0)
        self.assertEqual(WorkflowTrigger.objects.count(), 0)

    @patch("orchestration.agent_loop._execute_meta_tool", new_callable=AsyncMock)
    @patch("orchestration.agent_loop.get_llm_client")
    def test_resume_hands_the_turn_taint_to_a_paused_meta_tool(
        self, mock_get_llm, mock_meta,
    ):
        from orchestration.agent_loop import LoopState, run_agent_loop

        mock_llm = MagicMock()
        mock_llm.create_message = AsyncMock(return_value=_end_turn_response())
        mock_get_llm.return_value = mock_llm
        mock_meta.return_value = {"status": "success", "summary": "done"}

        state = LoopState(
            messages=[],
            pending_tool={
                "id": "t1", "name": "delegate_task", "input": {"task": "summarise"},
            },
            pending_tier=None,
            tainted=True,
        )

        async def _collect():
            return [
                event async for event in run_agent_loop(
                    user_message="",
                    context=self.context,
                    resumed_state=state,
                    confirmed_tool=True,
                )
            ]

        async_to_sync(_collect)()

        mock_meta.assert_awaited_once()
        preferences_arg = mock_meta.await_args.args[3]
        self.assertTrue(preferences_arg.get("_shell_tainted"))


class BackendCeleryLazyLoadTests(SimpleTestCase):
    """Backend/__init__.py lazy-loads celery_app (the manage.py check fix)."""

    def test_unknown_attribute_raises(self):
        import Backend

        with self.assertRaises(AttributeError):
            Backend.__getattr__("nonexistent_attribute_xyz")

    def test_celery_app_attribute_is_lazy_imported(self):
        from Backend import celery_app

        self.assertIsNotNone(celery_app)
