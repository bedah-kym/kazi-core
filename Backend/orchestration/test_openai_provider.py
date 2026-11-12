"""OpenAI Responses provider contract tests (T-P4).

The adapter runs over raw HTTP bodies through ``httpx.MockTransport``: only the
network is faked. Fixtures marked ``"source": "crafted"`` are hand-built from
OpenAI's documented Responses shape; the lead swaps in ``"recorded"`` bodies
from ``manage.py llm_probe``.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from django.test import SimpleTestCase, TransactionTestCase, override_settings

from orchestration.agent_loop import LENGTH_LIMIT_NOTE, run_agent_loop
from orchestration.llm_client import LLMClient, _inject_current_datetime
from orchestration.llm_openai import OpenAIResponsesError

FIXTURE_DIR = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "openai_responses"
)


def _fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _run(coro):
    return asyncio.run(coro)


def _client(**keys) -> LLMClient:
    client = LLMClient()
    client.openai_key = keys.get("openai", "fake-openai-key")  # nosec B105 - test fixture
    client.anthropic_key = keys.get("anthropic")
    client.deepseek_key = keys.get("deepseek")
    client.hf_key = keys.get("hf")
    return client


async def _with_transport(transport, factory):
    async with httpx.AsyncClient(transport=transport) as http:
        with patch("orchestration.llm_client._get_http_client", return_value=http):
            return await factory()


def _call_with_fixture(name: str, *, messages=None, tools=None, system="sys"):
    fixture = _fixture(name)
    captured: dict = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        captured["headers"] = dict(request.headers)
        return httpx.Response(fixture["http_status"], json=fixture["body"])

    client = _client()
    result = _run(_with_transport(
        httpx.MockTransport(handler),
        lambda: client._create_openai_responses(
            messages=messages or [{"role": "user", "content": "hi"}],
            system=system,
            tools=tools,
            temperature=0.3,
            max_tokens=256,
        ),
    ))
    return result, captured


PRIOR_TURN = [
    {"role": "user", "content": "What's the weather in Nairobi?"},
    {
        "role": "assistant",
        "content": [
            {
                "type": "provider_state",
                "provider": "openai",
                "items": [{
                    "type": "reasoning",
                    "id": "rs_prior",
                    "summary": [],
                    "encrypted_content": "encrypted-reasoning-prior",
                }],
            },
            {"type": "text", "text": "Let me check."},
            {"type": "tool_use", "id": "call_prior", "name": "get_weather", "input": {"city": "Nairobi"}},
        ],
    },
    {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "call_prior", "content": "23C"}],
    },
]


class FixtureFormatTests(SimpleTestCase):
    def test_every_fixture_is_labelled_and_named(self):
        files = sorted(FIXTURE_DIR.glob("*.json"))
        self.assertEqual(len(files), 9)
        for path in files:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn(data.get("source"), {"crafted", "recorded"}, path.name)
            self.assertEqual(data.get("name"), path.stem)


class ResponsesContractTests(SimpleTestCase):
    def test_plain_answer_maps_text_usage_and_reasoning(self):
        result, captured = _call_with_fixture("plain_answer")

        text = [b for b in result["content"] if b["type"] == "text"]
        state = [b for b in result["content"] if b["type"] == "provider_state"]
        self.assertEqual(text[0]["text"], "pong")
        self.assertEqual(result["stop_reason"], "end_turn")
        self.assertEqual(state[0]["provider"], "openai")
        self.assertEqual(state[0]["items"][0]["encrypted_content"], "encrypted-reasoning-plain")
        self.assertEqual(
            result["usage"],
            {
                "input_tokens": 24,
                "output_tokens": 12,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        )
        self.assertEqual(captured["body"]["max_output_tokens"], 256)

    def test_tool_call_maps_to_tool_use_with_call_id(self):
        result, _captured = _call_with_fixture("tool_call")

        tool = next(b for b in result["content"] if b["type"] == "tool_use")
        self.assertEqual(tool["id"], "call_crafted_1")
        self.assertEqual(tool["name"], "get_weather")
        self.assertEqual(tool["input"], {"city": "Nairobi"})
        self.assertEqual(result["stop_reason"], "tool_use")

    def test_parallel_tool_calls_and_cached_input(self):
        result, _captured = _call_with_fixture("parallel_tool_calls")

        tools = [b for b in result["content"] if b["type"] == "tool_use"]
        self.assertEqual([t["id"] for t in tools], ["call_crafted_1", "call_crafted_2"])
        self.assertEqual(result["usage"]["input_tokens"], 32)
        self.assertEqual(result["usage"]["cache_read_input_tokens"], 16)
        self.assertEqual(result["usage"]["output_tokens"], 24)

    def test_cut_off_ends_as_max_tokens(self):
        result, _captured = _call_with_fixture("cut_off_max_output_tokens")

        self.assertEqual(result["stop_reason"], "max_tokens")
        self.assertTrue(any(b["type"] == "tool_use" for b in result["content"]))
        self.assertTrue(any(b["type"] == "provider_state" for b in result["content"]))

    def test_reasoning_only_cut_off_has_no_visible_text(self):
        result, _captured = _call_with_fixture("reasoning_only_cut_off")

        self.assertEqual(result["stop_reason"], "max_tokens")
        self.assertFalse(any(b["type"] == "text" for b in result["content"]))
        self.assertTrue(any(b["type"] == "provider_state" for b in result["content"]))

    def test_refusal_ends_honestly(self):
        result, _captured = _call_with_fixture("refusal")

        self.assertEqual(result["stop_reason"], "refusal")

    def test_request_shape_drops_temperature_for_reasoning(self):
        _result, captured = _call_with_fixture(
            "plain_answer",
            system=_inject_current_datetime("STATIC SYSTEM TEXT"),
        )
        body = captured["body"]
        self.assertFalse(body["store"])
        self.assertEqual(body["include"], ["reasoning.encrypted_content"])
        self.assertEqual(body["reasoning"], {"effort": "low"})
        self.assertNotIn("temperature", body)
        self.assertTrue(body["instructions"].startswith("STATIC SYSTEM TEXT"))
        self.assertIn("Current date and time:", body["instructions"])

    def test_replays_reasoning_and_tool_results_to_openai_only(self):
        _result, captured = _call_with_fixture("tool_call", messages=PRIOR_TURN)
        items = captured["body"]["input"]

        reasoning = next(item for item in items if item.get("type") == "reasoning")
        self.assertEqual(reasoning["encrypted_content"], "encrypted-reasoning-prior")
        call = next(item for item in items if item.get("type") == "function_call")
        self.assertEqual(call["call_id"], "call_prior")
        output = next(item for item in items if item.get("type") == "function_call_output")
        self.assertEqual(output["call_id"], "call_prior")
        self.assertNotIn("provider_state", json.dumps(items))

    def test_rate_limit_and_unauthorized_are_errors(self):
        for name in ("rate_limit_429", "unauthorized_401"):
            fixture = _fixture(name)

            def handler(request, fixture=fixture):
                return httpx.Response(fixture["http_status"], json=fixture["body"])

            with self.assertRaises(Exception) as raised:
                _run(_with_transport(
                    httpx.MockTransport(handler),
                    lambda: _client()._create_openai_responses(
                        messages=[{"role": "user", "content": "hi"}],
                        system="sys",
                        tools=None,
                        temperature=0.3,
                        max_tokens=32,
                    ),
                ))
            self.assertIn(f"OpenAI Error {fixture['http_status']}", str(raised.exception))


class ResponsesStreamTests(SimpleTestCase):
    def _stream(self, events):
        payload = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
        payload += "data: [DONE]\n\n"

        def handler(request):
            return httpx.Response(
                200,
                content=payload.encode("utf-8"),
                headers={"content-type": "text/event-stream"},
            )

        async def collect():
            client = _client()
            return [
                event
                async for event in client._stream_openai_responses(
                    messages=[{"role": "user", "content": "hi"}],
                    system="sys",
                    tools=None,
                    temperature=0.3,
                    max_tokens=256,
                )
            ]

        return _run(_with_transport(httpx.MockTransport(handler), collect))

    def test_stream_parses_text_tool_call_and_reasoning(self):
        events = self._stream(_fixture("stream_answer_with_tool_call")["events"])

        text = "".join(e["text"] for e in events if e["type"] == "text")
        self.assertEqual(text, "Let me check.")
        self.assertTrue(any(e["type"] == "tool_use_start" for e in events))
        end = next(e for e in events if e["type"] == "tool_use_end")
        self.assertEqual(end["id"], "call_crafted_stream")
        self.assertEqual(end["input"], {"city": "Nairobi"})
        done = next(e for e in events if e["type"] == "message_done")
        self.assertEqual(done["stop_reason"], "tool_use")
        self.assertEqual(
            done["provider_state"]["items"][0]["encrypted_content"],
            "encrypted-reasoning-stream",
        )
        self.assertNotIn("encrypted-reasoning-stream", text)

    def test_stream_without_completed_is_an_error(self):
        events = [
            event for event in _fixture("stream_answer_with_tool_call")["events"]
            if event["type"] != "response.completed"
        ]
        with self.assertRaises(OpenAIResponsesError):
            self._stream(events)

    def test_stream_with_completed_before_deltas_still_ends(self):
        events = list(_fixture("stream_answer_with_tool_call")["events"])
        completed = next(e for e in events if e["type"] == "response.completed")
        events = [completed] + [e for e in events if e is not completed]

        parsed = self._stream(events)

        text = "".join(e["text"] for e in parsed if e["type"] == "text")
        self.assertEqual(text, "Let me check.")
        self.assertTrue(any(e["type"] == "message_done" for e in parsed))


class ProviderSelectionTests(SimpleTestCase):
    @override_settings(
        OPENAI_API_KEY="fake-openai-key",  # nosec B105 - test fixture
        ANTHROPIC_API_KEY="",
        DEEPSEEK_API_KEY="",
        HF_API_TOKEN="",
        LLM_DEFAULT_PROVIDER="",
    )
    def test_openai_key_alone_never_becomes_the_chat_default(self):
        client = LLMClient()
        self.assertIsNone(client.default_provider())
        self.assertEqual(client._auto_provider_order(), [])

    @override_settings(
        OPENAI_API_KEY="fake-openai-key",  # nosec B105 - test fixture
        DEEPSEEK_API_KEY="fake-deepseek-key",  # nosec B105 - test fixture
        ANTHROPIC_API_KEY="",
        HF_API_TOKEN="",
        LLM_DEFAULT_PROVIDER="",
    )
    def test_openai_key_does_not_displace_an_unset_default(self):
        client = LLMClient()
        self.assertEqual(client.default_provider(), "deepseek")

    @override_settings(
        OPENAI_API_KEY="fake-openai-key",  # nosec B105 - test fixture
        ANTHROPIC_API_KEY="",
        DEEPSEEK_API_KEY="",
        HF_API_TOKEN="",
        LLM_DEFAULT_PROVIDER="openai",
    )
    def test_openai_answers_when_named_as_default(self):
        client = LLMClient()
        self.assertEqual(client.default_provider(), "openai")

    def test_openai_preference_is_first_in_the_order(self):
        client = _client()
        self.assertEqual(client._provider_order("default", "openai")[0], "openai")

    def test_openai_is_not_a_trailing_fallback(self):
        client = _client()
        self.assertNotIn("openai", client._provider_order("default", None))
        self.assertNotIn("openai", client._provider_order("planner", None))
        self.assertIn("openai", client._provider_order("default", "openai"))


@override_settings(
    OPENAI_API_KEY="fake-openai-key",  # nosec B105 - test fixture
    ANTHROPIC_API_KEY="",
    DEEPSEEK_API_KEY="",
    HF_API_TOKEN="",
)
class RoomOpenAIModelTests(TransactionTestCase):
    def test_room_can_choose_an_openai_model(self):
        from chatbot.models import Chatroom
        from orchestration.agent_loop import _resolve_model_override

        room = Chatroom.objects.create(model_pref="openai/gpt-6.1-sol")
        self.assertEqual(
            _resolve_model_override(room.pk),
            ("openai", "gpt-6.1-sol"),
        )


class ProviderStateStrippingTests(SimpleTestCase):
    def test_anthropic_request_drops_provider_state(self):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "usage": {}},
            )

        client = _client(anthropic="fake-anthropic-key")  # nosec B105 - test fixture
        _run(_with_transport(
            httpx.MockTransport(handler),
            lambda: client._call_anthropic_create(
                messages=PRIOR_TURN,
                system="sys",
                tools=None,
                temperature=0.3,
                max_tokens=64,
                user_id=None,
                model="claude-x",
                use_prompt_cache=False,
            ),
        ))

        serialized = json.dumps(captured["body"]["messages"])
        self.assertNotIn("provider_state", serialized)
        self.assertNotIn("encrypted-reasoning-prior", serialized)
        self.assertIn("call_prior", serialized)

    def test_deepseek_request_drops_provider_state_from_an_assistant_message(self):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-1",
                    "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
            )

        client = _client(deepseek="fake-deepseek-key")  # nosec B105 - test fixture
        _run(_with_transport(
            httpx.MockTransport(handler),
            lambda: client._create_openai_message(
                provider="deepseek",
                messages=PRIOR_TURN,
                system="sys",
                tools=None,
                temperature=0.3,
                max_tokens=64,
                user_id=None,
                model=None,
            ),
        ))

        serialized = json.dumps(captured["body"]["messages"])
        self.assertNotIn("provider_state", serialized)
        self.assertNotIn("encrypted-reasoning-prior", serialized)

    def test_deepseek_request_drops_provider_state_even_in_a_user_message(self):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-1",
                    "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
            )

        messages = [{
            "role": "user",
            "content": [
                {"type": "provider_state", "provider": "openai", "items": [{"encrypted_content": "secret"}]},
                {"type": "text", "text": "hello"},
            ],
        }]
        client = _client(deepseek="fake-deepseek-key")  # nosec B105 - test fixture
        _run(_with_transport(
            httpx.MockTransport(handler),
            lambda: client._create_openai_message(
                provider="deepseek",
                messages=messages,
                system="sys",
                tools=None,
                temperature=0.3,
                max_tokens=64,
                user_id=None,
                model=None,
            ),
        ))

        serialized = json.dumps(captured["body"]["messages"])
        self.assertNotIn("provider_state", serialized)
        self.assertNotIn("secret", serialized)
        self.assertIn("hello", serialized)

    def test_plain_text_fallback_skips_provider_state(self):
        text = _client()._messages_to_plain_text(PRIOR_TURN)
        self.assertNotIn("encrypted-reasoning-prior", text)
        self.assertNotIn("provider_state", text)


class CutOffNeverDispatchesTests(SimpleTestCase):
    def test_a_cut_off_tool_call_is_not_run(self):
        fixture = _fixture("cut_off_max_output_tokens")
        from orchestration.llm_openai import parse_responses_body

        parsed = parse_responses_body(fixture["body"])
        fake_llm = MagicMock()
        fake_llm.default_provider = MagicMock(return_value="openai")
        fake_llm.create_message = AsyncMock(return_value=parsed)
        execute = AsyncMock(return_value={"status": "success"})

        async def collect():
            events = []
            async for event in run_agent_loop(
                user_message="do it",
                context={"user_id": None, "room_id": None, "username": "test"},
                preferences={},
            ):
                events.append(event)
            return events

        with patch("orchestration.agent_loop.get_llm_client", return_value=fake_llm), \
                patch("orchestration.agent_loop.execute_tool", execute), \
                patch("orchestration.agent_loop._resolve_persona", new_callable=AsyncMock, return_value=None), \
                patch("orchestration.agent_loop.enforce_agent_caps", return_value=True), \
                patch("orchestration.agent_loop._search_backend_available", return_value=False):
            events = _run(collect())

        execute.assert_not_awaited()
        self.assertFalse(any(event.kind == "tool_start" for event in events))
        self.assertTrue(any(
            event.kind == "error" and event.data.get("message") == LENGTH_LIMIT_NOTE
            for event in events
        ))
