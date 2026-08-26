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
from django.test import SimpleTestCase, TransactionTestCase, override_settings

from orchestration.coordinator import OrchestrationResult

from .consumers import (
    ChatConsumer,
    _agent_loop_locks,
    _agent_loop_lock_refs,
    _get_agent_loop_lock,
    _release_agent_loop_lock,
)
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

    def _make_consumer(self, alice, member, chatroom):
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
        return consumer

    @patch("orchestration.coordinator.OrchestrationCoordinator")
    def test_new_message_routes_ai_to_coordinator_and_persists(self, mock_coord):
        User = get_user_model()
        alice = User.objects.create_user(username="alice", password="pw")  # nosec B106 — test fixture — fake credential
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

    @patch("orchestration.coordinator.OrchestrationCoordinator")
    def test_new_message_serializes_agent_loop_per_room_user(self, mock_coord):
        User = get_user_model()
        alice = User.objects.create_user(username="alice_lock", password="pw")  # nosec B106 — test fixture — fake credential
        member = Member.objects.select_related("User").get(User=alice)
        chatroom = Chatroom.objects.get(participants=member)

        observed = {}

        async def handle(**kwargs):
            observed["called"] = True
            key = (kwargs["user_id"], str(kwargs["room_id"]))
            observed["locked"] = _agent_loop_locks[key].locked()
            return OrchestrationResult(full_response="", persist=False)

        mock_coord.return_value.handle_message = handle

        consumer = self._make_consumer(alice, member, chatroom)
        async_to_sync(consumer.new_message)(
            {"from": "alice_lock", "message": "@mathia hello", "chatid": str(chatroom.id)}
        )

        self.assertTrue(observed.get("called"), "handle never called")
        self.assertTrue(observed["locked"])
        self.assertEqual(_agent_loop_locks, {})
        self.assertEqual(_agent_loop_lock_refs, {})


class AgentLoopLockTests(SimpleTestCase):
    def setUp(self):
        _agent_loop_locks.clear()
        _agent_loop_lock_refs.clear()

    def tearDown(self):
        _agent_loop_locks.clear()
        _agent_loop_lock_refs.clear()

    def test_lock_is_shared_per_room_user_and_cleaned_up(self):
        first = _get_agent_loop_lock(1, "room")
        second = _get_agent_loop_lock(1, "room")
        other = _get_agent_loop_lock(2, "room")

        self.assertIs(first, second)
        self.assertIsNot(first, other)

        _release_agent_loop_lock(1, "room")
        _release_agent_loop_lock(1, "room")
        _release_agent_loop_lock(2, "room")

        self.assertEqual(_agent_loop_locks, {})
        self.assertEqual(_agent_loop_lock_refs, {})


class ChatConsumerRedisOutageTests(SimpleTestCase):
    """F7.2: a Redis/channel-layer outage must not kill the WS handshake.

    The consumer accepts the connection degraded — direct sends still work,
    group events and presence are best-effort until Redis recovers.
    """

    def _make_consumer(self):
        consumer = ChatConsumer()
        consumer.scope = {
            "user": MagicMock(is_authenticated=True, username="alice"),
            "url_route": {"kwargs": {"room_name": "1"}},
        }
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_add = AsyncMock()
        consumer.channel_layer.group_send = AsyncMock()
        consumer.channel_name = "test-channel"
        consumer.accept = AsyncMock()
        consumer.close = AsyncMock()
        consumer.send = AsyncMock()
        consumer.get_chatroom_for_user = AsyncMock(return_value=MagicMock())
        consumer.initialize_secure_session = AsyncMock(return_value=True)
        consumer.get_chatroom_participants = AsyncMock(return_value=[])
        return consumer

    def test_connect_accepts_when_channel_layer_down(self):
        async_to_sync(self._connect_channel_layer_down)()

    async def _connect_channel_layer_down(self):
        consumer = self._make_consumer()
        consumer.channel_layer.group_add = AsyncMock(side_effect=ConnectionError("redis down"))
        consumer.channel_layer.group_send = AsyncMock(side_effect=ConnectionError("redis down"))

        with patch(
            "chatbot.consumers.get_redis_connection",
            side_effect=ConnectionError("redis down"),
        ):
            await consumer.connect()

        consumer.accept.assert_awaited_once()
        consumer.close.assert_not_awaited()
        snapshot = json.loads(consumer.send.await_args.kwargs["text_data"])
        self.assertEqual(snapshot["command"], "presence_snapshot")

    def test_connect_accepts_and_still_broadcasts_presence_when_only_redis_down(self):
        async_to_sync(self._connect_only_redis_down)()

    async def _connect_only_redis_down(self):
        consumer = self._make_consumer()

        with patch(
            "chatbot.consumers.get_redis_connection",
            side_effect=ConnectionError("connection refused"),
        ):
            await consumer.connect()

        consumer.accept.assert_awaited_once()
        consumer.close.assert_not_awaited()
        consumer.channel_layer.group_send.assert_awaited_once()
        self.assertEqual(consumer.channel_layer.group_send.await_args[0][0], "chat_1")

    def test_default_cache_configured_to_ignore_redis_exceptions(self):
        from django.conf import settings

        self.assertTrue(settings.REDIS_CACHE_IGNORE_EXCEPTIONS)

    @override_settings(CACHES={
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": "redis://127.0.0.1:1/9",
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
                "IGNORE_EXCEPTIONS": True,
                "CONNECTION_POOL_KWARGS": {"socket_connect_timeout": 0.2},
            },
        },
    })
    def test_cache_reads_and_writes_survive_unreachable_redis(self):
        from django.core.cache import cache

        self.assertIsNone(cache.get("ws-degrade-probe"))
        self.assertFalse(cache.set("ws-degrade-probe", 1, timeout=10))
        self.assertIsNone(cache.get("ws-degrade-probe"))


class JsonForScriptTests(SimpleTestCase):
    """Room/user identifiers are injected into <script> context — the JSON
    payload must never allow a value to break out of the script element."""

    def test_script_breakout_sequences_are_neutralized(self):
        from chatbot.views import _json_for_script

        payload = _json_for_script("</script><script>alert(1)</script>")
        self.assertNotIn("</script>", payload)
        self.assertNotIn("<script>", payload)
        self.assertIn("\\u003c", payload)

    def test_plain_values_round_trip(self):
        from chatbot.views import _json_for_script

        payload = _json_for_script("room-a")
        self.assertEqual(payload, '"room-a"')

    def test_nested_data_still_json_encodes(self):
        from chatbot.views import _json_for_script

        payload = _json_for_script({"id": 1})
        self.assertEqual(payload, '{"id": 1}')
