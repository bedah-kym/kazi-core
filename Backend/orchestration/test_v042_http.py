"""Mocked-HTTP tests for v0.4.2 I/O paths: Telegram connector and DeepSeek
provider calls. No network, no Redis."""
import asyncio
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase, override_settings

from orchestration.llm_client import LLMClient


def _run(coro):
    return asyncio.run(coro)


class _FakeResponse:
    def __init__(self, data, status=200):
        self.status_code = status
        self._data = data

    def json(self):
        return self._data


class _FakeAsyncClient:
    def __init__(self, response):
        self.post = AsyncMock(return_value=response)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class TelegramBotConnectorTests(SimpleTestCase):
    def test_send_message_success(self):
        from orchestration.connectors.telegram_bot_connector import TelegramBotConnector
        c = TelegramBotConnector()
        mock = AsyncMock(return_value={"ok": True, "result": {"message_id": 42}})
        with patch.object(c, "_tg_post", mock):
            result = _run(c._send_message({"chat_id": "1", "message": "hi"}))
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"]["message_id"], 42)
        mock.assert_awaited_once()

    def test_send_message_missing_params(self):
        from orchestration.connectors.telegram_bot_connector import TelegramBotConnector
        c = TelegramBotConnector()
        result = _run(c._send_message({"chat_id": "", "message": ""}))
        self.assertEqual(result["status"], "error")

    def test_send_message_api_error(self):
        from orchestration.connectors.telegram_bot_connector import TelegramBotConnector
        c = TelegramBotConnector()
        with patch.object(c, "_tg_post", new=AsyncMock(return_value={"ok": False, "description": "Forbidden"})):
            result = _run(c._send_message({"chat_id": "1", "message": "hi"}))
        self.assertEqual(result["status"], "error")
        self.assertIn("Forbidden", result["clarification_prompt"])

    def test_send_media_invalid_type(self):
        from orchestration.connectors.telegram_bot_connector import TelegramBotConnector
        c = TelegramBotConnector()
        result = _run(c._send_media({"chat_id": "1", "media_url": "http://x", "media_type": "sticker"}))
        self.assertEqual(result["status"], "error")
        self.assertIn("media_type", result["clarification_prompt"])

    def test_send_keyboard_drops_invalid_buttons(self):
        from orchestration.connectors.telegram_bot_connector import TelegramBotConnector
        c = TelegramBotConnector()
        mock = AsyncMock(return_value={"ok": True, "result": {"message_id": 7}})
        with patch.object(c, "_tg_post", mock):
            result = _run(c._send_keyboard({
                "chat_id": "1",
                "text": "pick",
                "buttons": [
                    [{"text": "A", "callback_data": "a"}, {"text": "invalid"}],
                    [{"text": "B", "url": "https://b.example"}],
                ],
            }))
        self.assertEqual(result["status"], "success")
        method, payload = mock.await_args.args
        self.assertEqual(method, "sendMessage")
        keyboard = payload["reply_markup"]["inline_keyboard"]
        self.assertEqual(len(keyboard[0]), 1)  # invalid button dropped
        self.assertEqual(keyboard[0][0]["callback_data"], "a")

    def test_delete_message_success(self):
        from orchestration.connectors.telegram_bot_connector import TelegramBotConnector
        c = TelegramBotConnector()
        with patch.object(c, "_tg_post", new=AsyncMock(return_value={"ok": True, "result": True})):
            result = _run(c._delete_message({"chat_id": "1", "message_id": 5}))
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"]["status"], "deleted")

    def test_health_check_success(self):
        from orchestration.connectors.telegram_bot_connector import TelegramBotConnector
        c = TelegramBotConnector()
        with override_settings(TELEGRAM_BOT_TOKEN="tok"):  # nosec B106 — test fixture — fake credential
            with patch.object(c, "_tg_post", new=AsyncMock(return_value={"ok": True, "result": {"username": "kazi_bot", "first_name": "Kazi"}})):
                result = _run(c._health_check())
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"]["bot_username"], "kazi_bot")


class DeepSeekHTTPTests(SimpleTestCase):
    def test_create_openai_message(self):
        client = LLMClient()
        client.deepseek_key = "test-key"
        fake = _FakeResponse({
            "id": "m1",
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        })
        with patch("orchestration.llm_client.httpx.AsyncClient", return_value=_FakeAsyncClient(fake)):
            result = _run(client._create_openai_message(
                provider="deepseek",
                messages=[{"role": "user", "content": "hi"}],
                system="",
                tools=None,
                temperature=0.3,
                max_tokens=100,
                user_id=None,
            ))
        self.assertEqual(result["content"][0]["text"], "hi")
        self.assertEqual(result["stop_reason"], "end_turn")

    def test_call_huggingface_deepseek(self):
        client = LLMClient()
        client.deepseek_key = "k"
        fake = _FakeResponse({"choices": [{"message": {"content": "  answer  "}}]})
        with patch("orchestration.llm_client.httpx.AsyncClient", return_value=_FakeAsyncClient(fake)):
            text = _run(client._call_huggingface("sys", "user", 0.3, 100, False, provider="deepseek"))
        self.assertEqual(text, "answer")

    def test_call_huggingface_error_status(self):
        client = LLMClient()
        client.deepseek_key = "k"
        fake = _FakeResponse({"error": "bad"}, status=401)
        with patch("orchestration.llm_client.httpx.AsyncClient", return_value=_FakeAsyncClient(fake)):
            with self.assertRaises(Exception):
                _run(client._call_huggingface("s", "u", 0.3, 100, False, provider="deepseek"))


class GenerateJsonRepairTests(SimpleTestCase):
    def test_repairs_invalid_then_succeeds(self):
        client = LLMClient()
        client.generate_text = AsyncMock(side_effect=[
            "not json at all",
            '{"action": "search", "confidence": 0.9}',
        ])
        result = _run(client.generate_json(system_prompt="s", user_prompt="u", required_fields=["action"]))
        self.assertEqual(result["action"], "search")
        self.assertEqual(client.generate_text.await_count, 2)

    def test_returns_valid_first_try(self):
        client = LLMClient()
        client.generate_text = AsyncMock(return_value='{"action": "search"}')
        result = _run(client.generate_json(system_prompt="s", user_prompt="u", required_fields=["action"]))
        self.assertEqual(result["action"], "search")
        self.assertEqual(client.generate_text.await_count, 1)

    def test_missing_required_field_triggers_repair(self):
        client = LLMClient()
        client.generate_text = AsyncMock(side_effect=[
            '{"confidence": 0.5}',
            '{"action": "search", "confidence": 0.5}',
        ])
        result = _run(client.generate_json(system_prompt="s", user_prompt="u", required_fields=["action"]))
        self.assertEqual(result["action"], "search")
        self.assertEqual(client.generate_text.await_count, 2)
