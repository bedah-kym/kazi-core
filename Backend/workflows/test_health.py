"""Tests for workflow health watches and auto-pause (v0.7 W-C, issue #157)."""
from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workflows.health import (
    build_health_digest,
    check_workflow_health,
    compute_workflow_health,
)
from workflows.models import UserWorkflow, WorkflowApprovalRecord, WorkflowExecution, WorkflowTrigger

User = get_user_model()


class WorkflowHealthTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="healthowner", email="example@example.com",
            password="fake-token",  # nosec B106 — test fixture — fake credential
        )
        self.workflow = UserWorkflow.objects.create(
            user=self.user,
            name="Health watch",
            description="Health watch workflow.",
            definition={
                "workflow_name": "Health watch",
                "workflow_description": "Health watch workflow.",
                "triggers": [{"trigger_type": "manual"}],
                "steps": [],
            },
            status="active",
        )

    def _execution(self, status, started_ago_minutes=60, completed_ago_minutes=None, temporal_id=None):
        started = timezone.now() - timedelta(minutes=started_ago_minutes)
        completed = None
        if completed_ago_minutes is not None:
            completed = timezone.now() - timedelta(minutes=completed_ago_minutes)
        return WorkflowExecution.objects.create(
            workflow=self.workflow,
            temporal_workflow_id=temporal_id or f"wf-{status}-{started_ago_minutes}-{completed_ago_minutes}",
            trigger_type="manual",
            trigger_data={},
            status=status,
            started_at=started,
            completed_at=completed,
            failure_summary="Failing step broke." if status in {"failed", "cancelled"} else "",
        )

    def test_health_counts_failures_and_spike(self):
        self._execution("failed", 10)
        self._execution("failed", 20)
        self._execution("failed", 30)
        self._execution("completed", 40, 10)
        self._execution("completed", 50, 20)

        health = compute_workflow_health(self.workflow)

        self.assertEqual(health["runs"], 5)
        self.assertEqual(health["failed"], 3)
        self.assertEqual(health["failure_rate"], 0.6)
        self.assertTrue(health["spike"])
        self.assertIsNotNone(health["mean_duration_seconds"])

    def test_spike_pauses_workflow_but_never_mutates_definition(self):
        definition_before = dict(self.workflow.definition)
        for minute in (5, 10, 15):
            self._execution("failed", minute)

        result = check_workflow_health()

        self.assertEqual(result["paused"], 1)
        self.workflow.refresh_from_db()
        self.assertEqual(self.workflow.status, "paused")
        self.assertEqual(self.workflow.definition, definition_before)

    def test_pause_deactivates_schedule_triggers(self):
        trigger = WorkflowTrigger.objects.create(
            workflow=self.workflow,
            trigger_type="schedule",
            schedule_cron="0 9 * * 5",
        )
        for minute in (5, 10, 15):
            self._execution("failed", minute)

        check_workflow_health()

        trigger.refresh_from_db()
        self.assertFalse(trigger.is_active)
        self.assertEqual(trigger.schedule_status, "paused")

    def test_recovery_requires_manual_reactivation(self):
        for minute in (5, 10, 15):
            self._execution("failed", minute)
        check_workflow_health()
        self.workflow.refresh_from_db()
        self.assertEqual(self.workflow.status, "paused")

        check_workflow_health()
        self.workflow.refresh_from_db()
        self.assertEqual(self.workflow.status, "paused")

    def test_reactivation_restarts_the_health_window(self):
        from workflows.health import reactivate_workflow

        for minute in (5, 10, 15):
            self._execution("failed", minute)
        check_workflow_health()
        self.workflow.refresh_from_db()
        self.assertEqual(self.workflow.status, "paused")

        reactivate_workflow(self.workflow)
        result = check_workflow_health()

        self.assertEqual(result["paused"], 0)
        self.workflow.refresh_from_db()
        self.assertEqual(self.workflow.status, "active")
        self.assertIsNotNone(self.workflow.reactivated_at)

    def test_failure_spike_lapses_standing_grants(self):
        from workflows.grants import create_grant_from_approval, resolve_standing_grant
        from workflows.models import WorkflowApprovalRecord
        from workflows.versioning import create_workflow_version

        create_workflow_version(self.workflow, {
            "workflow_name": "Health watch",
            "workflow_description": "Health watch workflow.",
            "triggers": [{"trigger_type": "manual"}],
            "steps": [{
                "id": "step_1",
                "service": "gmail",
                "action": "send_email",
                "params": {"to": "example@example.com", "subject": "Hi", "text": "Hello"},
                "requires_approval": True,
            }],
        })
        self.workflow.refresh_from_db()
        approval = WorkflowApprovalRecord.objects.create(
            workflow=self.workflow,
            execution=WorkflowExecution.objects.create(
                workflow=self.workflow,
                temporal_workflow_id="wf-grant-spike",
                trigger_type="manual",
                trigger_data={},
                status="waiting",
            ),
            requested_by=self.user,
            kind="workflow",
            step_id="step_1",
            service="gmail",
            action="send_email",
            status="pending",
            metadata={"trigger_type": "manual"},
        )
        grant = create_grant_from_approval(approval, decision="always_allow")
        self.assertEqual(grant.status, "active")

        for minute in (5, 10, 15):
            self._execution("failed", minute)
        check_workflow_health()

        grant.refresh_from_db()
        self.assertEqual(grant.status, "lapsed")
        self.assertEqual(grant.lapse_reason, "failure_spike")
        decision = resolve_standing_grant(
            self.workflow.id, self.workflow.definition_version, "manual", None,
            "gmail:send_email", "send_email", self.user.id,
        )
        self.assertIsNone(decision)

    def test_health_digest_lists_states(self):
        self._execution("failed", 5)
        self._execution("failed", 10)
        self._execution("failed", 15)
        degraded = UserWorkflow.objects.create(
            user=self.user,
            name="Degraded one",
            description="Degraded workflow.",
            definition={"workflow_name": "Degraded one", "workflow_description": "d", "triggers": [{"trigger_type": "manual"}], "steps": []},
            status="active",
        )
        for index in range(6):
            WorkflowExecution.objects.create(
                workflow=degraded,
                temporal_workflow_id=f"wf-degraded-{index}",
                trigger_type="manual",
                trigger_data={},
                status="failed" if index < 2 else "completed",
                failure_summary="x" if index < 2 else "",
            )
        UserWorkflow.objects.create(
            user=self.user,
            name="Paused one",
            description="Paused workflow.",
            definition={"workflow_name": "Paused one", "workflow_description": "d", "triggers": [{"trigger_type": "manual"}], "steps": []},
            status="paused",
        )

        digest = build_health_digest(self.user.id)

        joined = "\n".join(digest["lines"])
        self.assertIn("paused", joined)
        self.assertIn("Degraded one", joined)
        self.assertIn("Paused one", joined)

    def test_approval_rate_from_records(self):
        for index in range(4):
            WorkflowApprovalRecord.objects.create(
                workflow=self.workflow,
                requested_by=self.user,
                kind="workflow",
                step_id=f"step_{index}",
                service="gmail",
                action="send_email",
                status="approved" if index < 3 else "rejected",
            )
        health = compute_workflow_health(self.workflow)
        self.assertEqual(health["approval_rate"], 0.75)
