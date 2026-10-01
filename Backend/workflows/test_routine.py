"""Tests for the v0.7 routine contract and pause-on-absence (W-F, issue #204)."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from workflows.models import (
    RoutineCheckIn,
    UserWorkflow,
    WorkflowDraft,
    WorkflowExecution,
    WorkflowTestRun,
    WorkflowTrigger,
)
from workflows.routine import (
    check_routine_absence,
    normalize_routine,
    routine_ready_for_grant,
    routine_run_history,
    validate_routine_contract,
)
from workflows.test_run import run_test_run
from workflows.versioning import create_workflow_version

User = get_user_model()


def _schedule_definition(routine=None):
    definition = {
        "workflow_name": "Friday backup",
        "workflow_description": "Check the backup every Friday.",
        "triggers": [{"trigger_type": "schedule", "cron": "0 9 * * 5"}],
        "steps": [
            {
                "id": "check",
                "service": "weather",
                "action": "get_weather",
                "params": {"city": "Nairobi"},
            }
        ],
    }
    if routine is not None:
        definition["routine"] = routine
    return definition


COMPLETE_ROUTINE = {
    "owner": "ops room",
    "inputs": ["backup host"],
    "output": "Email summary",
    "approval_boundary": ["send"],
    "no_data_policy": "Refuse and report if the backup log is missing or stale.",
    "partial_completion": "Report partial results in the ops room.",
    "idempotency": "execution",
}


class RoutineContractTests(TestCase):
    def test_manual_workflow_needs_no_routine(self):
        valid, error = validate_routine_contract({"triggers": [{"trigger_type": "manual"}]})
        self.assertTrue(valid, error)

    def test_schedule_without_routine_is_rejected(self):
        valid, error = validate_routine_contract(_schedule_definition())
        self.assertFalse(valid)
        self.assertIn("routine", error)

    def test_schedule_without_no_data_policy_is_rejected(self):
        routine = dict(COMPLETE_ROUTINE)
        routine.pop("no_data_policy")
        valid, error = validate_routine_contract(_schedule_definition(routine))
        self.assertFalse(valid)
        self.assertIn("no_data_policy", error)

    def test_complete_routine_passes(self):
        valid, error = validate_routine_contract(_schedule_definition(COMPLETE_ROUTINE))
        self.assertTrue(valid, error)

    def test_normalize_fills_approval_boundary_default(self):
        normalized = normalize_routine({"triggers": [{"trigger_type": "schedule"}]})
        self.assertIn("send", normalized["routine"]["approval_boundary"])


class EnableGateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="routine-owner", email="example@example.com", password="pw"
        )

    def test_confirming_a_draft_without_no_data_policy_is_refused(self):
        from workflows import workflow_agent

        routine = dict(COMPLETE_ROUTINE)
        routine.pop("no_data_policy")
        WorkflowDraft.objects.create(
            user=self.user,
            definition=_schedule_definition(routine),
            status="awaiting_confirmation",
        )
        response = async_to_sync(workflow_agent.handle_workflow_message)(
            self.user.id, None, "approve"
        )
        self.assertIn("can't be enabled", response)
        self.assertEqual(UserWorkflow.objects.count(), 0)


class RoutineTestRunTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testrun", email="example@example.com", password="pw"
        )
        definition = _schedule_definition(COMPLETE_ROUTINE)
        definition["steps"].append(
            {
                "id": "send",
                "service": "weather",
                "action": "get_weather",
                "params": {"city": "Mombasa"},
                "requires_approval": True,
            }
        )
        self.workflow = UserWorkflow.objects.create(
            user=self.user,
            name="Friday backup",
            description="Check backup",
            definition=definition,
            status="active",
        )

    @patch("orchestration.connector_registry.discover_connectors")
    def test_test_run_stubs_side_effects_and_passes(self, mock_discover):
        connector = MagicMock()
        connector.execute = AsyncMock(return_value={"status": "success"})
        mock_discover.return_value = {"get_weather": connector}

        run = async_to_sync(run_test_run)(self.workflow)

        self.assertEqual(run.status, "passed")
        self.assertEqual(run.approval_stop_point, "send")
        self.assertIn("check", run.output_preview)
        self.assertTrue(run.audit_trail)
        self.assertEqual(run.failure_states, [])
        connector.execute.assert_awaited_once()

    @patch("orchestration.connector_registry.discover_connectors")
    def test_test_run_records_failure_states(self, mock_discover):
        connector = MagicMock()
        connector.execute = AsyncMock(return_value={"status": "error", "error": "source missing"})
        mock_discover.return_value = {"get_weather": connector}

        run = async_to_sync(run_test_run)(self.workflow)

        self.assertEqual(run.status, "failed")
        self.assertEqual(run.failure_states[0]["step"], "check")
        self.assertIn("source missing", run.failure_states[0]["error"])

    def test_ready_for_grant_requires_passing_test_run(self):
        ready, reason = routine_ready_for_grant(self.workflow)
        self.assertFalse(ready)
        self.assertIn("test run", reason)

        WorkflowTestRun.objects.create(
            workflow=self.workflow,
            definition_version=self.workflow.definition_version,
            status="passed",
        )
        ready, reason = routine_ready_for_grant(self.workflow)
        self.assertTrue(ready, reason)

    def test_new_version_invalidates_the_test_run(self):
        WorkflowTestRun.objects.create(
            workflow=self.workflow,
            definition_version=self.workflow.definition_version,
            status="passed",
        )
        new_definition = _schedule_definition(COMPLETE_ROUTINE)
        new_definition["steps"][0]["params"]["city"] = "Kisumu"
        create_workflow_version(self.workflow, new_definition)

        ready, reason = routine_ready_for_grant(self.workflow)
        self.assertFalse(ready)
        self.assertIn("test run", reason)


class PauseOnAbsenceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="absent", email="example@example.com", password="pw"
        )
        self.workflow = UserWorkflow.objects.create(
            user=self.user,
            name="Friday backup",
            description="Check backup",
            definition=_schedule_definition(COMPLETE_ROUTINE),
            status="active",
        )
        self.trigger = WorkflowTrigger.objects.create(
            workflow=self.workflow,
            trigger_type="schedule",
            schedule_cron="0 9 * * 5",
        )
        from chatbot.models import Member

        Member.objects.create(
            User=self.user,
            last_seen=timezone.now() - timedelta(days=30),
        )

    @override_settings(ROUTINE_ABSENCE_IDLE_DAYS=14, ROUTINE_ABSENCE_PROMPT_WINDOW_DAYS=3)
    def test_absent_user_is_prompted_once(self):
        counts = check_routine_absence()
        self.assertEqual(counts["prompted"], 1)
        self.assertEqual(RoutineCheckIn.objects.filter(user=self.user, status="prompted").count(), 1)

        counts = check_routine_absence()
        self.assertEqual(counts["prompted"], 0)

    @override_settings(ROUTINE_ABSENCE_IDLE_DAYS=14, ROUTINE_ABSENCE_PROMPT_WINDOW_DAYS=3)
    def test_unanswered_prompt_pauses_routines(self):
        now = timezone.now()
        check_routine_absence(now=now)

        counts = check_routine_absence(now=now + timedelta(days=4))

        self.assertEqual(counts["paused"], 1)
        self.workflow.refresh_from_db()
        self.assertEqual(self.workflow.status, "paused")
        self.trigger.refresh_from_db()
        self.assertFalse(self.trigger.is_active)
        check_in = RoutineCheckIn.objects.get(user=self.user)
        self.assertEqual(check_in.status, "paused")
        self.assertEqual(check_in.paused_workflow_ids, [self.workflow.id])

    @override_settings(ROUTINE_ABSENCE_IDLE_DAYS=14, ROUTINE_ABSENCE_PROMPT_WINDOW_DAYS=3)
    def test_active_user_is_not_prompted(self):
        self.user.member_set.update(last_seen=timezone.now())
        counts = check_routine_absence()
        self.assertEqual(counts, {"prompted": 0, "paused": 0})


class RoutineHistoryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="history", email="example@example.com", password="pw"
        )
        self.workflow = UserWorkflow.objects.create(
            user=self.user,
            name="History",
            description="History check",
            definition=_schedule_definition(COMPLETE_ROUTINE),
            status="active",
        )

    def test_history_lists_success_and_failure(self):
        WorkflowExecution.objects.create(
            workflow=self.workflow, temporal_workflow_id="wf-ok",
            trigger_type="schedule", trigger_data={}, status="completed",
        )
        WorkflowExecution.objects.create(
            workflow=self.workflow, temporal_workflow_id="wf-bad",
            trigger_type="schedule", trigger_data={}, status="failed",
        )

        history = routine_run_history(self.workflow, limit=10)

        self.assertEqual(len(history), 2)
        statuses = {run["status"] for run in history}
        self.assertEqual(statuses, {"completed", "failed"})

    def test_history_respects_limit(self):
        for index in range(5):
            WorkflowExecution.objects.create(
                workflow=self.workflow, temporal_workflow_id=f"wf-{index}",
                trigger_type="schedule", trigger_data={}, status="completed",
            )
        self.assertEqual(len(routine_run_history(self.workflow, limit=3)), 3)
