"""OpenAI Responses provider contract tests (T-P4).

The adapter runs over raw HTTP bodies through ``httpx.MockTransport``: only the
network is faked. Crafted fixtures are labelled ``"source": "crafted"``; the
lead swaps in ``"recorded"`` bodies from ``manage.py llm_probe``. Expected
values are derived from each fixture's body, never from a crafted constant.
"""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from django.test import SimpleTestCase, TransactionTestCase, override_settings

from orchestration.agent_loop import LENGTH_LIMIT_NOTE, run_agent_loop
from orchestration.llm_client import LLMClient, _inject_current_datetime
from orchestration.llm_openai import (
    OpenAIResponsesError,
    _strict_schema,
    parse_responses_body,
    tools_to_responses,
)
from orchestration.openai_fixtures import CASES, CRAFTED_ONLY, messages_for, replay_messages

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


def _args(raw):
    try:
        parsed = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _expected_blocks(body: dict):
    output = [item for item in body.get("output") or [] if isinstance(item, dict)]
    blocks = []
    if output:
        blocks.append({
            "type": "provider_state",
            "provider": "openai",
            "model": body.get("model"),
            "items": output,
        })
    for item in output:
        if item.get("type") == "message":
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") == "output_text":
                    blocks.append({"type": "text", "text": part.get("text", "")})
        elif item.get("type") == "function_call":
            blocks.append({
                "type": "tool_use",
                "id": str(item.get("call_id") or item.get("id") or ""),
                "name": item.get("name"),
                "input": _args(item.get("arguments")),
            })
    return blocks or [{"type": "text", "text": ""}]


def _expected_stop(body: dict) -> str:
    status = body.get("status")
    reason = (body.get("incomplete_details") or {}).get("reason")
    if status == "incomplete":
        if reason == "max_output_tokens":
            return "max_tokens"
        if reason == "content_filter":
            return "refusal"
        raise AssertionError(f"unknown incomplete reason {reason!r}")
    if status != "completed":
        raise AssertionError(f"unexpected status {status!r}")
    refusal = any(
        item.get("type") == "refusal"
        or (
            item.get("type") == "message"
            and any(
                isinstance(part, dict) and part.get("type") == "refusal"
                for part in item.get("content") or []
            )
        )
        for item in body.get("output") or []
        if isinstance(item, dict)
    )
    if refusal:
        return "refusal"
    if any(item.get("type") == "function_call" for item in body.get("output") or []):
        return "tool_use"
    return "end_turn"


def _expected_usage(body: dict) -> dict:
    usage = body.get("usage") or {}
    details = usage.get("input_tokens_details") or {}
    total = int(usage.get("input_tokens") or 0)
    cached = int(details.get("cached_tokens") or 0)
    written = int(details.get("cache_write_tokens") or 0)
    return {
        "input_tokens": max(0, total - cached - written),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "cache_read_input_tokens": cached,
        "cache_creation_input_tokens": written,
    }


def _call_case(name: str, *, messages=None, target_model=None, system=None):
    case = CASES[name]
    fixture = _fixture(name)
    captured: dict = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(fixture["http_status"], json=fixture["body"])

    client = _client()
    client.openai_reasoning_effort = case["effort"]
    result = _run(_with_transport(
        httpx.MockTransport(handler),
        lambda: client._create_openai_responses(
            messages=messages if messages is not None else messages_for(case),
            system=system if system is not None else case.get("system", "sys"),
            tools=case["tools"] or None,
            temperature=0.3,
            max_tokens=case["max_output_tokens"],
            model=target_model,
        ),
    ))
    return fixture["body"], result, captured["body"]


def _replay_input(body: dict, target_model: str):
    blocks = parse_responses_body(body)["content"]
    messages = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": blocks},
    ]
    captured: dict = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=body)

    _run(_with_transport(
        httpx.MockTransport(handler),
        lambda: _client()._create_openai_responses(
            messages=messages,
            system="sys",
            tools=None,
            temperature=0.3,
            max_tokens=256,
            model=target_model,
        ),
    ))
    return captured["body"]["input"]


