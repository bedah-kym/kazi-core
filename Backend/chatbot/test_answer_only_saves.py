"""217a-1c: a tool turn saves and replays the answer only.

These drive the real coordinator, the real Turn rows and the real save path,
then read the stored row through the real decrypt helper. Only the model is
scripted, at the event boundary.
"""
from __future__ import annotations

import json
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from chatbot.history import decrypt_stored, get_history_rows
from chatbot.models import Chatroom, Member, Message
from chatbot.transcript import build_history_messages
from chatbot.turn_runner import save_turn_reply
from orchestration import turn_leases
from orchestration.agent_loop import AgentEvent
from orchestration.coordinator import OrchestrationCoordinator


NARRATION = "Let me check the directory first."


def _text(text, channel="answer"):
    return AgentEvent("text_delta", {"text": text, "channel": channel})


def _done(reason="finished"):
    return AgentEvent("done", {
        "end_reason": reason, "elapsed": 1, "active_seconds": 1,
    })


def _tool_result(call_id="c1", name="run_command", shown="file.txt"):
    return AgentEvent("tool_result", {
        "id": call_id,
        "name": name,
        "input": {"command": "ls"},
        "result": {"status": "success"},
        "shown": shown,
        "iteration": 1,
    })


def _loop(*events):
    async def _events(**_kwargs):
        for event in events:
            yield event

    return _events


@override_settings(
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
    CONSENT_SKEW_SECONDS=0,
)
class AnswerOnlySaveTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        User = get_user_model()
        self.user = User.objects.create_user(
            username="answer-only", password="fake-token",  # nosec B106 — test fixture, fake credential
        )
        self.member, _ = Member.objects.get_or_create(User=self.user)
        self.room = Chatroom.objects.create()
        self.room.participants.add(self.member)
        self.message = Message.objects.create(
            member=self.member,
            content="@kazi do the thing",
            timestamp=timezone.now(),
        )
        self.room.chats.add(self.message)
        turn_leases._LIVE_TURNS.clear()
        turn_leases._FENCED_TURNS.clear()
        turn_leases._LAST_RENEWAL.clear()
        self.addCleanup(turn_leases._LIVE_TURNS.clear)
        self.addCleanup(turn_leases._FENCED_TURNS.clear)
        self.addCleanup(turn_leases._LAST_RENEWAL.clear)

    def _run(self, *events):
        patches = {
            "orchestration.coordinator.load_memory_summary": AsyncMock(return_value=None),
            "orchestration.coordinator.get_user_preferences": lambda user_id, profile_obj=None: {},
            "orchestration.coordinator.has_pending_agent_state": AsyncMock(return_value=False),
            "orchestration.coordinator.has_pending_budget_state": AsyncMock(return_value=False),
            "orchestration.coordinator.has_expired_pause": AsyncMock(return_value=False),
            "orchestration.coordinator.cache": MagicMock(),
            "orchestration.coordinator.record_event": MagicMock(),
            "orchestration.coordinator.record_from_event": MagicMock(return_value=[]),
            "orchestration.coordinator.run_agent_loop": _loop(*events),
            "orchestration.turn_leases.ensure_heartbeat": MagicMock(return_value=None),
            "orchestration.turn_leases.ensure_cancel_poll": MagicMock(return_value=None),
        }

        async def run():
            try:
                return await OrchestrationCoordinator().handle_message(
                    query="do the thing",
                    user_id=self.user.id,
                    room_id=self.room.id,
                    username=self.user.username,
                    message_id=self.message.id,
                    send_chunk=AsyncMock(),
                    send_step_event=AsyncMock(),
                    get_context_prompt=AsyncMock(return_value=""),
                    bump_signals=MagicMock(),
                )
            finally:
                await turn_leases.stop_heartbeat()

        with ExitStack() as stack:
            for target, value in patches.items():
                stack.enter_context(patch(target, new=value))
            result = async_to_sync(run)()
        message = save_turn_reply(self.room, result, user_id=self.user.id)
        return result, message

    def _stored(self, message):
        return decrypt_stored(self.room, message)

    def test_a_tool_turn_saves_the_answer_and_keeps_narration_encrypted(self):
        result, message = self._run(
            _text(NARRATION, channel="narration"),
            _tool_result(),
            _text("The files are here.", channel="answer"),
            _done(),
        )

        content, payload = self._stored(message)

        self.assertEqual(content, "The files are here.")
        self.assertEqual(payload["model"], "The files are here.")
        self.assertEqual(payload.get("narration"), NARRATION)
        self.assertEqual(result.full_response, "The files are here.")

    def test_the_next_turn_replays_no_narration(self):
        _result, message = self._run(
            _text(NARRATION, channel="narration"),
            _tool_result(),
            _text("The files are here.", channel="answer"),
            _done(),
        )
        self.assertIsNotNone(message)

        rows = get_history_rows(self.room, limit=10)
        messages = build_history_messages(rows, viewer_user_id=self.user.id)
        replayed = json.dumps(messages)

        self.assertIn("The files are here.", replayed)
        self.assertNotIn(NARRATION, replayed)

    def test_an_answerless_turn_saves_its_last_narration_part(self):
        result, message = self._run(
            _text("first thought", channel="narration"),
            _text("second thought", channel="narration"),
            _done(),
        )

        content, payload = self._stored(message)

        self.assertEqual(content, "second thought")
        self.assertEqual(payload["model"], "second thought")
        self.assertEqual(payload.get("narration"), "first thoughtsecond thought")
        self.assertEqual(result.full_response, "second thought")

    def test_narration_is_capped_at_four_thousand_characters_keeping_the_end(self):
        narration = "0123456789" * 500
        _result, message = self._run(
            _text(narration, channel="narration"),
            _text("Done.", channel="answer"),
            _done(),
        )

        _content, payload = self._stored(message)
        stored = payload.get("narration")

        self.assertIsNotNone(stored)
        self.assertEqual(len(stored), 4000)
        self.assertEqual(stored, narration[-4000:])

    def test_narration_is_never_plaintext_in_the_stored_row(self):
        secret = "NARRATION-SECRET-123"  # nosec B105 — test fixture — fake credential
        _result, message = self._run(
            _text(secret, channel="narration"),
            _text("Done.", channel="answer"),
            _done(),
        )

        raw = message.content
        _content, payload = self._stored(message)

        self.assertNotIn(secret, raw)
        self.assertEqual(payload.get("narration"), secret)

    def test_a_run_with_neither_narration_nor_answer_saves_nothing(self):
        _result, message = self._run(_done())

        self.assertIsNone(message)
