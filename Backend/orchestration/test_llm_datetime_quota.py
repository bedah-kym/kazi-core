"""
Tests for LLM system-time injection and the staff token-quota exemption
(v0.6 local testing fixes).

Covers: the date line prepended to every LLM call path, idempotency of
the injection, and the async-safe superuser budget lookup.
"""
import asyncio
from datetime import datetime, timezone as dt_timezone
from unittest.mock import AsyncMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TransactionTestCase

from orchestration.llm_client import LLMClient, _inject_current_datetime

User = get_user_model()

FIXED_NOW = datetime(2026, 9, 26, 15, 30, tzinfo=dt_timezone.utc)


class DatetimeInjectionTests(SimpleTestCase):
    def test_prepends_date_line(self):
        prompt = _inject_current_datetime("You are helpful.", now=FIXED_NOW)
        self.assertTrue(prompt.startswith("Current date and time:"))
        self.assertIn("2026-09-26", prompt)
        self.assertIn("Saturday", prompt)
        self.assertTrue(prompt.endswith("\nYou are helpful."))

    def test_injection_is_idempotent(self):
        once = _inject_current_datetime("You are helpful.", now=FIXED_NOW)
        twice = _inject_current_datetime(once, now=FIXED_NOW)
        self.assertEqual(once, twice)
        self.assertEqual(once.count("Current date and time:"), 1)

    def test_empty_prompt_untouched(self):
        self.assertEqual(_inject_current_datetime("", now=FIXED_NOW), "")

    def test_generate_text_sends_injected_prompt(self):
        client = LLMClient()
        client.deepseek_key = "k"

        async def run():
            with patch.object(
                client, "_call_huggingface",
                new=AsyncMock(return_value="hello"),
            ) as mock_call:
                await client.generate_text("You are helpful.", "hi", user_id=None)
            sent_system = mock_call.await_args.args[0]
            return sent_system

        sent = asyncio.run(run())
        self.assertTrue(sent.startswith("Current date and time:"))

    def test_create_message_sends_injected_prompt(self):
        client = LLMClient()
        client.deepseek_key = "k"

        async def run():
            with patch.object(
                client, "_create_openai_message",
                new=AsyncMock(return_value={
                    "content": [{"type": "text", "text": "ok"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }),
            ) as mock_call:
                await client.create_message(
                    messages=[], system="You are helpful.", provider="deepseek", user_id=None,
                )
            return mock_call.await_args.kwargs.get("system")

        sent = asyncio.run(run())
        self.assertTrue(sent.startswith("Current date and time:"))


class TokenBudgetExemptionTests(TransactionTestCase):
    """TransactionTestCase: the budget lookup runs its ORM query on the
    sync_to_async worker thread, which cannot see TestCase's wrapping
    transaction."""
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.client = LLMClient()

    def test_superuser_exempt_from_async_context(self):
        user = User.objects.create_superuser(
            username="quota-admin", email="qa@example.com", password="secret",  # nosec B106 — test fixture — fake credential
        )
        budget = asyncio.run(self.client._get_user_token_budget(user.pk))
        self.assertEqual(budget["limit"], 10_000_000)

    def test_regular_user_gets_hourly_limit(self):
        user = User.objects.create_user(
            username="quota-user", email="qu@example.com", password="secret",  # nosec B106 — test fixture — fake credential
        )
        cache.set(f"llm_tokens:{user.pk}", 123, timeout=3600)
        budget = asyncio.run(self.client._get_user_token_budget(user.pk))
        self.assertEqual(budget["limit"], 50000)
        self.assertEqual(budget["used"], 123)