def _drop_trailing_reasoning(items):
    end = len(items)
    while end > 0 and items[end - 1].get("type") == "reasoning":
        end -= 1
    return items[:end]


def _rebuilt_items(items):
    out = []
    text = []

    def flush():
        if text:
            out.append({
                "role": "assistant",
                "content": [{"type": "output_text", "text": "".join(text)}],
            })
            text.clear()

    for item in items:
        if item.get("type") == "message":
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") == "output_text":
                    text.append(part.get("text", ""))
        elif item.get("type") == "function_call":
            flush()
            out.append({
                "type": "function_call",
                "call_id": str(item.get("call_id") or item.get("id") or ""),
                "name": item.get("name"),
                "arguments": json.dumps(_args(item.get("arguments")), default=str),
            })
    flush()
    return out


def _user_item(text: str):
    return {"role": "user", "content": [{"type": "input_text", "text": text}]}


def _body(items, *, model="gpt-6.1-sol", status="completed", reason=None):
    return {
        "id": "resp_synthetic",
        "object": "response",
        "status": status,
        "incomplete_details": {"reason": reason} if reason else None,
        "model": model,
        "output": items,
        "usage": {
            "input_tokens": 20,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
            "output_tokens": 10,
            "output_tokens_details": {"reasoning_tokens": 2},
            "total_tokens": 30,
        },
    }


def _reasoning(id_):
    return {
        "type": "reasoning", "id": id_, "summary": [], "content": [],
        "encrypted_content": f"enc-{id_}",
    }


def _function_call(id_, call_id, name="get_weather", args='{"city": "Nairobi"}'):
    return {
        "type": "function_call", "id": id_, "call_id": call_id,
        "name": name, "arguments": args, "status": "completed",
    }


