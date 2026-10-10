"""The presence service: per connection, self-healing, agent-aware."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase, override_settings

from chatbot import presence
from chatbot.transcript import BOT_USERNAME

# Forces the module down the Redis path so a fake client is used.
REDIS_CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": "redis://127.0.0.1:1/9",
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "IGNORE_EXCEPTIONS": True,
            "CONNECTION_POOL_KWARGS": {"socket_connect_timeout": 0.2},
        },
    },
}


def _clock(value):
    return patch.object(presence, "_now", new=lambda: value[0])


def _reset_store():
    presence._local_rooms.clear()
    presence._local_users.clear()
    presence._skip_until = 0.0


class AgentStatusTests(SimpleTestCase):
    def test_offline_when_no_provider_is_configured(self):
        from chatbot.presence import agent_status

        with patch("orchestration.model_catalog.provider_configured", return_value=False):
            self.assertEqual(agent_status(), "offline")

    def test_online_when_a_provider_is_configured(self):
        from chatbot.presence import agent_status

        with patch("orchestration.model_catalog.provider_configured", return_value=True):
            self.assertEqual(agent_status(), "online")


class PresenceStoreTests(SimpleTestCase):
    def setUp(self):
        _reset_store()

    def test_two_connections_merge_into_one_user(self):
        first, online = async_to_sync(presence.connect)(10, 1, "a")
        self.assertTrue(first)
        self.assertEqual(online, {1})

        first_again, online_again = async_to_sync(presence.connect)(10, 1, "b")
        self.assertFalse(first_again)
        self.assertEqual(online_again, {1})

        self.assertFalse(async_to_sync(presence.disconnect)(10, 1, "a"))
        self.assertEqual(presence.online_user_ids(10), {1})

        self.assertTrue(async_to_sync(presence.disconnect)(10, 1, "b"))
        self.assertEqual(presence.online_user_ids(10), set())

    def test_without_disconnect_a_user_expires_after_the_window(self):
        clock = [1000.0]
        with _clock(clock):
            async_to_sync(presence.connect)(11, 2, "a")
            self.assertEqual(presence.online_user_ids(11), {2})
            clock[0] += 74
            self.assertEqual(presence.online_user_ids(11), {2})
            clock[0] += 2
            self.assertEqual(presence.online_user_ids(11), set())

    def test_is_user_online_follows_the_window(self):
        clock = [1000.0]
        with _clock(clock):
            async_to_sync(presence.connect)(12, 3, "a")
            self.assertTrue(presence.is_user_online(3))
            clock[0] += 74
            self.assertTrue(presence.is_user_online(3))
            clock[0] += 2
            self.assertFalse(presence.is_user_online(3))

    def test_a_beat_keeps_a_user_online(self):
        clock = [1000.0]
        with _clock(clock):
            async_to_sync(presence.connect)(13, 4, "a")
            clock[0] += 70
            async_to_sync(presence.beat)(13, 4, "a")
            clock[0] += 70
            self.assertTrue(presence.is_user_online(4))
            self.assertEqual(presence.online_user_ids(13), {4})

    def test_two_users_are_both_online(self):
        async_to_sync(presence.connect)(14, 5, "a")
        async_to_sync(presence.connect)(14, 6, "b")
        self.assertEqual(presence.online_user_ids(14), {5, 6})


class PresenceBackoffTests(SimpleTestCase):
    def setUp(self):
        _reset_store()

    @override_settings(CACHES=REDIS_CACHES)
    def test_a_store_error_is_not_retried_inside_the_backoff(self):
        clock = [1000.0]
        with _clock(clock), patch(
            "chatbot.presence.get_redis_connection",
            side_effect=ConnectionError("down"),
        ) as client:
            self.assertEqual(presence.online_user_ids(20), set())
            self.assertEqual(client.call_count, 1)

            self.assertEqual(presence.online_user_ids(20), set())
            self.assertEqual(client.call_count, 1)

            clock[0] += 31
            self.assertEqual(presence.online_user_ids(20), set())
            self.assertEqual(client.call_count, 2)

    @override_settings(CACHES=REDIS_CACHES)
    def test_connect_accepts_when_the_store_is_down(self):
        with patch("chatbot.presence.get_redis_connection", side_effect=ConnectionError("down")):
            first, online = async_to_sync(presence.connect)(21, 7, "a")
        self.assertFalse(first)
        self.assertEqual(online, set())


class RedisPipelineTests(SimpleTestCase):
    """The Redis path is one pipeline per call, with an expiry and a trim."""

    def setUp(self):
        _reset_store()

    def _fake_client(self, zrange_result):
        client = MagicMock()
        pipe = MagicMock()
        client.pipeline.return_value = pipe
        pipe.execute.return_value = [None, None, zrange_result, True, True]
        return client, pipe

    @override_settings(CACHES=REDIS_CACHES)
    def test_connect_is_one_pipeline_that_sets_an_expiry(self):
        client, pipe = self._fake_client([b"1:a"])
        with patch("chatbot.presence.get_redis_connection", return_value=client):
            first, online = async_to_sync(presence.connect)(40, 1, "a")
        client.pipeline.assert_called_once_with(transaction=True)
        pipe.execute.assert_called_once()
        pipe.zremrangebyscore.assert_called_once()
        pipe.expire.assert_called_once()
        self.assertTrue(first)
        self.assertEqual(online, {1})

    @override_settings(CACHES=REDIS_CACHES)
    def test_beat_is_one_pipeline(self):
        client, pipe = self._fake_client([b"2:b"])
        with patch("chatbot.presence.get_redis_connection", return_value=client):
            online = async_to_sync(presence.beat)(41, 2, "b")
        client.pipeline.assert_called_once_with(transaction=True)
        pipe.execute.assert_called_once()
        self.assertEqual(online, {2})

    @override_settings(CACHES=REDIS_CACHES)
    def test_disconnect_is_one_pipeline_without_a_user_key(self):
        client, pipe = self._fake_client([])
        with patch("chatbot.presence.get_redis_connection", return_value=client):
            last = async_to_sync(presence.disconnect)(42, 3, "c")
        client.pipeline.assert_called_once_with(transaction=True)
        pipe.execute.assert_called_once()
        pipe.set.assert_not_called()
        self.assertTrue(last)


class _FakeMember:
    def __init__(self, user_id, username, last_seen=None):
        self.User = SimpleNamespace(id=user_id, username=username)
        self.last_seen = last_seen


class ConsumerPresenceTests(SimpleTestCase):
    def setUp(self):
        _reset_store()

    def _consumer(self, room_id, user_id, username, members):
        from chatbot.consumers import ChatConsumer

        consumer = ChatConsumer()
        consumer.scope = {
            "user": MagicMock(is_authenticated=True, username=username, id=user_id),
            "url_route": {"kwargs": {"room_name": str(room_id)}},
        }
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_add = AsyncMock()
        consumer.channel_layer.group_send = AsyncMock()
        consumer.channel_layer.group_discard = AsyncMock()
        consumer.channel_name = "test-channel"
        consumer.accept = AsyncMock()
        consumer.close = AsyncMock()
        consumer.send = AsyncMock()
        consumer.get_chatroom_for_user = AsyncMock(return_value=SimpleNamespace(id=room_id))
        consumer.get_current_chatroom = AsyncMock(return_value=SimpleNamespace(id=room_id))
        consumer.initialize_secure_session = AsyncMock(return_value=True)
        consumer.get_chatroom_participants = AsyncMock(return_value=members)
        return consumer

    def test_connect_snapshot_has_the_human_online_and_the_agent(self):
        members = [_FakeMember(1, "alice"), _FakeMember(99, BOT_USERNAME)]

        async def run():
            consumer = self._consumer(30, 1, "alice", members)
            with patch("chatbot.consumers.agent_status", return_value="online"):
                await consumer.connect()
            return json.loads(consumer.send.await_args.kwargs["text_data"])

        snapshot = async_to_sync(run)()
        by_user = {entry["user"]: entry for entry in snapshot["presence"]}
        self.assertEqual(by_user["alice"]["status"], "online")
        self.assertEqual(by_user["alice"]["kind"], "human")
        self.assertEqual(by_user[BOT_USERNAME]["status"], "online")
        self.assertEqual(by_user[BOT_USERNAME]["kind"], "agent")
        self.assertIsNone(by_user[BOT_USERNAME]["last_seen"])

    def test_a_tick_reports_an_expired_user_offline(self):
        clock = [1000.0]
        members = [_FakeMember(1, "alice"), _FakeMember(2, "bob")]

        async def run():
            with _clock(clock):
                await presence.connect(31, 1, "alice-conn")
                consumer = self._consumer(31, 2, "bob", members)
                with patch("chatbot.consumers.agent_status", return_value="offline"):
                    await consumer.connect()
                consumer.send.reset_mock()

                clock[0] += 80
                await consumer._presence_tick(31, 2, consumer._presence_connection_id)
                return json.loads(consumer.send.await_args.kwargs["text_data"])

        snapshot = async_to_sync(run)()
        by_user = {entry["user"]: entry for entry in snapshot["presence"]}
        self.assertEqual(by_user["alice"]["status"], "offline")
        self.assertEqual(by_user["bob"]["status"], "online")

    def test_a_tick_sends_nothing_when_nothing_changed(self):
        members = [_FakeMember(2, "bob")]

        async def run():
            consumer = self._consumer(32, 2, "bob", members)
            with patch("chatbot.consumers.agent_status", return_value="offline"):
                await consumer.connect()
            consumer.send.reset_mock()
            await consumer._presence_tick(32, 2, consumer._presence_connection_id)
            consumer.send.assert_not_awaited()

        async_to_sync(run)()

    def test_a_failed_tick_does_not_raise_and_the_next_one_works(self):
        members = [_FakeMember(2, "bob")]

        async def run():
            consumer = self._consumer(33, 2, "bob", members)
            with patch("chatbot.consumers.agent_status", return_value="offline"):
                await consumer.connect()

            with patch(
                "chatbot.presence.beat",
                new=AsyncMock(side_effect=RuntimeError("store down")),
            ):
                await consumer._presence_tick(33, 2, consumer._presence_connection_id)

            consumer.send.reset_mock()
            await consumer._presence_tick(33, 2, consumer._presence_connection_id)

        async_to_sync(run)()

    def test_disconnect_stops_the_beat(self):
        members = [_FakeMember(2, "bob")]

        async def run():
            consumer = self._consumer(34, 2, "bob", members)
            with patch("chatbot.consumers.agent_status", return_value="offline"):
                await consumer.connect()
            task = consumer._presence_task
            self.assertIsNotNone(task)

            await consumer.disconnect(1000)
            self.assertIsNone(getattr(consumer, "_presence_task", None))
            self.assertTrue(task.cancelled())

        async_to_sync(run)()

    def test_call_cancels_the_beat_without_a_disconnect(self):
        from channels.generic.websocket import AsyncWebsocketConsumer

        members = [_FakeMember(2, "bob")]

        async def run():
            consumer = self._consumer(35, 2, "bob", members)
            with patch("chatbot.consumers.agent_status", return_value="offline"):
                await consumer.connect()
            task = consumer._presence_task
            self.assertFalse(task.done())

            with patch.object(
                AsyncWebsocketConsumer, "__call__",
                new=AsyncMock(return_value=None),
            ):
                await consumer.__call__({}, AsyncMock(), AsyncMock())

            self.assertIsNone(getattr(consumer, "_presence_task", None))
            await asyncio.sleep(0)
            self.assertTrue(task.cancelled())

        async_to_sync(run)()

    @override_settings(CACHES=REDIS_CACHES)
    def test_snapshot_makes_no_store_call_per_participant(self):
        members = [_FakeMember(i, f"user{i}") for i in range(1, 6)]
        fake = MagicMock()
        pipe = MagicMock()
        fake.pipeline.return_value = pipe
        pipe.execute.return_value = [None, None, [b"1:a"], True, True]

        async def run():
            consumer = self._consumer(36, 1, "user1", members)
            with patch(
                "chatbot.presence.get_redis_connection", return_value=fake,
            ), patch(
                "chatbot.consumers.agent_status", return_value="offline",
            ):
                await consumer.connect()

        async_to_sync(run)()
        # One round trip for the whole room, however many participants it has.
        self.assertEqual(fake.pipeline.call_count, 1)

    @override_settings(CACHES=REDIS_CACHES)
    def test_connect_accepts_when_the_store_is_down(self):
        members = [_FakeMember(1, "alice"), _FakeMember(99, BOT_USERNAME)]

        async def run():
            consumer = self._consumer(37, 1, "alice", members)
            with patch(
                "chatbot.presence.get_redis_connection",
                side_effect=ConnectionError("down"),
            ), patch(
                "chatbot.consumers.agent_status", return_value="online",
            ):
                await consumer.connect()
            return consumer, json.loads(consumer.send.await_args.kwargs["text_data"])

        consumer, snapshot = async_to_sync(run)()
        consumer.accept.assert_awaited_once()
        consumer.close.assert_not_awaited()
        by_user = {entry["user"]: entry for entry in snapshot["presence"]}
        self.assertEqual(by_user["alice"]["status"], "online")
        self.assertEqual(by_user[BOT_USERNAME]["status"], "online")
