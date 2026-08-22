"""Retry backoff + circuit breaker tests for the agent loop (fix 2).

Locks the resilience boundary: a failed tool call is retried with exponential
backoff up to a budget, and repeated failures for a service trip a circuit
breaker that degrades the service instead of hammering it.
"""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase

from orchestration.agent_loop import (
    CIRCUIT_BREAKER_COOLDOWN_SECONDS,
    _circuit_breaker_open,
    _circuit_state,
    _record_tool_failure,
    _record_tool_success,
    _retry_backoff_seconds,
    _service_for_tool,
    reset_circuit_breakers,
)


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


def _make_llm_response(content_blocks, stop_reason="end_turn"):
    return {
        "content": content_blocks,
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def _text_block(text):
    return {"type": "text", "text": text}


def _tool_use_block(tool_id, name, tool_input):
    return {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}


class RetryBackoffTests(SimpleTestCase):
    def test_backoff_is_exponential_and_capped(self):
        self.assertEqual(_retry_backoff_seconds(0), 1.0)
        self.assertEqual(_retry_backoff_seconds(1), 2.0)
        self.assertEqual(_retry_backoff_seconds(2), 4.0)
        self.assertEqual(_retry_backoff_seconds(8), 256.0)
        self.assertEqual(_retry_backoff_seconds(20), 300.0)  # capped
        self.assertEqual(_retry_backoff_seconds(-1), 1.0)  # clamped at n=0

    def test_service_for_tool_maps_catalog_service(self):
        self.assertEqual(_service_for_tool("get_weather"), "weather")

    def test_service_for_tool_falls_back_to_tool_name(self):
        self.assertEqual(_service_for_tool("totally_unknown_tool"), "totally_unknown_tool")


class CircuitBreakerTests(SimpleTestCase):
    def setUp(self):
        reset_circuit_breakers()

    def tearDown(self):
        reset_circuit_breakers()

    def test_opens_after_failure_threshold(self):
        self.assertFalse(_circuit_breaker_open("weather"))
        _record_tool_failure("weather")
        _record_tool_failure("weather")
        self.assertFalse(_circuit_breaker_open("weather"))
        _record_tool_failure("weather")  # third failure trips the circuit
        self.assertTrue(_circuit_breaker_open("weather"))

    def test_success_resets_failures(self):
        for _ in range(3):
            _record_tool_failure("weather")
        self.assertTrue(_circuit_breaker_open("weather"))
        _record_tool_success("weather")
        self.assertFalse(_circuit_breaker_open("weather"))

    def test_half_opens_after_cooldown(self):
        for _ in range(3):
            _record_tool_failure("weather")
        self.assertTrue(_circuit_breaker_open("weather"))

        _circuit_state["weather"]["opened_at"] = (
            time.monotonic() - CIRCUIT_BREAKER_COOLDOWN_SECONDS - 1
        )
        self.assertFalse(_circuit_breaker_open("weather"))
        self.assertNotIn("weather", _circuit_state)

    def test_services_are_isolated(self):
        for _ in range(3):
            _record_tool_failure("weather")
        self.assertTrue(_circuit_breaker_open("weather"))
        self.assertFalse(_circuit_breaker_open("payments"))


class AgentLoopRetryIntegrationTests(SimpleTestCase):
    def setUp(self):
        reset_circuit_breakers()

    def tearDown(self):
        reset_circuit_breakers()

    @patch("orchestration.agent_loop.get_llm_client")
    @patch("orchestration.agent_loop.execute_tool", new_callable=AsyncMock)
    @patch("orchestration.agent_loop.cache")
    @patch("orchestration.agent_loop.asyncio.sleep", new_callable=AsyncMock)
    def test_identical_failed_tool_is_retried_with_backoff(
        self, mock_sleep, mock_cache, mock_exec, mock_get_llm,
    ):
        mock_cache.get.return_value = None
        mock_exec.side_effect = [
            {"status": "error", "message": "boom"},
            {"status": "success", "temperature": 22},
        ]
        mock_llm = MagicMock()
        mock_llm.create_message = AsyncMock(side_effect=[
            _make_llm_response(
                [_tool_use_block("t1", "get_weather", {"city": "Nairobi"})],
                stop_reason="tool_use",
            ),
            _make_llm_response(
                [_tool_use_block("t1", "get_weather", {"city": "Nairobi"})],
                stop_reason="tool_use",
            ),
            _make_llm_response([_text_block("It's 22C.")], stop_reason="end_turn"),
        ])
        mock_get_llm.return_value = mock_llm

        from orchestration.agent_loop import run_agent_loop

        events = run_async(collect_events(run_agent_loop(
            user_message="Weather in Nairobi",
            context={"user_id": 1, "room_id": 1, "username": "test"},
        )))

        kinds = [e.kind for e in events]
        self.assertEqual(kinds.count("tool_result"), 2)  # error then success
        self.assertTrue(any(e.kind == "done" for e in events))
        # Backoff before the retry: 2^1 = 2 seconds.
        sleep_args = [call.args[0] for call in mock_sleep.call_args_list if call.args]
        self.assertIn(2.0, sleep_args)

    @patch("orchestration.agent_loop.get_llm_client")
    @patch("orchestration.agent_loop.execute_tool", new_callable=AsyncMock)
    @patch("orchestration.agent_loop.cache")
    @patch("orchestration.agent_loop.asyncio.sleep", new_callable=AsyncMock)
    def test_identical_failed_tool_stops_after_max_retries(
        self, mock_sleep, mock_cache, mock_exec, mock_get_llm,
    ):
        mock_cache.get.return_value = None
        mock_exec.return_value = {"status": "error", "message": "always fails"}
        mock_llm = MagicMock()
        mock_llm.create_message = AsyncMock(side_effect=[
            _make_llm_response(
                [_tool_use_block("t1", "get_weather", {"city": "Nairobi"})],
                stop_reason="tool_use",
            ),
            _make_llm_response(
                [_tool_use_block("t1", "get_weather", {"city": "Nairobi"})],
                stop_reason="tool_use",
            ),
            _make_llm_response(
                [_tool_use_block("t1", "get_weather", {"city": "Nairobi"})],
                stop_reason="tool_use",
            ),
            _make_llm_response([_text_block("Giving up.")], stop_reason="end_turn"),
        ])
        mock_get_llm.return_value = mock_llm

        from orchestration.agent_loop import run_agent_loop

        events = run_async(collect_events(run_agent_loop(
            user_message="Weather in Nairobi",
            context={"user_id": 1, "room_id": 1, "username": "test"},
        )))

        # Initial attempt + 1 retry = 2 executions, then a max-retries result
        # (the budget is checked before re-executing, so the third identical
        # call is short-circuited).
        self.assertEqual(mock_exec.await_count, 2)
        results = [e.data.get("result") for e in events if e.kind == "tool_result"]
        self.assertTrue(any(
            "Max retries" in (r.get("message") or "") for r in results if isinstance(r, dict)
        ))
        # Exactly one backoff sleep (2^1 = 2s) before the single retry.
        backoff_sleeps = [c.args[0] for c in mock_sleep.call_args_list if c.args and c.args[0] > 0.1]
        self.assertEqual(backoff_sleeps, [2.0])

    @patch("orchestration.agent_loop.get_llm_client")
    @patch("orchestration.agent_loop.execute_tool", new_callable=AsyncMock)
    @patch("orchestration.agent_loop.cache")
    def test_open_circuit_blocks_execution(self, mock_cache, mock_exec, mock_get_llm):
        mock_cache.get.return_value = None
        mock_exec.return_value = {"status": "success", "temperature": 22}
        mock_llm = MagicMock()
        mock_llm.create_message = AsyncMock(side_effect=[
            _make_llm_response(
                [_tool_use_block("t1", "get_weather", {"city": "Nairobi"})],
                stop_reason="tool_use",
            ),
            _make_llm_response([_text_block("done")], stop_reason="end_turn"),
        ])
        mock_get_llm.return_value = mock_llm

        # Pre-trip the circuit for the weather service.
        for _ in range(3):
            _record_tool_failure("weather")

        from orchestration.agent_loop import run_agent_loop

        events = run_async(collect_events(run_agent_loop(
            user_message="Weather in Nairobi",
            context={"user_id": 1, "room_id": 1, "username": "test"},
        )))

        results = [e.data.get("result") for e in events if e.kind == "tool_result"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].get("status"), "error")
        self.assertIn("temporarily unavailable", results[0].get("message", ""))
        mock_exec.assert_not_called()  # circuit short-circuited the call
