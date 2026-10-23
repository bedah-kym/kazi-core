"""C3: a failed persona lookup removes tool reach for the turn.

A lookup that errors is not the same as "this room has no persona". The first
must fail closed: offer no tools, tell the model why, refuse anything that
still arrives, and end a delegated run with the same reason. The second keeps
today's behaviour (no persona bounds).
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase

from orchestration.agent_loop import LoopState, _run_sub_agent, run_agent_loop

REASON = "settings could not be checked"


def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def collect_events(async_gen):
    events = []
    async for event in async_gen:
        events.append(event)
    return events


def _response(content, stop_reason="end_turn"):
    return {
        "content": content,
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def _tool_use(call_id, name, tool_input):
    return {"type": "tool_use", "id": call_id, "name": name, "input": tool_input}


HISTORY_WITH_TOOLS = [
    {"role": "user", "content": "What's the weather?"},
    {"role": "assistant", "content": [_tool_use("hist-1", "get_weather", {"city": "Nairobi"})]},
    {"role": "user", "content": [{
        "type": "tool_result", "tool_use_id": "hist-1", "content": "22C",
    }]},
]


def _has_tool_blocks(messages):
    for message in messages:
        content = message.get("content")
        if isinstance(content, list) and any(
            isinstance(block, dict) and block.get("type") in ("tool_use", "tool_result")
            for block in content
        ):
            return True
    return False


class PersonaLookupDistinctionTests(SimpleTestCase):
    def test_lookup_raises_with_the_cause_class_and_no_message_text(self):
        from orchestration.personas import PersonaLookupError, lookup_room_persona

        with patch("workflows.models.Persona.objects") as manager:
            manager.filter.side_effect = RuntimeError("db password=secret")
            with self.assertRaises(PersonaLookupError) as caught:
                lookup_room_persona(1, 1)

        self.assertEqual(caught.exception.cause_type, "RuntimeError")
        self.assertNotIn("secret", str(caught.exception))

    def test_legacy_resolver_still_returns_none_on_failure(self):
        from orchestration.personas import resolve_room_persona

        with patch("workflows.models.Persona.objects") as manager, \
                self.assertLogs("orchestration.personas", level="WARNING") as logs:
            manager.filter.side_effect = RuntimeError("db password=secret")
            self.assertIsNone(resolve_room_persona(1, 1))

        self.assertIn("RuntimeError", logs.output[0])
        self.assertNotIn("secret", logs.output[0])


class PersonaLookupFailureMainLoopTests(SimpleTestCase):
    def _run_loop(self, llm, history=None, resumed_state=None, confirmed_tool=False):
        executor = AsyncMock(return_value={"status": "success"})
        record = MagicMock()
        logger = MagicMock()
        with patch("orchestration.agent_loop.get_llm_client", return_value=llm), \
                patch("orchestration.agent_loop._execute_with_timeout", new=executor), \
                patch("orchestration.agent_loop.record_event", new=record), \
                patch("orchestration.agent_loop.logger", new=logger), \
                patch("orchestration.agent_loop.cache"), \
                patch("workflows.models.Persona.objects") as manager:
            manager.filter.side_effect = RuntimeError("db password=secret")
            events = run_async(collect_events(run_agent_loop(
                user_message="What's the weather?",
                context={
                    "user_id": 1, "room_id": 1, "username": "test",
                    "caps_enforced": True,
                },
                history=history,
                resumed_state=resumed_state,
                confirmed_tool=confirmed_tool,
            )))
        return events, executor, llm, record, logger

    def _assert_failure_logged_once(self, logger):
        self.assertEqual(logger.error.call_count, 1)
        args = logger.error.call_args.args
        self.assertEqual(args[2], "RuntimeError")
        self.assertNotIn("secret", str(args))

    def _assert_request_is_valid(self, kwargs, label):
        if _has_tool_blocks(kwargs["messages"]):
            self.assertTrue(
                kwargs["tools"],
                f"{label}: tool blocks need tool definitions",
            )

    def test_no_tool_blocks_still_gets_the_harness_note(self):
        llm = MagicMock()
        llm.create_message = AsyncMock(return_value=_response([
            {"type": "text", "text": "I could not check this room's settings."},
        ]))
        events, executor, llm, record, logger = self._run_loop(llm)

        executor.assert_not_awaited()
        self.assertNotIn("tool_start", [event.kind for event in events])
        kwargs = llm.create_message.await_args.kwargs
        self.assertIn(REASON, kwargs["system"])

        self._assert_failure_logged_once(logger)
        events_logged = [call.args[0] for call in record.call_args_list]
        self.assertIn("persona_lookup_failed", events_logged)

    def test_replayed_tool_blocks_keep_the_request_valid_and_calls_refused(self):
        llm = MagicMock()
        llm.create_message = AsyncMock(side_effect=[
            _response(
                [_tool_use("t1", "get_weather", {"city": "Nairobi"})],
                stop_reason="tool_use",
            ),
            _response([{"type": "text", "text": "Tools are unavailable."}]),
        ])
        events, executor, llm, record, logger = self._run_loop(
            llm, history=HISTORY_WITH_TOOLS,
        )

        executor.assert_not_awaited()
        self.assertNotIn("tool_start", [event.kind for event in events])
        calls = llm.create_message.await_args_list
        for index, call in enumerate(calls):
            self.assertIn(REASON, call.kwargs["system"])
            self._assert_request_is_valid(call.kwargs, f"call {index + 1}")
        self.assertIn(REASON, json.dumps(calls[1].kwargs["messages"], default=str))
        self._assert_failure_logged_once(logger)

    def test_confirmation_resume_with_tool_history_keeps_the_request_valid(self):
        pending = {
            "id": "t-resume", "name": "send_email",
            "input": {"to": "example@example.com", "subject": "Hi", "text": "Hello"},
        }
        state = LoopState(
            messages=[
                {"role": "user", "content": "Email john"},
                {"role": "assistant", "content": [
                    _tool_use("hist-1", "get_weather", {"city": "Nairobi"}),
                ]},
                {"role": "user", "content": [{
                    "type": "tool_result", "tool_use_id": "hist-1", "content": "22C",
                }]},
                {"role": "assistant", "content": [_tool_use("t-resume", "send_email", pending["input"])]},
            ],
            pending_tool=pending,
            pending_tier="destructive",
        )
        llm = MagicMock()
        llm.create_message = AsyncMock(return_value=_response([
            {"type": "text", "text": "The room's settings could not be checked."},
        ]))
        events, executor, llm, record, logger = self._run_loop(
            llm, resumed_state=state, confirmed_tool=True,
        )

        executor.assert_not_awaited()
        self.assertIsNone(state.pending_tool)
        self.assertNotIn("tool_start", [event.kind for event in events])
        kwargs = llm.create_message.await_args.kwargs
        self.assertIn(REASON, kwargs["system"])
        self._assert_request_is_valid(kwargs, "resume")
        self.assertIn(
            REASON, json.dumps(kwargs["messages"], default=str),
            "the pending call must be answered with the refusal reason",
        )
        self._assert_failure_logged_once(logger)


class PersonaLookupFailureDelegatedTests(SimpleTestCase):
    def test_a_delegated_run_makes_no_tool_call_and_ends_with_the_reason(self):
        llm = MagicMock()
        llm.create_message = AsyncMock(side_effect=[
            _response(
                [_tool_use("t1", "get_weather", {"city": "Nairobi"})],
                stop_reason="tool_use",
            ),
            _response([{"type": "text", "text": "done"}]),
        ])
        executor = AsyncMock(return_value={"status": "success"})
        record = MagicMock()
        logger = MagicMock()
        with patch("orchestration.agent_loop.get_llm_client", return_value=llm), \
                patch("orchestration.agent_loop._execute_with_timeout", new=executor), \
                patch("orchestration.agent_loop.record_event", new=record), \
                patch("orchestration.agent_loop.logger", new=logger), \
                patch("workflows.models.Persona.objects") as manager:
            manager.filter.side_effect = RuntimeError("db password=secret")
            result = run_async(_run_sub_agent(
                {"task": "check the weather"},
                {"user_id": 1, "room_id": 2},
                None,
                "system",
                [{"name": "get_weather", "input_schema": {}}],
            ))

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["stopped_reason"], "persona_lookup_failed")
        self.assertIn(REASON, result["message"])
        self.assertEqual(result["tools_used"], [])
        self.assertEqual(result["tool_calls"], 0)
        llm.create_message.assert_not_awaited()
        executor.assert_not_awaited()

        self.assertEqual(logger.error.call_count, 1)
        args = logger.error.call_args.args
        self.assertEqual(args[2], "RuntimeError")
        self.assertNotIn("secret", str(args))
        events_logged = [call.args[0] for call in record.call_args_list]
        self.assertIn("persona_lookup_failed", events_logged)
