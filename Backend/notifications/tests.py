"""Tests for the unified notification service."""
from asgiref.sync import async_to_sync
from unittest.mock import AsyncMock, patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from orchestration.user_preferences import _normalize_notify_matrix

User = get_user_model()


class NotifyMatrixNormalizationTests(SimpleTestCase):
    """Test that the preference matrix normalizer fills defaults correctly."""

    def test_empty_input_returns_all_defaults(self):
        result = _normalize_notify_matrix(None)
        self.assertIn("payment.deposit", result)
        self.assertIn("message.unread", result)
        # Defaults for message.unread: in_app=True, email=False, whatsapp=False
        self.assertTrue(result["message.unread"]["in_app"])
        self.assertFalse(result["message.unread"]["email"])
        self.assertFalse(result["message.unread"]["whatsapp"])

    def test_partial_override_preserves_defaults(self):
        result = _normalize_notify_matrix({
            "payment.deposit": {"email": False},
        })
        # email overridden to False
        self.assertFalse(result["payment.deposit"]["email"])
        # in_app still defaults to True
        self.assertTrue(result["payment.deposit"]["in_app"])
        # Other event types still have full defaults
        self.assertTrue(result["reminder.due"]["in_app"])

    def test_unknown_event_type_ignored(self):
        result = _normalize_notify_matrix({
            "unknown.event": {"in_app": True},
        })
        self.assertNotIn("unknown.event", result)

    def test_string_bool_coercion(self):
        result = _normalize_notify_matrix({
            "payment.error": {"whatsapp": "false", "email": "1"},
        })
        self.assertFalse(result["payment.error"]["whatsapp"])
        self.assertTrue(result["payment.error"]["email"])


class NotificationServiceTests(TestCase):
    """Integration tests for NotificationService.notify."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="testnotif", email="t@example.com", password="pass"  # nosec B106 — test fixture — fake credential
        )

    @patch("notifications.services.NotificationService._push_ws")
    def test_notify_creates_notification(self, mock_ws):
        from notifications.models import Notification
        from notifications.services import NotificationService

        n = NotificationService.notify(
            user=self.user,
            event_type="payment.deposit",
            title="Deposit OK",
            body="500 KES",
            severity="success",
        )
        self.assertIsNotNone(n)
        self.assertEqual(n.event_type, "payment.deposit")
        self.assertEqual(n.title, "Deposit OK")
        self.assertFalse(n.is_read)
        self.assertEqual(Notification.objects.filter(user=self.user).count(), 1)

    @patch("notifications.services.NotificationService._push_ws")
    def test_notify_respects_in_app_false(self, mock_ws):
        from notifications.models import Notification
        from notifications.services import NotificationService

        # Set user preference to disable in_app for system.info
        profile = self.user.profile
        profile.notification_preferences = {
            "notify_matrix": {"system.info": {"in_app": False, "email": False, "whatsapp": False}}
        }
        profile.save()

        n = NotificationService.notify(
            user=self.user,
            event_type="system.info",
            title="Test",
        )
        self.assertIsNone(n)
        self.assertEqual(
            Notification.objects.filter(user=self.user, event_type="system.info").count(),
            0,
        )


class WhatsAppDeliveryTaskTests(TestCase):
    """deliver_notification_whatsapp must await the async connector execute()."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="wauser", email="wa@example.com", password="pass"  # nosec B106 — test fixture — fake credential
        )
        profile = cls.user.profile
        profile.notification_preferences = {"phone_number": "+254712345678"}
        profile.save()

    @patch(
        "orchestration.connectors.whatsapp_connector.WhatsAppConnector.execute",
        new_callable=AsyncMock,
    )
    def test_successful_send_marks_delivered(self, mock_execute):
        from notifications.models import Notification
        from notifications.tasks import deliver_notification_whatsapp

        notification = Notification.objects.create(
            user=self.user, event_type="workflow.approval", title="Approval", body="Hi",
        )
        mock_execute.return_value = {"status": "sent"}

        deliver_notification_whatsapp.run(notification.id, self.user.id, "workflow.approval", "Approval", "Hi")

        mock_execute.assert_called_once()
        params = mock_execute.call_args[0][0]
        self.assertEqual(params["action"], "send_whatsapp")
        self.assertEqual(params["phone_number"], "+254712345678")
        notification.refresh_from_db()
        self.assertTrue(notification.delivered_whatsapp)


