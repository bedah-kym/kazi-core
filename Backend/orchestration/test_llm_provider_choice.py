"""T-F4: the LLM client follows the operator's choice.

Covers the default-provider setting, that a Claude model name never reaches a
non-Anthropic provider, that the volatile date stamp lands after the static
system text on OpenAI-compatible providers (so prompt caching hits), and that
the search connector never returns model prose as search results.
"""
import asyncio
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase, override_settings

from orchestration.llm_client import LLMClient, _inject_current_datetime


def _run(coro):
    return asyncio.run(coro)


class _FakeResponse:
    def __init__(self, data, status=200):
        self.status_code = status
        self._data = data

    def json(self):
        return self._data


class _CapturingClient:
    """Shared-client stand-in: records the body of one OpenAI-style POST."""

    is_closed = False

    def __init__(self, response):
        self.response = response
        self.body = None

    async def post(self, url, headers=None, json=None, timeout=None):
        self.body = json
        return self.response


def _client(*, anthropic=None, deepseek=None, hf=None):
    client = LLMClient()
    client.anthropic_key = anthropic
    client.deepseek_key = deepseek
    client.hf_key = hf
    return client


class DefaultProviderTests(SimpleTestCase):
    @override_settings(LLM_DEFAULT_PROVIDER="deepseek")
    def test_default_provider_answers_even_with_anthropic_key(self):
        client = _client(anthropic="a-key", deepseek="d-key")  # nosec B106 - test fixture
        client._create_openai_message = AsyncMock(
            return_value={"content": [], "stop_reason": "end_turn", "usage": {}}
        )
        client._call_anthropic_create = AsyncMock(
            return_value={"content": [], "stop_reason": "end_turn", "usage": {}}
        )

        _run(client.create_message(messages=[{"role": "user", "content": "hi"}], system="s"))

        client._create_openai_message.assert_awaited_once()
        client._call_anthropic_create.assert_not_awaited()
        self.assertEqual(
            client._create_openai_message.await_args.kwargs["provider"], "deepseek"
        )

    @override_settings(LLM_DEFAULT_PROVIDER="")
    def test_unset_default_uses_documented_first_configured(self):
        client = _client(deepseek="d-key")  # nosec B106 - test fixture
        self.assertEqual(client.default_provider(), "deepseek")

    @override_settings(LLM_DEFAULT_PROVIDER="anthropic")
    def test_default_naming_unconfigured_provider_falls_back(self):
        client = _client(deepseek="d-key")  # nosec B106 - test fixture
        self.assertEqual(client.default_provider(), "deepseek")


class ModelLeakTests(SimpleTestCase):
    @override_settings(LLM_DEFAULT_PROVIDER="deepseek")
    def test_claude_model_name_never_sent_to_deepseek(self):
        client = _client(deepseek="d-key")  # nosec B106 - test fixture
        client._create_openai_message = AsyncMock(
            return_value={"content": [], "stop_reason": "end_turn", "usage": {}}
        )

        _run(client.create_message(
            messages=[{"role": "user", "content": "hi"}],
            system="s",
            model="claude-haiku-4-5-20251001",
        ))

        kwargs = client._create_openai_message.await_args.kwargs
        self.assertEqual(kwargs["provider"], "deepseek")
        self.assertIsNone(kwargs["model"])


class DeepSeekPromptCacheTests(SimpleTestCase):
    def test_system_static_text_precedes_volatile_date(self):
        client = _client(deepseek="d-key")  # nosec B106 - test fixture
        fake = _CapturingClient(_FakeResponse({
            "id": "m1",
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }))

        with patch("orchestration.llm_client._get_http_client", return_value=fake):
            _run(client._create_openai_message(
                provider="deepseek",
                messages=[{"role": "user", "content": "hi"}],
                system=_inject_current_datetime("STATIC SYSTEM TEXT"),
                tools=None,
                temperature=0.3,
                max_tokens=10,
                user_id=None,
                model=None,
            ))

        system_message = fake.body["messages"][0]
        self.assertEqual(system_message["role"], "system")
        content = system_message["content"]
        self.assertTrue(content.startswith("STATIC SYSTEM TEXT"))
        self.assertIn("Current date and time:", content)
        self.assertLess(
            content.index("STATIC SYSTEM TEXT"),
            content.index("Current date and time:"),
        )