def _message(text, phase="final_answer"):
    return {
        "type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
        "phase": phase,
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


class FixtureFormatTests(SimpleTestCase):
    def test_every_fixture_is_labelled_named_and_matches_a_spec(self):
        files = sorted(FIXTURE_DIR.glob("*.json"))
        self.assertEqual(len(files), 11)
        names = {path.stem for path in files}
        self.assertEqual(names, set(CASES) | set(CRAFTED_ONLY))
        for path in files:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn(data.get("source"), {"crafted", "recorded"}, path.name)
            self.assertEqual(data.get("name"), path.stem)


class ResponsesContractTests(SimpleTestCase):
    def test_every_spec_case_maps_from_its_fixture_body(self):
        for name in CASES:
            if CASES[name].get("stream"):
                continue
            with self.subTest(name=name):
                body, result, request = _call_case(name)
                self.assertEqual(result["content"], _expected_blocks(body))
                self.assertEqual(result["stop_reason"], _expected_stop(body))
                self.assertEqual(result["usage"], _expected_usage(body))
                self.assertEqual(request["max_output_tokens"], CASES[name]["max_output_tokens"])
                self.assertEqual(request["reasoning"], {"effort": CASES[name]["effort"]})
                state = result["content"][0]
                if body.get("output"):
                    self.assertEqual(state["items"], body["output"])
                    self.assertEqual(state["model"], body.get("model"))

    def test_refusal_ends_honestly(self):
        fixture = _fixture("refusal")
        result = parse_responses_body(fixture["body"])
        self.assertEqual(result["stop_reason"], "refusal")
        self.assertEqual(result["content"], _expected_blocks(fixture["body"]))

    def test_request_shape_drops_temperature_for_reasoning(self):
        _body, _result, request = _call_case(
            "plain_answer", system=_inject_current_datetime("STATIC SYSTEM TEXT"),
        )
        self.assertFalse(request["store"])
        self.assertEqual(request["include"], ["reasoning.encrypted_content"])
        self.assertNotIn("temperature", request)
        self.assertTrue(request["instructions"].startswith("STATIC SYSTEM TEXT"))
        self.assertIn("Current date and time:", request["instructions"])

    def test_rate_limit_and_unauthorized_never_leak_the_body(self):
        for name in ("rate_limit_429", "unauthorized_401"):
            fixture = _fixture(name)

            def handler(request, fixture=fixture):
                return httpx.Response(fixture["http_status"], json=fixture["body"])

            with self.assertLogs(level="INFO") as captured:
                with self.assertRaises(OpenAIResponsesError) as raised:
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
            message = str(raised.exception)
            logged = chr(10).join(captured.output)
            self.assertNotIn("org-abc123", logged)
            self.assertNotIn("sk-proj-****abcd", logged)
            self.assertIn(f"OpenAI Error {fixture['http_status']}", message)
            self.assertIn(
                f"{fixture['body']['error']['type']}/{fixture['body']['error']['code']}",
                message,
            )
            self.assertNotIn("org-abc123", message)
            self.assertNotIn("sk-proj-****abcd", message)

    def test_unknown_statuses_are_errors(self):
        base = _fixture("plain_answer")["body"]
        for status in ("failed", "cancelled", "in_progress", None):
            with self.subTest(status=status):
                body = deepcopy(base)
                if status is None:
                    body.pop("status", None)
                else:
                    body["status"] = status
                with self.assertRaises(OpenAIResponsesError):
                    parse_responses_body(body)

    def test_unknown_incomplete_reason_is_an_error(self):
        body = _body([], status="incomplete", reason="something_new")
        with self.assertRaises(OpenAIResponsesError):
            parse_responses_body(body)

    def test_content_filter_is_a_refusal(self):
        body = _body([_message("")], status="incomplete", reason="content_filter")
        self.assertEqual(parse_responses_body(body)["stop_reason"], "refusal")


class ReplayOrderTests(SimpleTestCase):
    def test_replays_openai_items_verbatim_in_order(self):
        items = [
            _reasoning("rs_1"),
            _function_call("fc_1", "call_1"),
            _reasoning("rs_2"),
            _function_call("fc_2", "call_2"),
        ]
        body = _body(items)
        self.assertEqual(
            _replay_input(body, "gpt-6.1-sol"),
            [_user_item("go")] + items,
        )

    def test_a_dated_snapshot_reply_still_replays_verbatim_to_the_alias(self):
        # OpenAI answers an alias request ("gpt-5") with the dated snapshot it
        # resolved to ("gpt-5-2025-08-07"). The block must remember the model
        # the request named, or every later call would drop its reasoning.
        items = [
            _reasoning("rs_1"),
            _function_call("fc_1", "call_1"),
        ]
        first = _body(items, model="gpt-6.1-sol-2026-11-01")

        def first_handler(request):
            return httpx.Response(200, json=first)

        result = _run(_with_transport(
            httpx.MockTransport(first_handler),
            lambda: _client()._create_openai_responses(
                messages=[{"role": "user", "content": "go"}],
                system="sys",
                tools=None,
                temperature=0.3,
                max_tokens=256,
                model="gpt-6.1-sol",
            ),
        ))
        captured: dict = {}

        def second_handler(request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=_body([_message("done")]))

        _run(_with_transport(
            httpx.MockTransport(second_handler),
            lambda: _client()._create_openai_responses(
                messages=[
                    {"role": "user", "content": "go"},
                    {"role": "assistant", "content": result["content"]},
                ],
                system="sys",
                tools=None,
                temperature=0.3,
                max_tokens=256,
                model="gpt-6.1-sol",
            ),
        ))
        self.assertEqual(captured["body"]["input"], [_user_item("go")] + items)

    def test_a_streamed_snapshot_reply_remembers_the_requested_model(self):
        events = [{
            "type": "response.completed",
            "response": _body(
                [_reasoning("rs_1"), _function_call("fc_1", "call_1")],
                model="gpt-6.1-sol-2026-11-01",
            ),
        }]
        payload = "".join(f"data: {json.dumps(event)}\n\n" for event in events)

        def handler(request):
            return httpx.Response(
                200, content=payload.encode("utf-8"),
                headers={"content-type": "text/event-stream"},
            )

        async def collect():
            return [
                event
                async for event in _client()._stream_openai_responses(
                    messages=[{"role": "user", "content": "go"}],
                    system="sys",
                    tools=None,
                    temperature=0.3,
                    max_tokens=256,
                    model="gpt-6.1-sol",
                )
            ]

        done = _run(_with_transport(httpx.MockTransport(handler), collect))[-1]
        self.assertEqual(done["type"], "message_done")
        self.assertEqual(done["provider_state"]["model"], "gpt-6.1-sol")

    def test_replays_message_after_reasoning(self):
        items = [_reasoning("rs_1"), _message("It is 23C.")]
        body = _body(items)
        self.assertEqual(
            _replay_input(body, "gpt-6.1-sol"),
            [_user_item("go")] + items,
        )

    def test_replays_function_call_before_message(self):
        items = [
            _reasoning("rs_1"),
            _function_call("fc_1", "call_1"),
            _message("Checking."),
        ]
        body = _body(items)
        self.assertEqual(
            _replay_input(body, "gpt-6.1-sol"),
            [_user_item("go")] + items,
        )

    def test_model_switch_drops_reasoning_and_keeps_calls(self):
        items = [
            _reasoning("rs_1"),
            _function_call("fc_1", "call_1"),
            _reasoning("rs_2"),
            _function_call("fc_2", "call_2"),
        ]
        body = _body(items, model="gpt-6.1-sol")
        self.assertEqual(
            _replay_input(body, "gpt-6.1-terra"),
            [_user_item("go")] + _rebuilt_items(items),
        )

    def test_never_sends_reasoning_with_nothing_after_it(self):
        items = [
            _reasoning("rs_1"),
            _function_call("fc_1", "call_1"),
            _reasoning("rs_2"),
        ]
        body = _body(items)
        self.assertEqual(
            _replay_input(body, "gpt-6.1-sol"),
            [_user_item("go")] + _drop_trailing_reasoning(items),
        )

    def test_recorded_replay_request_matches_the_probe_spec(self):
        prior = _fixture("tool_call")
        replay = _fixture("reasoning_replay")
        messages = replay_messages(
            prior["body"], CASES["tool_call"]["prompt"], CASES["reasoning_replay"]["prompt"],
        )
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=replay["body"])

        client = _client()
        client.openai_reasoning_effort = CASES["reasoning_replay"]["effort"]
        _run(_with_transport(
            httpx.MockTransport(handler),
            lambda: client._create_openai_responses(
                messages=messages,
                system=CASES["reasoning_replay"]["system"],
                tools=CASES["reasoning_replay"]["tools"] or None,
                temperature=0.3,
                max_tokens=CASES["reasoning_replay"]["max_output_tokens"],
            ),
        ))
        self.assertEqual(captured["body"]["input"], replay["request_input"])


