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
_DEFAULT_ROOM = object()


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

    def _reminder(self, via_email=False, via_whatsapp=False, priority="medium", room=_DEFAULT_ROOM):
        return Reminder.objects.create(
            user=self.user,
            room=self.room if room is _DEFAULT_ROOM else room,
            content="Pack bags",
            scheduled_time=FUTURE,
            priority=priority,
            via_email=via_email,
            via_whatsapp=via_whatsapp,
        )

    def test_auto_offline_still_delivers_in_chat(self):
        reminder = self._reminder(via_email=True, via_whatsapp=True)
        with patch.object(chatbot_tasks, "_is_user_online", return_value=False), \
             patch.object(chatbot_tasks, "_deliver_reminder_to_chat", return_value=True), \
             patch.object(chatbot_tasks, "_send_reminder_telegram", return_value=False) as mock_tg, \
             patch.object(chatbot_tasks, "_send_reminder_email", return_value=False) as mock_email, \
             patch("notifications.services.NotificationService.notify") as mock_notify:
            ok, channel = chatbot_tasks._deliver_reminder(reminder)
        self.assertEqual((ok, channel), (True, "chat"))
        mock_tg.assert_not_called()
        mock_email.assert_not_called()
        mock_notify.assert_called_once()

    def test_auto_room_failure_does_not_fall_back_to_extras(self):
        reminder = self._reminder(via_email=True, via_whatsapp=True)
        with patch.object(chatbot_tasks, "_is_user_online", return_value=False), \
             patch.object(chatbot_tasks, "_deliver_reminder_to_chat", return_value=False) as mock_chat, \
             patch.object(chatbot_tasks, "_send_reminder_telegram", return_value=True) as mock_tg, \
             patch.object(chatbot_tasks, "_send_reminder_email", return_value=True) as mock_email:
            ok, channel = chatbot_tasks._deliver_reminder(reminder)
        self.assertEqual((ok, channel), (False, "chat"))
        mock_chat.assert_called_once()
        mock_tg.assert_not_called()
        mock_email.assert_not_called()

    def test_auto_offline_adds_telegram_as_an_extra_channel(self):
        reminder = self._reminder(via_email=True, via_whatsapp=True)
        with patch.object(chatbot_tasks, "_is_user_online", return_value=False):
            self.assertEqual(
                chatbot_tasks._extra_channels_for_reminder(reminder),
                ["email", "telegram"],
            )
        with patch.object(chatbot_tasks, "_is_user_online", return_value=True):
            self.assertEqual(
                chatbot_tasks._extra_channels_for_reminder(reminder),
                ["email"],
            )

    def test_in_app_mode_without_room_uses_notification(self):
        reminder = self._reminder(via_email=False, via_whatsapp=False, room=None)
        with patch("notifications.services.NotificationService.notify") as mock_notify:
            ok, channel = chatbot_tasks._deliver_reminder(reminder)
        self.assertEqual((ok, channel), (True, "in_app"))
        mock_notify.assert_called_once()

    def test_in_app_mode_fails_when_notification_fails(self):
        reminder = self._reminder(via_email=False, via_whatsapp=False, room=None)
        with patch("notifications.services.NotificationService.notify",
                   side_effect=RuntimeError("db down")):
            ok, channel = chatbot_tasks._deliver_reminder(reminder)
        self.assertEqual((ok, channel), (False, "in_app"))

    def test_retry_attempt_skips_in_app_notification(self):
        reminder = self._reminder(via_email=True, via_whatsapp=False)
        with patch("notifications.services.NotificationService.notify") as mock_notify, \
             patch.object(chatbot_tasks, "_deliver_reminder_to_chat", return_value=True):
            ok, channel = chatbot_tasks._deliver_reminder(reminder, create_notification=False)
        self.assertEqual((ok, channel), (True, "chat"))
        mock_notify.assert_not_called()

    def test_notification_recreated_on_in_app_retries(self):
        in_app = self._reminder(via_email=False, via_whatsapp=False)
        self.assertTrue(chatbot_tasks._should_create_notification(in_app, 0))
        self.assertTrue(chatbot_tasks._should_create_notification(in_app, 2))
        email_mode = self._reminder(via_email=True, via_whatsapp=False)
        self.assertTrue(chatbot_tasks._should_create_notification(email_mode, 0))
        self.assertFalse(chatbot_tasks._should_create_notification(email_mode, 2))

    def test_explicit_email_mode_still_posts_to_chat(self):
        reminder = self._reminder(via_email=True, via_whatsapp=False)
        with patch.object(chatbot_tasks, "_is_user_online", return_value=False), \
             patch.object(chatbot_tasks, "_deliver_reminder_to_chat", return_value=True) as mock_chat, \
             patch.object(chatbot_tasks, "_send_reminder_email", return_value=True) as mock_email:
            ok, channel = chatbot_tasks._deliver_reminder(reminder)
        self.assertEqual((ok, channel), (True, "chat"))
        mock_chat.assert_called_once()
        mock_email.assert_not_called()

    def test_email_mode_without_room_uses_notification_as_base(self):
        reminder = self._reminder(via_email=True, via_whatsapp=False, room=None)
        with patch("notifications.services.NotificationService.notify") as mock_notify, \
             patch.object(chatbot_tasks, "_send_reminder_email", return_value=False) as mock_email:
            ok, channel = chatbot_tasks._deliver_reminder(reminder)
        self.assertEqual((ok, channel), (True, "in_app"))
        mock_notify.assert_called_once()
        mock_email.assert_not_called()

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
        with patch(
            "orchestration.connectors.whatsapp_connector.WhatsAppConnector._send_message_sync",
            return_value={"status": "sent"},
        ) as mock_send:
            self.assertTrue(chatbot_tasks._send_reminder_whatsapp(reminder))
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
             patch.object(chatbot_tasks.cache, "add", return_value=True):
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
        with patch.object(chatbot_tasks.cache, "add", return_value=False), \
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
        with patch.object(chatbot_tasks.cache, "add", return_value=True), \
             patch.object(chatbot_tasks.cache, "delete") as released, \
             patch.object(chatbot_tasks, "_deliver_reminder", return_value=(True, "chat")), \
             patch("notifications.services.NotificationService.notify"):
            result = chatbot_tasks.send_reminder.run(reminder.id)
        self.assertEqual(result["status"], "sent")
        released.assert_called_with(f"reminder_claim:{reminder.id}")

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
        with override_settings(CELERY_TASK_ALWAYS_EAGER=True), \
             patch.object(chatbot_tasks.cache, "add", return_value=True) as claim, \
             patch.object(chatbot_tasks, "_deliver_reminder",
                          side_effect=[(False, "email"), (True, "email")]) as mock_deliver, \
             patch("notifications.services.NotificationService.notify"):
            chatbot_tasks.send_reminder.apply(args=[reminder.id], retries=0)
        self.assertEqual(claim.call_count, 1)
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

    @override_settings(GMAIL_OAUTH_CLIENT_ID=None, GMAIL_OAUTH_CLIENT_SECRET=None)
    def test_auto_delivery_sets_both_flags_when_email_is_ready(self):
        import os

        with _NoChannelConfig(), patch.dict(os.environ, {
            "MAILGUN_API_KEY": "fake-key",
            "MAILGUN_DOMAIN": "example.com",
        }):
            result = self._execute(
                {"content": "Pack bags", "time": "in 2 hours", "delivery": "auto"}
            )
        reminder = Reminder.objects.get(id=result["reminder_id"])
        self.assertTrue(reminder.via_email)
        self.assertTrue(reminder.via_whatsapp)
        self.assertEqual(result["delivery"], "auto")

    @override_settings(GMAIL_OAUTH_CLIENT_ID=None, GMAIL_OAUTH_CLIENT_SECRET=None)
    def test_explicit_email_delivery_sets_email_flag_when_ready(self):
        import os

        with _NoChannelConfig(), patch.dict(os.environ, {
            "MAILGUN_API_KEY": "fake-key",
            "MAILGUN_DOMAIN": "example.com",
        }):
            result = self._execute(
                {"content": "Pack bags", "time": "in 2 hours", "delivery": "email"}
            )
        reminder = Reminder.objects.get(id=result["reminder_id"])
        self.assertTrue(reminder.via_email)
        self.assertFalse(reminder.via_whatsapp)
        self.assertEqual(result["delivery"], "email")

    @override_settings(GMAIL_OAUTH_CLIENT_ID=None, GMAIL_OAUTH_CLIENT_SECRET=None)
    def test_urgent_without_email_keeps_high_priority_and_chat_only(self):
        with _NoChannelConfig():
            result = self._execute(
                {"content": "Exam", "time": "in 2 hours", "urgent": True, "delivery": "in_app"}
            )
        reminder = Reminder.objects.get(id=result["reminder_id"])
        self.assertEqual(reminder.priority, "high")
        self.assertFalse(reminder.via_email)
        self.assertFalse(reminder.via_whatsapp)

    @override_settings(GMAIL_OAUTH_CLIENT_ID=None, GMAIL_OAUTH_CLIENT_SECRET=None)
    def test_urgent_without_email_resolves_chat_only(self):
        with _NoChannelConfig():
            result = self._execute(
                {"content": "Flight", "time": "in 2 hours", "urgent": True, "delivery": "auto"}
            )
        reminder = Reminder.objects.get(id=result["reminder_id"])
        self.assertFalse(reminder.via_email)
        self.assertFalse(reminder.via_whatsapp)
        self.assertEqual(result["delivery"], "in_app")

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


