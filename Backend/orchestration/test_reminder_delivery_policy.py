"""
Tests for the reminder delivery policy (v0.6): presence-based routing,
delivery modes, urgent retries/dead-letter, timezone fallback, dedupe,
and the list_reminders/list_notifications tools.
"""
import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from chatbot.models import Chatroom, Reminder
from chatbot import tasks as chatbot_tasks

User = get_user_model()

FUTURE = timezone.now() + timedelta(hours=2)


class DeliveryModeResolutionTests(TestCase):
    def test_flag_combinations_map_to_modes(self):
        def reminder(email, whatsapp):
            r = Reminder(via_email=email, via_whatsapp=whatsapp)
            return chatbot_tasks._resolve_delivery_mode(r)

        self.assertEqual(reminder(True, True), "auto")
        self.assertEqual(reminder(True, False), "email")
        self.assertEqual(reminder(False, True), "whatsapp")
        self.assertEqual(reminder(False, False), "in_app")

    def test_retry_delay_backoff_capped(self):
        self.assertEqual(chatbot_tasks._reminder_retry_delay(0), 30)
        self.assertEqual(chatbot_tasks._reminder_retry_delay(1), 60)
        self.assertEqual(chatbot_tasks._reminder_retry_delay(2), 120)
        self.assertEqual(chatbot_tasks._reminder_retry_delay(10), 300)


class ReminderDeliveryRouterTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="delivery-user", email="d@example.com", password="secret",  # nosec B106 — test fixture — fake credential
        )
        self.room = Chatroom.objects.create()

    def _reminder(self, via_email=False, via_whatsapp=False, priority="medium"):
        return Reminder.objects.create(
            user=self.user,
            room=self.room,
            content="Pack bags",
            scheduled_time=FUTURE,
            priority=priority,
            via_email=via_email,
            via_whatsapp=via_whatsapp,
        )

    def test_auto_delivers_in_chat_when_online(self):
        reminder = self._reminder(via_email=True, via_whatsapp=True)
        with patch.object(chatbot_tasks, "_is_user_online", return_value=True), \
             patch.object(chatbot_tasks, "_deliver_reminder_to_chat", return_value=True), \
             patch.object(chatbot_tasks, "_send_reminder_telegram", return_value=False) as mock_tg, \
             patch.object(chatbot_tasks, "_send_reminder_email", return_value=False) as mock_email, \
             patch("notifications.services.NotificationService.notify") as mock_notify:
            ok, channel = chatbot_tasks._deliver_reminder(reminder)
        self.assertEqual((ok, channel), (True, "chat"))
        mock_tg.assert_not_called()
        mock_email.assert_not_called()
        mock_notify.assert_called_once()

    def test_auto_offline_prefers_telegram_then_email(self):
        reminder = self._reminder(via_email=True, via_whatsapp=True)
        with patch.object(chatbot_tasks, "_is_user_online", return_value=False), \
             patch.object(chatbot_tasks, "_deliver_reminder_to_chat", return_value=False) as mock_chat, \
             patch.object(chatbot_tasks, "_send_reminder_telegram", return_value=False), \
             patch.object(chatbot_tasks, "_send_reminder_email", return_value=True) as mock_email:
            ok, channel = chatbot_tasks._deliver_reminder(reminder)
        self.assertEqual((ok, channel), (True, "email"))
        mock_chat.assert_not_called()
        mock_email.assert_called_once()

    def test_auto_offline_uses_telegram_when_connected(self):
        reminder = self._reminder(via_email=True, via_whatsapp=True)
        with patch.object(chatbot_tasks, "_is_user_online", return_value=False), \
             patch.object(chatbot_tasks, "_send_reminder_telegram", return_value=True), \
             patch.object(chatbot_tasks, "_send_reminder_email", return_value=False) as mock_email:
            ok, channel = chatbot_tasks._deliver_reminder(reminder)
        self.assertEqual((ok, channel), (True, "telegram"))
        mock_email.assert_not_called()

    def test_in_app_mode_always_succeeds_and_notifies(self):
        reminder = self._reminder(via_email=False, via_whatsapp=False)
        with patch("notifications.services.NotificationService.notify") as mock_notify:
            ok, channel = chatbot_tasks._deliver_reminder(reminder)
        self.assertEqual((ok, channel), (True, "in_app"))
        mock_notify.assert_called_once()

    def test_explicit_email_mode_ignores_presence(self):
        reminder = self._reminder(via_email=True, via_whatsapp=False)
        with patch.object(chatbot_tasks, "_is_user_online", return_value=True), \
             patch.object(chatbot_tasks, "_send_reminder_email", return_value=True) as mock_email:
            ok, channel = chatbot_tasks._deliver_reminder(reminder)
        self.assertEqual((ok, channel), (True, "email"))
        mock_email.assert_called_once()

    def test_email_mode_failure_returns_false(self):
        reminder = self._reminder(via_email=True, via_whatsapp=False)
        with patch.object(chatbot_tasks, "_send_reminder_email", return_value=False):
            ok, channel = chatbot_tasks._deliver_reminder(reminder)
        self.assertEqual((ok, channel), (False, "email"))

    def test_dead_letter_marks_failed_and_notifies(self):
        reminder = self._reminder(priority="high")
        with patch("notifications.services.NotificationService.notify") as mock_notify:
            chatbot_tasks._finalize_failed_delivery(reminder, attempts=5, channel="email")
        reminder.refresh_from_db()
        self.assertEqual(reminder.status, "failed")
        self.assertIn("dead letter", reminder.error_log)
        mock_notify.assert_called_once()


class ReminderConnectorToolTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="rem-tool-user", email="rt@example.com", password="secret",  # nosec B106 — test fixture — fake credential
        )

    def _execute(self, parameters, room_id=None):
        from orchestration.tool_router import ReminderConnector

        with patch("chatbot.reminder_service.LLMTimeParser") as mock_parser:
            mock_parser.return_value.parse = AsyncMock(return_value={
                "datetime": FUTURE.isoformat(),
                "needs_clarification": False,
                "confidence": 0.9,
                "interpretation": "in 2 hours",
            })
            return asyncio.run(
                ReminderConnector().execute(
                    parameters,
                    {"user_id": self.user.id, "room_id": room_id},
                )
            )

    def test_timezone_falls_back_to_deployment_tz(self):
        result = self._execute({"content": "Pack bags", "time": "in 2 hours"})
        self.assertEqual(result["status"], "success")
        reminder = Reminder.objects.get(id=result["reminder_id"])
        from django.conf import settings
        self.assertEqual(reminder.timezone, settings.TIME_ZONE)

    def test_auto_delivery_sets_both_flags(self):
        result = self._execute(
            {"content": "Pack bags", "time": "in 2 hours", "delivery": "auto"}
        )
        reminder = Reminder.objects.get(id=result["reminder_id"])
        self.assertTrue(reminder.via_email)
        self.assertTrue(reminder.via_whatsapp)

    def test_explicit_email_delivery_sets_email_flag(self):
        result = self._execute(
            {"content": "Pack bags", "time": "in 2 hours", "delivery": "email"}
        )
        reminder = Reminder.objects.get(id=result["reminder_id"])
        self.assertTrue(reminder.via_email)
        self.assertFalse(reminder.via_whatsapp)

    def test_urgent_forces_high_priority_and_email(self):
        result = self._execute(
            {"content": "Exam", "time": "in 2 hours", "urgent": True, "delivery": "in_app"}
        )
        reminder = Reminder.objects.get(id=result["reminder_id"])
        self.assertEqual(reminder.priority, "high")
        self.assertTrue(reminder.via_email)

    def test_duplicate_reminder_updates_instead_of_stacking(self):
        first = self._execute({"content": "Same reminder", "time": "in 2 hours"})
        second = self._execute({"content": "Same reminder", "time": "in 2 hours"})
        self.assertEqual(first["reminder_id"], second["reminder_id"])
        self.assertEqual(
            Reminder.objects.filter(user=self.user, content="Same reminder").count(), 1
        )

    def test_list_reminders_returns_rows(self):
        self._execute({"content": "List me", "time": "in 2 hours"})
        result = asyncio.run(
            self._list_reminders_via_connector()
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["reminders"][0]["content"], "List me")

    def _list_reminders_via_connector(self):
        from orchestration.tool_router import ReminderConnector

        async def _run():
            return await ReminderConnector().execute(
                {"action": "list_reminders"},
                {"user_id": self.user.id},
            )

        return _run()