class UsageTests(SimpleTestCase):
    def test_cache_write_maps_and_the_sum_equals_total_input(self):
        fixture = _fixture("parallel_tool_calls")
        usage = parse_responses_body(fixture["body"])["usage"]
        details = fixture["body"]["usage"]["input_tokens_details"]
        self.assertEqual(usage["cache_read_input_tokens"], details["cached_tokens"])
        self.assertEqual(usage["cache_creation_input_tokens"], details["cache_write_tokens"])
        self.assertEqual(
            usage["input_tokens"]
            + usage["cache_read_input_tokens"]
            + usage["cache_creation_input_tokens"],
            fixture["body"]["usage"]["input_tokens"],
        )


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

    @staticmethod
    def _expected(raw_events):
        """What the parser must yield for a fixture, derived from its raw events.

        Text deltas pass through as they arrive (or the terminal's text once,
        if none streamed); each function call in the terminal output follows in
        output order as start, its own argument deltas, end; then message_done.
        Works the same for a crafted or a recorded stream.
        """
        terminal = next(
            e["response"] for e in raw_events
            if e.get("type") in ("response.completed", "response.incomplete", "response.failed")
        )
        deltas = [
            e["delta"] for e in raw_events
            if e.get("type") == "response.output_text.delta" and e.get("delta")
        ]
        text = "".join(deltas) or "".join(
            part.get("text", "")
            for item in terminal.get("output") or []
            if item.get("type") == "message"
            for part in item.get("content") or []
            if part.get("type") == "output_text"
        )
        types = ["text"] * len(deltas) if deltas else (["text"] if text else [])
        calls = []
        for index, item in enumerate(terminal.get("output") or []):
            if item.get("type") != "function_call":
                continue
            arg_deltas = [
                e for e in raw_events
                if e.get("type") == "response.function_call_arguments.delta"
                and int(e.get("output_index") or 0) == index and e.get("delta")
            ]
            types += ["tool_use_start"] + ["tool_use_input"] * len(arg_deltas) + ["tool_use_end"]
            calls.append((item["call_id"], json.loads(item.get("arguments") or "{}")))
        types.append("message_done")
        return types, text, calls

    def test_stream_event_sequence_is_exact(self):
        raw = _fixture("stream_answer_with_tool_call")["events"]
        expected_types, expected_text, expected_calls = self._expected(raw)
        self.assertTrue(expected_calls, "the fixture must contain a tool call")

        events = self._stream(raw)

        self.assertEqual([event["type"] for event in events], expected_types)
        self.assertEqual(
            "".join(e["text"] for e in events if e["type"] == "text"), expected_text,
        )
        ends = [event for event in events if event["type"] == "tool_use_end"]
        self.assertEqual([(e["id"], e["input"]) for e in ends], expected_calls)
        self.assertEqual(events[-1]["stop_reason"], "tool_use")

    def test_cut_off_stream_yields_no_tool_events(self):
        partial = _function_call("fc_x", "call_x", name="set_reminder", args='{"text": "re')
        events = self._stream([
            {"type": "response.output_text.delta", "output_index": 1, "delta": "I was"},
            {"type": "response.output_item.added", "output_index": 2, "item": partial},
            {"type": "response.function_call_arguments.delta", "output_index": 2, "delta": '{"text": "re'},
            {"type": "response.incomplete", "response": _body(
                [_reasoning("rs_x"), _message("I was"), partial],
                status="incomplete", reason="max_output_tokens",
            )},
        ])
        self.assertEqual([event["type"] for event in events], ["text", "message_done"])
        self.assertEqual(events[-1]["stop_reason"], "max_tokens")

    def test_argument_delta_without_added_still_has_one_ordered_call(self):
        call = _function_call("fc_1", "call_1")
        events = self._stream([
            {"type": "response.function_call_arguments.delta", "output_index": 0, "delta": '{"city": "Nairobi"}'},
            {"type": "response.completed", "response": _body([call])},
        ])
        self.assertEqual(
            [event["type"] for event in events],
            ["tool_use_start", "tool_use_input", "tool_use_end", "message_done"],
        )
        start = next(event for event in events if event["type"] == "tool_use_start")
        end = next(event for event in events if event["type"] == "tool_use_end")
        self.assertEqual(start["id"], "call_1")
        self.assertEqual(end["id"], "call_1")

    def test_added_without_call_id_does_not_double_end(self):
        no_call_id = {
            "type": "function_call", "id": "fc_1", "name": "get_weather",
            "arguments": "", "status": "in_progress",
        }
        completed = _function_call("fc_1", "call_1")
        events = self._stream([
            {"type": "response.output_item.added", "output_index": 0, "item": no_call_id},
            {"type": "response.function_call_arguments.done", "output_index": 0, "arguments": '{"city": "Nairobi"}'},
            {"type": "response.completed", "response": _body([completed])},
        ])
        ends = [event for event in events if event["type"] == "tool_use_end"]
        self.assertEqual(len(ends), 1)
        self.assertEqual(ends[0]["id"], "call_1")

    def test_refusal_delta_is_text(self):
        events = self._stream([
            {"type": "response.refusal.delta", "delta": "I can't."},
            {"type": "response.completed", "response": _body([{
                "type": "message", "id": "msg_r", "role": "assistant",
                "content": [{"type": "refusal", "refusal": "I can't."}],
            }])},
        ])
        self.assertEqual([event["type"] for event in events], ["text", "message_done"])
        self.assertEqual(events[0]["text"], "I can't.")
        self.assertEqual(events[-1]["stop_reason"], "refusal")

    def test_stream_without_completed_is_an_error(self):
        events = [
            event for event in _fixture("stream_answer_with_tool_call")["events"]
            if event["type"] != "response.completed"
        ]
        with self.assertRaises(OpenAIResponsesError):
            self._stream(events)

    def test_stream_error_event_is_an_error(self):
        # A terminal event follows the error, so only the error handler can
        # raise here; without it the stream would end as a normal reply.
        with self.assertRaises(OpenAIResponsesError) as raised:
            self._stream([
                {
                    "type": "error",
                    "error": {"type": "server_error", "code": "boom", "message": "org-secret"},
                },
                {"type": "response.completed", "response": _body([_message("ok")])},
            ])
        self.assertEqual(str(raised.exception), "OpenAI stream error: server_error/boom.")
        self.assertNotIn("org-secret", str(raised.exception))

    def test_stream_failed_response_is_an_error(self):
        with self.assertRaises(OpenAIResponsesError) as raised:
            self._stream([{
                "type": "response.failed",
                "response": {"status": "failed", "output": []},
            }])
        self.assertIn("status 'failed'", str(raised.exception))

    def test_stream_with_completed_before_deltas_still_ends(self):
        events = list(_fixture("stream_answer_with_tool_call")["events"])
        expected_types, expected_text, expected_calls = self._expected(events)
        completed = next(e for e in events if e["type"] == "response.completed")
        events = [completed] + [e for e in events if e is not completed]

        parsed = self._stream(events)

        self.assertEqual([e["type"] for e in parsed], expected_types)
        self.assertEqual("".join(e["text"] for e in parsed if e["type"] == "text"), expected_text)
        self.assertEqual(
            [(e["id"], e["input"]) for e in parsed if e["type"] == "tool_use_end"],
            expected_calls,
        )
        self.assertEqual(parsed[-1]["stop_reason"], "tool_use")


