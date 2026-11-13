"""One end-to-end OpenAI turn through the real agent loop.

The LLM is the real ``LLMClient`` and the real Responses adapter; only the
network is faked (``httpx.MockTransport``). The gated action uses a real
in-process connector stand-in, so no external service is touched.

Shows reasoning items survive the encrypted pause and are sent back on the
resumed call.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import httpx
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from chatbot.models import Chatroom
from orchestration import llm_client as llm_client_module
from orchestration import turns as turns_mod
from orchestration.agent_loop import (
    DEFAULT_TURN_TOKEN_BUDGET,
    _harness_budget_note,
    _serialize_tool_result,
    reset_circuit_breakers,
    run_agent_loop,
    resume_after_confirmation,
)
from orchestration.connector_registry import discover_connectors
from orchestration.models import Turn


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _reasoning(text: str) -> dict:
    return {
        "type": "reasoning",
        "id": "rs_e2e",
        "summary": [],
        "content": [],
        "encrypted_content": text,
    }


def _usage(input_tokens=30, output_tokens=10, cached=0) -> dict:
    return {
        "input_tokens": input_tokens,
        "input_tokens_details": {"cached_tokens": cached, "cache_write_tokens": 0},
        "output_tokens": output_tokens,
        "output_tokens_details": {"reasoning_tokens": 4},
        "total_tokens": input_tokens + output_tokens,
    }


def _tool_response() -> dict:
    return {
        "id": "resp_e2e_tool",
        "object": "response",
        "status": "completed",
        "model": "gpt-6.1-sol",
        "output": [
            _reasoning("encrypted-reasoning-e2e"),
            {
                "type": "function_call",
                "id": "fc_e2e",
                "call_id": "call_e2e",
                "name": "send_email",
                "arguments": json.dumps({
                    "to": "ops@example.com",
                    "subject": "Report",
                    "text": "Here it is.",
                }),
                "status": "completed",
            },
        ],
        "usage": _usage(),
    }


def _final_response() -> dict:
    return {
        "id": "resp_e2e_final",
        "object": "response",
        "status": "completed",
        "model": "gpt-6.1-sol",
        "output": [
            _reasoning("encrypted-reasoning-final"),
            {
                "type": "message",
                "id": "msg_e2e_final",
                "role": "assistant",
                "status": "completed",
                "phase": "final_answer",
                "content": [{"type": "output_text", "text": "Sent.", "annotations": []}],
            },
        ],
        "usage": _usage(input_tokens=52, output_tokens=6),
    }


class _FakeEmailConnector:
    name = "fake_email"

    def __init__(self):
        self.calls = []

    async def preview(self, parameters, context):
        return None

    async def execute(self, parameters, context):
        self.calls.append(dict(parameters))
        return {"status": "success", "data": {"id": "fake-email-1"}}


@override_settings(
    OPENAI_API_KEY="fake-openai-key",  # nosec B105 - test fixture
    ANTHROPIC_API_KEY="",
    DEEPSEEK_API_KEY="",
    HF_API_TOKEN="",  # nosec B106 - test fixture: an empty token, not a credential
    LLM_DEFAULT_PROVIDER="",
)
class OpenAITurnTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        reset_circuit_breakers()
        llm_client_module._client = None
        self.addCleanup(lambda: setattr(llm_client_module, "_client", None))
        self.addCleanup(reset_circuit_breakers)

        User = get_user_model()
        self.user = User.objects.create_user(username="openai-user", password="pw")  # nosec B106
        self.room = Chatroom.objects.create(model_pref="openai/gpt-6.1-sol")
        self.turn = Turn.objects.create(
            room=self.room,
            user=self.user,
            state="running",
            version=0,
            started_at=timezone.now(),
            attempt=1,
        )

        self.connector = _FakeEmailConnector()
        connectors = discover_connectors()
        self._original_connector = connectors.get("send_email")
        connectors["send_email"] = self.connector

        def restore():
            if self._original_connector is None:
                connectors.pop("send_email", None)
            else:
                connectors["send_email"] = self._original_connector

        self.addCleanup(restore)

    def _context(self) -> dict:
        return {
            "user_id": self.user.id,
            "room_id": self.room.id,
            "username": self.user.username,
            "turn_id": str(self.turn.pk),
            "turn_live": lambda: True,
            "preferences": {"allow_email": True},
        }

    def test_tool_call_pauses_and_resumes_with_reasoning_intact(self):
        requests = []

        def handler(request):
            body = json.loads(request.content)
            requests.append(body)
            return httpx.Response(
                200,
                json=_tool_response() if len(requests) == 1 else _final_response(),
            )

        transport = httpx.MockTransport(handler)

        async def scenario():
            events = []
            async for event in run_agent_loop(
                user_message="Send the report.",
                context=self._context(),
                preferences={"allow_email": True},
            ):
                events.append(event)

            await sync_to_async(self.turn.refresh_from_db)()
            paused = await sync_to_async(turns_mod.get_turn_paused_state)(self.turn)

            resumed = []
            async for event in resume_after_confirmation(
                context=self._context(),
                preferences={"allow_email": True},
            ):
                resumed.append(event)
            return events, paused, resumed

        async def run_with_client():
            async with httpx.AsyncClient(transport=transport) as http:
                with patch(
                    "orchestration.llm_client._get_http_client",
                    return_value=http,
                ), patch(
                    "orchestration.agent_loop._resolve_persona",
                    new_callable=AsyncMock,
                    return_value=None,
                ), patch(
                    "orchestration.agent_loop.enforce_agent_caps",
                    return_value=True,
                ), patch(
                    "orchestration.agent_loop._search_backend_available",
                    return_value=False,
                ):
                    return await scenario()

        first, paused, resumed = _run(run_with_client())

        self.assertTrue(any(event.kind == "confirmation" for event in first))
        self.assertTrue(any(event.kind == "paused" for event in first))
        self.assertEqual(len(self.connector.calls), 1)

        self.assertIsNotNone(paused)
        blocks = [
            block
            for message in paused["messages"]
            if isinstance(message.get("content"), list)
            for block in message["content"]
            if isinstance(block, dict) and block.get("type") == "provider_state"
        ]
        self.assertEqual(blocks[0]["model"], "gpt-6.1-sol")
        self.assertEqual(blocks[0]["items"], _tool_response()["output"])
        self.assertNotIn("encrypted-reasoning-e2e", str(self.turn.paused_state_enc))

        self.assertEqual(len(requests), 2)
        expected_output = _serialize_tool_result(
            "send_email", {"status": "success", "data": {"id": "fake-email-1"}},
        )
        first_note = _harness_budget_note(0, DEFAULT_TURN_TOKEN_BUDGET)
        second_note = _harness_budget_note(10, DEFAULT_TURN_TOKEN_BUDGET)
        self.assertEqual(
            requests[1]["input"],
            [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Send the report." + first_note}],
                },
                *_tool_response()["output"],
                {
                    "type": "function_call_output",
                    "call_id": "call_e2e",
                    "output": expected_output,
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": second_note}],
                },
            ],
        )

        self.assertTrue(any(
            event.kind == "done" and event.data.get("end_reason") == "finished"
            for event in resumed
        ))
        self.assertFalse(any(event.kind == "error" for event in resumed))
        shown = "".join(
            str(event.data.get("text") or "")
            for event in first + resumed
            if event.kind in ("text", "text_delta", "harness")
        )
        self.assertNotIn("encrypted-reasoning-e2e", shown)

    def test_unknown_status_ends_as_an_error_with_nothing_dispatched(self):
        bad = {
            "id": "resp_bad",
            "object": "response",
            "status": "in_progress",
            "model": "gpt-6.1-sol",
            "output": [
                _reasoning("encrypted-reasoning-bad"),
                {
                    "type": "function_call",
                    "id": "fc_bad",
                    "call_id": "call_bad",
                    "name": "send_email",
                    "arguments": json.dumps({
                        "to": "ops@example.com",
                        "subject": "Report",
                        "text": "Here it is.",
                    }),
                    "status": "in_progress",
                },
            ],
            "usage": _usage(),
        }

        def handler(request):
            return httpx.Response(200, json=bad)

        async def scenario():
            events = []
            async for event in run_agent_loop(
                user_message="Send the report.",
                context=self._context(),
                preferences={"allow_email": True},
            ):
                events.append(event)
            return events

        async def run_with_client():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
                with patch(
                    "orchestration.llm_client._get_http_client",
                    return_value=http,
                ), patch(
                    "orchestration.agent_loop._resolve_persona",
                    new_callable=AsyncMock,
                    return_value=None,
                ), patch(
                    "orchestration.agent_loop.enforce_agent_caps",
                    return_value=True,
                ), patch(
                    "orchestration.agent_loop._search_backend_available",
                    return_value=False,
                ):
                    return await scenario()

        events = _run(run_with_client())

        self.assertEqual(self.connector.calls, [])
        self.assertFalse(any(event.kind == "tool_start" for event in events))
        self.assertTrue(any(event.kind == "error" for event in events))
        self.assertFalse(any(
            event.kind == "done" and event.data.get("end_reason") == "finished"
            for event in events
        ))
