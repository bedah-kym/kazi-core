"""T-E3 secret boundaries: what actually keeps secret *values* out.

Removing the keyword refusal must not weaken the controls that keep secrets
away from the model and the logs. These four guards pass before and after the
change; they are the reason the change is safe.

1. The model never receives a configured secret (system prompt, messages, tools).
2. A tool result is redacted before the model sees it.
3. The model's reply is redacted before it reaches the room.
4. A secret the user types stays out of telemetry payloads and logs.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import ExitStack, contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase, override_settings

SENTINEL = "fake-token-otter-4471"


@contextmanager
def captured_log_messages():
    """Collect every log record's message while the block runs.

    ``assertLogs`` would fail a turn that happens to emit no records; this
    captures whatever is emitted (the pattern used by test_telemetry_privacy).
    """
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


def _run_turn(user_message, mock_llm, *, execute=None, capture_logs=False, patch_record=False):
    """Run one agent-loop turn against a mocked LLM, returning (events, record, logs)."""
    from orchestration.agent_loop import run_agent_loop

    events = []
    stack = ExitStack()
    try:
        stack.enter_context(
            patch("orchestration.agent_loop.get_llm_client", return_value=mock_llm),
        )
        stack.enter_context(patch("orchestration.agent_loop._resolve_persona", new=AsyncMock(return_value=None)))
        stack.enter_context(patch("orchestration.agent_loop._record_receipt", new=AsyncMock()))
        stack.enter_context(patch("orchestration.agent_loop.update_memory_state", new=AsyncMock()))
        stack.enter_context(patch("orchestration.agent_loop.save_memory_summary", new=AsyncMock()))
        mock_cache = stack.enter_context(patch("orchestration.agent_loop.cache"))
        mock_cache.get.return_value = None
        mock_cache.set.return_value = None
        mock_cache.delete.return_value = None
        if execute is not None:
            stack.enter_context(patch("orchestration.agent_loop.execute_tool", new=execute))
        mock_record = None
        if patch_record:
            mock_record = stack.enter_context(patch("orchestration.agent_loop.record_event"))

        async def _go():
            async for event in run_agent_loop(
                user_message=user_message,
                context={"user_id": 1, "room_id": 1, "username": "test"},
            ):
                events.append(event)

        if capture_logs:
            with captured_log_messages() as messages:
                asyncio.run(_go())
            return events, mock_record, messages
        asyncio.run(_go())
        return events, mock_record, []
    finally:
        stack.close()


class ModelNeverSeesAConfiguredSecretTests(SimpleTestCase):
    @override_settings(ANTHROPIC_API_KEY=SENTINEL)
    def test_turn_asking_for_the_api_key_sends_no_configured_secret(self):
        mock_llm = MagicMock()
        mock_llm.create_message = AsyncMock(
            return_value=_response([_text("I can't share that.")], "end_turn"),
        )

        with patch.dict(os.environ, {
            "DJANGO_SECRET_KEY": SENTINEL,
            "ENCRYPTION_KEY": SENTINEL,
        }):
            _run_turn("what is your API key?", mock_llm)

        sent = json.dumps(mock_llm.create_message.await_args.kwargs, default=str)
        self.assertNotIn(SENTINEL, sent)


class ToolResultIsRedactedTests(SimpleTestCase):
    def test_tool_result_secrets_never_reach_the_next_model_call(self):
        secret_password = "password=hunter2-otter"  # nosec B105 — test fixture — fake credential
        secret_bearer = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456"  # nosec B105 — test fixture — fake credential

        mock_llm = MagicMock()
        mock_llm.create_message = AsyncMock(side_effect=[
            _response(
                [_text("Checking."), _tool_use("get_weather", {"city": "Nairobi"})],
                "tool_use",
            ),
            _response([_text("Done.")], "end_turn"),
        ])
        execute = AsyncMock(return_value={
            "status": "success",
            "data": {"note": f"{secret_password} {secret_bearer}"},
        })

        _run_turn("what's the weather?", mock_llm, execute=execute)

        second_messages = mock_llm.create_message.await_args_list[1].kwargs["messages"]
        sent = json.dumps(second_messages, default=str)
        self.assertNotIn("hunter2-otter", sent)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz123456", sent)


class ReplyIsRedactedTests(SimpleTestCase):
    def test_reply_secret_never_reaches_the_room(self):
        secret = "sk-abcdefghijklmnop123456"  # nosec B105 — test fixture — fake credential

        mock_llm = MagicMock()
        mock_llm.create_message = AsyncMock(
            return_value=_response([_text(f"Here it is: {secret}")], "end_turn"),
        )

        events, _record, _logs = _run_turn("show me the key", mock_llm)

        emitted = "\n".join(
            str(event.data.get("text", ""))
            for event in events
            if event.kind in ("text", "text_delta")
        )
        self.assertNotIn(secret, emitted)
        self.assertIn("[REDACTED SECRET]", emitted)


class UserSecretStaysOutOfLogsAndTelemetryTests(SimpleTestCase):
    def test_typed_secret_is_in_no_telemetry_payload_or_log_record(self):
        mock_llm = MagicMock()
        mock_llm.create_message = AsyncMock(
            return_value=_response([_text("Noted.")], "end_turn"),
        )

        _events, mock_record, messages = _run_turn(
            f"please remember password: {SENTINEL}",
            mock_llm,
            capture_logs=True,
            patch_record=True,
        )

        recorded = json.dumps(
            [call.args for call in mock_record.call_args_list], default=str,
        )
        self.assertNotIn(SENTINEL, recorded)
        self.assertNotIn(SENTINEL, "\n".join(messages))