class EndingTests(SimpleTestCase):
    def test_generate_text_raises_on_a_cut_off_and_never_caches(self):
        fixture = _fixture("cut_off_max_output_tokens")
        client = _client()

        def handler(request):
            return httpx.Response(200, json=fixture["body"])

        client._store_cache = MagicMock()
        with self.assertRaises(Exception):
            _run(_with_transport(
                httpx.MockTransport(handler),
                lambda: client.generate_text(
                    system_prompt="sys", user_prompt="hi", provider_preference="openai",
                ),
            ))
        client._store_cache.assert_not_called()

    def test_generate_text_raises_on_a_refusal_without_text(self):
        fixture = _fixture("refusal")
        client = _client()

        def handler(request):
            return httpx.Response(200, json=fixture["body"])

        client._store_cache = MagicMock()
        with self.assertRaises(Exception):
            _run(_with_transport(
                httpx.MockTransport(handler),
                lambda: client.generate_text(
                    system_prompt="sys", user_prompt="hi", provider_preference="openai",
                ),
            ))
        client._store_cache.assert_not_called()

    def test_stream_text_raises_on_a_cut_off(self):
        terminal = _body([_reasoning("rs_x")], status="incomplete", reason="max_output_tokens")
        events = [
            {"type": "response.output_text.delta", "delta": "partial"},
            {"type": "response.incomplete", "response": terminal},
        ]
        with self.assertRaises(Exception):
            _run(self._collect_stream_text(events))

    def test_stream_text_raises_on_a_refusal_without_text(self):
        terminal = _body([{
            "type": "message", "id": "msg_r", "role": "assistant",
            "content": [{"type": "refusal", "refusal": "I can't."}],
        }])
        with self.assertRaises(Exception):
            _run(self._collect_stream_text([
                {"type": "response.completed", "response": terminal},
            ]))

    def _collect_stream_text(self, events):
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
                chunk
                async for chunk in client.stream_text(
                    system_prompt="sys",
                    user_prompt="hi",
                    provider_preference="openai",
                )
            ]

        return _with_transport(httpx.MockTransport(handler), collect)


