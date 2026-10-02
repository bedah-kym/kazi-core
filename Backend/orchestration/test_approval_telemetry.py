"""Tests for approval telemetry per rule (v0.7, issue #166)."""
from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from orchestration.approval_telemetry import (
    approval_digest_lines,
    compute_approval_metrics,
    rollup_approval_telemetry,
    user_metrics,
    user_rule_candidates,
)
from workflows.models import WorkflowApprovalRecord

User = get_user_model()


class ApprovalMetricsUnitTests(SimpleTestCase):
    def test_counts_and_median_latency(self):
        now = timezone.now()
        records = [
            {"service": "gmail", "action": "send_email", "status": "approved", "risk_level": "high",
             "created_at": now - timedelta(minutes=10), "reviewed_at": now - timedelta(minutes=9)},
            {"service": "gmail", "action": "send_email", "status": "approved", "risk_level": "high",
             "created_at": now - timedelta(minutes=20), "reviewed_at": now - timedelta(minutes=14)},
            {"service": "gmail", "action": "send_email", "status": "approved", "risk_level": "high",
             "created_at": now - timedelta(minutes=30), "reviewed_at": now - timedelta(minutes=20)},
            {"service": "gmail", "action": "send_email", "status": "rejected", "risk_level": "high"},
            {"service": "gmail", "action": "send_email", "status": "timed_out", "risk_level": "high"},
        ]
        metrics = compute_approval_metrics(records)

        self.assertEqual(len(metrics), 1)
        metric = metrics[0]
        self.assertEqual(metric["approved"], 3)
        self.assertEqual(metric["rejected"], 1)
        self.assertEqual(metric["expired"], 1)
        self.assertEqual(metric["approval_rate"], 0.75)
        self.assertEqual(metric["median_latency_seconds"], 360.0)

    def test_metrics_contain_no_user_content(self):
        now = timezone.now()
        metrics = compute_approval_metrics([
            {"service": "gmail", "action": "send_email", "status": "approved",
             "created_at": now - timedelta(minutes=5), "reviewed_at": now,
             "user_content": "SECRET", "params": {"to": "secret@example.com"}},
        ])
        allowed = {
            "service", "action", "risk_level", "rule", "approved", "rejected",
            "expired", "total", "approval_rate", "median_latency_seconds",
        }
        for metric in metrics:
            self.assertEqual(set(metric.keys()), allowed)

    def test_rule_key_groups_grants(self):
        now = timezone.now()
        metrics = compute_approval_metrics([
            {"service": "gmail", "action": "send_email", "status": "approved", "rule": "grant:7",
             "created_at": now - timedelta(minutes=5), "reviewed_at": now},
            {"service": "gmail", "action": "send_email", "status": "approved", "rule": "none",
             "created_at": now - timedelta(minutes=5), "reviewed_at": now},
        ])
        rules = {metric["rule"] for metric in metrics}
        self.assertEqual(rules, {"grant:7", "none"})


class ApprovalTelemetryRollupTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="metrics-owner", email="example@example.com",
            password="fake-token",  # nosec B106 — test fixture — fake credential
        )

    def _create_approvals(self, count, status="approved", metadata=None):
        for index in range(count):
            WorkflowApprovalRecord.objects.create(
                requested_by=self.user,
                kind="workflow",
                step_id=f"step_{index}",
                service="gmail",
                action="send_email",
                status=status,
                metadata=metadata or {},
                reviewed_at=timezone.now(),
            )

    def test_rollup_caches_metrics_and_candidates(self):
        self._create_approvals(55)
        result = rollup_approval_telemetry()

        self.assertEqual(result["users"], 1)
        self.assertGreaterEqual(result["candidates"], 1)
        self.assertTrue(user_metrics(self.user.id))
        candidates = user_rule_candidates(self.user.id)
        self.assertTrue(any(c["action"] == "send_email" for c in candidates))

    def test_below_threshold_no_candidate(self):
        self._create_approvals(10)
        rollup_approval_telemetry()

        self.assertEqual(user_rule_candidates(self.user.id), [])
        self.assertTrue(user_metrics(self.user.id))

    def test_digest_lines_surface_candidates(self):
        self._create_approvals(60)
        rollup_approval_telemetry()

        lines = approval_digest_lines(self.user.id)

        self.assertTrue(any("standing rule" in line for line in lines))
        self.assertTrue(any("send_email" in line for line in lines))
