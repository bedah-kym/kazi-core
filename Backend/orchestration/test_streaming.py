"""A finished reply is sent as one block; no simulated typing, one telemetry line."""
import asyncio
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from orchestration.agent_loop import AgentEvent, run_agent_loop
from orchestration.coordinator import OrchestrationCoordinator
from orchestration.testsupport import turn_test_patches


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


def _llm_response(text):
    return {
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }


def _run_coordinator_turn(reply):
    record = MagicMock()

    async def _events(**kwargs):
        yield AgentEvent("text_delta", {"text": reply})

    cache = MagicMock()
    cache.get.return_value = None
    targets = {
        "orchestration.coordinator.load_memory_summary": AsyncMock(return_value=None),
        "orchestration.coordinator.get_user_preferences": lambda user_id, profile_obj=None: {},
        "orchestration.coordinator.has_pending_agent_state": AsyncMock(return_value=False),
        "orchestration.coordinator.cache": cache,
        "orchestration.coordinator.record_event": record,
        "orchestration.coordinator.run_agent_loop": _events,
        **turn_test_patches(),
    }

    async def run():
        return await OrchestrationCoordinator().handle_message(
            query="hello",
            user_id=1,
            room_id="1",
            username="alice",
            message_id=42,
            send_chunk=AsyncMock(),
            send_step_event=AsyncMock(),
            get_context_prompt=AsyncMock(return_value=""),
            bump_signals=MagicMock(),
        )

    with ExitStack() as stack:
        for target, new in targets.items():
            stack.enter_context(patch(target, new))
        result = async_to_sync(run)()
    return result, record


class StreamingTests(SimpleTestCase):

    @patch("orchestration.agent_loop.get_llm_client")
    @patch("orchestration.agent_loop.execute_tool", new_callable=AsyncMock)
    @patch("orchestration.agent_loop.cache")
    def test_a_reply_is_one_text_delta_event(self, mock_cache, mock_exec, mock_get_llm):
        mock_cache.get.return_value = None
        reply = "y" * 600
        mock_llm = MagicMock()
        mock_llm.create_message = AsyncMock(return_value=_llm_response(reply))
        mock_get_llm.return_value = mock_llm

        events = run_async(collect_events(run_agent_loop(
            user_message="tell me something long",
            context={"user_id": 1, "room_id": 1, "username": "test"},
        )))

        deltas = [e for e in events if e.kind == "text_delta"]
        self.assertEqual(len(deltas), 1)
        self.assertEqual(deltas[0].data["text"], reply)

    @patch("orchestration.agent_loop.asyncio.sleep", new_callable=AsyncMock)
    @patch("orchestration.agent_loop.get_llm_client")
    @patch("orchestration.agent_loop.execute_tool", new_callable=AsyncMock)
    @patch("orchestration.agent_loop.cache")
    def test_a_turn_never_sleeps_for_the_typing_effect(self, mock_cache, mock_exec, mock_get_llm, mock_sleep):
        mock_cache.get.return_value = None
        reply = "z" * 600
        mock_llm = MagicMock()
        mock_llm.create_message = AsyncMock(return_value=_llm_response(reply))
        mock_get_llm.return_value = mock_llm

        run_async(collect_events(run_agent_loop(
            user_message="tell me something long",
            context={"user_id": 1, "room_id": 1, "username": "test"},
        )))

        awaited = [c.args[0] for c in mock_sleep.call_args_list if c.args]
        self.assertNotIn(0.02, awaited)

    def test_a_coordinator_turn_records_one_stream_completed_event(self):
        reply = "A" * 600
        result, record = _run_coordinator_turn(reply)

        progress = [call.args for call in record.call_args_list if call.args and call.args[0] == "progress_event"]
        chunk_events = [args for args in progress if args[1].get("state") == "chunk"]
        self.assertEqual(chunk_events, [])

        completed = [
            args for args in progress
            if args[1].get("phase") == "stream" and args[1].get("state") == "completed"
        ]
        self.assertEqual(len(completed), 1)
        payload = completed[0][1]
        self.assertEqual(payload["chars"], len(reply))
        self.assertEqual(payload["chunks"], 1)
        self.assertEqual(result.full_response, reply)