class ProviderSelectionTests(SimpleTestCase):
    @override_settings(
        OPENAI_API_KEY="fake-openai-key",  # nosec B105 - test fixture
        ANTHROPIC_API_KEY="",
        DEEPSEEK_API_KEY="",
        HF_API_TOKEN="",  # nosec B106 - test fixture: an empty token, not a credential
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
        HF_API_TOKEN="",  # nosec B106 - test fixture: an empty token, not a credential
        LLM_DEFAULT_PROVIDER="",
    )
    def test_openai_key_does_not_displace_an_unset_default(self):
        client = LLMClient()
        self.assertEqual(client.default_provider(), "deepseek")

    @override_settings(
        OPENAI_API_KEY="fake-openai-key",  # nosec B105 - test fixture
        ANTHROPIC_API_KEY="",
        DEEPSEEK_API_KEY="",
        HF_API_TOKEN="",  # nosec B106 - test fixture: an empty token, not a credential
        LLM_DEFAULT_PROVIDER="openai",
    )
    def test_openai_answers_when_named_as_default(self):
        client = LLMClient()
        self.assertEqual(client.default_provider(), "openai")

    @override_settings(
        OPENAI_API_KEY="",
        ANTHROPIC_API_KEY="",
        DEEPSEEK_API_KEY="fake-deepseek-key",  # nosec B105 - test fixture
        HF_API_TOKEN="",  # nosec B106 - test fixture: an empty token, not a credential
        LLM_DEFAULT_PROVIDER="openai",
    )
    def test_default_openai_without_a_key_falls_back(self):
        self.assertEqual(LLMClient().default_provider(), "deepseek")

    def test_named_openai_without_a_key_is_refused(self):
        client = _client(openai=None, deepseek="fake-deepseek-key")  # nosec B105

        def handler(request):
            return httpx.Response(500, json={"error": {"type": "server_error", "code": "boom"}})

        with self.assertRaises(Exception) as raised:
            _run(_with_transport(
                httpx.MockTransport(handler),
                lambda: client.create_message(
                    messages=[{"role": "user", "content": "hi"}],
                    system="sys",
                    provider="openai",
                ),
            ))
        self.assertIn("OpenAI API key is not configured", str(raised.exception))

    def test_openai_preference_is_first_in_the_order(self):
        client = _client()
        self.assertEqual(client._provider_order("default", "openai")[0], "openai")

    def test_openai_is_not_a_trailing_fallback(self):
        client = _client()
        self.assertNotIn("openai", client._provider_order("default", None))
        self.assertNotIn("openai", client._provider_order("planner", None))
        self.assertIn("openai", client._provider_order("default", "openai"))

    def test_a_named_non_openai_preference_never_reaches_openai(self):
        client = _client(openai="fake-openai-key", deepseek="fake-deepseek-key")  # nosec B105
        urls = []

        def handler(request):
            urls.append(request.url.host)
            return httpx.Response(500, json={"error": {"type": "server_error", "code": "boom"}})

        with self.assertRaises(Exception):
            _run(_with_transport(
                httpx.MockTransport(handler),
                lambda: client.generate_text(
                    system_prompt="sys", user_prompt="hi", provider_preference="deepseek",
                ),
            ))
        self.assertTrue(urls)
        self.assertNotIn("api.openai.com", urls)

    @override_settings(LLM_EXECUTOR_PROVIDER="deepseek")
    def test_a_named_role_never_reaches_openai(self):
        client = _client()
        self.assertNotIn("openai", client._provider_order("executor", None))


