"""Tests for the kazi_trace management command (v0.4 M4-2)."""
from __future__ import annotations

import json
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase

from workflows.models import (
    UserWorkflow,
    WorkflowApprovalRecord,
    WorkflowExecution,
)


User = get_user_model()


def _make_workflow(user, definition=None):
    if definition is None:
        definition = {
            "workflow_name": "Demo",
            "workflow_description": "Two-step demo",
            "triggers": [{"trigger_type": "manual"}],
            "steps": [
                {
                    "id": "draft",
                    "service": "echo",
                    "action": "echo",
                    "params": {"message": "drafting"},
                    "safe_to_replay": True,
                },
                {
                    "id": "send",
                    "service": "echo",
                    "action": "echo",
                    "params": {"message": "sending"},
                    "requires_approval": True,
                    "safe_to_replay": False,
                    "depends_on": ["draft"],
                },
            ],
        }
    return UserWorkflow.objects.create(
        user=user,
        name="Trace Test",
        description="for tests",
        definition=definition,
    )


def _make_execution(workflow, **overrides):
    fields = {
        "workflow": workflow,
        "temporal_workflow_id": "test-tw-1",
        "trigger_type": "manual",
        "status": "running",
        "current_step": "draft",
    }
    fields.update(overrides)
    return WorkflowExecution.objects.create(**fields)


class KaziTraceCommandTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="trace-user", password="x")
        self.workflow = _make_workflow(self.user)

    def test_pretty_output_lists_steps_and_status(self):
        execution = _make_execution(
            self.workflow,
            status="completed",
            current_step="send",
            last_completed_step="send",
            result_summary="draft: ok\nsend: ok",
        )

        out = StringIO()
        call_command("kazi_trace", str(execution.id), stdout=out)
        rendered = out.getvalue()

        self.assertIn(f"Execution #{execution.id}", rendered)
        self.assertIn("status:         completed", rendered)
        self.assertIn("draft", rendered)
        self.assertIn("send", rendered)
        # Each step's status should appear once and the step status line should
        # mark the second step as needing approval.
        self.assertIn("[approval]", rendered)
        self.assertIn("[replay-safe]", rendered)
        # Result summary echoed back.
        self.assertIn("draft: ok", rendered)

    def test_waiting_status_surfaces_waiting_on_and_pending_approval(self):
        execution = _make_execution(
            self.workflow,
            status="waiting",
            current_step="send",
            last_completed_step="draft",
            waiting_on="approval",
        )
        approval = WorkflowApprovalRecord.objects.create(
            workflow=self.workflow,
            execution=execution,
            requested_by=self.user,
            step_id="send",
            action="echo",
            status="pending",
            approval_message="Sending follow-up",
        )
        execution.pending_approval = approval
        execution.save()

        out = StringIO()
        call_command("kazi_trace", str(execution.id), stdout=out)
        rendered = out.getvalue()

        self.assertIn("waiting on:     approval", rendered)
        # The Steps section should mark 'draft' as completed, 'send' as waiting.
        self.assertIn("completed", rendered)
        self.assertIn("waiting", rendered)
        # Approvals section appears with the right step.
        self.assertIn("Approvals:", rendered)
        self.assertIn("step='send'", rendered)
        self.assertIn("Sending follow-up", rendered)

    def test_json_mode_emits_parseable_document(self):
        execution = _make_execution(
            self.workflow,
            status="completed",
            current_step="send",
            last_completed_step="send",
        )

        out = StringIO()
        call_command("kazi_trace", str(execution.id), "--json", stdout=out)
        payload = json.loads(out.getvalue())

        self.assertEqual(payload["execution"]["id"], execution.id)
        self.assertEqual(payload["execution"]["status"], "completed")
        self.assertEqual(len(payload["steps"]), 2)
        step_ids = [s["id"] for s in payload["steps"]]
        self.assertEqual(step_ids, ["draft", "send"])
        self.assertTrue(payload["steps"][0]["safe_to_replay"])
        self.assertTrue(payload["steps"][1]["requires_approval"])

    def test_unknown_execution_raises_command_error(self):
        with self.assertRaises(CommandError):
            call_command("kazi_trace", "999999", stdout=StringIO())
