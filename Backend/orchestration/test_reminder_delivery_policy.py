"""
Tests for the reminder delivery policy (v0.6): presence-based routing,
delivery modes, urgent retries/dead-letter, timezone fallback, dedupe,
and the list_reminders/list_notifications tools.
"""
import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from chatbot.models import Chatroom, Reminder
from chatbot import tasks as chatbot_tasks

User = get_user_model()

FUTURE = timezone.now() + timedelta(hours=2)


class _FreeRedis:
    def __init__(self):
        self.deleted = []

    def set(self, *args, **kwargs):
        return True

    def delete(self, *args, **kwargs):
        self.deleted.append(args[0])
        return True


class _HeldRedis:
    def set(self, *args, **kwargs):
        return None

    def delete(self, *args, **kwargs):
        return True


class _ClaimRedis:
    def __init__(self):
        self.store = {}
        self.set_calls = 0

    def set(self, key, value, nx=False, ex=None):
        self.set_calls += 1
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    def delete(self, *args, **kwargs):
        self.store.pop(args[0], None)
        return True


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

    def test_in_app_mode_fails_when_notification_fails(self):
        reminder = self._reminder(via_email=False, via_whatsapp=False)
        with patch("notifications.services.NotificationService.notify",
                   side_effect=RuntimeError("db down")):
            ok, channel = chatbot_tasks._deliver_reminder(reminder)
        self.assertEqual((ok, channel), (False, "in_app"))

    def test_retry_attempt_skips_in_app_notification(self):
        reminder = self._reminder(via_email=True, via_whatsapp=False)
        with patch("notifications.services.NotificationService.notify") as mock_notify, \
             patch.object(chatbot_tasks, "_send_reminder_email", return_value=False):
            ok, channel = chatbot_tasks._deliver_reminder(reminder, create_notification=False)
        self.assertEqual((ok, channel), (False, "email"))
        mock_notify.assert_not_called()

    def test_notification_recreated_on_in_app_retries(self):
        in_app = self._reminder(via_email=False, via_whatsapp=False)
        self.assertTrue(chatbot_tasks._should_create_notification(in_app, 0))
        self.assertTrue(chatbot_tasks._should_create_notification(in_app, 2))
        email_mode = self._reminder(via_email=True, via_whatsapp=False)
        self.assertTrue(chatbot_tasks._should_create_notification(email_mode, 0))
        self.assertFalse(chatbot_tasks._should_create_notification(email_mode, 2))

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

    @override_settings(GMAIL_OAUTH_CLIENT_ID=None, GMAIL_OAUTH_CLIENT_SECRET=None)
    def test_email_mock_response_not_counted_as_delivered(self):
        reminder = self._reminder(via_email=True, via_whatsapp=False)
        with patch(
            "orchestration.connectors.mailgun_connector.MailgunConnector.execute",
            new=AsyncMock(return_value={"status": "success", "mock": True}),
        ):
            self.assertFalse(chatbot_tasks._send_reminder_email(reminder))

    def test_online_check_uses_the_presence_window(self):
        from chatbot import presence

        presence._local_rooms.clear()
        presence._local_users.clear()
        presence._skip_until = 0.0
        clock = [1000.0]
        with patch.object(presence, "_now", new=lambda: clock[0]):
            asyncio.run(presence.connect(901, self.user.id, "conn"))
            self.assertTrue(chatbot_tasks._is_user_online(self.user))
            clock[0] += 76
            self.assertFalse(chatbot_tasks._is_user_online(self.user))

    def test_whatsapp_mode_uses_sync_send_with_profile_phone(self):
        reminder = self._reminder(via_email=False, via_whatsapp=True)
        profile = self.user.profile
        profile.notification_preferences = {"phone_number": "+254700000000"}
        profile.save(update_fields=["notification_preferences"])
        with (
            patch("notifications.services.NotificationService.notify"),
            patch(
                "orchestration.connectors.whatsapp_connector.WhatsAppConnector._send_message_sync",
                return_value={"status": "sent"},
            ) as mock_send,
        ):
            ok, channel = chatbot_tasks._deliver_reminder(reminder)
        self.assertEqual((ok, channel), (True, "whatsapp"))
        self.assertEqual(mock_send.call_args.args[0], "+254700000000")

    def test_dead_letter_marks_failed_and_notifies(self):
        reminder = self._reminder(priority="high")
        with patch("notifications.services.NotificationService.notify") as mock_notify:
            chatbot_tasks._finalize_failed_delivery(reminder, attempts=5, channel="email")
        reminder.refresh_from_db()
        self.assertEqual(reminder.status, "failed")
        self.assertIn("dead letter", reminder.error_log)
        mock_notify.assert_called_once()

    def test_delivery_save_survives_passed_scheduled_time(self):
        reminder = self._reminder()
        Reminder.objects.filter(pk=reminder.pk).update(
            scheduled_time=timezone.now() - timedelta(hours=1)
        )
        reminder.refresh_from_db()
        with patch.object(chatbot_tasks, "_deliver_reminder", return_value=(True, "chat")):
            result = chatbot_tasks.send_reminder.run(reminder.id)
        self.assertEqual(result["status"], "sent")
        reminder.refresh_from_db()
        self.assertEqual(reminder.status, "sent")
        self.assertIsNotNone(reminder.sent_at)

    def test_dead_letter_survives_passed_scheduled_time(self):
        reminder = self._reminder(priority="high")
        Reminder.objects.filter(pk=reminder.pk).update(
            scheduled_time=timezone.now() - timedelta(hours=1)
        )
        reminder.refresh_from_db()
        with patch.object(chatbot_tasks.send_reminder, "max_retries", 0), \
             patch.object(chatbot_tasks, "_deliver_reminder", return_value=(False, "email")), \
             patch("notifications.services.NotificationService.notify"), \
             patch("django_redis.get_redis_connection", return_value=_FreeRedis()):
            result = chatbot_tasks.send_reminder.run(reminder.id)
        self.assertEqual(result["status"], "dead_letter")
        reminder.refresh_from_db()
        self.assertEqual(reminder.status, "failed")
        self.assertIn("dead letter", reminder.error_log)

    def test_send_reminder_skips_when_claim_already_held(self):
        reminder = self._reminder()
        Reminder.objects.filter(pk=reminder.pk).update(
            scheduled_time=timezone.now() - timedelta(hours=1)
        )
        reminder.refresh_from_db()
        with patch("django_redis.get_redis_connection", return_value=_HeldRedis()), \
             patch.object(chatbot_tasks, "_deliver_reminder") as mock_deliver:
            result = chatbot_tasks.send_reminder.run(reminder.id)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "delivery_in_progress")
        mock_deliver.assert_not_called()

    def test_send_reminder_releases_claim_after_delivery(self):
        reminder = self._reminder()
        Reminder.objects.filter(pk=reminder.pk).update(
            scheduled_time=timezone.now() - timedelta(hours=1)
        )
        reminder.refresh_from_db()
        fake = _FreeRedis()
        with patch("django_redis.get_redis_connection", return_value=fake), \
             patch.object(chatbot_tasks, "_deliver_reminder", return_value=(True, "chat")), \
             patch("notifications.services.NotificationService.notify"):
            result = chatbot_tasks.send_reminder.run(reminder.id)
        self.assertEqual(result["status"], "sent")
        self.assertIn(f"reminder_claim:{reminder.id}", fake.deleted)

    def test_send_reminder_reschedules_within_the_final_minute(self):
        reminder = self._reminder(via_email=True, via_whatsapp=False)
        soon = timezone.now() + timedelta(seconds=30)
        Reminder.objects.filter(pk=reminder.pk).update(scheduled_time=soon)
        reminder.refresh_from_db()
        with patch.object(chatbot_tasks, "schedule_reminder_delivery") as mock_sched, \
             patch.object(chatbot_tasks, "_deliver_reminder") as mock_deliver:
            result = chatbot_tasks.send_reminder.run(reminder.id)
        self.assertEqual(result["status"], "rescheduled")
        mock_sched.assert_called_once()
        mock_deliver.assert_not_called()

    def test_retry_skips_claim_and_redelivers(self):
        reminder = self._reminder(priority="high")
        Reminder.objects.filter(pk=reminder.pk).update(
            scheduled_time=timezone.now() - timedelta(hours=1)
        )
        reminder.refresh_from_db()
        redis = _ClaimRedis()
        with override_settings(CELERY_TASK_ALWAYS_EAGER=True), \
             patch("django_redis.get_redis_connection", return_value=redis), \
             patch.object(chatbot_tasks, "_deliver_reminder",
                          side_effect=[(False, "email"), (True, "email")]) as mock_deliver, \
             patch("notifications.services.NotificationService.notify"):
            chatbot_tasks.send_reminder.apply(args=[reminder.id], retries=0)
        self.assertEqual(redis.set_calls, 1)
        self.assertEqual(mock_deliver.call_count, 2)
        reminder.refresh_from_db()
        self.assertEqual(reminder.status, "sent")
        reminder.refresh_from_db()
        self.assertEqual(reminder.status, "sent")


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

    def test_urgent_with_auto_delivery_resolves_email_only(self):
        result = self._execute(
            {"content": "Flight", "time": "in 2 hours", "urgent": True, "delivery": "auto"}
        )
        reminder = Reminder.objects.get(id=result["reminder_id"])
        self.assertTrue(reminder.via_email)
        self.assertFalse(reminder.via_whatsapp)
        self.assertEqual(result["delivery"], "email")

    def test_duplicate_update_persists_new_scheduled_time(self):
        from orchestration.tool_router import ReminderConnector
        later = FUTURE + timedelta(seconds=30)
        with patch("chatbot.reminder_service.LLMTimeParser") as mock_parser:
            mock_parser.return_value.parse = AsyncMock(side_effect=[
                {"datetime": FUTURE.isoformat(), "needs_clarification": False, "confidence": 0.9, "interpretation": "x"},
                {"datetime": later.isoformat(), "needs_clarification": False, "confidence": 0.9, "interpretation": "x"},
            ])
            first = asyncio.run(ReminderConnector().execute(
                {"content": "Twin", "time": "t"}, {"user_id": self.user.id, "room_id": None},
            ))
            second = asyncio.run(ReminderConnector().execute(
                {"content": "Twin", "time": "t"}, {"user_id": self.user.id, "room_id": None},
            ))
        self.assertEqual(first["reminder_id"], second["reminder_id"])
        reminder = Reminder.objects.get(id=first["reminder_id"])
        self.assertAlmostEqual(reminder.scheduled_time.timestamp(), later.timestamp(), delta=1)

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
