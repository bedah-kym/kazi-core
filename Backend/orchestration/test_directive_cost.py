"""FU-47 r3/r4: a directive path never pays for the persona lookup.

Runs the real coordinator with a whole-message directive, counts the
``sync_to_async`` hops and queries, and asserts the persona resolver is not
called. It has no imports from the turn loader, so the same tests can be run
against ``origin/main`` for the before numbers. The runner variant also proves
the runner finalizes the Turn.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync, SyncToAsync
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.db.backends.utils import CursorWrapper
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from chatbot.history import decrypt_stored
from chatbot.kazi_messages import encrypt_for_room
from chatbot.models import Chatroom, Member, Message
from chatbot.turn_runner import TurnRunner, enqueue_turn
from orchestration import turn_leases
from orchestration.coordinator import OrchestrationCoordinator

DIRECTIVE_HOPS = 10
DIRECTIVE_QUERIES = 13
DIRECTIVE_POSTGRES_QUERIES = 11
RUNNER_DIRECTIVE_HOPS = 12
RUNNER_DIRECTIVE_QUERIES = 23
RUNNER_DIRECTIVE_POSTGRES_QUERIES = 19

REPLY = "Got it. I will stop showing that kind of suggestion here."


@override_settings(
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
    TURN_QUEUE_MAX=3,
)
class DirectiveCostTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        turn_leases._LIVE_TURNS.clear()
        turn_leases._FENCED_TURNS.clear()
        turn_leases._LAST_RENEWAL.clear()
        self.addCleanup(turn_leases._LIVE_TURNS.clear)
        self.addCleanup(turn_leases._FENCED_TURNS.clear)
        self.addCleanup(turn_leases._LAST_RENEWAL.clear)
        User = get_user_model()
        self.user = User.objects.create_user(
            username="directive-cost", password="pw",  # nosec B106
        )
        self.member = Member.objects.select_related("User").get(User=self.user)
        self.room = Chatroom.objects.get(participants=self.member)
        self.message = Message.objects.create(
            member=self.member, content="dismiss suggestions",
            timestamp=timezone.now(),
        )
        self.room.chats.add(self.message)

    def _counters(self):
        counts = {"hops": 0, "queries": 0}
        real_call = SyncToAsync.__call__
        real_execute = CursorWrapper.execute

        async def counting_call(sync_self, *args, **kwargs):
            counts["hops"] += 1
            return await real_call(sync_self, *args, **kwargs)

        def counting_execute(cursor, sql, params=None):
            counts["queries"] += 1
            return real_execute(cursor, sql, params)

        return counts, counting_call, counting_execute

    def _assert_counts(self, counts, hops, sqlite_queries, postgres_queries):
        self.assertEqual(counts["hops"], hops)
        expected = (
            sqlite_queries if connection.vendor == "sqlite"
            else postgres_queries
        )
        self.assertEqual(counts["queries"], expected)

    def test_a_directive_does_not_look_up_the_persona_and_finalizes(self):
        counts, counting_call, counting_execute = self._counters()
        sent = []

        async def send_chunk(*args, **kwargs):
            if len(args) >= 2:
                sent.append(args[1])

        async def drive():
            return await OrchestrationCoordinator().handle_message(
                query="dismiss suggestions",
                user_id=self.user.id,
                room_id=self.room.id,
                username=self.user.username,
                message_id=self.message.id,
                send_chunk=send_chunk,
                send_step_event=AsyncMock(),
                get_context_prompt=AsyncMock(return_value=""),
                bump_signals=MagicMock(),
                history_messages=None,
            )

        with patch(
            "orchestration.personas.resolve_room_persona",
        ) as persona, patch.object(
            SyncToAsync, "__call__", new=counting_call,
        ), patch.object(
            CursorWrapper, "execute", new=counting_execute,
        ):
            result = async_to_sync(drive)()

        persona.assert_not_called()
        self.assertIn(REPLY, "".join(sent))
        async_to_sync(OrchestrationCoordinator().finalize_turn)(
            result, reply_saved=True,
        )
        from orchestration.models import Turn

        self.assertEqual(Turn.objects.get(pk=result.turn_id).state, "completed")
        self._assert_counts(
            counts, DIRECTIVE_HOPS, DIRECTIVE_QUERIES,
            DIRECTIVE_POSTGRES_QUERIES,
        )

    def test_a_runner_directive_finalizes_and_keeps_the_persona_lazy(self):
        content = encrypt_for_room(
            self.room, {"content": "@kazi dismiss suggestions"},
        )
        message = Message.objects.create(
            member=self.member, content=json.dumps(content),
            timestamp=timezone.now(),
        )
        self.room.chats.add(message)
        enqueue_turn(
            room_id=self.room.id, user_id=self.user.id,
            request_message_id=message.id, cap=3,
        )
        counts, counting_call, counting_execute = self._counters()
        runner = TurnRunner()

        async def scenario():
            await runner.submit(self.room.id, self.user.id)
            await runner.drain(timeout=30)

        with patch(
            "orchestration.personas.resolve_room_persona",
        ) as persona, patch.object(
            SyncToAsync, "__call__", new=counting_call,
        ), patch.object(
            CursorWrapper, "execute", new=counting_execute,
        ):
            async_to_sync(scenario)()

        from orchestration.models import Turn

        persona.assert_not_called()
        self.assertEqual(Turn.objects.filter(state="completed").count(), 1)
        saved = Message.objects.order_by("-id").first()
        self.assertIsNotNone(saved)
        self.assertEqual(decrypt_stored(self.room, saved)[0], REPLY)
        self._assert_counts(
            counts, RUNNER_DIRECTIVE_HOPS, RUNNER_DIRECTIVE_QUERIES,
            RUNNER_DIRECTIVE_POSTGRES_QUERIES,
        )
