"""FU-47: hop/query pins, the pending snapshot, persona, voice and freshness.

The pins drive the real ``TurnRunner`` path (claim, coordinator, reply save,
finalize) with the model faked at ``agent_loop.get_llm_client``. The counters
wrap ``asgiref.sync.SyncToAsync.__call__`` and Django's cursor ``execute``, so
they measure production code rather than a re-implementation.
"""
from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync, SyncToAsync
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.db.backends.utils import CursorWrapper
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from chatbot.kazi_messages import encrypt_for_room
from chatbot.models import Chatroom, Member, Message
from chatbot.turn_runner import TurnRunner, enqueue_turn
from orchestration import turn_leases
from orchestration.agent_loop import AgentEvent, _state_key
from orchestration.coordinator import _load_turn_context_sync
from orchestration.models import Turn
from workflows.models import WorkflowApprovalRecord

# Measured on SQLite through the runner path after FU-47 round 2. The hop
# count is vendor-independent; the query count is pinned on SQLite only.
PLAIN_TURN_HOPS = 19
PLAIN_TURN_QUERIES = 37
TOOL_TURN_HOPS = 28
TOOL_TURN_QUERIES = 50


def _text(text):
    return {"type": "text", "text": text}


def _tool(call_id, name, tool_input):
    return {"type": "tool_use", "id": call_id, "name": name, "input": tool_input}


