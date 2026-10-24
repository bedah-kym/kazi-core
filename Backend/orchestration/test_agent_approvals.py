"""Durable agent-loop approval tests (Phase 5).

Locks the security boundary: high-risk tools paused in the agent loop are
gated by a durable ``WorkflowApprovalRecord``, not just a Redis key. These
are attack-blocking tests — cross-user isolation, expiry, no
double-execution, and loop-state-loss safety.
"""
from datetime import timedelta
from unittest.mock import MagicMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from django.utils import timezone

from chatbot.models import Chatroom

from orchestration import turns as turns_mod
from orchestration.agent_loop import (
    AgentEvent,
    _args_hash,
    cancel_pending_action,
    dismiss_pending_confirmation,
    has_pending_agent_state,
    resume_after_confirmation,
    save_pending_confirmation,
)
from orchestration.models import Turn
from workflows.models import WorkflowApprovalRecord


class DurableApprovalTests(TransactionTestCase):
    def setUp(self):
        User = get_user_model()
        self.alice = User.objects.create_user(username="alice", password="pw")  # nosec B106 — test fixture — fake credential
        self.bob = User.objects.create_user(username="bob", password="pw")  # nosec B106 — test fixture — fake credential
        self.room = Chatroom.objects.create()
        self.room_id = self.room.id

    # -- helpers --------------------------------------------------------- #

    def _save_pending(self, room_id=None, user=None, action="send_email", turn_id=None):
        user = user or self.alice
        room_id = self.room_id if room_id is None else room_id
        return async_to_sync(save_pending_confirmation)(
            room_id,
            user.id,
            {"id": "t1", "name": action, "input": {"to": "a@b.c"}},
            "Send the email?",
            turn_id=turn_id,
        )

    def _paused_turn(self, user=None, call=None):
        user = user or self.alice
        call = call or {"id": "t1", "name": "send_email", "input": {"to": "a@b.c"}}
        turn = Turn.objects.create(
            room=self.room, user=user, state="running",
            version=0, started_at=timezone.now(), attempt=1,
        )
        payload = {
            "messages": [{"role": "assistant", "content": [
                {"type": "tool_use", "id": call["id"], "name": call["name"], "input": call["input"]},
            ]}],
            "pending_tools": [call],
            "pending_tiers": [None],
            "pending_tool": call,
            "pending_tier": None,
            "pending_args_hashes": {call["id"]: _args_hash(call["input"])},
            "paused_for_confirmation": True,
            "active_seconds": 0.0,
        }
        self.assertTrue(turns_mod.pause_turn(
            str(turn.pk), turn.version,
            state_payload=payload, pause_kind="approval",
        ))
        turn.refresh_from_db()
        return turn

    def _record(self, room_id=None, user=None, **filters):
        user = user or self.alice
        room_id = self.room_id if room_id is None else room_id
        return WorkflowApprovalRecord.objects.filter(
            kind="agent_loop", room_id=room_id, requested_by=user, **filters,
        ).order_by("-created_at").first()

    def _resume(self, room_id, user_id, turn_id=None):
        async def _run():
            events = []
            context = {"room_id": room_id, "user_id": user_id}
            if turn_id is not None:
                context["turn_id"] = str(turn_id)
            async for event in resume_after_confirmation(context=context):
                events.append(event)
            return events

        return async_to_sync(_run)()

    # -- tests ----------------------------------------------------------- #

    def test_save_pending_confirmation_writes_durable_record(self):
        approval_id = self._save_pending()

        record = WorkflowApprovalRecord.objects.get(id=approval_id)
        self.assertEqual(record.kind, "agent_loop")
        self.assertEqual(record.status, "pending")
        self.assertEqual(record.action, "send_email")
        self.assertEqual(record.room_id, self.room_id)
        self.assertEqual(record.requested_by_id, self.alice.id)
        self.assertIsNone(record.workflow_id)
        self.assertIsNone(record.execution_id)
        self.assertIsNotNone(record.expires_at)

    def test_cross_user_cannot_see_or_confirm_others_approval(self):
        turn = self._paused_turn()
        self._save_pending(user=self.alice, turn_id=turn.pk)

        self.assertTrue(async_to_sync(has_pending_agent_state)(self.room_id, self.alice.id))
        self.assertFalse(async_to_sync(has_pending_agent_state)(self.room_id, self.bob.id))

        # Bob cannot use Alice's Turn id as a resume handle either.
        events = self._resume(self.room_id, self.bob.id, turn_id=turn.pk)
        self.assertTrue(any(e.kind == "error" for e in events))

        # Alice's approval is untouched and still pending.
        self.assertIsNotNone(self._record(status="pending"))

    def test_expired_approval_is_not_pending(self):
        approval_id = self._save_pending()
        WorkflowApprovalRecord.objects.filter(id=approval_id).update(
            expires_at=timezone.now() - timedelta(seconds=1),
        )

        self.assertFalse(async_to_sync(has_pending_agent_state)(self.room_id, self.alice.id))

    def test_expired_approval_cannot_be_confirmed(self):
        turn = self._paused_turn()
        approval_id = self._save_pending(turn_id=turn.pk)
        WorkflowApprovalRecord.objects.filter(id=approval_id).update(
            expires_at=timezone.now() - timedelta(seconds=1),
        )

        with patch("orchestration.agent_loop.run_agent_loop") as mock_run:
            events = self._resume(self.room_id, self.alice.id, turn_id=turn.pk)

        self.assertTrue(any(e.kind == "error" for e in events))
        mock_run.assert_not_called()

    def test_lost_loop_state_marks_rejected_and_does_not_execute(self):
        turn = self._paused_turn()
        self._save_pending(turn_id=turn.pk)
        turns_mod.clear_paused_state(turn.pk)

        with patch("orchestration.agent_loop.run_agent_loop") as mock_run:
            events = self._resume(self.room_id, self.alice.id, turn_id=turn.pk)

        self.assertTrue(any(e.kind == "error" for e in events))
        mock_run.assert_not_called()

        record = self._record(status="rejected")
        self.assertIsNotNone(record)
        self.assertIn("lost", record.review_comment)
        self.assertFalse(async_to_sync(has_pending_agent_state)(self.room_id, self.alice.id))

    def test_resume_approves_durably_and_executes_once(self):
        turn = self._paused_turn()
        self._save_pending(turn_id=turn.pk)

        async def _fake_run(**kwargs):
            yield AgentEvent("done", {})

        with (
            patch("orchestration.agent_loop.clear_loop_state", new=MagicMock()),
            patch("orchestration.agent_loop.run_agent_loop", side_effect=_fake_run) as mock_run,
        ):
            events = self._resume(self.room_id, self.alice.id, turn_id=turn.pk)

        self.assertTrue(any(e.kind == "done" for e in events))
        mock_run.assert_called_once()

        # Approved durably, and no longer pending — a second confirm is a no-op.
        record = self._record(status="approved")
        self.assertIsNotNone(record)
        self.assertFalse(async_to_sync(has_pending_agent_state)(self.room_id, self.alice.id))

    def test_cancel_marks_cancelled_durably(self):
        self._save_pending()

        with patch("orchestration.agent_loop.clear_loop_state", new=MagicMock()):
            message = async_to_sync(cancel_pending_action)(self.room_id, self.alice.id)

        self.assertIsNotNone(message)
        self.assertIn("send email", message)
        self.assertIsNotNone(self._record(status="cancelled"))
        self.assertFalse(async_to_sync(has_pending_agent_state)(self.room_id, self.alice.id))

    def test_dismiss_marks_cancelled_durably(self):
        self._save_pending()

        with patch("orchestration.agent_loop.clear_loop_state", new=MagicMock()):
            async_to_sync(dismiss_pending_confirmation)(self.room_id, self.alice.id)

        self.assertIsNotNone(self._record(status="cancelled"))
        self.assertFalse(async_to_sync(has_pending_agent_state)(self.room_id, self.alice.id))

    def test_save_pending_confirmation_is_idempotent_per_room_user(self):
        first = self._save_pending()
        second = self._save_pending()

        self.assertEqual(first, second)
        self.assertEqual(
            WorkflowApprovalRecord.objects.filter(
                kind="agent_loop", room_id=self.room_id, requested_by=self.alice, status="pending",
            ).count(),
            1,
        )

    def test_pending_approval_unique_per_user_in_same_room(self):
        alice_id = self._save_pending(user=self.alice)
        bob_id = self._save_pending(user=self.bob)

        self.assertNotEqual(alice_id, bob_id)
        self.assertEqual(
            WorkflowApprovalRecord.objects.filter(
                kind="agent_loop", room_id=self.room_id, status="pending",
            ).count(),
            2,
        )

    def test_stale_expired_row_is_refreshed_on_new_pause(self):
        approval_id = self._save_pending()
        WorkflowApprovalRecord.objects.filter(id=approval_id).update(
            expires_at=timezone.now() - timedelta(seconds=1),
        )

        new_id = self._save_pending()

        self.assertEqual(new_id, approval_id)
        record = WorkflowApprovalRecord.objects.get(id=approval_id)
        self.assertGreater(record.expires_at, timezone.now())
        self.assertEqual(
            WorkflowApprovalRecord.objects.filter(
                kind="agent_loop", room_id=self.room_id, requested_by=self.alice, status="pending",
            ).count(),
            1,
        )
