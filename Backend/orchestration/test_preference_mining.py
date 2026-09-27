from __future__ import annotations

import unittest

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from orchestration.preference_mining import (
    clear_learned_overrides,
    effective_approval_overrides,
    get_learned_overrides,
    merge_learned_overrides,
    mine_approval_overrides,
    mine_overrides,
)
from workflows.models import WorkflowApprovalRecord


def _record(user_id, room_id, action, status):
    return {"user_id": user_id, "room_id": room_id, "action": action, "status": status}


class MineOverridesPureTests(unittest.TestCase):
    def test_threshold_met(self):
        records = [_record(1, 5, "send_email", "approved")] * 3
        self.assertEqual(mine_overrides(records, 3), {"1": {"5": {"send_email": "auto"}}})

    def test_below_threshold(self):
        records = [_record(1, 5, "send_email", "approved")] * 2
        self.assertEqual(mine_overrides(records, 3), {})

    def test_a_denial_resets_the_tally(self):
        records = [_record(1, 5, "send_email", "approved")] * 5 + [_record(1, 5, "send_email", "rejected")]
        self.assertEqual(mine_overrides(records, 3), {})

    def test_room_scoped(self):
        records = [_record(1, 5, "send_email", "approved")] * 3 + [_record(1, 6, "send_email", "approved")] * 3
        mined = mine_overrides(records, 3)
        self.assertEqual(set(mined["1"]), {"5", "6"})

    def test_pending_is_ignored(self):
        self.assertEqual(mine_overrides([_record(1, 5, "x", "pending")], 1), {})


class MineOverridesDbTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = get_user_model().objects.create_user(username="miner", password="x")  # nosec B106 test fixture

    def _row(self, action, status, room_id=5):
        return WorkflowApprovalRecord.objects.create(
            requested_by=self.user,
            kind="agent_loop",
            room_id=room_id,
            step_id=f"agent_loop:{room_id}:{self.user.id}",
            action=action,
            status=status,
        )

    @override_settings(PREFERENCE_MINING_ENABLED=True, PREFERENCE_MINING_MIN_APPROVALS=3)
    def test_mine_persists_and_explicit_override_wins(self):
        for _ in range(3):
            self._row("send_email", "approved")
        result = mine_approval_overrides()
        self.assertEqual(result["users"], 1)
        self.assertEqual(effective_approval_overrides(self.user.id, 5), {"send_email": "auto"})
        merged = merge_learned_overrides(
            {"approval_overrides": {"send_email": "always"}}, self.user.id, 5,
        )
        self.assertEqual(merged["approval_overrides"]["send_email"], "always")

    @override_settings(PREFERENCE_MINING_ENABLED=True, PREFERENCE_MINING_MIN_APPROVALS=3)
    def test_denial_blocks_auto(self):
        for _ in range(3):
            self._row("send_email", "approved")
        self._row("send_email", "rejected")
        mine_approval_overrides()
        self.assertEqual(effective_approval_overrides(self.user.id, 5), {})

    @override_settings(PREFERENCE_MINING_ENABLED=True, PREFERENCE_MINING_MIN_APPROVALS=3)
    def test_reversal_clears_overrides(self):
        for _ in range(3):
            self._row("send_email", "approved")
        mine_approval_overrides()
        self.assertTrue(clear_learned_overrides(self.user.id))
        cache.clear()
        self.assertEqual(get_learned_overrides(self.user.id), {})

    @override_settings(PREFERENCE_MINING_ENABLED=False)
    def test_disabled_is_a_noop(self):
        self.assertEqual(mine_approval_overrides().get("enabled"), False)
