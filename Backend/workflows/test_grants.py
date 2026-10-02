"""Tests for standing grants (v0.7 W-E, issue #159)."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from users.models import UserProfile
from workflows.grants import (
    apply_grant_decision,
    create_grant_from_approval,
    resolve_standing_grant,
    sweep_expired_grants,
)
from workflows.models import (
    StandingGrant,
    UserWorkflow,
    WorkflowApprovalRecord,
    WorkflowExecution,
    WorkflowTestRun,
)
from workflows.versioning import create_workflow_version

User = get_user_model()


def _definition(routine=None):
    definition = {
        "workflow_name": "Weekly summary",
        "workflow_description": "Send a weekly summary email.",
        "triggers": [{"trigger_type": "manual"}],
        "steps": [
            {
                "id": "email_step",
                "service": "gmail",
                "action": "send_email",
                "params": {"to": "example@example.com", "subject": "Hi", "text": "Hello"},
                "requires_approval": True,
            }
        ],
    }
    if routine is not None:
        definition["triggers"] = [{"trigger_type": "schedule", "cron": "0 9 * * 5"}]
        definition["routine"] = routine
    return definition


COMPLETE_ROUTINE = {
    "owner": "ops room",
    "inputs": ["to"],
    "output": "Email summary",
    "no_data_policy": "Refuse and report when the source is missing.",
    "partial_completion": "Report partial results in the ops room.",
    "idempotency": "execution",
}


class StandingGrantServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="grantor", email="example@example.com",
            password="fake-token",  # nosec B106 — test fixture — fake credential
        )
        self.workflow = UserWorkflow.objects.create(
            user=self.user,
            name="Weekly summary",
            description="Send a weekly summary email.",
            definition=_definition(),
            status="active",
        )
        self.execution = WorkflowExecution.objects.create(
            workflow=self.workflow,
            temporal_workflow_id="wf-grant-1",
            trigger_type="manual",
            trigger_data={},
            status="waiting",
        )
        self.approval = WorkflowApprovalRecord.objects.create(
            workflow=self.workflow,
            execution=self.execution,
            requested_by=self.user,
            kind="workflow",
            step_id="email_step",
            service="gmail",
            action="send_email",
            status="pending",
            metadata={"trigger_type": "manual"},
        )
        self.execution.pending_approval = self.approval
        self.execution.save(update_fields=["pending_approval"])

    def test_grant_created_from_approval_is_scoped(self):
        grant = create_grant_from_approval(self.approval, decision="always_allow")

        self.assertEqual(grant.status, "active")
        self.assertEqual(grant.workflow_version, self.workflow.definition_version)
        self.assertEqual(grant.trigger_scope, {"trigger_type": "manual"})
        self.assertEqual(grant.capability_scope, ["gmail:send_email"])
        self.assertEqual(grant.approval_record_id, self.approval.id)
        self.assertIsNotNone(grant.expires_at)

    def test_resolve_returns_matching_always_allow(self):
        grant = create_grant_from_approval(self.approval, decision="always_allow")
        decision = resolve_standing_grant(
            self.workflow.id, self.workflow.definition_version, "manual", None,
            "gmail:send_email", "send_email", self.user.id,
        )
        self.assertEqual(decision, {"grant_id": grant.id, "decision": "always_allow"})

    def test_resolve_returns_deny(self):
        grant = create_grant_from_approval(self.approval, decision="deny")
        decision = resolve_standing_grant(
            self.workflow.id, self.workflow.definition_version, "manual", None,
            "gmail:send_email", "send_email", self.user.id,
        )
        self.assertEqual(decision, {"grant_id": grant.id, "decision": "deny"})

    def test_ask_first_wins_over_allow(self):
        create_grant_from_approval(self.approval, decision="always_allow")
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.notification_preferences = {"approval_overrides": {"send_email": "always"}}
        profile.save(update_fields=["notification_preferences"])

        decision = resolve_standing_grant(
            self.workflow.id, self.workflow.definition_version, "manual", None,
            "gmail:send_email", "send_email", self.user.id,
        )
        self.assertIsNone(decision)

    def test_out_of_scope_attempt_lapses_grant(self):
        grant = create_grant_from_approval(self.approval, decision="always_allow")
        decision = resolve_standing_grant(
            self.workflow.id, self.workflow.definition_version, "manual", None,
            "weather:get_weather", "get_weather", self.user.id,
        )
        self.assertIsNone(decision)
        grant.refresh_from_db()
        self.assertEqual(grant.status, "lapsed")
        self.assertEqual(grant.lapse_reason, "out_of_scope_action")

    def test_different_preexisting_step_does_not_lapse_grant(self):
        definition = _definition()
        definition["steps"].append({
            "id": "weather_step",
            "service": "weather",
            "action": "get_weather",
            "params": {"city": "Nairobi"},
            "requires_approval": True,
        })
        workflow = UserWorkflow.objects.create(
            user=self.user,
            name="Multi-step",
            description="Multi-step workflow.",
            definition=definition,
            status="active",
        )
        approval = WorkflowApprovalRecord.objects.create(
            workflow=workflow,
            execution=self.execution,
            requested_by=self.user,
            kind="workflow",
            step_id="email_step",
            service="gmail",
            action="send_email",
            status="pending",
            metadata={"trigger_type": "manual"},
        )
        grant = create_grant_from_approval(approval, decision="always_allow")

        decision = resolve_standing_grant(
            workflow.id, workflow.definition_version, "manual", None,
            "weather:get_weather", "get_weather", self.user.id,
        )

        self.assertIsNone(decision)
        grant.refresh_from_db()
        self.assertEqual(grant.status, "active")

    def test_new_version_lapses_grants(self):
        grant = create_grant_from_approval(self.approval, decision="always_allow")
        new_definition = _definition()
        new_definition["steps"][0]["params"]["subject"] = "Changed"
        create_workflow_version(self.workflow, new_definition)

        grant.refresh_from_db()
        self.assertEqual(grant.status, "lapsed")
        self.assertEqual(grant.lapse_reason, "version_changed")

    def test_apply_allow_writes_receipt_and_consumes_allow_once(self):
        grant = create_grant_from_approval(self.approval, decision="allow_once")
        record_id = apply_grant_decision(
            execution_id=self.execution.id,
            workflow_id=self.workflow.id,
            user_id=self.user.id,
            step_id="email_step",
            service="gmail",
            action="send_email",
            params={"to": "example@example.com"},
            grant_id=grant.id,
            decision="allow_once",
        )
        record = WorkflowApprovalRecord.objects.get(id=record_id)
        self.assertEqual(record.status, "approved")
        self.assertEqual(record.metadata["standing_grant_id"], grant.id)
        self.assertTrue(record.metadata["auto"])

        grant.refresh_from_db()
        self.assertEqual(grant.status, "lapsed")
        self.assertEqual(grant.lapse_reason, "used_once")

    def test_apply_deny_writes_rejected_receipt(self):
        grant = create_grant_from_approval(self.approval, decision="deny")
        record_id = apply_grant_decision(
            execution_id=self.execution.id,
            workflow_id=self.workflow.id,
            user_id=self.user.id,
            step_id="email_step",
            service="gmail",
            action="send_email",
            params={},
            grant_id=grant.id,
            decision="deny",
        )
        record = WorkflowApprovalRecord.objects.get(id=record_id)
        self.assertEqual(record.status, "rejected")
        self.assertIn("standing rule", record.review_comment)

    def test_expiry_sweep_lapses_grant(self):
        grant = create_grant_from_approval(self.approval, decision="always_allow")
        grant.expires_at = timezone.now() - timedelta(days=1)
        grant.save(update_fields=["expires_at"])

        result = sweep_expired_grants()

        self.assertEqual(result["lapsed"], 1)
        grant.refresh_from_db()
        self.assertEqual(grant.status, "lapsed")
        self.assertEqual(grant.lapse_reason, "expired")

    def test_event_triggered_grant_requires_passing_test_run(self):
        routine_workflow = UserWorkflow.objects.create(
            user=self.user,
            name="Scheduled summary",
            description="Scheduled summary email.",
            definition=_definition(COMPLETE_ROUTINE),
            status="active",
        )
        approval = WorkflowApprovalRecord.objects.create(
            workflow=routine_workflow,
            execution=self.execution,
            requested_by=self.user,
            kind="workflow",
            step_id="email_step",
            service="gmail",
            action="send_email",
            status="pending",
            metadata={"trigger_type": "schedule"},
        )
        with self.assertRaises(ValueError):
            create_grant_from_approval(approval, decision="always_allow")

        WorkflowTestRun.objects.create(
            workflow=routine_workflow,
            definition_version=routine_workflow.definition_version,
            status="passed",
        )
        grant = create_grant_from_approval(approval, decision="always_allow")
        self.assertEqual(grant.status, "active")

    def test_resolve_step_grant_activity(self):
        from workflows import temporal_integration as ti

        grant = create_grant_from_approval(self.approval, decision="always_allow")
        decision = async_to_sync(ti.resolve_step_grant)(
            self.workflow.id,
            self.workflow.definition_version,
            "manual",
            None,
            "gmail:send_email",
            "send_email",
            self.user.id,
        )
        self.assertEqual(decision["grant_id"], grant.id)


class GrantUiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="grantui", email="example@example.com",
            password="fake-token",  # nosec B106 — test fixture — fake credential
        )
        self.workflow = UserWorkflow.objects.create(
            user=self.user,
            name="UI summary",
            description="UI summary email.",
            definition=_definition(),
            status="active",
        )
        self.execution = WorkflowExecution.objects.create(
            workflow=self.workflow,
            temporal_workflow_id="wf-grant-ui",
            trigger_type="manual",
            trigger_data={},
            status="waiting",
        )
        self.approval = WorkflowApprovalRecord.objects.create(
            workflow=self.workflow,
            execution=self.execution,
            requested_by=self.user,
            kind="workflow",
            step_id="email_step",
            service="gmail",
            action="send_email",
            status="pending",
            metadata={"trigger_type": "manual"},
        )
        self.execution.pending_approval = self.approval
        self.execution.save(update_fields=["pending_approval"])
        self.client.force_login(self.user)

    @patch("workflows.ui_views.submit_execution_approval", new=AsyncMock())
    def test_approve_with_save_rule_creates_grant(self):
        response = self.client.post(
            f"/workflows/executions/{self.execution.id}/approve/",
            {"save_rule": "on", "comment": ""},
        )
        self.assertEqual(response.status_code, 302)
        grant = StandingGrant.objects.get(workflow=self.workflow)
        self.assertEqual(grant.decision, "always_allow")

    def test_inbox_lists_active_grants(self):
        create_grant_from_approval(self.approval, decision="always_allow")
        response = self.client.get("/workflows/inbox/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Active standing grants")
        self.assertContains(response, "UI summary")