_CHANNEL_ENV = (
    "MAILGUN_API_KEY",
    "MAILGUN_DOMAIN",
    "MAILGUN_USE_SANDBOX",
    "MAILGUN_API_KEY_SANDBOX",
    "MAILGUN_DOMAIN_SANDBOX",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_FROM_NUMBER",
    "TELEGRAM_BOT_TOKEN",
    "KAZI_DEMO_MODE",
)


class _NoChannelConfig:
    """Remove every real channel credential for the duration of a test."""

    def __enter__(self):
        import os

        self._saved = {name: os.environ.get(name) for name in _CHANNEL_ENV}
        for name in _CHANNEL_ENV:
            os.environ.pop(name, None)
        return self

    def __exit__(self, *exc):
        import os

        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        return False


class ReminderChatFirstDeliveryTests(TransactionTestCase):
    """D1: the room is the delivery, regardless of presence or mode."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="chat-first-user", email="cf@example.com", password="secret",  # nosec B106 — test fixture — fake credential
        )
        self.room = Chatroom.objects.create()

    def _reminder(self, via_email, via_whatsapp, priority="medium"):
        reminder = Reminder.objects.create(
            user=self.user,
            room=self.room,
            content="Stretch",
            scheduled_time=FUTURE,
            priority=priority,
            via_email=via_email,
            via_whatsapp=via_whatsapp,
        )
        Reminder.objects.filter(pk=reminder.pk).update(
            scheduled_time=timezone.now() - timedelta(minutes=1)
        )
        reminder.refresh_from_db()
        return reminder

    def _send(self, reminder):
        with _NoChannelConfig(), \
             patch.object(chatbot_tasks, "_is_user_online", return_value=False), \
             patch("notifications.services.NotificationService.notify"):
            return chatbot_tasks.send_reminder.run(reminder.id)

    @override_settings(IN_PROCESS_SCHEDULER=True)
    def test_auto_offline_without_email_keys_still_posts_to_the_room(self):
        reminder = self._reminder(via_email=True, via_whatsapp=True)
        result = self._send(reminder)
        self.assertEqual(result["status"], "sent")
        reminder.refresh_from_db()
        self.assertEqual(reminder.status, "sent")
        self.assertNotIn("dead letter", reminder.error_log)
        self.assertEqual(self.room.chats.count(), 1)

    @override_settings(IN_PROCESS_SCHEDULER=True)
    def test_in_app_mode_with_a_room_still_posts_to_the_room(self):
        reminder = self._reminder(via_email=False, via_whatsapp=False)
        result = self._send(reminder)
        self.assertEqual(result["status"], "sent")
        reminder.refresh_from_db()
        self.assertEqual(reminder.status, "sent")
        self.assertEqual(self.room.chats.count(), 1)


class ReminderCreationNamesChannelsTests(TransactionTestCase):
    """D2: creation only asks for channels that are ready and says which."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="channel-reply-user", email="cr@example.com", password="secret",  # nosec B106 — test fixture — fake credential
        )
        self.room = Chatroom.objects.create()

    def _execute(self, parameters):
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
                    {"user_id": self.user.id, "room_id": self.room.id},
                )
            )

    @override_settings(GMAIL_OAUTH_CLIENT_ID=None, GMAIL_OAUTH_CLIENT_SECRET=None)
    def test_default_delivery_names_chat_and_stores_chat_only(self):
        with _NoChannelConfig():
            result = self._execute({"content": "Stretch", "time": "in 2 hours"})
        self.assertEqual(result["status"], "success")
        self.assertIn("chat", result["message"].lower())
        self.assertEqual(result["delivery"], "in_app")
        self.assertIn("chat", result["channels"])
        reminder = Reminder.objects.get(id=result["reminder_id"])
        self.assertFalse(reminder.via_email)
        self.assertFalse(reminder.via_whatsapp)

    @override_settings(GMAIL_OAUTH_CLIENT_ID=None, GMAIL_OAUTH_CLIENT_SECRET=None)
    def test_urgent_email_without_keys_says_email_is_not_set_up(self):
        with _NoChannelConfig():
            result = self._execute({
                "content": "Flight",
                "time": "in 2 hours",
                "delivery": "email",
                "urgent": True,
            })
        self.assertEqual(result["status"], "success")
        self.assertIn("email isn't set up", result["message"].lower())
        self.assertIn("chat", result["message"].lower())
        reminder = Reminder.objects.get(id=result["reminder_id"])
        self.assertEqual(reminder.priority, "high")
        self.assertFalse(reminder.via_email)
        self.assertFalse(reminder.via_whatsapp)

    @override_settings(GMAIL_OAUTH_CLIENT_ID=None, GMAIL_OAUTH_CLIENT_SECRET=None)
    def test_mailgun_keys_name_chat_and_email(self):
        import os

        with _NoChannelConfig(), patch.dict(os.environ, {
            "MAILGUN_API_KEY": "fake-key",
            "MAILGUN_DOMAIN": "example.com",
        }):
            result = self._execute({"content": "Water", "time": "in 2 hours"})
        self.assertEqual(result["status"], "success")
        self.assertIn("chat", result["message"].lower())
        self.assertIn("email", result["message"].lower())
        self.assertEqual(result["delivery"], "auto")
        self.assertIn("email", result["channels"])
        reminder = Reminder.objects.get(id=result["reminder_id"])
        self.assertTrue(reminder.via_email)

    @override_settings(GMAIL_OAUTH_CLIENT_ID=None, GMAIL_OAUTH_CLIENT_SECRET=None)
    def test_demo_mode_mock_is_never_ready(self):
        import os

        from chatbot.reminder_channels import channel_ready

        with _NoChannelConfig(), patch.dict(os.environ, {
            "KAZI_DEMO_MODE": "true",
            "MAILGUN_API_KEY": "fake-key",
            "MAILGUN_DOMAIN": "example.com",
        }):
            ready, reason = channel_ready(self.user, "email")
        self.assertFalse(ready)
        self.assertIn("demo", reason.lower())