class NotificationCenterViewTests(TestCase):
    """The /notifications/ page renders the human-facing inbox."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="notifcenter", email="nc@example.com", password="pass"  # nosec B106 — test fixture — fake credential
        )
        from notifications.models import Notification

        for index in range(25):
            Notification.objects.create(
                user=cls.user,
                event_type="system.info" if index % 2 == 0 else "payment.deposit",
                title=f"Item {index}",
                body=f"Body {index}",
                severity="info" if index % 2 == 0 else "success",
                is_read=index < 5,
            )

    def test_requires_login(self):
        response = self.client.get("/notifications/")
        self.assertRedirects(
            response, "/accounts/login/?next=/notifications/", fetch_redirect_response=False
        )

    def test_renders_notifications_newest_first(self):
        self.client.force_login(self.user)
        response = self.client.get("/notifications/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Item 24")
        self.assertNotContains(response, "Item 0")  # page 1 shows latest 20

    def test_pagination_exposes_older_pages(self):
        self.client.force_login(self.user)
        response = self.client.get("/notifications/?page=2")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Item 4")
        self.assertContains(response, "Page 2 of 2")

    def test_unread_filter_shows_only_unread(self):
        self.client.force_login(self.user)
        response = self.client.get("/notifications/?unread=1")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Item 20")
        self.assertNotContains(response, "Item 4")  # read items hidden

    def test_invalid_event_type_falls_back_to_all(self):
        self.client.force_login(self.user)
        response = self.client.get("/notifications/?event_type=not.a.type")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Item 24")
        self.assertContains(response, "Item 23")

    def test_dismissed_notifications_are_hidden(self):
        from notifications.models import Notification

        latest = Notification.objects.filter(user=self.user).first()
        latest.is_dismissed = True
        latest.save()
        self.client.force_login(self.user)
        response = self.client.get("/notifications/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Item 24")

    def test_related_room_links_to_chat(self):
        from chatbot.models import Chatroom
        from notifications.models import Notification

        room = Chatroom.objects.create()
        Notification.objects.create(
            user=self.user,
            event_type="message.unread",
            title="New message",
            body="Someone said hi",
            severity="info",
            related_room=room,
        )
        self.client.force_login(self.user)
        response = self.client.get("/notifications/?unread=1")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"/chatbot/home/{room.id}/")


class NotificationConsumerRedisOutageTests(SimpleTestCase):
    """F7.2: the notification socket must accept degraded when the channel
    layer (Redis) is down, and disconnect must not raise either."""

    def _make_consumer(self):
        from notifications.consumers import NotificationConsumer

        consumer = NotificationConsumer()
        consumer.scope = {"user": MagicMock(is_authenticated=True, id=42)}
        consumer.channel_layer = MagicMock()
        consumer.channel_name = "test-channel"
        consumer.accept = AsyncMock()
        consumer.close = AsyncMock()
        consumer.send_json = AsyncMock()
        return consumer

    def test_connect_accepts_when_channel_layer_down(self):
        async_to_sync(self._connect_channel_layer_down)()

    async def _connect_channel_layer_down(self):
        consumer = self._make_consumer()
        consumer.channel_layer.group_add = AsyncMock(side_effect=ConnectionError("redis down"))

        with patch(
            "notifications.services.NotificationService.aget_unread_count",
            new=AsyncMock(return_value=0),
        ):
            await consumer.connect()

        consumer.accept.assert_awaited_once()
        consumer.close.assert_not_awaited()
        init = consumer.send_json.await_args[0][0]
        self.assertEqual(init["type"], "init")

    def test_disconnect_swallows_group_discard_failure(self):
        async_to_sync(self._disconnect_with_dead_layer)()

    async def _disconnect_with_dead_layer(self):
        consumer = self._make_consumer()
        consumer.group_name = "notifications_42"
        consumer.channel_layer.group_discard = AsyncMock(side_effect=ConnectionError("redis down"))

        await consumer.disconnect(1001)

        consumer.channel_layer.group_discard.assert_awaited_once()
