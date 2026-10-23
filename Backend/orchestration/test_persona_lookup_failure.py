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

from orchestration.agent_loop import _run_sub_agent, run_agent_loop

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
    def _run_loop(self, llm):
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
            )))
        return events, executor, llm, record, logger

    def _assert_failure_logged_once(self, logger):
        self.assertEqual(logger.error.call_count, 1)
        args = logger.error.call_args.args
        self.assertEqual(args[2], "RuntimeError")
        self.assertNotIn("secret", str(args))

    def test_no_tools_are_offered_and_the_model_is_told_why(self):
        llm = MagicMock()
        llm.create_message = AsyncMock(return_value=_response([
            {"type": "text", "text": "I could not check this room's settings."},
        ]))
        events, executor, llm, record, logger = self._run_loop(llm)

        executor.assert_not_awaited()
        self.assertNotIn("tool_start", [event.kind for event in events])
        kwargs = llm.create_message.await_args.kwargs
        self.assertIsNone(kwargs["tools"])
        self.assertIn(REASON, kwargs["system"])

        self._assert_failure_logged_once(logger)
        events_logged = [call.args[0] for call in record.call_args_list]
        self.assertIn("persona_lookup_failed", events_logged)

    def test_a_tool_call_that_still_arrives_is_refused_with_the_same_reason(self):
        llm = MagicMock()
        llm.create_message = AsyncMock(side_effect=[
            _response(
                [_tool_use("t1", "get_weather", {"city": "Nairobi"})],
                stop_reason="tool_use",
            ),
            _response([{"type": "text", "text": "Tools are unavailable."}]),
        ])
        events, executor, llm, record, logger = self._run_loop(llm)

        executor.assert_not_awaited()
        self.assertNotIn("tool_start", [event.kind for event in events])
        second_messages = llm.create_message.await_args_list[1].kwargs["messages"]
        self.assertIn(REASON, json.dumps(second_messages, default=str))
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