class _LoopHoldingClient:
    """Stands in for httpx.AsyncClient: like a pooled connection, it holds its loop."""

    is_closed = False

    def __init__(self, *args, **kwargs):
        self.loop = asyncio.get_running_loop()


class AnthropicPromptCacheTests(SimpleTestCase):
    def test_cached_system_puts_the_breakpoint_on_static_text(self):
        from orchestration.llm_client import _anthropic_system

        blocks = _anthropic_system(_inject_current_datetime("STATIC SYSTEM TEXT"), True)
        self.assertEqual(blocks[0]["text"], "STATIC SYSTEM TEXT")
        self.assertIn("cache_control", blocks[0])
        self.assertTrue(blocks[1]["text"].startswith("Current date and time:"))

    def test_uncached_system_has_static_text_first(self):
        from orchestration.llm_client import _anthropic_system

        system = _anthropic_system(_inject_current_datetime("STATIC SYSTEM TEXT"), False)
        self.assertTrue(system.startswith("STATIC SYSTEM TEXT"))

    def test_streaming_path_splits_the_stamp_too(self):
        client = _client(anthropic="a-key")  # nosec B106 - test fixture
        sent = {}

        class _Stream:
            status_code = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def aiter_lines(self):
                if False:
                    yield ""

        class _Client:
            is_closed = False

            def stream(self, method, url, headers=None, json=None, timeout=None):
                sent["body"] = json
                return _Stream()

        async def drain():
            async for _ in client._stream_anthropic_events(
                messages=[{"role": "user", "content": "hi"}],
                system=_inject_current_datetime("STATIC SYSTEM TEXT"),
                use_prompt_cache=True,
            ):
                pass

        with patch("orchestration.llm_client._get_http_client", return_value=_Client()):
            try:
                _run(drain())
            except Exception:  # nosec B110 - only the request body matters here
                pass
        self.assertEqual(sent["body"]["system"][0]["text"], "STATIC SYSTEM TEXT")
        self.assertTrue(sent["body"]["system"][1]["text"].startswith("Current date and time:"))


class SharedHttpClientTests(SimpleTestCase):
    def test_short_lived_loops_do_not_accumulate_clients(self):
        from orchestration import llm_client

        async def borrow():
            return llm_client._get_http_client()

        llm_client._http_clients.clear()
        with patch("orchestration.llm_client.httpx.AsyncClient", _LoopHoldingClient):
            for _ in range(5):
                asyncio.run(borrow())
            kept = len(llm_client._http_clients)
        llm_client._http_clients.clear()
        self.assertLessEqual(kept, 1)

    def test_one_loop_reuses_one_client(self):
        from orchestration import llm_client

        async def borrow_twice():
            return llm_client._get_http_client() is llm_client._get_http_client()

        self.assertTrue(asyncio.run(borrow_twice()))


class SearchConnectorHonestyTests(SimpleTestCase):
    """T-F4: the search connector never returns language-model prose as results."""

    def setUp(self):
        from orchestration.tool_router import register_search_backend
        register_search_backend(None)
        self.addCleanup(lambda: register_search_backend(None))

    def test_no_backend_reports_cannot_search(self):
        from orchestration.tool_router import SearchConnector

        result = _run(SearchConnector().execute({"query": "weather in Nairobi"}, {}))

        self.assertEqual(result.get("status"), "error")
        self.assertIn("can't search the web", result.get("message", ""))
        self.assertNotEqual(result.get("source"), "claude_search")
        self.assertEqual(result.get("results"), [])

    def test_registered_backend_result_is_returned(self):
        from orchestration.tool_router import SearchConnector, register_search_backend

        async def backend(query, context):
            return {"status": "success", "results": [{"title": query}]}

        register_search_backend(backend)
        result = _run(SearchConnector().execute({"query": "Nairobi"}, {}))

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["results"], [{"title": "Nairobi"}])
