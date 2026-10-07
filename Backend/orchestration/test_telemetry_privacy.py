"""T-S2b: telemetry and router logs carry no tool inputs or provider bodies."""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TransactionTestCase, override_settings

from users.encryption import TokenEncryption
from users.models import CalendlyProfile

FAKE = "fake-token-otter-4471"
User = get_user_model()


@contextmanager
def captured_log_messages():
    """Collect every log record's message while the block runs."""
    messages = []

    class _Handler(logging.Handler):
        def emit(self, record):
            try:
                messages.append(record.getMessage())
            except Exception:
                messages.append(str(record.msg))

    handler = _Handler()
    root = logging.getLogger()
    previous = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield messages
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)


def _run(coro):
    return asyncio.run(coro)


def _text(text):
    return {"type": "text", "text": text}


def _tool_use(name, tool_input):
    return {"type": "tool_use", "id": "t1", "name": name, "input": tool_input}


def _response(blocks, stop_reason):
    return {
        "content": blocks,
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 5, "output_tokens": 5},
    }


class AgentLoopTelemetryPrivacyTests(SimpleTestCase):
    def test_tool_input_values_are_not_written_to_telemetry_or_logs(self):
        from orchestration.agent_loop import run_agent_loop

        payload = {"to": "ops@example.com", "subject": "Report", "text": FAKE}
        mock_llm = MagicMock()
        mock_llm.create_message = AsyncMock(side_effect=[
            _response([_text("Mailing."), _tool_use("send_email", payload)], "tool_use"),
            _response([_text("Sent.")], "end_turn"),
        ])

        async def _collect():
            return [
                event async for event in run_agent_loop(
                    user_message="email the report",
                    context={"user_id": None, "room_id": 1, "username": "test"},
                    preferences={"approval_overrides": {"send_email": "auto"}},
                )
            ]

        with (
            patch("orchestration.agent_loop.get_llm_client", return_value=mock_llm),
            patch(
                "orchestration.agent_loop.execute_tool",
                new=AsyncMock(return_value={"status": "success"}),
            ),
            patch("orchestration.agent_loop.record_event") as mock_record,
            patch("orchestration.agent_loop._record_receipt", new=AsyncMock()),
            patch("orchestration.agent_loop.update_memory_state", new=AsyncMock()),
            patch("orchestration.agent_loop.save_memory_summary", new=AsyncMock()),
            patch("orchestration.agent_loop.cache") as mock_cache,
        ):
            mock_cache.get.return_value = None
            with captured_log_messages() as messages:
                _run(_collect())

        recorded = json.dumps(
            [call.args for call in mock_record.call_args_list], default=str,
        )
        self.assertNotIn(FAKE, recorded)
        self.assertNotIn(FAKE, "\n".join(messages))

        done_payload = next(
            call.args[1] for call in mock_record.call_args_list
            if call.args and call.args[0] == "agent_loop_done"
        )
        entry = done_payload["transcript"][0]
        self.assertEqual(entry["tool"], "send_email")
        self.assertNotIn("input", entry)
        self.assertEqual(entry["input_keys"], ["subject", "text", "to"])
        self.assertEqual(entry["input_chars"], len(json.dumps(payload)))


class RouterLogPrivacyTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="router-privacy", email="example@example.com",
            password="fake-token",  # nosec B106 — test fixture — fake credential
        )
        profile = CalendlyProfile.objects.create(
            user=self.user,
            is_connected=True,
            calendly_user_uri="https://api.calendly.com/users/abc",
        )
        profile.encrypted_access_token = TokenEncryption.encrypt("access-token")
        profile.save(update_fields=["encrypted_access_token"])

    def test_calendly_api_error_body_not_logged(self):
        from orchestration.tool_router import CalendarConnector

        async def fake_get(self, url, headers=None, params=None):
            return httpx.Response(500, content=FAKE.encode())

        with patch.object(httpx.AsyncClient, "get", new=fake_get):
            with captured_log_messages() as messages:
                _run(CalendarConnector().execute(
                    {"action": "check_availability"}, {"user_id": self.user.id},
                ))

        self.assertNotIn(FAKE, "\n".join(messages))

    @override_settings(  # nosec B106 — test fixture — fake credential
        CALENDLY_CLIENT_ID="client-id", CALENDLY_CLIENT_SECRET="secret",
    )
    def test_calendly_token_refresh_body_not_logged(self):
        from orchestration.tool_router import CalendarConnector

        async def fake_post(self, url, data=None):
            return httpx.Response(400, content=FAKE.encode())

        profile = SimpleNamespace(get_refresh_token=lambda: "refresh-token")

        with patch.object(httpx.AsyncClient, "post", new=fake_post):
            with captured_log_messages() as messages:
                _run(CalendarConnector()._refresh_token(profile))

        self.assertNotIn(FAKE, "\n".join(messages))

    @override_settings(OPENWEATHER_API_KEY="key")
    def test_weather_error_body_not_logged(self):
        from orchestration.tool_router import WeatherConnector

        async def fake_get(self, url, params=None):
            return httpx.Response(500, content=FAKE.encode())

        with patch.object(httpx.AsyncClient, "get", new=fake_get):
            with captured_log_messages() as messages:
                _run(WeatherConnector().execute({"city": "Nairobi"}, {}))

        self.assertNotIn(FAKE, "\n".join(messages))

    @override_settings(GIPHY_API_KEY="key")
    def test_giphy_error_body_not_logged(self):
        from orchestration.tool_router import GiphyConnector

        async def fake_get(self, url, params=None):
            return httpx.Response(500, content=FAKE.encode())

        with patch.object(httpx.AsyncClient, "get", new=fake_get):
            with captured_log_messages() as messages:
                _run(GiphyConnector().execute({"query": "cats"}, {}))

        self.assertNotIn(FAKE, "\n".join(messages))

    @override_settings(EXCHANGE_RATE_API_KEY="key")
    def test_currency_error_body_not_logged(self):
        from orchestration.tool_router import CurrencyConnector

        async def fake_get(self, url, params=None):
            return httpx.Response(500, content=FAKE.encode())

        with patch.object(httpx.AsyncClient, "get", new=fake_get):
            with captured_log_messages() as messages:
                _run(CurrencyConnector().execute(
                    {"amount": 1, "from_currency": "USD", "to_currency": "KES"}, {},
                ))

        self.assertNotIn(FAKE, "\n".join(messages))

    def test_dialog_state_read_error_logs_only_the_class(self):
        from orchestration.tool_router import MCPRouter

        router = MCPRouter()
        with patch("orchestration.tool_router.cache") as mock_cache:
            mock_cache.get.side_effect = RuntimeError(f"cache down {FAKE}")
            with captured_log_messages() as messages:
                _run(router._get_dialog_state({"user_id": 1, "room_id": 2}))

        self.assertNotIn(FAKE, "\n".join(messages))

    def test_dialog_state_write_error_logs_only_the_class(self):
        from orchestration.tool_router import MCPRouter

        router = MCPRouter()
        with patch("orchestration.tool_router.cache") as mock_cache:
            mock_cache.set.side_effect = RuntimeError(f"cache down {FAKE}")
            with captured_log_messages() as messages:
                _run(router._store_dialog_state(
                    {"user_id": 1, "room_id": 2}, "get_weather", {}, "success",
                ))

        self.assertNotIn(FAKE, "\n".join(messages))
