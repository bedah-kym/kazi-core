"""T-F6 per-message work: tracker off the hot path and no success claim.

Each test is written to fail on ``main``:
- the loop no longer calls ``update_memory_state`` per tool call;
- the loop no longer writes a ``Completed: ...`` summary;
- the pause helpers accept state the coordinator already read;
- the producer-less named-entity branch of the telemetry rollup is gone.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase

from orchestration.agent_loop import LoopState


def _make_llm_response(content_blocks, stop_reason="end_turn", usage=None):
    return {
        "content": content_blocks,
        "stop_reason": stop_reason,
        "usage": usage or {"input_tokens": 100, "output_tokens": 50},
    }


def _tool_use_block(tool_id, name, tool_input):
    return {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}


async def _collect(async_gen):
    return [event async for event in async_gen]


class _LoopCase(SimpleTestCase):
    def _run_one_tool_turn(self):
        from orchestration.agent_loop import run_agent_loop

        llm = MagicMock()
        llm.default_provider = MagicMock(return_value="deepseek")
        llm.create_message = AsyncMock(side_effect=[
            _make_llm_response(
                [_tool_use_block("t1", "get_weather", {"city": "Nairobi"})],
                stop_reason="tool_use",
            ),
            _make_llm_response([{"type": "text", "text": "Done."}]),
        ])

        async def _drive():
            return await _collect(run_agent_loop(
                user_message="weather?",
                context={"user_id": 1, "room_id": 1, "username": "t"},
            ))

        with patch("orchestration.agent_loop.get_llm_client", return_value=llm), \
                patch("orchestration.agent_loop.execute_tool", new=AsyncMock(
                    return_value={"status": "success"},
                )), \
                patch("orchestration.agent_loop._record_receipt", new=AsyncMock()), \
                patch("orchestration.agent_loop.enforce_agent_caps", return_value=True), \
                patch("orchestration.agent_loop._search_backend_available", return_value=False), \
                patch("orchestration.agent_loop.cache") as mock_cache:
            mock_cache.get.return_value = None
            return asyncio.run(_drive())


class TrackerOffHotPathTests(_LoopCase):
    def test_loop_does_not_update_memory_state_per_tool_call(self):
        with patch(
            "orchestration.agent_loop.update_memory_state",
            new=AsyncMock(),
            create=True,
        ) as tracker:
            self._run_one_tool_turn()

        tracker.assert_not_called()

    def test_loop_does_not_write_a_completion_summary(self):
        with patch(
            "orchestration.agent_loop.save_memory_summary",
            new=AsyncMock(),
            create=True,
        ) as summary:
            self._run_one_tool_turn()

        summary.assert_not_called()


class PauseStatePassthroughTests(SimpleTestCase):
    def test_budget_helpers_use_the_state_the_coordinator_read(self):
        from orchestration.agent_loop import has_pending_budget_state, pending_resume_kind

        state = LoopState(messages=[], awaiting_budget=True, awaiting_quota=True)

        async def _check():
            return (
                await has_pending_budget_state(1, 1, state=state),
                await pending_resume_kind(1, 1, state=state),
            )

        with patch("orchestration.agent_loop.load_loop_state") as loader:
            budget, kind = asyncio.run(_check())

        self.assertTrue(budget)
        self.assertEqual(kind, "quota")
        loader.assert_not_called()


class DeadTelemetryBranchTests(SimpleTestCase):
    def test_tool_params_and_inputs_produce_no_named_entities(self):
        from orchestration.telemetry_rollups import extract_entity_facts

        facts = extract_entity_facts([
            {"event": "agent_loop_done", "params": {"city": "Nairobi"}},
            {"event": "agent_loop_done", "input": {"email": "ops@example.com"}},
        ])

        self.assertEqual(facts, {})
