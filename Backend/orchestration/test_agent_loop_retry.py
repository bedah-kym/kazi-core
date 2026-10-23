"""Retry backoff + circuit breaker tests for the agent loop (fix 2).

Locks the resilience boundary: a failed tool call is retried with exponential
backoff up to a budget, and repeated failures for a service trip a circuit
breaker that degrades the service instead of hammering it.
"""
import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase, override_settings

from orchestration.agent_loop import (
    CIRCUIT_BREAKER_COOLDOWN_SECONDS,
    _account_tool_result,
    _circuit_breaker_open,
    _circuit_state,
    _record_tool_failure,
    _record_tool_success,
    _retry_backoff_seconds,
    _retry_key,
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


class RetryAccountingTests(SimpleTestCase):
    def setUp(self):
        reset_circuit_breakers()

    def tearDown(self):
        reset_circuit_breakers()

    def test_retry_key_is_stable_and_hides_raw_input(self):
        first = {"command": "cat /etc/shadow"}
        second = {"command": "ls -la"}
        key = _retry_key("run_command", first)
        self.assertEqual(key, _retry_key("run_command", first))
        self.assertNotEqual(key, _retry_key("run_command", second))
        self.assertNotIn("cat", key)
        self.assertNotIn("/etc/shadow", key)

    def test_failures_are_counted_per_call_not_per_tool(self):
        from orchestration.agent_loop import LoopState

        state = LoopState(messages=[])
        call_a = {"command": "ls a"}
        call_b = {"command": "ls b"}
        _account_tool_result(
            state, "run_command", call_a, "shell", {"status": "error"},
        )
        _account_tool_result(
            state, "run_command", call_b, "shell", {"status": "error"},
        )

        self.assertEqual(state.retry_counts[_retry_key("run_command", call_a)], 1)
        self.assertEqual(state.retry_counts[_retry_key("run_command", call_b)], 1)

    def test_success_of_another_call_does_not_reset_a_failure(self):
        from orchestration.agent_loop import LoopState

        state = LoopState(messages=[])
        call_a = {"command": "ls a"}
        call_b = {"command": "ls b"}
        _account_tool_result(
            state, "run_command", call_a, "shell", {"status": "error"},
        )
        _account_tool_result(
            state, "run_command", call_b, "shell", {"status": "success"},
        )

        self.assertEqual(state.retry_counts[_retry_key("run_command", call_a)], 1)
        self.assertNotIn(_retry_key("run_command", call_b), state.retry_counts)

    @patch("orchestration.agent_loop.cache")
    def test_saved_loop_state_holds_only_digests(self, mock_cache):
        from orchestration.agent_loop import LoopState, save_loop_state

        state = LoopState(messages=[])
        command_text = "curl -H 'Authorization: Bearer fake-token' https://example.com?email=a@b.c"
        _account_tool_result(
            state, "run_command", {"command": command_text}, "shell",
            {"status": "error"},
        )

        save_loop_state(1, 1, state)

        payload = mock_cache.set.call_args[0][1]
        self.assertNotIn("fake-token", payload)
        self.assertNotIn("example.com", payload)
        self.assertNotIn("a@b.c", payload)
        saved = json.loads(payload)
        self.assertEqual(len(saved["retry_counts"]), 1)
        self.assertNotIn("curl", next(iter(saved["retry_counts"])))

    @patch("orchestration.agent_loop._resolve_persona", new_callable=AsyncMock, return_value=None)
    @patch("orchestration.agent_loop.get_llm_client")
    @patch("orchestration.agent_loop.execute_tool", new_callable=AsyncMock)
    @patch("orchestration.agent_loop.cache")
    def test_old_name_keyed_state_loads_and_resumes(
        self, mock_cache, mock_exec, mock_get_llm, mock_persona,
    ):
        from orchestration.agent_loop import load_loop_state, run_agent_loop

        old_state = {
            "messages": [],
            "iteration": 2,
            "tool_call_count": 2,
            "tokens_used": 50,
            "tool_call_log": [],
            "retry_counts": {"get_weather": 2},
            "pending_tool": {
                "id": "t1", "name": "get_weather", "input": {"city": "Nairobi"},
            },
            "pending_tier": "bounded",
        }
        mock_cache.get.return_value = json.dumps(old_state)
        state = load_loop_state(1, 1)
        self.assertEqual(state.retry_counts, {"get_weather": 2})

        mock_exec.return_value = {"status": "success", "temperature": 22}
        mock_llm = MagicMock()
        mock_llm.create_message = AsyncMock(
            return_value=_make_llm_response([_text_block("It's 22C.")]),
        )
        mock_get_llm.return_value = mock_llm

        events = run_async(collect_events(run_agent_loop(
            user_message="",
            context={"user_id": 1, "room_id": 1, "username": "test"},
            resumed_state=state,
            confirmed_tool=True,
        )))

        self.assertEqual(mock_exec.await_count, 1)
        results = [e.data.get("result") for e in events if e.kind == "tool_result"]
        self.assertEqual(results[0].get("status"), "success")
        self.assertTrue(any(e.kind == "done" for e in events))


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
        # These tests run without a database, so the real persona lookup would
        # fail closed and remove all tool reach. "No persona" is the case here.
        persona_patcher = patch(
            "orchestration.agent_loop._resolve_persona",
            new_callable=AsyncMock,
            return_value=None,
        )
        persona_patcher.start()
        self.addCleanup(persona_patcher.stop)

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
            "Stopped retrying get_weather" in (r.get("message") or "")
            and "after 2 failures in this turn." in (r.get("message") or "")
            for r in results if isinstance(r, dict)
        ))
        # Exactly one backoff sleep (2^1 = 2s) before the single retry.
        backoff_sleeps = [c.args[0] for c in mock_sleep.call_args_list if c.args and c.args[0] > 0.1]
        self.assertEqual(backoff_sleeps, [2.0])

    def _run_shell_commands(self, commands, results, mock_cache, mock_exec, mock_get_llm):
        mock_cache.get.return_value = None
        mock_exec.side_effect = results
        mock_llm = MagicMock()
        mock_llm.create_message = AsyncMock(side_effect=[
            _make_llm_response(
                [_tool_use_block(f"t{i}", "run_command", {"command": command})],
                stop_reason="tool_use",
            )
            for i, command in enumerate(commands)
        ] + [_make_llm_response([_text_block("Done.")], stop_reason="end_turn")])
        mock_get_llm.return_value = mock_llm

        from orchestration.agent_loop import run_agent_loop

        events = run_async(collect_events(run_agent_loop(
            user_message="Run these",
            context={"user_id": 1, "room_id": 1, "username": "test"},
        )))
        return [e.data.get("result") for e in events if e.kind == "tool_result"]

    @override_settings(SHELL_EXEC_PROFILE="standard")
    @patch("orchestration.agent_loop.asyncio.sleep", new_callable=AsyncMock)
    @patch("orchestration.agent_loop.get_llm_client")
    @patch("orchestration.agent_loop.execute_tool", new_callable=AsyncMock)
    @patch("orchestration.agent_loop.cache")
    def test_different_failing_calls_do_not_block_each_others_retry(
        self, mock_cache, mock_exec, mock_get_llm, mock_sleep,
    ):
        ok = {"status": "success", "stdout": "a"}
        down = {"status": "error", "message": "The shell sidecar is unreachable."}
        results = self._run_shell_commands(
            ["ls a", "ls b", "ls a"], [down, down, ok],
            mock_cache, mock_exec, mock_get_llm,
        )

        self.assertEqual(mock_exec.await_count, 3)
        self.assertEqual(results[-1], ok)

    @override_settings(SHELL_EXEC_PROFILE="standard")
    @patch("orchestration.agent_loop.asyncio.sleep", new_callable=AsyncMock)
    @patch("orchestration.agent_loop.get_llm_client")
    @patch("orchestration.agent_loop.execute_tool", new_callable=AsyncMock)
    @patch("orchestration.agent_loop.cache")
    def test_success_of_another_call_does_not_reset_the_count(
        self, mock_cache, mock_exec, mock_get_llm, mock_sleep,
    ):
        ok = {"status": "success", "stdout": "b"}
        down = {"status": "error", "message": "The shell sidecar is unreachable."}
        results = self._run_shell_commands(
            ["ls a", "ls a", "ls b", "ls a"], [down, down, ok, ok],
            mock_cache, mock_exec, mock_get_llm,
        )

        self.assertEqual(mock_exec.await_count, 3)
        stopped = results[-1].get("message") or ""
        self.assertIn("Stopped retrying run_command ls a", stopped)
        self.assertIn("after 2 failures in this turn.", stopped)
        self.assertNotIn("ls b", stopped)

    @override_settings(SHELL_EXEC_PROFILE="standard")
    @patch("orchestration.agent_loop.asyncio.sleep", new_callable=AsyncMock)
    @patch("orchestration.agent_loop.get_llm_client")
    @patch("orchestration.agent_loop.execute_tool", new_callable=AsyncMock)
    @patch("orchestration.agent_loop.cache")
    def test_refusal_label_does_not_carry_restricted_values(
        self, mock_cache, mock_exec, mock_get_llm, mock_sleep,
    ):
        secrets = {
            "password": "hunter2-secret",  # nosec B105 - test fixture - fake credential
            "token": "tok-abc123",  # nosec B105 - test fixture - fake credential
            "api_key": "key-xyz789",
        }
        # No command-like key, so the label falls back to the rendered input —
        # the path where restricted values used to leak.
        secret_input = {"city": "Nairobi", **secrets}
        shell_input = {"command": "ls a"}
        down = {"status": "error", "message": "The shell sidecar is unreachable."}
        mock_cache.get.return_value = None
        mock_exec.return_value = down
        calls = [
            _tool_use_block(f"w{i}", "get_weather", secret_input)
            for i in range(3)
        ] + [
            _tool_use_block(f"s{i}", "run_command", shell_input)
            for i in range(3)
        ]
        mock_llm = MagicMock()
        mock_llm.create_message = AsyncMock(side_effect=[
            _make_llm_response([call], stop_reason="tool_use") for call in calls
        ] + [_make_llm_response([_text_block("Done.")], stop_reason="end_turn")])
        mock_get_llm.return_value = mock_llm

        from orchestration.agent_loop import run_agent_loop

        events = run_async(collect_events(run_agent_loop(
            user_message="Run it",
            context={"user_id": 1, "room_id": 1, "username": "test"},
        )))

        messages = [
            e.data.get("result", {}).get("message") or ""
            for e in events if e.kind == "tool_result"
        ]
        weather_stopped = next(
            m for m in messages if "Stopped retrying get_weather" in m
        )
        for value in secrets.values():
            self.assertNotIn(value, weather_stopped)
        shell_stopped = next(
            m for m in messages if "Stopped retrying run_command" in m
        )
        self.assertIn("run_command ls a", shell_stopped)
        for value in secrets.values():
            self.assertNotIn(value, shell_stopped)

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

    @patch("orchestration.agent_loop.get_llm_client")
    @patch("orchestration.agent_loop.execute_tool", new_callable=AsyncMock)
    @patch("orchestration.agent_loop.cache")
    def test_tool_raising_exception_becomes_error_result(
        self, mock_cache, mock_exec, mock_get_llm,
    ):
        mock_cache.get.return_value = None
        mock_exec.side_effect = RuntimeError("connector exploded")
        mock_llm = MagicMock()
        mock_llm.create_message = AsyncMock(side_effect=[
            _make_llm_response(
                [_tool_use_block("t1", "get_weather", {"city": "Nairobi"})],
                stop_reason="tool_use",
            ),
            _make_llm_response([_text_block("Something went wrong.")], stop_reason="end_turn"),
        ])
        mock_get_llm.return_value = mock_llm

        from orchestration.agent_loop import run_agent_loop

        events = run_async(collect_events(run_agent_loop(
            user_message="Weather in Nairobi",
            context={"user_id": 1, "room_id": 1, "username": "test"},
        )))

        results = [e.data.get("result") for e in events if e.kind == "tool_result"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].get("status"), "error")
        self.assertIn("connector exploded", results[0].get("message", ""))

    @patch("orchestration.agent_loop.get_llm_client")
    @patch("orchestration.agent_loop.cache")
    def test_meta_tool_is_dispatched_through_safe_executor(
        self, mock_cache, mock_get_llm,
    ):
        mock_cache.get.return_value = None
        mock_llm = MagicMock()
        mock_llm.create_message = AsyncMock(side_effect=[
            _make_llm_response(
                [_tool_use_block("t1", "list_skills", {})],
                stop_reason="tool_use",
            ),
            _make_llm_response([_text_block("Here are the skills.")], stop_reason="end_turn"),
        ])
        mock_get_llm.return_value = mock_llm

        from orchestration.agent_loop import run_agent_loop

        with patch(
            "orchestration.agent_loop._execute_meta_tool",
            new_callable=AsyncMock,
            return_value={"status": "success", "skills": []},
        ) as mock_meta:
            events = run_async(collect_events(run_agent_loop(
                user_message="List skills",
                context={"user_id": 1, "room_id": 1, "username": "test"},
            )))

        mock_meta.assert_awaited_once()
        self.assertTrue(any(e.kind == "done" for e in events))
