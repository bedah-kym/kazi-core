"""217a-1a: facts only the loop can give the stream (#217a).

- Each text block says whether it is narration (written before a tool step) or
  the answer; harness lines are harness events, never model text.
- A call is announced with its id before the fence check, so a consumer that
  is slow on that event can never sit between the check and the dispatch. A
  fenced batch answers every announced call with a not-run result.
- An approved resume announces each call before its executor runs.
- Every call in a parallel batch reports its own duration.
- The approval card carries each call's id and the pause's expiry.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase

from orchestration.agent_loop import (
    LoopState,
    reset_circuit_breakers,
    run_agent_loop,
)


NOT_STARTED_REASON = "the turn ended before this call started"


def _response(content, stop_reason="end_turn", output_tokens=5):
    return {
        "content": content,
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 10, "output_tokens": output_tokens},
    }


def _text(text):
    return {"type": "text", "text": text}


def _tool(call_id, name, tool_input):
    return {"type": "tool_use", "id": call_id, "name": name, "input": tool_input}


def _Llm(*responses):
    llm = MagicMock()
    llm.create_message = AsyncMock(side_effect=list(responses))
    return llm


class LoopFactsTests(SimpleTestCase):
    def setUp(self):
        reset_circuit_breakers()
        self.addCleanup(reset_circuit_breakers)
        self.executor = AsyncMock(return_value={"status": "success", "temperature": 22})
        patches = [
            patch("orchestration.agent_loop._resolve_persona", new=AsyncMock(return_value=None)),
            patch("orchestration.agent_loop._execute_with_timeout", new=self.executor),
            patch("orchestration.agent_loop._record_receipt", new=AsyncMock(return_value=None)),
            patch("orchestration.agent_loop.record_event"),
            patch("orchestration.agent_loop.cache"),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def _run(self, llm, *, context=None, consume=None, **kwargs):
        """Drive the real loop; ``consume`` sees each event as it is yielded."""
        base = {"user_id": 1, "room_id": None, "username": "test", "caps_enforced": True}
        base.update(context or {})

        async def drive():
            events = []
            with patch("orchestration.agent_loop.get_llm_client", return_value=llm):
                async for event in run_agent_loop(
                    user_message=kwargs.pop("user_message", "hello"),
                    context=base,
                    **kwargs,
                ):
                    events.append(event)
                    if consume is not None:
                        await consume(event)
            return events

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(drive())
        finally:
            loop.close()

    # ---- channels ------------------------------------------------------ #

    def test_text_before_a_tool_step_is_narration_and_the_last_text_is_the_answer(self):
        llm = _Llm(
            _response(
                [_text("Let me check."), _tool("c1", "get_weather", {"city": "Paris"})],
                stop_reason="tool_use",
            ),
            _response([_text("It is 22C in Paris.")]),
        )
        events = self._run(llm)
        texts = [
            (e.data["text"], e.data.get("channel"))
            for e in events if e.kind == "text_delta"
        ]
        self.assertEqual(texts, [
            ("Let me check.", "narration"),
            ("It is 22C in Paris.", "answer"),
        ])

    def test_text_in_a_step_that_asks_for_more_budget_is_the_answer(self):
        llm = _Llm(_response(
            [_text("Scanning 40 files needs more budget; may I continue?"),
             _tool("b1", "request_more_budget", {"reason": "40 files"})],
            stop_reason="tool_use",
        ))
        with patch("orchestration.agent_loop.save_loop_state"):
            events = self._run(llm, context={"room_id": 7})
        self.assertEqual(
            [(e.data["text"], e.data.get("channel")) for e in events if e.kind == "text_delta"],
            [("Scanning 40 files needs more budget; may I continue?", "answer")],
        )

    def test_running_low_is_a_harness_event_not_model_text(self):
        llm = _Llm(_response([_text("Done.")], output_tokens=10_000_000))
        events = self._run(llm)
        note = "*(Running low on this request's budget.)*"
        self.assertEqual(
            [e.kind for e in events if note in str(e.data.get("text", ""))],
            ["harness"],
        )

    def test_the_refusal_note_is_a_harness_event_not_model_text(self):
        llm = _Llm(_response([], stop_reason="refusal"))
        events = self._run(llm)
        note = "*(The model declined to answer this request.)*"
        self.assertEqual(
            [e.kind for e in events if note in str(e.data.get("text", ""))],
            ["harness"],
        )

    # ---- dispatch inside the fence -------------------------------------- #

    def test_a_fence_while_the_dispatch_event_is_consumed_runs_nothing(self):
        live = {"value": True}
        llm = _Llm(_response(
            [_tool("c1", "get_weather", {"city": "Paris"}),
             _tool("c2", "get_weather", {"city": "Rome"})],
            stop_reason="tool_use",
        ))

        async def consume(event):
            # The consumer is slow on the announcement; the lease lapses.
            if event.kind == "tool_dispatch":
                live["value"] = False

        events = self._run(
            llm, consume=consume,
            context={"turn_live": lambda: live["value"]},
        )
        self.executor.assert_not_awaited()
        dispatched = [e.data["call_id"] for e in events if e.kind == "tool_dispatch"]
        self.assertEqual(dispatched, ["c1", "c2"])
        not_run = [
            (e.data["id"], e.data["result"]["status"], e.data["result"]["message"])
            for e in events if e.kind == "tool_result"
        ]
        self.assertEqual(not_run, [
            ("c1", "not_started", NOT_STARTED_REASON),
            ("c2", "not_started", NOT_STARTED_REASON),
        ])
        self.assertTrue(all(e.data.get("not_run") for e in events if e.kind == "tool_result"))

    def test_each_call_is_announced_before_it_runs(self):
        order = []

        async def executor(name, tool_input, context):
            order.append(("run", tool_input["city"]))
            return {"status": "success"}

        self.executor.side_effect = executor
        llm = _Llm(
            _response([_tool("c1", "get_weather", {"city": "Paris"})], stop_reason="tool_use"),
            _response([_text("Done.")]),
        )

        async def consume(event):
            if event.kind == "tool_dispatch":
                order.append(("dispatch", event.data["call_id"]))

        self._run(llm, consume=consume)
        self.assertEqual(order, [("dispatch", "c1"), ("run", "Paris")])

    # ---- approved resume ------------------------------------------------ #

    def _paused_state(self, *calls):
        state = LoopState(messages=[
            {"role": "user", "content": "send it"},
            {"role": "assistant", "content": list(calls)},
        ])
        state.pending_tools = [
            {"id": c["id"], "name": c["name"], "input": c["input"]} for c in calls
        ]
        state.pending_tool = state.pending_tools[0]
        state.pending_tiers = ["bounded"] * len(calls)
        state.pending_tier = "bounded"
        return state

    def test_an_approved_resume_announces_the_call_before_its_executor_runs(self):
        order = []

        async def executor(name, tool_input, context):
            order.append(("run", name))
            return {"status": "success"}

        self.executor.side_effect = executor

        async def consume(event):
            if event.kind == "tool_dispatch":
                order.append(("dispatch", event.data["call_id"]))

        llm = _Llm(_response([_text("Sent.")]))
        with patch("orchestration.agent_loop._refresh_dispatched_marker",
                   new=AsyncMock(return_value=True)):
            self._run(
                llm, consume=consume, user_message="",
                resumed_state=self._paused_state(
                    _tool("p1", "send_email", {"to": "a@example.com", "body": "hi"}),
                ),
                confirmed_tool=True,
            )
        self.assertEqual(order, [("dispatch", "p1"), ("run", "send_email")])

    def test_an_approved_resume_fenced_on_the_announcement_runs_nothing(self):
        live = {"value": True}

        async def consume(event):
            if event.kind == "tool_dispatch":
                live["value"] = False

        llm = _Llm(_response([_text("unused")]))
        events = self._run(
            llm, consume=consume, user_message="",
            context={"turn_live": lambda: live["value"]},
            resumed_state=self._paused_state(
                _tool("p1", "send_email", {"to": "a@example.com", "body": "hi"}),
            ),
            confirmed_tool=True,
        )
        self.executor.assert_not_awaited()
        self.assertEqual(
            [(e.data["id"], e.data["result"]["status"]) for e in events if e.kind == "tool_result"],
            [("p1", "not_started")],
        )

    def test_a_call_dispatched_before_a_restart_is_not_announced_again(self):
        state = self._paused_state(
            _tool("p1", "send_email", {"to": "a@example.com", "body": "hi"}),
        )
        state.dispatched_calls = ["p1"]
        llm = _Llm(_response([_text("I could not confirm it ran.")]))
        events = self._run(
            llm, user_message="", resumed_state=state, confirmed_tool=True,
        )
        self.executor.assert_not_awaited()
        self.assertEqual([e for e in events if e.kind == "tool_dispatch"], [])
        result = next(e for e in events if e.kind == "tool_result")
        self.assertTrue(result.data["result"].get("outcome_unknown"))

    def test_a_lost_dispatch_marker_answers_the_announced_call_as_not_run(self):
        llm = _Llm(_response([_text("I could not run it.")]))
        with patch("orchestration.agent_loop._refresh_dispatched_marker",
                   new=AsyncMock(return_value=False)):
            events = self._run(
                llm, user_message="",
                resumed_state=self._paused_state(
                    _tool("p1", "send_email", {"to": "a@example.com", "body": "hi"}),
                ),
                confirmed_tool=True,
            )
        self.executor.assert_not_awaited()
        self.assertEqual(
            [e.data["call_id"] for e in events if e.kind == "tool_dispatch"], ["p1"],
        )
        result = next(e for e in events if e.kind == "tool_result")
        self.assertEqual(
            (result.data["id"], result.data["result"]["status"], result.data.get("not_run")),
            ("p1", "not_started", True),
        )

    def test_a_resume_with_the_persona_unavailable_announces_nothing(self):
        from orchestration.personas import PERSONA_LOOKUP_FAILED

        llm = _Llm(_response([_text("I could not check this room's settings.")]))
        with patch("orchestration.agent_loop._resolve_persona",
                   new=AsyncMock(return_value=PERSONA_LOOKUP_FAILED)):
            events = self._run(
                llm, user_message="",
                resumed_state=self._paused_state(
                    _tool("p1", "send_email", {"to": "a@example.com", "body": "hi"}),
                ),
                confirmed_tool=True,
            )
        self.executor.assert_not_awaited()
        self.assertEqual([e for e in events if e.kind == "tool_dispatch"], [])
        self.assertEqual(
            [e.data["id"] for e in events if e.kind == "tool_result"], ["p1"],
        )

    # ---- durations ------------------------------------------------------- #

    def test_each_call_in_a_parallel_batch_reports_its_own_duration(self):
        async def executor(name, tool_input, context):
            await asyncio.sleep(0.5 if tool_input["city"] == "Slow" else 0)
            return {"status": "success"}

        self.executor.side_effect = executor
        llm = _Llm(
            _response(
                [_tool("fast", "get_weather", {"city": "Fast"}),
                 _tool("slow", "get_weather", {"city": "Slow"})],
                stop_reason="tool_use",
            ),
            _response([_text("Done.")]),
        )
        events = self._run(llm)
        durations = {
            e.data["id"]: e.data.get("duration_ms")
            for e in events if e.kind == "tool_result"
        }
        self.assertIsInstance(durations["fast"], int)
        self.assertIsInstance(durations["slow"], int)
        self.assertLess(durations["fast"], 200)
        self.assertGreaterEqual(durations["slow"] - durations["fast"], 300)

    # ---- the approval card ---------------------------------------------- #

    def test_the_confirmation_event_carries_call_ids_and_the_row_expiry(self):
        saved = {}

        async def save_row(*args, **kwargs):
            saved.update(kwargs)

        llm = _Llm(_response(
            [_tool("e1", "send_email", {"to": "a@example.com", "body": "hi"})],
            stop_reason="tool_use",
        ))
        with patch("orchestration.agent_loop.save_pending_confirmation", new=save_row), \
                patch("orchestration.agent_loop.preview_tool", new=AsyncMock(return_value=None)), \
                patch("orchestration.agent_loop.save_loop_state"):
            events = self._run(llm, context={"room_id": 7})
        confirmation = next(e for e in events if e.kind == "confirmation")
        self.assertEqual(
            [c.get("call_id") for c in confirmation.data["calls"]], ["e1"],
        )
        self.assertIn("expires_at", confirmation.data)
        self.assertEqual(
            datetime.fromisoformat(confirmation.data["expires_at"]),
            saved["expires_at"],
        )
