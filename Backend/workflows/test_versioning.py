"""Tests for versioned workflow definitions (v0.7 W-A, issue #155)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from workflows.models import UserWorkflow, WorkflowExecution, WorkflowTrigger, WorkflowVersion
from workflows.versioning import (
    create_workflow_version,
    definition_for_version,
    diff_definition_versions,
    record_initial_version,
)

User = get_user_model()


def _definition(name="Backup check", step_action="send_email"):
    return {
        "workflow_name": name,
        "workflow_description": "Demo definition",
        "triggers": [{"trigger_type": "manual"}],
        "steps": [
            {
                "id": "step_1",
                "service": "gmail",
                "action": step_action,
                "params": {"to": "example@example.com", "subject": "Hi", "text": "Hello"},
            }
        ],
    }


class WorkflowVersionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="versioner", email="example@example.com", password="pw"
        )
        self.workflow = UserWorkflow.objects.create(
            user=self.user,
            name="Backup check",
            description="Demo definition",
            definition=_definition(),
            status="active",
        )

    def test_creation_records_initial_version(self):
        version = WorkflowVersion.objects.get(workflow=self.workflow)
        self.assertEqual(version.version, 1)
        self.assertEqual(version.definition, self.workflow.definition)
        self.assertEqual(self.workflow.definition_version, 1)

    def test_record_initial_version_is_idempotent(self):
        record_initial_version(self.workflow)
        record_initial_version(self.workflow)
        self.assertEqual(WorkflowVersion.objects.filter(workflow=self.workflow).count(), 1)

    def test_new_version_snapshots_and_bumps(self):
        new_definition = _definition(name="Backup check v2")
        new_definition["steps"].append(
            {"id": "step_2", "service": "weather", "action": "get_weather", "params": {"city": "Nairobi"}}
        )

        version = create_workflow_version(
            self.workflow, new_definition, created_by=self.user, change_summary="add weather step"
        )

        self.workflow.refresh_from_db()
        self.assertEqual(version.version, 2)
        self.assertEqual(self.workflow.definition_version, 2)
        self.assertEqual(self.workflow.definition, new_definition)
        self.assertEqual(self.workflow.name, "Backup check v2")
        self.assertEqual(WorkflowVersion.objects.filter(workflow=self.workflow).count(), 2)

        v1 = WorkflowVersion.objects.get(workflow=self.workflow, version=1)
        self.assertEqual(v1.definition["workflow_name"], "Backup check")
        self.assertEqual(len(v1.definition["steps"]), 1)

    def test_execution_keeps_its_bound_definition_after_new_version(self):
        execution = WorkflowExecution.objects.create(
            workflow=self.workflow,
            temporal_workflow_id="wf-version-1",
            trigger_type="manual",
            trigger_data={},
            definition_version=self.workflow.definition_version,
            status="running",
        )

        new_definition = _definition(name="Renamed later")
        create_workflow_version(self.workflow, new_definition)

        execution.refresh_from_db()
        self.assertEqual(execution.definition_version, 1)
        bound = definition_for_version(self.workflow, execution.definition_version)
        self.assertEqual(bound["workflow_name"], "Backup check")

    def test_diff_between_versions(self):
        new_definition = _definition(name="Backup check renamed")
        create_workflow_version(self.workflow, new_definition)

        lines = diff_definition_versions(self.workflow, 1, 2)
        self.assertIsNotNone(lines)
        joined = "\n".join(lines)
        self.assertIn("v1", joined)
        self.assertIn("v2", joined)
        self.assertIn("Backup check renamed", joined)

    def test_diff_unknown_version_returns_none(self):
        self.assertIsNone(diff_definition_versions(self.workflow, 1, 9))

    def test_start_execution_binds_current_version(self):
        from workflows import temporal_integration as ti

        create_workflow_version(self.workflow, _definition(name="Second version"))

        client = MagicMock()
        client.start_workflow = AsyncMock(return_value=MagicMock(id="wf-2", run_id="run-2"))
        with patch.object(ti, "get_temporal_client", new=AsyncMock(return_value=client)):
            execution = async_to_sync(ti.start_workflow_execution)(
                self.workflow, {}, "manual"
            )

        self.assertEqual(execution.definition_version, 2)
        args = client.start_workflow.await_args.kwargs["args"]
        self.assertEqual(args[1]["workflow_name"], "Second version")
        self.assertEqual(args[6], 2)

    def test_refresh_schedule_definition_updates_action(self):
        from workflows import temporal_integration as ti

        trigger = WorkflowTrigger.objects.create(
            workflow=self.workflow,
            trigger_type="schedule",
            schedule_cron="0 9 * * 5",
            temporal_schedule_id="workflow-1-trigger-1",
        )
        client = MagicMock()
        handle = MagicMock()
        handle.update = AsyncMock()
        client.get_schedule_handle = MagicMock(return_value=handle)

        with patch.object(ti, "get_temporal_client", new=AsyncMock(return_value=client)):
            async_to_sync(ti.refresh_schedule_definition)(trigger)

        handle.update.assert_awaited_once()
        client.get_schedule_handle.assert_called_once_with("workflow-1-trigger-1")

    def test_version_history_view_renders(self):
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("workflows:workflow_versions", args=[self.workflow.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Definition versions")

    def test_version_history_view_shows_diff(self):
        create_workflow_version(self.workflow, _definition(name="Renamed"))
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("workflows:workflow_versions", args=[self.workflow.id]),
            {"from": 1, "to": 2},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Renamed")
