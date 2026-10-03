"""Tests for @admin chat escalation (Workstream C)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase

from chatbot.consumers import ChatConsumer
from chatbot.models import Chatroom, Member

User = get_user_model()


class AdminEscalationTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

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
    def test_admin_mention_notifies_superuser_and_skips_bot(self, mock_coord):
        alice = User.objects.create_user(
            username="alice_admin", password="fake-token",  # nosec B106 — test fixture — fake credential
        )
        superuser = User.objects.create_user(
            username="rootadmin", password="fake-token",  # nosec B106 — test fixture — fake credential
            is_superuser=True, is_staff=True,
        )
        member = Member.objects.select_related("User").get(User=alice)
        chatroom = Chatroom.objects.get(participants=member)
        consumer = self._make_consumer(alice, member, chatroom)

        async_to_sync(consumer.new_message)({
            "from": "alice_admin",
            "message": "@admin please look at the Night Hawk schedule",
            "chatid": str(chatroom.id),
        })

        mock_coord.assert_not_called()
        from notifications.models import Notification

        self.assertTrue(
            Notification.objects.filter(user=superuser, event_type="message.mention").exists()
        )
        # The bot acknowledged in-room.
        consumer.send_chat_message.assert_awaited()

    def test_persona_request_creates_row(self):
        from workflows.models import PersonaRequest

        alice = User.objects.create_user(
            username="alice_request", password="fake-token",  # nosec B106 — test fixture — fake credential
        )
        User.objects.create_user(
            username="rootadmin2", password="fake-token",  # nosec B106 — test fixture — fake credential
            is_superuser=True, is_staff=True,
        )
        member = Member.objects.select_related("User").get(User=alice)
        chatroom = Chatroom.objects.get(participants=member)
        consumer = self._make_consumer(alice, member, chatroom)

        async_to_sync(consumer.new_message)({
            "from": "alice_request",
            "message": '@admin request persona "Night Owl" - "overnight custodian"',
            "chatid": str(chatroom.id),
        })

        request_row = PersonaRequest.objects.get(user=alice)
        self.assertEqual(request_row.name, "Night Owl")
        self.assertIn("overnight custodian", request_row.description)
        self.assertEqual(request_row.status, "pending")

    def test_admin_escalation_has_a_per_user_cooldown(self):
        from notifications.models import Notification

        alice = User.objects.create_user(
            username="alice_cooldown", password="fake-token",  # nosec B106 — test fixture — fake credential
        )
        User.objects.create_user(
            username="rootadmin3", password="fake-token",  # nosec B106 — test fixture — fake credential
            is_superuser=True, is_staff=True,
        )
        member = Member.objects.select_related("User").get(User=alice)
        chatroom = Chatroom.objects.get(participants=member)
        consumer = self._make_consumer(alice, member, chatroom)

        async_to_sync(consumer.new_message)({
            "from": "alice_cooldown", "message": "@admin first", "chatid": str(chatroom.id),
        })
        async_to_sync(consumer.new_message)({
            "from": "alice_cooldown", "message": "@admin second", "chatid": str(chatroom.id),
        })

        self.assertEqual(
            Notification.objects.filter(user__is_superuser=True).count(), 1,
        )
