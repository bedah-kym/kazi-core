"""
Tests for credential-aware email routing and the async-safe reminder
handler (v0.6 local testing fixes).
"""
import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

User = get_user_model()


class EmailRoutingTests(TestCase):
    def setUp(self):
        from orchestration.connector_registry import reset_registry

        reset_registry()

    def tearDown(self):
        from orchestration.connector_registry import reset_registry

        reset_registry()

    def test_mailgun_wins_without_gmail_oauth(self):
        from orchestration.connector_registry import discover_connectors
        from orchestration.connectors.mailgun_connector import MailgunConnector

        connectors = discover_connectors()
        self.assertIsInstance(connectors["send_email"], MailgunConnector)

    @override_settings(GMAIL_OAUTH_CLIENT_ID="client-x", GMAIL_OAUTH_CLIENT_SECRET="secret-y")
    def test_gmail_wins_when_oauth_configured(self):
        from orchestration.connector_registry import discover_connectors
        from orchestration.connectors.gmail_connector import GmailConnector

        connectors = discover_connectors()
        self.assertIsInstance(connectors["send_email"], GmailConnector)


class ReminderConnectorAsyncTests(TransactionTestCase):
    """TransactionTestCase: the reminder handler touches the user's profile
    through sync_to_async's worker thread, which cannot see TestCase's
    wrapping transaction."""

    def test_execute_succeeds_in_async_context(self):
        from chatbot.models import Reminder
        from orchestration.tool_router import ReminderConnector

        user = User.objects.create_user(
            username="rem-user", email="r@example.com", password="secret",  # nosec B106 — test fixture — fake credential
        )
        future = (timezone.now() + timedelta(hours=2)).isoformat()
        with patch("chatbot.reminder_service.LLMTimeParser") as mock_parser:
            mock_parser.return_value.parse = AsyncMock(return_value={
                "datetime": future,
                "needs_clarification": False,
                "confidence": 0.9,
                "interpretation": "tomorrow at 8am",
            })
            result = asyncio.run(
                ReminderConnector().execute(
                    {"content": "Pack bags", "time": "tomorrow at 8am"},
                    {"user_id": user.id, "room_id": None},
                )
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(Reminder.objects.filter(user=user).count(), 1)
