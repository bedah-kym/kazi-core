"""Admin surface smoke tests for the v0.7 models."""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()

V07_CHANGELISTS = (
    "/admin/workflows/persona/",
    "/admin/workflows/handoff/",
    "/admin/workflows/standinggrant/",
    "/admin/workflows/workflowversion/",
    "/admin/workflows/workflowtestrun/",
    "/admin/workflows/routinecheckin/",
    "/admin/workflows/workflowcandidate/",
)


class V07AdminRegistrationTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username="admin-owner", email="example@example.com",
            password="fake-token",  # nosec B106 — test fixture — fake credential
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(self.staff)

    def test_v07_changelists_load(self):
        for path in V07_CHANGELISTS:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)

    def test_persona_activate_action(self):
        from orchestration.personas import create_persona

        persona = create_persona(self.staff, name="Admin bot", tool_scope=["get_weather"])
        self.assertEqual(persona.status, "draft")

        response = self.client.post(
            "/admin/workflows/persona/",
            {"action": "activate_personas", "_selected_action": [str(persona.id)]},
        )

        self.assertEqual(response.status_code, 302)
        persona.refresh_from_db()
        self.assertEqual(persona.status, "active")

    def test_persona_request_approval_creates_draft_persona(self):
        from workflows.models import Persona, PersonaRequest

        request_row = PersonaRequest.objects.create(
            user=self.staff, name="Requested bot", description="From chat",
        )
        response = self.client.post(
            "/admin/workflows/personarequest/",
            {"action": "approve_requests", "_selected_action": [str(request_row.id)]},
        )

        self.assertEqual(response.status_code, 302)
        request_row.refresh_from_db()
        self.assertEqual(request_row.status, "approved")
        self.assertTrue(
            Persona.objects.filter(
                user=self.staff, name="Requested bot", status="draft",
            ).exists()
        )

    def test_standing_grant_revoke_action(self):
        from workflows.grants import create_grant_from_approval
        from workflows.models import (
            UserWorkflow,
            WorkflowApprovalRecord,
            WorkflowExecution,
        )

        workflow = UserWorkflow.objects.create(
            user=self.staff,
            name="Admin workflow",
            description="Admin workflow.",
            definition={
                "workflow_name": "Admin workflow",
                "workflow_description": "Admin workflow.",
                "triggers": [{"trigger_type": "manual"}],
                "steps": [{
                    "id": "email_step",
                    "service": "gmail",
                    "action": "send_email",
                    "params": {"to": "example@example.com", "subject": "Hi", "text": "Hello"},
                    "requires_approval": True,
                }],
            },
            status="active",
        )
        execution = WorkflowExecution.objects.create(
            workflow=workflow,
            temporal_workflow_id="wf-admin-grant",
            trigger_type="manual",
            trigger_data={},
            status="waiting",
        )
        approval = WorkflowApprovalRecord.objects.create(
            workflow=workflow,
            execution=execution,
            requested_by=self.staff,
            kind="workflow",
            step_id="email_step",
            service="gmail",
            action="send_email",
            status="pending",
            metadata={"trigger_type": "manual"},
        )
        grant = create_grant_from_approval(approval, decision="always_allow")
        self.assertEqual(grant.status, "active")

        response = self.client.post(
            "/admin/workflows/standinggrant/",
            {"action": "revoke_grants", "_selected_action": [str(grant.id)]},
        )

        self.assertEqual(response.status_code, 302)
        grant.refresh_from_db()
        self.assertEqual(grant.status, "revoked")
