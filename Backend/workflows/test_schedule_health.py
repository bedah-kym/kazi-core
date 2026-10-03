"""Tests for the schedule silent-skip health watch (Workstream E)."""
from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workflows.health import check_schedule_health
from workflows.models import UserWorkflow, WorkflowTrigger

User = get_user_model()


class ScheduleHealthTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = User.objects.create_user(
            username="sched-owner", email="example@example.com",
            password="fake-token",  # nosec B106 — test fixture — fake credential
        )
        self.workflow = UserWorkflow.objects.create(
            user=self.user,
            name="Scheduled routine",
            description="Scheduled routine.",
            definition={
                "workflow_name": "Scheduled routine",
                "workflow_description": "Scheduled routine.",
                "triggers": [{"trigger_type": "schedule", "cron": "*/15 * * * *"}],
                "steps": [],
            },
            status="active",
        )
        self.trigger = WorkflowTrigger.objects.create(
            workflow=self.workflow,
            trigger_type="schedule",
            schedule_cron="*/15 * * * *",
            temporal_schedule_id="workflow-9-trigger-1",
            is_active=True,
        )

    @patch("workflows.temporal_integration.fetch_schedule_health")
    def test_first_run_snapshots_without_alert(self, mock_fetch):
        mock_fetch.return_value = {"num_actions": 10, "skipped_overlap": 0, "missed_catchup": 0}

        result = check_schedule_health()

        self.assertEqual(result, {"checked": 1, "skipped_alerts": 0})

    @patch("workflows.temporal_integration.fetch_schedule_health")
    def test_skip_growth_alerts(self, mock_fetch):
        from notifications.models import Notification

        mock_fetch.return_value = {"num_actions": 10, "skipped_overlap": 0, "missed_catchup": 0}
        check_schedule_health()

        mock_fetch.return_value = {"num_actions": 37, "skipped_overlap": 27, "missed_catchup": 0}
        result = check_schedule_health()

        self.assertEqual(result["skipped_alerts"], 1)
        self.assertTrue(
            Notification.objects.filter(
                user=self.user, event_type="workflow.health",
            ).exists()
        )

        result = check_schedule_health()
        self.assertEqual(result["skipped_alerts"], 0)