@override_settings(
    OPENAI_API_KEY="fake-openai-key",  # nosec B105 - test fixture
    ANTHROPIC_API_KEY="",
    DEEPSEEK_API_KEY="",
    HF_API_TOKEN="",  # nosec B106 - test fixture: an empty token, not a credential
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


PRIOR_TURN = [
    {"role": "user", "content": "What's the weather in Nairobi?"},
    {
        "role": "assistant",
        "content": [
            {
                "type": "provider_state",
                "provider": "openai",
                "model": "gpt-6.1-sol",
                "items": [
                    _reasoning("rs_prior"),
                    _function_call("fc_prior", "call_prior"),
                    _message("Let me check."),
                ],
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


class ProviderStateStrippingTests(SimpleTestCase):
    def _anthropic_response(self):
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "usage": {}},
        )

    def test_anthropic_request_drops_provider_state(self):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return self._anthropic_response()

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
        self.assertNotIn("enc-rs_prior", serialized)
        self.assertIn("call_prior", serialized)

    def test_streaming_anthropic_request_drops_provider_state(self):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                content=b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{}}\n\n',
            )

        client = _client(anthropic="fake-anthropic-key")  # nosec B105 - test fixture

        async def collect():
            return [
                event
                async for event in client._stream_anthropic_events(
                    messages=PRIOR_TURN,
                    system="sys",
                    tools=None,
                    temperature=0.3,
                    max_tokens=64,
                    model="claude-x",
                )
            ]

        _run(_with_transport(httpx.MockTransport(handler), collect))
        serialized = json.dumps(captured["body"]["messages"])
        self.assertNotIn("provider_state", serialized)
        self.assertNotIn("enc-rs_prior", serialized)
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
        self.assertNotIn("enc-rs_prior", serialized)

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
        self.assertNotIn("enc-rs_prior", text)
        self.assertNotIn("provider_state", text)


class StrictSchemaTests(SimpleTestCase):
    def test_nested_object_and_array_schemas_are_checked(self):
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "nested": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                },
                "items": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["nested", "items"],
        }
        self.assertTrue(_strict_schema(schema))

    def test_a_top_level_strict_schema_with_a_loose_nested_object_is_rejected(self):
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "nested": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                },
            },
            "required": ["nested"],
        }
        self.assertFalse(_strict_schema(schema))

    def test_a_loose_nested_array_item_is_rejected(self):
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                        "required": ["value"],
                    },
                },
            },
            "required": ["items"],
        }
        self.assertFalse(_strict_schema(schema))

    def test_tools_only_get_strict_when_the_schema_allows_it(self):
        strict_tool = {
            "name": "strict",
            "description": "x",
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
        }
        loose_tool = {
            "name": "loose",
            "description": "x",
            "input_schema": {"type": "object", "properties": {"value": {"type": "string"}}},
        }
        by_name = {tool["name"]: tool for tool in tools_to_responses([strict_tool, loose_tool])}
        self.assertTrue(by_name["strict"]["strict"])
        self.assertFalse(by_name["loose"]["strict"])


class CutOffNeverDispatchesTests(SimpleTestCase):
    def test_a_cut_off_offered_tool_is_not_run(self):
        from orchestration.tool_schemas import get_tool_definitions

        fixture = _fixture("cut_off_with_tool_call")
        parsed = parse_responses_body(fixture["body"])
        offered = {tool["name"] for tool in get_tool_definitions()}
        self.assertIn("set_reminder", offered)
        self.assertTrue(any(
            block.get("type") == "tool_use" and block.get("name") == "set_reminder"
            for block in parsed["content"]
        ))

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
