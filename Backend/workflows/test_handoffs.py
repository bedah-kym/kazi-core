"""Tests for internal specialist handoffs (v0.7 W-G2, issue #137)."""
from __future__ import annotations

from unittest.mock import AsyncMock

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TestCase

from orchestration.personas import confirm_persona, create_persona
from workflows.handoffs import create_handoff, run_handoff

User = get_user_model()


class HandoffTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="handoff-owner", email="example@example.com",
            password="fake-token",  # nosec B106 — test fixture — fake credential
        )
        self.other = User.objects.create_user(
            username="handoff-other", email="other@example.com",
            password="fake-token",  # nosec B106 — test fixture — fake credential
        )
        self.persona = confirm_persona(create_persona(
            self.user, name="Research bot",
            tool_scope=["get_weather", "search_info"],
            risk_ceiling="medium",
        ))

    def test_unknown_persona_fails_closed(self):
        with self.assertRaises(ValueError):
            create_handoff(to_persona_name="Ghost bot", requested_by=self.user, task="look it up")

    def test_foreign_persona_fails_closed(self):
        other_persona = confirm_persona(create_persona(
            self.other, name="Private bot", tool_scope=["get_weather"],
        ))
        self.assertIsNotNone(other_persona)
        with self.assertRaises(ValueError):
            create_handoff(to_persona_name="Private bot", requested_by=self.user, task="look it up")

    def test_scope_cannot_exceed_persona_scope(self):
        with self.assertRaises(ValueError):
            create_handoff(
                to_persona_name="Research bot", requested_by=self.user,
                task="look it up", working_scope=["withdraw"],
            )

    def test_successful_handoff_returns_result_and_receipt(self):
        handoff = create_handoff(
            to_persona_name="Research bot", requested_by=self.user,
            task="Check the weather in Nairobi",
            working_scope=["get_weather"], room_id=7,
        )
        executor = AsyncMock(return_value={
            "status": "success",
            "summary": "It is sunny.",
            "tools_used": ["get_weather"],
            "tokens_used": 120,
        })
        completed = async_to_sync(run_handoff)(handoff.id, executor=executor)

        self.assertEqual(completed.status, "completed")
        self.assertEqual(completed.outcome, "completed")
        self.assertEqual(completed.result["summary"], "It is sunny.")
        self.assertEqual(completed.budget_used, 1)
        self.assertEqual(len(completed.receipts), 1)
        self.assertEqual(completed.receipts[0]["status"], "completed")

    def test_out_of_scope_tool_use_is_denied(self):
        handoff = create_handoff(
            to_persona_name="Research bot", requested_by=self.user,
            task="do things", working_scope=["get_weather"],
        )
        executor = AsyncMock(return_value={
            "status": "success",
            "summary": "I did more than asked.",
            "tools_used": ["get_weather", "withdraw"],
        })
        completed = async_to_sync(run_handoff)(handoff.id, executor=executor)

        self.assertEqual(completed.status, "failed")
        self.assertEqual(completed.outcome, "denied")
        self.assertEqual(completed.stopped_reason, "out_of_scope_tool_use")

    def test_budget_exhaustion_returns_partial_work(self):
        handoff = create_handoff(
            to_persona_name="Research bot", requested_by=self.user,
            task="big job", working_scope=["get_weather"], budget=2,
        )
        executor = AsyncMock(return_value={
            "status": "success",
            "summary": "Partial findings...",
            "tools_used": ["get_weather", "get_weather", "get_weather"],
        })
        completed = async_to_sync(run_handoff)(handoff.id, executor=executor)

        self.assertEqual(completed.status, "completed")
        self.assertEqual(completed.outcome, "partial")
        self.assertEqual(completed.stopped_reason, "budget_exhausted")
        self.assertIn("Partial findings", completed.result["summary"])

    def test_executor_crash_fails_with_reason(self):
        handoff = create_handoff(
            to_persona_name="Research bot", requested_by=self.user,
            task="boom", working_scope=["get_weather"],
        )
        executor = AsyncMock(side_effect=RuntimeError("sub-agent down"))
        completed = async_to_sync(run_handoff)(handoff.id, executor=executor)

        self.assertEqual(completed.status, "failed")
        self.assertEqual(completed.outcome, "failed")
        self.assertIn("sub-agent down", completed.result["error"])
        self.assertEqual(len(completed.receipts), 1)
