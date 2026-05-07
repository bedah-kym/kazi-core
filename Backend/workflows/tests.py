"""Workflow runtime regression tests for approvals, replay, and inbox APIs."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from orchestration.workflow_planner import execute_adhoc_workflow
from workflows.capabilities import validate_workflow_definition
from workflows.models import (
    DeferredWorkflowExecution,
    WorkflowApprovalRecord,
    WorkflowExecution,
    WorkflowImprovementSuggestion,
    WorkflowTrigger,
    UserWorkflow,
)
from workflows.tasks import replay_deferred_workflows


User = get_user_model()


class WorkflowDefinitionValidationTests(TestCase):
    def test_accepts_human_gated_step_fields(self):
        workflow_def = {
            "workflow_name": "Approval flow",
            "workflow_description": "Send a reviewed email",
            "triggers": [{"trigger_type": "manual"}],
            "steps": [
                {
                    "id": "email_step",
                    "service": "gmail",
                    "action": "send_email",
                    "params": {
                        "to": "ops@example.com",
                        "subject": "Status",
                        "text": "Hello",
                    },
                    "requires_approval": True,
                    "approval_message": "Approve the outbound email",
                    "approval_timeout_minutes": 30,
                    "on_timeout": "cancel",
                    "safe_to_replay": True,
                    "timeout_seconds": 180,
                    "max_attempts": 2,
                    "idempotency_key_source": "workflow.id",
                }
            ],
        }

        valid, error = validate_workflow_definition(workflow_def)
        self.assertTrue(valid)
        self.assertIsNone(error)

    def test_rejects_invalid_timeout_policy(self):
        workflow_def = {
            "workflow_name": "Bad timeout",
            "workflow_description": "Invalid timeout policy",
            "triggers": [{"trigger_type": "manual"}],
            "steps": [
                {
                    "id": "email_step",
                    "service": "gmail",
                    "action": "send_email",
                    "params": {
                        "to": "ops@example.com",
                        "subject": "Status",
                        "text": "Hello",
                    },
                    "on_timeout": "explode",
                }
            ],
        }

        valid, error = validate_workflow_definition(workflow_def)
        self.assertFalse(valid)
        self.assertIn("on_timeout", error)


class AdhocWorkflowFallbackTests(TestCase):
    @override_settings(TEMPORAL_DISABLED=True)
    @patch("orchestration.workflow_planner._create_adhoc_workflow", new_callable=AsyncMock)
    @patch("orchestration.workflow_planner._enqueue_deferred_execution", new_callable=AsyncMock)
    @patch("orchestration.workflow_planner._run_inline", new_callable=AsyncMock)
    @patch("orchestration.workflow_planner.cache")
    def test_high_risk_workflow_is_queued_when_temporal_disabled(
        self,
        mock_cache,
        mock_run_inline,
        mock_enqueue,
        mock_create_adhoc,
    ):
        mock_cache.add.return_value = True
        mock_enqueue.return_value = 55
        mock_create_adhoc.return_value = MagicMock(id=88)
        definition = {
            "workflow_name": "Withdraw money",
            "workflow_description": "High risk",
            "steps": [{"service": "payments", "action": "withdraw", "params": {"amount": 10}}],
        }

        result = async_to_sync(execute_adhoc_workflow)(definition, user_id=1, room_id=2)

        self.assertEqual(result["status"], "queued")
        self.assertEqual(result["mode"], "deferred")
        mock_run_inline.assert_not_called()

    @override_settings(TEMPORAL_DISABLED=True)
    @patch("orchestration.workflow_planner._create_adhoc_workflow", new_callable=AsyncMock)
    @patch("orchestration.workflow_planner._run_inline", new_callable=AsyncMock)
    @patch("orchestration.workflow_planner.cache")
    def test_low_risk_workflow_can_still_run_inline(
        self,
        mock_cache,
        mock_run_inline,
        mock_create_adhoc,
    ):
        mock_cache.add.return_value = True
        mock_create_adhoc.return_value = MagicMock(id=89)
        mock_run_inline.return_value = {"weather": {"status": "success"}}
        definition = {
            "workflow_name": "Check weather",
            "workflow_description": "Low risk",
            "steps": [{"service": "weather", "action": "get_weather", "params": {"city": "Nairobi"}}],
        }

        result = async_to_sync(execute_adhoc_workflow)(definition, user_id=1, room_id=2)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["mode"], "inline")
        mock_run_inline.assert_awaited_once()


class WorkflowApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="qa-user", email="qa@example.com", password="secret")
        self.client.force_authenticate(self.user)
        self.workflow = UserWorkflow.objects.create(
            user=self.user,
            name="Ops workflow",
            description="Review and send",
            definition={
                "workflow_name": "Ops workflow",
                "workflow_description": "Review and send",
                "triggers": [{"trigger_type": "manual"}],
                "steps": [
                    {
                        "id": "email_step",
                        "service": "gmail",
                        "action": "send_email",
                        "params": {"to": "ops@example.com", "subject": "Hi", "text": "Body"},
                    }
                ],
            },
        )

    def test_execution_detail_exposes_runtime_fields(self):
        execution = WorkflowExecution.objects.create(
            workflow=self.workflow,
            temporal_workflow_id="wf-1",
            trigger_type="manual",
            trigger_data={},
            status="waiting",
            current_step="email_step",
            waiting_on="approval",
            attempts={"email_step": 1},
            receipt_ids=[7],
        )
        approval = WorkflowApprovalRecord.objects.create(
            workflow=self.workflow,
            execution=execution,
            requested_by=self.user,
            step_id="email_step",
            service="gmail",
            action="send_email",
            approval_message="Approve the email",
            sanitized_params={"to": "ops@example.com"},
        )
        execution.pending_approval = approval
        execution.save(update_fields=["pending_approval"])

        with patch("workflows.views.fetch_execution_runtime_state", new=AsyncMock(return_value={
            "status": "waiting",
            "current_step": "email_step",
            "waiting_on": "approval",
            "attempts": {"email_step": 1},
            "receipt_ids": [7],
        })):
            response = self.client.get(f"/api/workflows/executions/{execution.id}/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()["execution"]
        self.assertEqual(payload["status"], "waiting")
        self.assertEqual(payload["current_step"], "email_step")
        self.assertEqual(payload["waiting_on"], "approval")
        self.assertEqual(payload["receipt_ids"], [7])
        self.assertEqual(payload["pending_approval"]["id"], approval.id)

    def test_approve_endpoint_signals_temporal_execution(self):
        execution = WorkflowExecution.objects.create(
            workflow=self.workflow,
            temporal_workflow_id="wf-2",
            temporal_run_id="run-2",
            trigger_type="manual",
            trigger_data={},
            status="waiting",
        )
        approval = WorkflowApprovalRecord.objects.create(
            workflow=self.workflow,
            execution=execution,
            requested_by=self.user,
            step_id="email_step",
            service="gmail",
            action="send_email",
            sanitized_params={"to": "ops@example.com"},
        )
        execution.pending_approval = approval
        execution.save(update_fields=["pending_approval"])

        with patch("workflows.views.submit_execution_approval", new=AsyncMock()) as mock_submit:
            response = self.client.post(
                f"/api/workflows/executions/{execution.id}/approve/",
                {"comment": "Looks good"},
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["decision"], "approved")
        mock_submit.assert_awaited_once()

    def test_rerun_endpoint_rejects_unsafe_replay(self):
        execution = WorkflowExecution.objects.create(
            workflow=self.workflow,
            temporal_workflow_id="wf-3",
            trigger_type="manual",
            trigger_data={},
            status="failed",
            current_step="email_step",
        )

        response = self.client.post(
            f"/api/workflows/executions/{execution.id}/rerun/",
            {"from_failed_step": True},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("not safe to replay", response.json()["error"])

    def test_operations_inbox_returns_approvals_failures_deferred_and_suggestions(self):
        execution = WorkflowExecution.objects.create(
            workflow=self.workflow,
            temporal_workflow_id="wf-4",
            trigger_type="manual",
            trigger_data={},
            status="waiting",
            current_step="email_step",
            waiting_on="approval",
        )
        approval = WorkflowApprovalRecord.objects.create(
            workflow=self.workflow,
            execution=execution,
            requested_by=self.user,
            step_id="email_step",
            service="gmail",
            action="send_email",
            sanitized_params={"to": "ops@example.com"},
        )
        execution.pending_approval = approval
        execution.save(update_fields=["pending_approval"])
        DeferredWorkflowExecution.objects.create(
            workflow=self.workflow,
            user=self.user,
            status="queued",
            trigger_data={"room_id": 1},
            recovery_hint="Wait for Temporal",
        )
        WorkflowImprovementSuggestion.objects.create(
            workflow=self.workflow,
            execution=execution,
            user=self.user,
            suggestion_type="approval_rule",
            title="Always ask before withdraw",
            summary="Require approval",
            proposed_changes={"step_id": "email_step", "requires_approval": True},
        )

        response = self.client.get("/api/workflows/inbox/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["pending_approvals"]), 1)
        self.assertEqual(len(payload["attention_executions"]), 1)
        self.assertEqual(len(payload["deferred_runs"]), 1)
        self.assertEqual(len(payload["suggestions"]), 1)

    def test_pause_and_resume_trigger_endpoints_delegate_to_temporal_helpers(self):
        trigger = WorkflowTrigger.objects.create(
            workflow=self.workflow,
            trigger_type="schedule",
            service="schedule",
            event="cron",
            schedule_cron="0 * * * *",
        )

        with patch("workflows.views.pause_trigger_schedule", new=AsyncMock()) as mock_pause:
            pause_response = self.client.post(f"/api/workflows/triggers/{trigger.id}/pause/")
        with patch("workflows.views.resume_trigger_schedule", new=AsyncMock()) as mock_resume:
            resume_response = self.client.post(f"/api/workflows/triggers/{trigger.id}/resume/")

        self.assertEqual(pause_response.status_code, 200)
        self.assertEqual(resume_response.status_code, 200)
        mock_pause.assert_awaited_once()
        mock_resume.assert_awaited_once()


class DeferredReplayTaskTests(TestCase):
    def test_replay_task_marks_dead_letter_and_recovery_hint(self):
        user = User.objects.create_user(username="replay-user", password="secret")
        workflow = UserWorkflow.objects.create(
            user=user,
            name="Replay me",
            description="Queued run",
            definition={"workflow_name": "Replay me", "workflow_description": "Queued run", "triggers": [], "steps": []},
        )
        deferred = DeferredWorkflowExecution.objects.create(
            workflow=workflow,
            user=user,
            status="queued",
            trigger_data={},
        )

        with patch("workflows.tasks.MAX_ATTEMPTS", 1), patch(
            "workflows.tasks.start_workflow_execution",
            new=AsyncMock(side_effect=Exception("Temporal unavailable")),
        ):
            result = replay_deferred_workflows(limit=1)

        deferred.refresh_from_db()
        self.assertEqual(result["failed"], 1)
        self.assertEqual(deferred.status, "abandoned")
        self.assertTrue(deferred.dead_letter_reason)
        self.assertTrue(deferred.recovery_hint)


class SeedDemoWorkflowCommandTests(TestCase):
    """v0.4 M5-1: the seed_demo_workflow management command loads
    examples/workflows/follow_up_email/workflow.json and persists it as a
    UserWorkflow row. Idempotent — re-running updates instead of duplicating.
    """

    def test_command_creates_user_workflow_from_example_json(self):
        from io import StringIO
        from django.core.management import call_command

        user = User.objects.create_user(username="demo-seed", password="secret")
        out = StringIO()

        call_command("seed_demo_workflow", "--user", "demo-seed", stdout=out)

        workflows = UserWorkflow.objects.filter(user=user)
        self.assertEqual(workflows.count(), 1)
        wf = workflows.first()
        self.assertEqual(wf.status, "active")
        steps = wf.definition.get("steps") or []
        self.assertEqual(len(steps), 2)
        step_ids = [s.get("id") for s in steps]
        self.assertEqual(step_ids, ["draft_follow_up", "send_follow_up"])
        # Second step is the human-gated one — it must declare requires_approval
        self.assertTrue(steps[1].get("requires_approval"))
        self.assertFalse(steps[1].get("safe_to_replay"))

    def test_command_is_idempotent(self):
        from io import StringIO
        from django.core.management import call_command

        user = User.objects.create_user(username="demo-seed-2", password="secret")
        call_command("seed_demo_workflow", "--user", "demo-seed-2", stdout=StringIO())
        call_command("seed_demo_workflow", "--user", "demo-seed-2", stdout=StringIO())

        self.assertEqual(UserWorkflow.objects.filter(user=user).count(), 1)

    def test_command_errors_when_user_missing(self):
        from io import StringIO
        from django.core.management import call_command, CommandError

        with self.assertRaises(CommandError):
            call_command("seed_demo_workflow", "--user", "no-such-user", stdout=StringIO())