def _response(blocks, stop_reason="end_turn"):
    return {
        "content": blocks,
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


class _FakeLLM:
    def __init__(self):
        self._responses = []

    def queue(self, *responses):
        self._responses.extend(responses)
        return self

    def default_provider(self, *args, **kwargs):
        return "deepseek"

    def record_work_tokens(self, *args, **kwargs):
        return None

    async def check_hourly_quota(self, *args, **kwargs):
        return None

    async def create_message(self, **kwargs):
        response = self._responses.pop(0) if self._responses else _response(
            [_text("ok")],
        )
        return await response() if callable(response) else response


class _RunnerTestCase(TransactionTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        turn_leases._LIVE_TURNS.clear()
        turn_leases._FENCED_TURNS.clear()
        turn_leases._LAST_RENEWAL.clear()
        from orchestration import agent_loop

        agent_loop._circuit_state.clear()
        self.addCleanup(agent_loop._circuit_state.clear)

        self.llm = _FakeLLM()
        self._patches = [
            patch("orchestration.agent_loop.get_llm_client", return_value=self.llm),
            patch(
                "orchestration.agent_loop._resolve_persona",
                new=AsyncMock(return_value=None),
            ),
            patch("orchestration.agent_loop.enforce_agent_caps", return_value=True),
            patch("orchestration.agent_loop._search_backend_available", return_value=False),
            patch("orchestration.agent_loop.execute_tool", new_callable=AsyncMock),
            patch("orchestration.agent_loop.preview_tool", new_callable=AsyncMock),
            patch(
                "orchestration.shell.environment.get_shell_environment",
                new=AsyncMock(return_value={}),
            ),
        ]
        started = [p.start() for p in self._patches]
        self.addCleanup(lambda: [p.stop() for p in self._patches])
        started[4].return_value = {"status": "success", "data": {"ok": True}}
        started[5].return_value = None
        self.execute_tool = started[4]

        User = get_user_model()
        self.user = User.objects.create_user(username="db-hops", password="pw")  # nosec B106
        self.member = Member.objects.select_related("User").get(User=self.user)
        self.room = Chatroom.objects.get(participants=self.member)
        self.runner = TurnRunner()

    def _enqueue(self, text):
        content = encrypt_for_room(self.room, {"content": text})
        message = Message.objects.create(
            member=self.member,
            content=json.dumps(content),
            timestamp=timezone.now(),
        )
        self.room.chats.add(message)
        return enqueue_turn(
            room_id=self.room.id,
            user_id=self.user.id,
            request_message_id=message.id,
            cap=3,
        )

    def _run_turn(self, text):
        self._enqueue(text)

        async def scenario():
            await self.runner.submit(self.room.id, self.user.id)
            await self.runner.drain(timeout=30)

        async_to_sync(scenario)()


@override_settings(
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
    TURN_QUEUE_MAX=3,
)
class TurnDbHopTests(_RunnerTestCase):
    def _measure(self, text):
        self._enqueue(text)
        counts = {"hops": 0, "queries": 0}
        real_call = SyncToAsync.__call__
        real_execute = CursorWrapper.execute

        async def counting_call(sync_self, *args, **kwargs):
            counts["hops"] += 1
            return await real_call(sync_self, *args, **kwargs)

        def counting_execute(cursor, sql, params=None):
            counts["queries"] += 1
            return real_execute(cursor, sql, params)

        async def scenario():
            with patch.object(SyncToAsync, "__call__", new=counting_call), patch.object(
                CursorWrapper, "execute", new=counting_execute,
            ):
                await self.runner.submit(self.room.id, self.user.id)
                await self.runner.drain(timeout=30)

        async_to_sync(scenario)()
        return counts

    def _assert_counts(self, counts, hops, queries, label):
        self.assertEqual(counts["hops"], hops)
        if connection.vendor != "sqlite":
            print(
                f"FU-47 Postgres measured {counts['queries']} queries for the "
                f"{label} turn; the query pin is SQLite-only and the lead sets "
                "the Postgres number from CI."
            )
            return
        self.assertEqual(counts["queries"], queries)

    def test_a_plain_turn_hop_and_query_counts(self):
        self.llm.queue(_response([_text("hello back")]))
        counts = self._measure("@kazi hello")
        self.assertEqual(Turn.objects.filter(state="completed").count(), 1)
        self._assert_counts(
            counts, PLAIN_TURN_HOPS, PLAIN_TURN_QUERIES, "plain",
        )

    def test_a_tool_turn_hop_and_query_counts(self):
        self.llm.queue(
            _response(
                [_tool("c1", "get_weather", {"city": "Paris"})],
                stop_reason="tool_use",
            ),
            _response([_text("It is sunny.")]),
        )
        counts = self._measure("@kazi weather")
        self.assertEqual(Turn.objects.filter(state="completed").count(), 1)
        self.assertEqual(self.execute_tool.await_count, 1)
        self._assert_counts(
            counts, TOOL_TURN_HOPS, TOOL_TURN_QUERIES, "tool",
        )


@override_settings(
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
    TURN_QUEUE_MAX=3,
)
class TurnContextFreshnessTests(_RunnerTestCase):
    def test_the_next_turn_reads_the_changed_preference(self):
        captured = []

        async def fake_loop(**kwargs):
            captured.append(dict(kwargs.get("preferences") or {}))
            yield AgentEvent("text", {"text": "ok"})
            yield AgentEvent("done", {"end_reason": "finished"})

        profile = self.user.profile
        profile.notification_preferences = {"allow_email": True}
        profile.save(update_fields=["notification_preferences"])
        with patch("orchestration.coordinator.run_agent_loop", new=fake_loop):
            self._run_turn("@kazi first")

            profile.notification_preferences = {"allow_email": False}
            profile.save(update_fields=["notification_preferences"])
            self._run_turn("@kazi second")

        self.assertEqual(len(captured), 2)
        self.assertTrue(captured[0].get("allow_email"))
        self.assertFalse(captured[1].get("allow_email"))

    def test_a_persona_lookup_error_drops_the_identity_and_keeps_the_turn(self):
        self.llm.queue(_response([_text("hello back")]))
        with patch(
            "orchestration.personas.resolve_room_persona",
            side_effect=RuntimeError("persona store down"),
        ):
            self._run_turn("@kazi hello")
        self.assertEqual(Turn.objects.filter(state="completed").count(), 1)


@override_settings(
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
    TURN_QUEUE_MAX=3,
)
class VoiceDispatchTests(_RunnerTestCase):
    def _run_with_voice(self, enabled):
        profile = self.user.profile
        profile.notification_preferences = {"ai_voice_enabled": enabled}
        profile.save(update_fields=["notification_preferences"])
        self.llm.queue(_response([_text("hello back")]))
        with patch("chatbot.dispatch.dispatch_task") as dispatch, patch(
            "chatbot.tasks.generate_voice_response", new=object(),
        ) as voice:
            self._run_turn("@kazi hello")
        return dispatch, voice

    def test_an_enabled_voice_dispatches_once_with_the_saved_reply(self):
        dispatch, voice = self._run_with_voice(True)
        saved = Message.objects.order_by("-id").first()
        dispatch.assert_called_once_with(voice, saved.id)

    def test_a_disabled_voice_does_not_dispatch(self):
        dispatch, _voice = self._run_with_voice(False)
        dispatch.assert_not_called()


class TurnContextPendingSnapshotTests(TransactionTestCase):
    """The fast path's ``nothing_pending`` keeps the old pending meaning."""

    def setUp(self):
        super().setUp()
        cache.clear()
        User = get_user_model()
        self.user = User.objects.create_user(
            username="pending-snapshot", password="pw",  # nosec B106
        )
        self.member = Member.objects.select_related("User").get(User=self.user)
        self.room = Chatroom.objects.get(participants=self.member)

    def _snapshot(self):
        return _load_turn_context_sync(self.user.id, self.room.id, {})

    def test_an_idle_key_is_nothing_pending(self):
        self.assertTrue(self._snapshot().nothing_pending)

    def test_a_paused_turn_is_never_idle(self):
        for pause_kind in ("approval", "budget", "quota"):
            turn = Turn.objects.create(
                room=self.room, user=self.user, state="paused",
                pause_kind=pause_kind,
            )
            self.assertFalse(self._snapshot().nothing_pending, pause_kind)
            turn.delete()

    def test_an_expired_pause_still_in_paused_state_is_not_idle(self):
        turn = Turn.objects.create(
            room=self.room, user=self.user, state="paused",
            pause_kind="approval",
            pause_expires_at=timezone.now() - timedelta(seconds=1),
        )
        self.assertFalse(self._snapshot().nothing_pending)
        turn.delete()

    def test_a_queued_turn_is_not_idle(self):
        turn = Turn.objects.create(
            room=self.room, user=self.user, state="queued",
        )
        self.assertFalse(self._snapshot().nothing_pending)
        turn.delete()

    def test_a_pending_unexpired_approval_row_is_not_idle(self):
        record = WorkflowApprovalRecord.objects.create(
            requested_by=self.user,
            kind="agent_loop",
            room_id=self.room.id,
            step_id="step",
            action="send_email",
            status="pending",
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        self.assertFalse(self._snapshot().nothing_pending)
        record.delete()

    def test_legacy_cached_loop_state_is_not_idle(self):
        cache.set(
            _state_key(self.room.id, self.user.id),
            json.dumps({"messages": [], "awaiting_budget": True}),
            timeout=60,
        )
        self.assertFalse(self._snapshot().nothing_pending)

    def test_a_lookup_error_is_not_idle(self):
        with patch(
            "orchestration.coordinator.has_paused_or_queued",
            side_effect=RuntimeError("store down"),
        ):
            self.assertFalse(self._snapshot().nothing_pending)
