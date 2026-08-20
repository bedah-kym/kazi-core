"""Contract tests for ChatConsumer's input surface.

These lock the consumer's outermost behavior — auth on connect, command
dispatch in receive, and sender/room validation in new_message — so the
OrchestrationCoordinator extraction (Phase 1) cannot silently change the
WebSocket contract.

Deeper routing (agent loop, workflows, intent routing) is intentionally
covered elsewhere (orchestration/workflows suites) until the coordinator
is extracted.
"""
import json
from unittest.mock import AsyncMock, MagicMock

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from .consumers import ChatConsumer


class ChatConsumerContractTests(SimpleTestCase):
    """Input-contract guards that must survive refactoring."""

    def test_connect_unauthenticated_closes_4001(self):
        async_to_sync(self._connect_unauthenticated)()

    async def _connect_unauthenticated(self):
        consumer = ChatConsumer()
        consumer.scope = {
            "user": MagicMock(is_authenticated=False),
            "url_route": {"kwargs": {"room_name": "1"}},
        }
        consumer.close = AsyncMock()
        await consumer.connect()
        consumer.close.assert_awaited_once_with(code=4001)

    def test_receive_unknown_command_sends_system_message(self):
        async_to_sync(self._receive_unknown_command)()

    async def _receive_unknown_command(self):
        consumer = ChatConsumer()
        consumer.scope = {"user": MagicMock()}
        consumer.send_message = AsyncMock()
        await consumer.receive(json.dumps({"command": "bogus"}))
        consumer.send_message.assert_awaited_once()
        self.assertEqual(
            consumer.send_message.await_args[0][0]["content"],
            "Unknown command: bogus",
        )

    def test_receive_typing_groupsend(self):
        async_to_sync(self._receive_typing)()

    async def _receive_typing(self):
        consumer = ChatConsumer()
        consumer.scope = {"user": MagicMock()}
        consumer.room_group_name = "chat_1"
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_send = AsyncMock()
        await consumer.receive(json.dumps({"command": "typing", "from": "alice"}))
        consumer.channel_layer.group_send.assert_awaited_once_with(
            "chat_1",
            {"type": "typing_message", "from": "alice"},
        )

    def test_new_message_rejects_wrong_sender(self):
        async_to_sync(self._new_message_wrong_sender)()

    async def _new_message_wrong_sender(self):
        consumer = ChatConsumer()
        consumer.scope = {"user": MagicMock(username="alice")}
        consumer.send_chat_message = AsyncMock()
        await consumer.new_message({"from": "mallory", "message": "hi", "chatid": "1"})
        consumer.send_chat_message.assert_awaited_once()
        self.assertEqual(
            consumer.send_chat_message.await_args[0][0]["content"],
            "Invalid sender.",
        )

