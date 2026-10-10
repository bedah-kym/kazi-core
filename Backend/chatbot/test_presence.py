"""PR 1: the agent shows online, and every snapshot entry carries a kind."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from chatbot.transcript import BOT_USERNAME


class AgentStatusTests(SimpleTestCase):
    def test_offline_when_no_provider_is_configured(self):
        from chatbot.presence import agent_status

        with patch("orchestration.model_catalog.provider_configured", return_value=False):
            self.assertEqual(agent_status(), "offline")

    def test_online_when_a_provider_is_configured(self):
        from chatbot.presence import agent_status

        with patch("orchestration.model_catalog.provider_configured", return_value=True):
            self.assertEqual(agent_status(), "online")


class PresenceSnapshotTests(SimpleTestCase):
    def _participants(self, *usernames):
        return [
            SimpleNamespace(User=SimpleNamespace(username=name)) for name in usernames
        ]

    def _snapshot(self, participants, username="alice", agent="online"):
        from chatbot.consumers import ChatConsumer

        async def _run():
            consumer = ChatConsumer()
            consumer.scope = {
                "user": MagicMock(is_authenticated=True, username=username),
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
            consumer.get_chatroom_participants = AsyncMock(return_value=participants)

            with patch("chatbot.consumers.agent_status", return_value=agent), patch(
                "chatbot.consumers.get_redis_connection",
                side_effect=ConnectionError("redis down"),
            ):
                await consumer.connect()

            return json.loads(consumer.send.await_args.kwargs["text_data"])

        return async_to_sync(_run)()

    def _entry(self, snapshot, username):
        return next(e for e in snapshot["presence"] if e["user"] == username)

    def test_agent_is_offline_without_a_provider(self):
        snapshot = self._snapshot(self._participants("alice", BOT_USERNAME), agent="offline")
        entry = self._entry(snapshot, BOT_USERNAME)
        self.assertEqual(entry["status"], "offline")
        self.assertEqual(entry["kind"], "agent")
        self.assertIsNone(entry["last_seen"])

    def test_agent_is_online_with_a_provider(self):
        snapshot = self._snapshot(self._participants("alice", BOT_USERNAME), agent="online")
        entry = self._entry(snapshot, BOT_USERNAME)
        self.assertEqual(entry["status"], "online")
        self.assertEqual(entry["kind"], "agent")
        self.assertIsNone(entry["last_seen"])

    def test_humans_are_labelled_human(self):
        snapshot = self._snapshot(self._participants("alice", BOT_USERNAME), agent="offline")
        entry = self._entry(snapshot, "alice")
        self.assertEqual(entry["kind"], "human")
        self.assertEqual(entry["status"], "online")
        self.assertIsNotNone(entry["last_seen"])
