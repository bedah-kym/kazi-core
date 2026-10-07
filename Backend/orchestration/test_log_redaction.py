"""Log redaction tests (T-S2a): provider bodies and model output stay out of logs."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from django.test import SimpleTestCase

from orchestration.connectors.gmail_connector import GmailConnector
from orchestration.connectors.mailgun_connector import MailgunConnector

FAKE = "fake-token-otter-4471"


def _run(coro):
    return asyncio.run(coro)


class GmailLogRedactionTests(SimpleTestCase):
    def test_send_failure_body_not_logged(self):
        connector = GmailConnector()
        connector.client_id = "client-id"
        connector.client_secret = "client-secret"  # nosec B105 - test fixture
        integration = SimpleNamespace(
            is_connected=True, encrypted_credentials="enc", metadata={},
        )

        async def fake_post(self, url, headers=None, json=None):
            return httpx.Response(500, content=FAKE.encode())

        with (
            patch.object(
                GmailConnector, "_get_integration",
                new=AsyncMock(return_value=integration),
            ),
            patch.object(
                GmailConnector, "_decrypt_credentials",
                return_value={"access_token": "tok", "expires_at": None},  # nosec B105 - test fixture - fake token
            ),
            patch.object(
                GmailConnector, "_get_user_email",
                new=AsyncMock(return_value="me@example.com"),
            ),
            patch.object(httpx.AsyncClient, "post", new=fake_post),
        ):
            with self.assertLogs(level="DEBUG") as captured:
                result = _run(connector.send_email(
                    to="a@b.com", subject="s", text="body",
                    html=None, from_email=None, user_id=1,
                ))

        self.assertEqual(result["status"], "error")
        self.assertNotIn(FAKE, "\n".join(captured.output))

    def test_token_refresh_failure_body_not_logged(self):
        connector = GmailConnector()

        async def fake_post(self, url, data=None, headers=None, json=None):
            return httpx.Response(400, content=FAKE.encode())

        with patch.object(httpx.AsyncClient, "post", new=fake_post):
            with self.assertLogs(level="DEBUG") as captured:
                result = _run(connector._refresh_access_token(
                    integration=SimpleNamespace(),
                    refresh_token="refresh",  # nosec B106 - test fixture - fake credential
                    credentials={},
                ))

        self.assertIsNone(result)
        self.assertNotIn(FAKE, "\n".join(captured.output))


class MailgunLogRedactionTests(SimpleTestCase):
    def test_send_failure_body_not_logged(self):
        connector = MailgunConnector()
        connector.api_key = "api-key"  # nosec B105 - test fixture
        connector.domain = "example.com"
        connector.base_url = "https://api.mailgun.net/v3/example.com"

        async def fake_post(self, url, auth=None, data=None):
            return httpx.Response(500, content=FAKE.encode())

        with patch.object(httpx.AsyncClient, "post", new=fake_post):
            with self.assertLogs(level="DEBUG") as captured:
                result = _run(connector.send_email(
                    to="a@b.com", subject="s", text="body",
                ))

        self.assertIn("error", result)
        self.assertNotIn(FAKE, "\n".join(captured.output))


class LLMClientLogRedactionTests(SimpleTestCase):
    def test_extract_json_failure_does_not_log_model_output(self):
        from orchestration.llm_client import LLMClient

        client = LLMClient()
        with self.assertLogs(level="DEBUG") as captured:
            result = client.extract_json(f"this is not json {FAKE}")

        self.assertEqual(result, {})
        self.assertNotIn(FAKE, "\n".join(captured.output))
