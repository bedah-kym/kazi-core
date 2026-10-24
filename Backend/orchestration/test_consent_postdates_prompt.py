"""#216b-0: a reply is consent only after the prompt existed.

These are security-boundary tests. A "yes" that waited on the per-room lock,
or came from a second tab, must never approve an action whose prompt was
created after the reply was sent. A no/cancel still fails safe.
"""
from __future__ import annotations

from contextlib import ExitStack
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from django.utils import timezone

from chatbot.models import Chatroom, Member, Message

from orchestration import turns as turns_mod
from orchestration.agent_loop import AgentEvent, _args_hash, save_pending_confirmation
from orchestration.coordinator import OrchestrationCoordinator
from orchestration.models import Turn
from workflows.models import WorkflowApprovalRecord

User = get_user_model()


class ConsentPostdatesPromptTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="consent-user", password="fake-token",  # nosec B106
        )
        self.room = Chatroom.objects.create()
        self.member = Member.objects.create(User=self.user)
        self.room.participants.add(self.member)

    def _message(self, text, sent_at):
        message = Message.objects.create(
            member=self.member, content=text, timestamp=sent_at,
        )
        self.room.chats.add(message)
        return message

    def _paused_turn(self, call=None):
        call = call or {
            "id": "t1", "name": "send_email", "input": {"to": "a@b.c"},
        }
        turn = Turn.objects.create(
            room=self.room, user=self.user, state="running",
            version=0, started_at=timezone.now(), attempt=1,
        )
        payload = {
            "messages": [{"role": "assistant", "content": [
                {
                    "type": "tool_use", "id": call["id"],
                    "name": call["name"], "input": call["input"],
                },
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

    def _save_pending(self, turn, call=None):
        call = call or {
            "id": "t1", "name": "send_email", "input": {"to": "a@b.c"},
        }
        return async_to_sync(save_pending_confirmation)(
            self.room.id, self.user.id, call, "Send the email?",
            turn_id=turn.pk,
        )

    def _handle(self, query, message, *, resume=None, arm=None):
        captured = {}

        if resume is None:
            resume = MagicMock(side_effect=AssertionError("must not resume"))

        async def _new_turn(**kwargs):
            captured.update(kwargs)
            yield AgentEvent("text", {"text": "new turn"})
            yield AgentEvent("done", {"end_reason": "finished"})

        if arm is None:
            arm = MagicMock(return_value=True)

        patches = {
            "orchestration.coordinator.load_memory_summary": AsyncMock(return_value=None),
            "orchestration.coordinator.get_user_preferences": lambda *a, **k: {},
            "orchestration.coordinator.has_pending_budget_state": AsyncMock(return_value=False),
            "orchestration.coordinator.pending_resume_kind": AsyncMock(return_value=""),
            "orchestration.coordinator._quota_resume_still_blocked": AsyncMock(return_value=None),
            "orchestration.coordinator.dismiss_pending_confirmation": AsyncMock(),
            "orchestration.coordinator.cache": MagicMock(),
            "orchestration.coordinator.record_event": MagicMock(),
            "orchestration.turns.record_event": MagicMock(),
            "orchestration.coordinator.resume_after_confirmation": resume,
            "orchestration.coordinator.run_agent_loop": _new_turn,
            "orchestration.coordinator._conversation_tainted": MagicMock(return_value=False),
            "orchestration.shell.autopilot.arm": arm,
        }
        send_chunk = AsyncMock()

        async def _call():
            return await OrchestrationCoordinator().handle_message(
                query=query,
                user_id=self.user.id,
                room_id=self.room.id,
                username=self.user.username,
                message_id=message.id,
                send_chunk=send_chunk,
                send_step_event=AsyncMock(),
                get_context_prompt=AsyncMock(return_value=""),
                bump_signals=MagicMock(),
            )

        with ExitStack() as stack:
            for target, value in patches.items():
                stack.enter_context(patch(target, value))
            result = async_to_sync(_call)()
            async_to_sync(OrchestrationCoordinator().finalize_turn)(
                result, reply_saved=True,
            )

        return result, captured, send_chunk, resume, arm

    def _pending_record(self):
        return WorkflowApprovalRecord.objects.get(
            kind="agent_loop", room_id=self.room.id,
            requested_by=self.user, status="pending",
        )

    # -- must fail on main -------------------------------------------------- #

    def test_an_earlier_yes_does_not_approve_a_new_prompt(self):
        message = self._message("yes", timezone.now() - timedelta(seconds=10))
        turn = self._paused_turn()
        self._save_pending(turn)

        result, captured, _chunks, resume, _arm = self._handle("yes", message)

        resume.assert_not_called()
        self.assertEqual(self._pending_record().status, "pending")
        turn.refresh_from_db()
        self.assertEqual(turn.state, "paused")
        self.assertIn("waiting for your approval", captured.get("pending_note", ""))
        self.assertIn("Should I go ahead?", result.full_response)
        self.assertEqual(Turn.objects.filter(state="completed").count(), 1)

    def test_an_earlier_yes_does_not_approve_a_reused_row(self):
        old_created = timezone.now() - timedelta(days=1)
        old_id = async_to_sync(save_pending_confirmation)(
            self.room.id, self.user.id,
            {"id": "old", "name": "send_email", "input": {"to": "old@b.c"}},
            "Send the old email?",
            turn_id=None,
        )
        WorkflowApprovalRecord.objects.filter(id=old_id).update(
            created_at=old_created,
            expires_at=old_created + timedelta(seconds=1),
        )
        message = self._message("yes", old_created + timedelta(seconds=3))

        turn = self._paused_turn()
        self._save_pending(turn)
        record = self._pending_record()
        self.assertGreater(record.created_at, message.timestamp)

        result, captured, _chunks, resume, _arm = self._handle("yes", message)

        resume.assert_not_called()
        self.assertEqual(self._pending_record().status, "pending")
        turn.refresh_from_db()
        self.assertEqual(turn.state, "paused")
        self.assertIn("waiting for your approval", captured.get("pending_note", ""))
        self.assertIn("Should I go ahead?", result.full_response)

    def test_an_earlier_autopilot_reply_neither_arms_nor_confirms(self):
        shell_call = {
            "id": "t1", "name": "run_command", "input": {"command": "ls"},
        }
        message = self._message("autopilot", timezone.now() - timedelta(seconds=10))
        turn = self._paused_turn(call=shell_call)
        self._save_pending(turn, call=shell_call)

        result, captured, _chunks, resume, arm = self._handle("autopilot", message)

        arm.assert_not_called()
        resume.assert_not_called()
        self.assertEqual(self._pending_record().status, "pending")
        turn.refresh_from_db()
        self.assertEqual(turn.state, "paused")
        self.assertIn("waiting for your approval", captured.get("pending_note", ""))
        self.assertIn("Should I go ahead?", result.full_response)
        self.assertEqual(Turn.objects.filter(state="completed").count(), 1)

    # -- old behaviour kept ------------------------------------------------- #

    def test_a_yes_sent_after_the_prompt_approves_once(self):
        turn = self._paused_turn()
        self._save_pending(turn)
        message = self._message("yes", timezone.now() + timedelta(seconds=10))
        calls = []

        async def _resume(**_kwargs):
            calls.append(1)
            yield AgentEvent("text", {"text": "Done."})
            yield AgentEvent("done", {"end_reason": "finished"})

        self._handle("yes", message, resume=_resume)

        self.assertEqual(len(calls), 1)
        turn.refresh_from_db()
        self.assertEqual(turn.state, "completed")

    def test_an_earlier_no_still_cancels(self):
        message = self._message("no", timezone.now() - timedelta(seconds=10))
        turn = self._paused_turn()
        approval_id = self._save_pending(turn)

        _result, _captured, _chunks, resume, _arm = self._handle("no", message)

        resume.assert_not_called()
        self.assertEqual(
            WorkflowApprovalRecord.objects.get(id=approval_id).status,
            "cancelled",
        )
        turn.refresh_from_db()
        self.assertEqual(turn.state, "cancelled")
        self.assertEqual(turn.end_reason, "cancelled")


class ConsentSkewSettingTests(TransactionTestCase):
    """The configured margin applies to the comparison, including settings."""

    def test_consent_uses_the_configured_skew(self):
        from django.test import override_settings

        from orchestration.coordinator import _consent_postdates_prompt

        user = User.objects.create_user(
            username="skew-user", password="fake-token",  # nosec B106
        )
        room = Chatroom.objects.create()
        member = Member.objects.create(User=user)
        room.participants.add(member)
        created = timezone.now() - timedelta(minutes=1)
        record = WorkflowApprovalRecord.objects.create(
            kind="agent_loop", requested_by=user, room_id=room.id,
            step_id="agent_loop:test", action="send_email",
            status="pending",
            expires_at=created + timedelta(minutes=10),
        )
        WorkflowApprovalRecord.objects.filter(id=record.id).update(created_at=created)
        message = Message.objects.create(
            member=member, content="yes",
            timestamp=created + timedelta(seconds=4),
        )

        with override_settings(CONSENT_SKEW_SECONDS=2):
            allowed = async_to_sync(_consent_postdates_prompt)(
                room.id, user.id, message.id,
            )
        with override_settings(CONSENT_SKEW_SECONDS=5):
            denied = async_to_sync(_consent_postdates_prompt)(
                room.id, user.id, message.id,
            )

        self.assertTrue(allowed)
        self.assertFalse(denied)
