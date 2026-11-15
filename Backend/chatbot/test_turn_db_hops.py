"""FU-47 a: count the ``sync_to_async`` hops and queries one turn costs.

Drive the real ``TurnRunner`` path (claim, coordinator, reply save, finalize)
with the model faked at ``agent_loop.get_llm_client``. The counters wrap
``asgiref.sync.SyncToAsync.__call__`` and Django's cursor ``execute``, so they
measure production code rather than a re-implementation.
"""
from __future__ import annotations

import asyncio
import json
from unittest import skipUnless
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync, sync_to_async, SyncToAsync
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.db.backends.utils import CursorWrapper
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from chatbot.kazi_messages import encrypt_for_room
from chatbot.models import Chatroom, Member, Message
from chatbot.turn_runner import (
    TurnRunner,
    _close_worker_connection,
    _load_turn_setup_off_thread,
    enqueue_turn,
)
from orchestration import turn_leases
from orchestration.models import Turn
from orchestration.turns import create_turn

# Measured on SQLite through the runner path after FU-47. Every hop is
# vendor-independent. Postgres opens the off-thread read connection inside the
# window, so the query pin there allows for its connection setup statements.
PLAIN_TURN_HOPS = 17
PLAIN_TURN_QUERIES = 37
TOOL_TURN_HOPS = 26
TOOL_TURN_QUERIES = 50
POSTGRES_CONNECTION_SETUP_QUERIES = 8


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


@override_settings(
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
    TURN_QUEUE_MAX=3,
)
class TurnDbHopTests(TransactionTestCase):
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

    def _assert_counts(self, counts, hops, queries):
        self.assertEqual(counts["hops"], hops)
        if connection.vendor == "sqlite":
            self.assertEqual(counts["queries"], queries)
        else:
            self.assertLessEqual(
                counts["queries"],
                queries + POSTGRES_CONNECTION_SETUP_QUERIES,
            )

    def test_a_plain_turn_hop_and_query_counts(self):
        self.llm.queue(_response([_text("hello back")]))
        counts = self._measure("@kazi hello")
        self.assertEqual(Turn.objects.filter(state="completed").count(), 1)
        self._assert_counts(counts, PLAIN_TURN_HOPS, PLAIN_TURN_QUERIES)

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
        self._assert_counts(counts, TOOL_TURN_HOPS, TOOL_TURN_QUERIES)

    def test_the_shared_thread_connection_is_not_closed_on_postgres(self):
        """FU-47 e3: closing the claim's connection re-established it every turn."""
        calls = []
        with patch(
            "chatbot.turn_runner._writes_are_thread_sensitive", return_value=False,
        ), patch(
            "django.db.connection.close", side_effect=lambda: calls.append(1),
        ):
            _close_worker_connection()
        self.assertEqual(calls, [])

    @skipUnless(connection.vendor == "postgresql", "Postgres lane only")
    def test_ten_concurrent_off_thread_reads_complete_without_cross_thread_errors(self):
        turns = []
        for index in range(10):
            content = encrypt_for_room(self.room, {"content": f"turn {index}"})
            message = Message.objects.create(
                member=self.member,
                content=json.dumps(content),
                timestamp=timezone.now(),
            )
            self.room.chats.add(message)
            turns.append(create_turn(
                room_id=self.room.id,
                user_id=self.user.id,
                request_message_id=message.id,
            ))

        async def scenario():
            return await asyncio.gather(*[
                sync_to_async(
                    _load_turn_setup_off_thread, thread_sensitive=False,
                )(turn)
                for turn in turns
            ])

        setups = async_to_sync(scenario)()
        self.assertEqual(len(setups), 10)
        for setup in setups:
            self.assertIsNotNone(setup.room)
            self.assertIsNotNone(setup.request)


class TurnContextFreshnessTests(TransactionTestCase):
    """FU-47 b: the batched loader is per-turn, never a cross-turn cache."""

    def setUp(self):
        super().setUp()
        User = get_user_model()
        self.user = User.objects.create_user(
            username="context-fresh", password="pw",  # nosec B106
        )
        self.member = Member.objects.select_related("User").get(User=self.user)
        self.room = Chatroom.objects.get(participants=self.member)

    def test_preferences_are_read_again_on_the_next_turn(self):
        from orchestration.coordinator import _load_turn_context_sync

        profile = self.user.profile
        profile.notification_preferences = {"allow_email": True}
        profile.save(update_fields=["notification_preferences"])
        first = _load_turn_context_sync(self.user.id, self.room.id, {})
        self.assertTrue(first.user_preferences.get("allow_email"))

        profile.notification_preferences = {"allow_email": False}
        profile.save(update_fields=["notification_preferences"])
        second = _load_turn_context_sync(self.user.id, self.room.id, {})
        self.assertFalse(second.user_preferences.get("allow_email"))
