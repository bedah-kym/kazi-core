"""FU-47 r3: a directive path never pays for the persona lookup.

Runs the real coordinator with a whole-message directive, counts the
``sync_to_async`` hops and queries, and asserts the persona resolver is not
called. It has no imports from the turn loader, so the same test can be run
against ``origin/main`` for the before number.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync, SyncToAsync
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.db.backends.utils import CursorWrapper
from django.test import TransactionTestCase
from django.utils import timezone

from chatbot.models import Chatroom, Member, Message
from orchestration.coordinator import OrchestrationCoordinator

DIRECTIVE_HOPS = 10
DIRECTIVE_QUERIES = 13
DIRECTIVE_POSTGRES_QUERIES = 11


class DirectiveCostTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
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

    def test_a_dismiss_directive_does_not_look_up_the_persona(self):
        counts = {"hops": 0, "queries": 0}
        real_call = SyncToAsync.__call__
        real_execute = CursorWrapper.execute

        async def counting_call(sync_self, *args, **kwargs):
            counts["hops"] += 1
            return await real_call(sync_self, *args, **kwargs)

        def counting_execute(cursor, sql, params=None):
            counts["queries"] += 1
            return real_execute(cursor, sql, params)

        async def drive():
            return await OrchestrationCoordinator().handle_message(
                query="dismiss suggestions",
                user_id=self.user.id,
                room_id=self.room.id,
                username=self.user.username,
                message_id=self.message.id,
                send_chunk=AsyncMock(),
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
            async_to_sync(drive)()

        persona.assert_not_called()
        self.assertEqual(counts["hops"], DIRECTIVE_HOPS)
        expected = (
            DIRECTIVE_QUERIES if connection.vendor == "sqlite"
            else DIRECTIVE_POSTGRES_QUERIES
        )
        self.assertEqual(counts["queries"], expected)
