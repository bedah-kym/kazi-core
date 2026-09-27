from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, TransactionTestCase, override_settings

from orchestration import initiative
from orchestration.initiative import (
    build_digest,
    candidate_rules,
    clear_approved_rules,
    consume_proactive_budget,
    execute_approved_rule,
    is_allowed_by_rule,
    proactive_budget_remaining,
    promote_rule,
    propose_action,
)
from workflows.models import WorkflowApprovalRecord


def _run(coro):
    return asyncio.run(coro)


def _user(name):
    return get_user_model().objects.create_user(username=name, password="x")  # nosec B106 test fixture


class BudgetTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = _user("ini-budget")

    @override_settings(PROACTIVE_BUDGET_PER_DAY=2)
    def test_budget_exhausts_and_refuses(self):
        self.assertEqual(proactive_budget_remaining(self.user.id), 2)
        self.assertTrue(consume_proactive_budget(self.user.id))
        self.assertTrue(consume_proactive_budget(self.user.id))
        self.assertFalse(consume_proactive_budget(self.user.id))
        self.assertEqual(proactive_budget_remaining(self.user.id), 0)


class RuleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = _user("ini-rules")

    def test_promote_and_room_scope(self):
        promote_rule(self.user.id, {"action": "set_reminder", "room_id": 5})
        self.assertTrue(is_allowed_by_rule(self.user.id, "set_reminder", 5))
        self.assertFalse(is_allowed_by_rule(self.user.id, "set_reminder", 9))
        self.assertFalse(is_allowed_by_rule(self.user.id, "send_email", 5))

    def test_clear_rules(self):
        promote_rule(self.user.id, {"action": "set_reminder"})
        self.assertTrue(clear_approved_rules(self.user.id))
        self.assertFalse(is_allowed_by_rule(self.user.id, "set_reminder"))

    def test_candidates_from_learned_overrides(self):
        with patch(
            "orchestration.preference_mining.get_learned_overrides",
            return_value={"5": {"set_reminder": "auto"}},
        ):
            self.assertEqual(
                candidate_rules(self.user.id),
                [{"action": "set_reminder", "room_id": 5, "tier": "safe"}],
            )


class ExecuteRuleTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = _user("ini-exec")

    @override_settings(PROACTIVE_BUDGET_PER_DAY=2)
    def test_no_rule_no_action(self):
        with patch("orchestration.tool_executor.execute_tool", new=AsyncMock()) as execute:
            result = _run(execute_approved_rule(user_id=self.user.id, room_id=5, action="set_reminder"))
        self.assertEqual(result["status"], "error")
        self.assertFalse(execute.called)

    @override_settings(PROACTIVE_BUDGET_PER_DAY=2)
    def test_rule_dispatches_and_writes_receipt(self):
        promote_rule(self.user.id, {"action": "set_reminder", "room_id": 5})
        with patch(
            "orchestration.tool_executor.execute_tool",
            new=AsyncMock(return_value={"status": "success"}),
        ) as execute, patch(
            "orchestration.action_receipts.record_action_receipt", new=AsyncMock(),
        ) as receipt:
            result = _run(execute_approved_rule(
                user_id=self.user.id, room_id=5, action="set_reminder", params={"content": "x"},
            ))
        self.assertEqual(result["status"], "success")
        self.assertTrue(execute.called)
        self.assertTrue(receipt.called)

    @override_settings(PROACTIVE_BUDGET_PER_DAY=0)
    def test_budget_exhausted_refuses(self):
        promote_rule(self.user.id, {"action": "set_reminder", "room_id": 5})
        with patch("orchestration.tool_executor.execute_tool", new=AsyncMock()) as execute:
            result = _run(execute_approved_rule(user_id=self.user.id, room_id=5, action="set_reminder"))
        self.assertEqual(result["status"], "error")
        self.assertFalse(execute.called)

    @override_settings(PROACTIVE_BUDGET_PER_DAY=2)
    def test_receipt_failure_is_a_defined_error(self):
        promote_rule(self.user.id, {"action": "set_reminder", "room_id": 5})
        with patch(
            "orchestration.tool_executor.execute_tool",
            new=AsyncMock(return_value={"status": "success"}),
        ), patch(
            "orchestration.action_receipts.record_action_receipt",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ):
            result = _run(execute_approved_rule(user_id=self.user.id, room_id=5, action="set_reminder"))
        self.assertEqual(result["status"], "error")
        self.assertTrue(result.get("receipt_failed"))


class ProposeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = _user("ini-propose")

    def test_propose_reuses_durable_seam(self):
        with patch(
            "orchestration.agent_loop.save_pending_confirmation",
            new=AsyncMock(return_value=42),
        ) as save:
            record_id = _run(propose_action(
                user_id=self.user.id, room_id=5, action="set_reminder",
                params={"content": "x"}, effects=["create a reminder"],
            ))
        self.assertEqual(record_id, 42)
        self.assertTrue(save.called)

    def test_propose_rule_opens_durable_approval(self):
        with patch(
            "orchestration.agent_loop.save_pending_confirmation",
            new=AsyncMock(return_value=7),
        ) as save:
            record_id = _run(initiative.propose_rule(
                user_id=self.user.id, room_id=5, action="set_reminder", rule_text="run it",
            ))
        self.assertEqual(record_id, 7)
        self.assertTrue(save.called)

    def test_activate_rule_stores_after_approval(self):
        initiative.activate_rule(self.user.id, {"action": "set_reminder", "room_id": 5})
        self.assertTrue(is_allowed_by_rule(self.user.id, "set_reminder", 5))

    @override_settings(OPENAI_API_KEY="")
    def test_draft_rule_falls_back_without_llm(self):
        rule = _run(initiative.draft_rule({"action": "set_reminder", "room_id": 5}))
        self.assertEqual(rule["action"], "set_reminder")
        self.assertIn("set reminder", rule["text"])


class DigestTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = _user("ini-digest")

    def test_empty_when_nothing_to_report(self):
        with patch("orchestration.initiative.fired_watches", return_value=[]):
            digest = build_digest(self.user.id)
        self.assertTrue(digest["empty"])

    def test_includes_watches_and_proposals(self):
        WorkflowApprovalRecord.objects.create(
            requested_by=self.user, kind="agent_loop", room_id=5, step_id="s",
            action="send_email", approval_message="ok?", status="pending",
        )
        with patch("orchestration.initiative.fired_watches", return_value=[{"label": "disk growth"}]):
            digest = build_digest(self.user.id)
        self.assertFalse(digest["empty"])
        self.assertIn("disk growth", digest["message"])
        self.assertIn("send_email", digest["message"])

    def test_anomalies_are_listed(self):
        watch = {
            "metric": "action_error_rate",
            "labels": {"action": "send_email"},
            "trend": "rising",
            "current_value": 0.5,
        }
        with patch("orchestration.initiative.fired_watches", return_value=[watch]):
            digest = build_digest(self.user.id)
        self.assertIn("Anomalies:", digest["message"])
        self.assertEqual(len(digest["anomalies"]), 1)
