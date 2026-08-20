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

from orchestration.agent_loop import (
    AgentEvent,
    LoopState,
    cancel_pending_action,
    dismiss_pending_confirmation,
    has_pending_agent_state,
    resume_after_confirmation,
    save_pending_confirmation,
)
from workflows.models import WorkflowApprovalRecord


class DurableApprovalTests(TransactionTestCase):
    def setUp(self):
        User = get_user_model()
        self.alice = User.objects.create_user(username="alice", password="pw")
        self.bob = User.objects.create_user(username="bob", password="pw")

    # -- helpers --------------------------------------------------------- #

    def _save_pending(self, room_id=1, user=None, action="send_email"):
        user = user or self.alice
        return async_to_sync(save_pending_confirmation)(
            room_id,
            user.id,
            {"id": "t1", "name": action, "input": {"to": "a@b.c"}},
            "Send the email?",
        )

    def _record(self, room_id=1, user=None, **filters):
        user = user or self.alice
        return WorkflowApprovalRecord.objects.filter(
            kind="agent_loop", room_id=room_id, requested_by=user, **filters,
        ).order_by("-created_at").first()

    def _resume(self, room_id, user_id):
        async def _run():
            events = []
            async for event in resume_after_confirmation(
                context={"room_id": room_id, "user_id": user_id},
            ):
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
        self.assertEqual(record.room_id, 1)
        self.assertEqual(record.requested_by_id, self.alice.id)
        self.assertIsNone(record.workflow_id)
        self.assertIsNone(record.execution_id)
        self.assertIsNotNone(record.expires_at)

    def test_cross_user_cannot_see_or_confirm_others_approval(self):
        self._save_pending(user=self.alice)

        self.assertTrue(async_to_sync(has_pending_agent_state)(1, self.alice.id))
        self.assertFalse(async_to_sync(has_pending_agent_state)(1, self.bob.id))

        # Bob's resume attempt finds nothing and must not execute anything.
        events = self._resume(1, self.bob.id)
        self.assertTrue(any(e.kind == "error" for e in events))

        # Alice's approval is untouched and still pending.
        self.assertIsNotNone(self._record(status="pending"))

    def test_expired_approval_is_not_pending(self):
        approval_id = self._save_pending()
        WorkflowApprovalRecord.objects.filter(id=approval_id).update(
            expires_at=timezone.now() - timedelta(seconds=1),
        )

        self.assertFalse(async_to_sync(has_pending_agent_state)(1, self.alice.id))

    def test_expired_approval_cannot_be_confirmed(self):
        approval_id = self._save_pending()
        WorkflowApprovalRecord.objects.filter(id=approval_id).update(
            expires_at=timezone.now() - timedelta(seconds=1),
        )

        with patch("orchestration.agent_loop.run_agent_loop") as mock_run:
            events = self._resume(1, self.alice.id)

        self.assertTrue(any(e.kind == "error" for e in events))
        mock_run.assert_not_called()

    def test_lost_loop_state_marks_rejected_and_does_not_execute(self):
        self._save_pending()  # DB record exists, Redis loop state does not

        with (
            patch("orchestration.agent_loop.load_loop_state", return_value=None),
            patch("orchestration.agent_loop.run_agent_loop") as mock_run,
        ):
            events = self._resume(1, self.alice.id)

        self.assertTrue(any(e.kind == "error" for e in events))
        mock_run.assert_not_called()

        record = self._record(status="rejected")
        self.assertIsNotNone(record)
        self.assertIn("lost", record.review_comment)
        self.assertFalse(async_to_sync(has_pending_agent_state)(1, self.alice.id))

    def test_resume_approves_durably_and_executes_once(self):
        self._save_pending()
        state = LoopState(
            messages=[],
            pending_tool={"id": "t1", "name": "send_email", "input": {"to": "a@b.c"}},
        )

        async def _fake_run(**kwargs):
            yield AgentEvent("done", {})

        with (
            patch("orchestration.agent_loop.load_loop_state", return_value=state),
            patch("orchestration.agent_loop.clear_loop_state", new=MagicMock()),
            patch("orchestration.agent_loop.run_agent_loop", side_effect=_fake_run) as mock_run,
        ):
            events = self._resume(1, self.alice.id)

        self.assertTrue(any(e.kind == "done" for e in events))
        mock_run.assert_called_once()

        # Approved durably, and no longer pending — a second confirm is a no-op.
        record = self._record(status="approved")
        self.assertIsNotNone(record)
        self.assertFalse(async_to_sync(has_pending_agent_state)(1, self.alice.id))

    def test_cancel_marks_cancelled_durably(self):
        self._save_pending()

        with patch("orchestration.agent_loop.clear_loop_state", new=MagicMock()):
            message = async_to_sync(cancel_pending_action)(1, self.alice.id)

        self.assertIsNotNone(message)
        self.assertIn("send email", message)
        self.assertIsNotNone(self._record(status="cancelled"))
        self.assertFalse(async_to_sync(has_pending_agent_state)(1, self.alice.id))

    def test_dismiss_marks_cancelled_durably(self):
        self._save_pending()

        with patch("orchestration.agent_loop.clear_loop_state", new=MagicMock()):
            async_to_sync(dismiss_pending_confirmation)(1, self.alice.id)

        self.assertIsNotNone(self._record(status="cancelled"))
        self.assertFalse(async_to_sync(has_pending_agent_state)(1, self.alice.id))