class ReminderExtraChannelFailureTests(TransactionTestCase):
    """D3: extras never take the row down after the room got the reminder."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="extra-failure-user", email="ef@example.com", password="secret",  # nosec B106 — test fixture — fake credential
        )
        self.room = Chatroom.objects.create()

    def _reminder(self, via_email, via_whatsapp=False, priority="medium"):
        reminder = Reminder.objects.create(
            user=self.user,
            room=self.room,
            content="Drink water",
            scheduled_time=FUTURE,
            priority=priority,
            via_email=via_email,
            via_whatsapp=via_whatsapp,
        )
        Reminder.objects.filter(pk=reminder.pk).update(
            scheduled_time=timezone.now() - timedelta(minutes=1)
        )
        reminder.refresh_from_db()
        return reminder

    def _mailgun(self):
        import os

        return patch.dict(os.environ, {
            "MAILGUN_API_KEY": "fake-key",
            "MAILGUN_DOMAIN": "example.com",
            "TWILIO_ACCOUNT_SID": "fake-sid",
            "TWILIO_AUTH_TOKEN": "fake-token",  # nosec B105 — test fixture — fake credential
        })

    @override_settings(IN_PROCESS_SCHEDULER=True, GMAIL_OAUTH_CLIENT_ID=None, GMAIL_OAUTH_CLIENT_SECRET=None)
    def test_failed_email_is_written_with_sent_status(self):
        reminder = self._reminder(via_email=True)
        with _NoChannelConfig(), self._mailgun(), \
             patch.object(chatbot_tasks, "_is_user_online", return_value=False), \
             patch.object(chatbot_tasks, "_send_reminder_email", return_value=False), \
             patch("notifications.services.NotificationService.notify"):
            result = chatbot_tasks.send_reminder.run(reminder.id)
        self.assertEqual(result["status"], "sent")
        reminder.refresh_from_db()
        self.assertEqual(reminder.status, "sent")
        self.assertIn("email", reminder.error_log.lower())
        self.assertIn("delivered in chat", reminder.error_log.lower())
        self.assertEqual(self.room.chats.count(), 1)

    @override_settings(IN_PROCESS_SCHEDULER=True, GMAIL_OAUTH_CLIENT_ID=None, GMAIL_OAUTH_CLIENT_SECRET=None)
    def test_urgent_email_retries_then_reports_chat_delivery(self):
        reminder = self._reminder(via_email=True, priority="high")
        with _NoChannelConfig(), self._mailgun(), \
             patch.object(chatbot_tasks, "_is_user_online", return_value=False), \
             patch.object(chatbot_tasks, "_send_reminder_email", return_value=False) as send_email, \
             patch("notifications.services.NotificationService.notify"):
            result = chatbot_tasks.send_reminder.run(reminder.id)
        self.assertEqual(result["status"], "sent")
        self.assertEqual(send_email.call_count, 6)
        reminder.refresh_from_db()
        self.assertEqual(reminder.status, "sent")
        self.assertIn("after 6 attempt(s)", reminder.error_log.lower())
        self.assertIn("delivered in chat", reminder.error_log.lower())
        self.assertEqual(self.room.chats.count(), 1)

    @override_settings(GMAIL_OAUTH_CLIENT_ID=None, GMAIL_OAUTH_CLIENT_SECRET=None)
    def test_whatsapp_mock_reply_is_not_delivered(self):
        reminder = self._reminder(via_email=False, via_whatsapp=True)
        profile = self.user.profile
        profile.notification_preferences = {"phone_number": "+254700000000"}
        profile.save(update_fields=["notification_preferences"])
        with _NoChannelConfig(), self._mailgun(), patch(
            "orchestration.connectors.whatsapp_connector.WhatsAppConnector._send_message_sync",
            return_value={"status": "sent", "mock": True},
        ):
            self.assertFalse(chatbot_tasks._send_reminder_whatsapp(reminder))
