"""Contract tests for ChatConsumer's input surface and routing handoff.

The SimpleTestCase class locks the consumer's outermost behavior — auth on
connect, command dispatch in receive, and sender/room validation in
new_message — so the OrchestrationCoordinator extraction (Phase 1) cannot
silently change the WebSocket contract.

The TransactionTestCase class locks the handoff boundary: a routed `@mathia`
message must delegate to the coordinator and persist the returned response.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TransactionTestCase

from orchestration.coordinator import OrchestrationResult

from .consumers import ChatConsumer
from .models import Chatroom, Member


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


class ChatConsumerRoutingTests(TransactionTestCase):
    """Lock the consumer -> coordinator handoff (Phase 1 wiring)."""

    @patch("orchestration.coordinator.OrchestrationCoordinator")
    def test_new_message_routes_ai_to_coordinator_and_persists(self, mock_coord):
        User = get_user_model()
        alice = User.objects.create_user(username="alice", password="pw")
        # The post_save signal already creates a Member + General Chatroom.
        # select_related('User') mirrors get_chatroom_participants, so the
        # FK is cached and `m.User.username` won't hit the DB in async code.
        member = Member.objects.select_related("User").get(User=alice)
        chatroom = Chatroom.objects.get(participants=member)

        handle = AsyncMock(
            return_value=OrchestrationResult(full_response="Hi there", persist=True)
        )
        mock_coord.return_value.handle_message = handle

        consumer = ChatConsumer()
        consumer.scope = {"user": alice}
        consumer.room_name = str(chatroom.id)
        consumer.room_group_name = f"chat_{chatroom.id}"
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_send = AsyncMock()
        consumer.send_chat_message = AsyncMock()
        consumer.send_message = AsyncMock()
        consumer.encrypt_message = AsyncMock(return_value={"data": "enc", "nonce": "nonce"})
        consumer.check_user_muted = AsyncMock(return_value=False)
        consumer.check_rate_limit = AsyncMock(return_value=True)
        consumer.check_key_rotation = AsyncMock()
        consumer.buffer_message_for_moderation = AsyncMock()
        consumer.get_current_chatroom = AsyncMock(return_value=chatroom)
        consumer.get_chatroom_participants = AsyncMock(return_value=[member])
        consumer.message_to_json = AsyncMock(return_value={})
        consumer.schedule_context_summary = AsyncMock()
        consumer.schedule_idle_nudge_if_needed = AsyncMock()
        consumer.get_history_as_text = AsyncMock(return_value="")

        async_to_sync(consumer.new_message)(
            {"from": "alice", "message": "@mathia hello", "chatid": str(chatroom.id)}
        )

        handle.assert_awaited_once()
        kwargs = handle.await_args.kwargs
        self.assertEqual(kwargs["query"], "hello")
        self.assertEqual(kwargs["user_id"], alice.id)
        self.assertEqual(kwargs["room_id"], str(chatroom.id))

        # The coordinator result was persisted as a Mathia message.
        from .models import Message

        self.assertTrue(
            Message.objects.filter(
                member__User__username="mathia", content__contains="enc"
            ).exists()
        )
