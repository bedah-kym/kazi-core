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


class ReplaySafetyRegressionTests(TestCase):
    """v0.4.1 regression pins for Bug #2A (is_step_safe_to_replay) and
    Bug #2B (rerun HTTP endpoint enforcement)."""

    def test_safe_to_replay_honors_explicit_false(self):
        """v0.4.1 Bug #2A — explicit False is respected, not silently dropped."""
        from workflows.runtime import is_step_safe_to_replay

        # get_weather is action-level safe (low-risk, no confirmation), but the
        # step explicitly opts out → must be False.
        step = {"id": "x", "action": "get_weather", "safe_to_replay": False}
        self.assertFalse(
            is_step_safe_to_replay(step),
            "v0.4.1 Bug #2A regressed: explicit safe_to_replay=False ignored",
        )

    def test_safe_to_replay_honors_explicit_true(self):
        """v0.4.1 Bug #2A — explicit True still works (back-compat)."""
        from workflows.runtime import is_step_safe_to_replay

        step = {"id": "x", "action": "send_email", "safe_to_replay": True}
        self.assertTrue(is_step_safe_to_replay(step))

    def test_safe_to_replay_falls_through_when_unset(self):
        """v0.4.1 Bug #2A — None still falls through to action-level fallback."""
        from workflows.runtime import is_step_safe_to_replay

        # send_email is high-risk per the catalog → action-level fallback says unsafe.
        unset = {"id": "x", "action": "send_email"}
        self.assertFalse(is_step_safe_to_replay(unset))
        # get_weather is low-risk → action-level fallback says safe.
        unset_safe = {"id": "y", "action": "get_weather"}
        self.assertTrue(is_step_safe_to_replay(unset_safe))


class RerunEndpointReplaySafetyTests(TestCase):
    """v0.4.1 Bug #2B — rerun HTTP view honors documented from_step + force."""

    def setUp(self):
        self.user = User.objects.create_user(username="rerun-user", password="x")
        self.workflow = UserWorkflow.objects.create(
            user=self.user,
            name="Two-step",
            description="safe + unsafe",
            definition={
                "workflow_name": "Two-step",
                "workflow_description": "safe + unsafe",
                "triggers": [{"trigger_type": "manual"}],
                "steps": [
                    {"id": "draft", "service": "echo", "action": "echo",
                     "params": {"message": "draft"}, "safe_to_replay": True},
                    {"id": "send", "service": "echo", "action": "echo",
                     "params": {"message": "send"}, "safe_to_replay": False,
                     "requires_approval": True},
                ],
            },
        )
        self.execution = WorkflowExecution.objects.create(
            workflow=self.workflow,
            temporal_workflow_id="rerun-test-1",
            trigger_type="manual",
            status="completed",
            current_step="send",
        )

    def _post_rerun(self, body):
        client = APIClient()
        client.force_authenticate(self.user)
        return client.post(
            f"/api/workflows/executions/{self.execution.id}/rerun/",
            body, format="json",
        )

    @patch("workflows.views.start_workflow_execution", new=AsyncMock(
        return_value=MagicMock(id=999, workflow_id=1)))
    def test_rerun_from_unsafe_step_refused_400(self):
        """Bug #2B: from_step pointing at unsafe step returns 400."""
        response = self._post_rerun({"from_step": "send"})
        self.assertEqual(
            response.status_code, 400,
            f"v0.4.1 Bug #2B regressed: rerun from unsafe step returned "
            f"{response.status_code}, body={response.content!r}",
        )
        self.assertIn("not safe to replay", response.content.decode().lower())

    @patch("workflows.views.start_workflow_execution", new=AsyncMock(
        return_value=MagicMock(id=999, workflow_id=1)))
    def test_rerun_from_unsafe_with_force_allowed(self):
        """Bug #2B: force=true bypasses the safety check."""
        response = self._post_rerun({"from_step": "send", "force": True})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body.get("mode"), "forced")
        self.assertTrue(body.get("forced"))
        self.assertEqual(body.get("from_step"), "send")

    @patch("workflows.views.start_workflow_execution", new=AsyncMock(
        return_value=MagicMock(id=999, workflow_id=1)))
    def test_rerun_from_safe_step_with_unsafe_tail_refused(self):
        """Bug #2B: rerun from a safe step still refuses if the slice from
        that step onwards includes an unsafe step. Per replay-safety contract,
        rerun replays the chosen step AND everything after; the safety check
        is over the whole slice, not just the entry point. Override with
        force=true (covered by test_rerun_from_unsafe_with_force_allowed).
        """
        response = self._post_rerun({"from_step": "draft"})
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("not safe to replay", response.content.decode().lower())

    @patch("workflows.views.start_workflow_execution", new=AsyncMock(
        return_value=MagicMock(id=999, workflow_id=1)))
    def test_rerun_from_step_with_safe_tail_allowed(self):
        """Bug #2B: rerun from a safe step IS allowed when the slice from
        that step onwards is entirely safe. Uses a separate all-safe workflow
        so the slice doesn't include any unsafe step."""
        all_safe_workflow = UserWorkflow.objects.create(
            user=self.user,
            name="All-safe",
            description="all safe steps",
            definition={
                "workflow_name": "All-safe",
                "workflow_description": "all safe steps",
                "triggers": [{"trigger_type": "manual"}],
                "steps": [
                    {"id": "lookup", "service": "echo", "action": "echo",
                     "params": {"message": "lookup"}, "safe_to_replay": True},
                    {"id": "report", "service": "echo", "action": "echo",
                     "params": {"message": "report"}, "safe_to_replay": True},
                ],
            },
        )
        execution = WorkflowExecution.objects.create(
            workflow=all_safe_workflow,
            temporal_workflow_id="rerun-test-2",
            trigger_type="manual",
            status="completed",
            current_step="report",
        )
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.post(
            f"/api/workflows/executions/{execution.id}/rerun/",
            {"from_step": "lookup"}, format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body.get("mode"), "from_step")
        self.assertEqual(body.get("from_step"), "lookup")
        self.assertFalse(body.get("forced"))

    @patch("workflows.views.start_workflow_execution", new=AsyncMock(
        return_value=MagicMock(id=999, workflow_id=1)))
    def test_legacy_from_failed_step_still_works(self):
        """Bug #2B: back-compat — old from_failed_step boolean still accepted."""
        response = self._post_rerun({"from_failed_step": True})
        # Will refuse because current_step is the unsafe "send"
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("not safe to replay", response.content.decode().lower())


class WorkflowExecutorRegistryFallbackTests(TestCase):
    """v0.4.1 Bug #1 — registry-only connectors execute via fallback."""

    @patch.dict("os.environ", {"KAZI_DEMO_MODE": "true"}, clear=False)
    def test_executor_dispatches_echo_from_registry(self):
        """Echo isn't in action_catalog (PR #47), but workflow executor
        should still dispatch it via the registry fallback (v0.4.1 Bug #1)."""
        import asyncio
        from orchestration.connector_registry import reset_registry, discover_connectors
        from workflows.activity_executors import execute_workflow_step

        reset_registry()
        discover_connectors()  # populate registry under demo mode

        step = {"id": "t", "service": "echo", "action": "echo",
                "params": {"message": "ping"}}
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                execute_workflow_step(step, {"user_id": 1, "room_id": None})
            )
        finally:
            loop.close()

        self.assertEqual(
            result.get("status"), "success",
            f"v0.4.1 Bug #1 regressed: {result}",
        )
        # The echo connector echoes the input back in `data.input`
        data = result.get("data") if isinstance(result.get("data"), dict) else result
        self.assertIn("ping", str(data))
