"""Tests for the unified notification service."""
from unittest.mock import patch, MagicMock

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
            username="testnotif", email="t@example.com", password="pass"
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
