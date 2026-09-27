"""Provider continuity fixtures (#171).

One multi-step tool-call turn per provider adapter. Asserts that reasoning and
tool results stay paired inside a turn, and that a new user message starts a
clean context. All HTTP is mocked — no network, no provider spend.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import httpx
from django.test import SimpleTestCase

from orchestration.llm_client import LLMClient

# A canonical two-step turn in Anthropic message shape:
# user -> assistant(text + tool_use) -> user(tool_result) -> assistant(final)
TURN = [
    {"role": "user", "content": "What's the weather in Nairobi?"},
    {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "Let me check."},
            {"type": "tool_use", "id": "call_1", "name": "get_weather", "input": {"city": "Nairobi"}},
        ],
    },
    {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "23C"}],
    },
    {"role": "assistant", "content": [{"type": "text", "text": "It's 23C."}]},
]

NEW_USER = {"role": "user", "content": "Thanks. And tomorrow?"}


def _run(coro):
    return asyncio.run(coro)


class DeepSeekContinuityTests(SimpleTestCase):
    def setUp(self):
        self.client = LLMClient()

    def test_tool_call_and_result_stay_paired_within_a_turn(self):
        out = self.client._anthropic_to_openai_messages("sys", TURN)
        self.assertEqual([m["role"] for m in out], ["system", "user", "assistant", "tool", "assistant"])
        assistant = out[2]
        self.assertEqual(assistant["tool_calls"][0]["id"], "call_1")
        self.assertEqual(assistant["tool_calls"][0]["function"]["name"], "get_weather")
        self.assertEqual(out[3]["tool_call_id"], "call_1")
        self.assertEqual(out[3]["content"], "23C")

    def test_new_user_message_is_a_clean_turn(self):
        out = self.client._anthropic_to_openai_messages("sys", TURN + [NEW_USER])
        last = out[-1]
        self.assertEqual(last["role"], "user")
        self.assertEqual(last["content"], "Thanks. And tomorrow?")
        self.assertNotIn("tool_calls", last)

    def test_response_maps_tool_call_id_back(self):
        data = {
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{
                        "id": "call_1",
                        "function": {"name": "get_weather", "arguments": '{"city": "Nairobi"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
        }
        blocks = self.client._openai_response_to_anthropic(data)["content"]
        self.assertEqual(blocks[0]["type"], "tool_use")
        self.assertEqual(blocks[0]["id"], "call_1")


class ClaudeContinuityTests(SimpleTestCase):
    def setUp(self):
        self.client = LLMClient()
        self.client.anthropic_key = "test-key"  # nosec B105 - test fixture

    def _capture_body(self, messages):
        captured = {}

        async def fake_post(self, url, headers=None, json=None):
            captured["json"] = json
            return httpx.Response(
                200,
                json={"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "usage": {}},
            )

        with patch.object(httpx.AsyncClient, "post", new=fake_post), \
                patch.object(self.client, "_record_token_usage"):
            _run(self.client._call_anthropic_create(
                messages=messages,
                system="sys",
                tools=None,
                temperature=0.2,
                max_tokens=100,
                user_id=None,
                model="claude-x",
                use_prompt_cache=False,
            ))
        return captured["json"]

    def test_tool_use_and_result_stay_paired(self):
        body = self._capture_body(TURN)
        messages = body["messages"]
        self.assertEqual(messages[1]["content"][1]["type"], "tool_use")
        self.assertEqual(messages[1]["content"][1]["id"], "call_1")
        self.assertEqual(messages[2]["content"][0]["type"], "tool_result")
        self.assertEqual(messages[2]["content"][0]["tool_use_id"], "call_1")

    def test_new_user_message_is_a_separate_turn(self):
        body = self._capture_body(TURN + [NEW_USER])
        last = body["messages"][-1]
        self.assertEqual(last["role"], "user")
        self.assertEqual(last["content"], "Thanks. And tomorrow?")
