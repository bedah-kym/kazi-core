from __future__ import annotations

import asyncio
from unittest.mock import patch

import httpx
from django.test import SimpleTestCase, override_settings

from orchestration.connectors.tts_connector import TTSConnector

_KEY = "fixture-key"  # nosec B105 test fixture, not a credential


def _run(coro):
    return asyncio.run(coro)


class TTSConnectorTests(SimpleTestCase):
    def setUp(self):
        self.connector = TTSConnector()

    @override_settings(OPENAI_API_KEY=_KEY)
    def test_generates_audio(self):
        captured = {}

        async def fake_post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["json"] = json
            return httpx.Response(200, content=b"AUDIO-BYTES")

        with patch.object(httpx.AsyncClient, "post", new=fake_post):
            result = _run(self.connector.execute({"action": "generate_speech", "text": "la la"}, {}))
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"]["bytes"], len(b"AUDIO-BYTES"))
        self.assertEqual(captured["json"]["input"], "la la")
        self.assertEqual(captured["json"]["response_format"], "mp3")

    @override_settings(OPENAI_API_KEY="")
    def test_missing_key_is_an_error(self):
        result = _run(self.connector.execute({"action": "generate_speech", "text": "x"}, {}))
        self.assertEqual(result["status"], "error")

    @override_settings(OPENAI_API_KEY=_KEY)
    def test_empty_text_is_an_error(self):
        result = _run(self.connector.execute({"action": "generate_speech", "text": "   "}, {}))
        self.assertEqual(result["status"], "error")

    @override_settings(OPENAI_API_KEY=_KEY)
    def test_provider_error_is_normalized(self):
        async def err_post(self, url, headers=None, json=None):
            return httpx.Response(502, content=b"nope")

        with patch.object(httpx.AsyncClient, "post", new=err_post):
            result = _run(self.connector.execute({"action": "generate_speech", "text": "x"}, {}))
        self.assertEqual(result["status"], "error")

    @override_settings(OPENAI_API_KEY=_KEY)
    def test_provider_unreachable_is_normalized(self):
        async def boom(self, url, headers=None, json=None):
            raise httpx.ConnectError("down")

        with patch.object(httpx.AsyncClient, "post", new=boom):
            result = _run(self.connector.execute({"action": "generate_speech", "text": "x"}, {}))
        self.assertEqual(result["status"], "error")
