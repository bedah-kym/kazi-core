"""The agent loop is the only way a chat message is answered.

When the loop raises, the turn ends with one message and nothing else runs.
A progress frame that cannot be delivered does not abort the turn.
"""
import inspect
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from orchestration.agent_loop import AgentEvent
from orchestration.coordinator import OrchestrationCoordinator

ERROR_TEXT = "Something went wrong on my side. Nothing further was run."


def _call_handler(*, send_chunk, send_step_event, query="do the thing"):
    """Run one turn against whatever `handle_message` signature is loaded.

    The turn-state parameter this task deletes is only passed when the loaded
    coordinator still accepts it, so the same tests run before and after the
    change (and show the old fallback behaviour on the old code).
    """
    kwargs = dict(
        query=query,
        user_id=1,
        room_id="1",
        username="alice",
        message_id=42,
        send_chunk=send_chunk,
        send_step_event=send_step_event,
        get_context_prompt=AsyncMock(return_value=""),
        bump_signals=MagicMock(),
    )
    params = inspect.signature(OrchestrationCoordinator.handle_message).parameters
    if "history_text" in params:
        kwargs["history_text"] = ""
    async_to_sync(OrchestrationCoordinator().handle_message)(**kwargs)


class CoordinatorLoopFailureTests(SimpleTestCase):
    def _state_patches(self, run_agent_loop, record):
        cache = MagicMock()
        cache.get.return_value = None
        return {
            "orchestration.coordinator.load_memory_summary": AsyncMock(return_value=None),
            "orchestration.coordinator.get_user_preferences": lambda user_id: {},
            "orchestration.coordinator.has_pending_agent_state": AsyncMock(return_value=False),
            "orchestration.coordinator.cache": cache,
            "orchestration.coordinator.record_event": record,
            "orchestration.coordinator.run_agent_loop": run_agent_loop,
        }

    def _run(self, run_agent_loop, record, send_step_event=None):
        send_chunk = AsyncMock()
        patches = self._state_patches(run_agent_loop, record)
        with ExitStack() as stack:
            for target, mock in patches.items():
                stack.enter_context(patch(target, new=mock))
            _call_handler(
                send_chunk=send_chunk,
                send_step_event=send_step_event or AsyncMock(),
            )
        return send_chunk

    def test_a_loop_failure_ends_the_turn_with_one_error_message(self):
        record = MagicMock()
        run_loop = MagicMock(side_effect=RuntimeError("loop blew up"))

        send_chunk = self._run(run_loop, record)

        sent = [call.args[1] for call in send_chunk.call_args_list]
        self.assertEqual(sent.count(ERROR_TEXT), 1)
        run_loop.assert_called_once()
        failures = [c for c in record.call_args_list if c.args and c.args[0] == "agent_loop_failed"]
        self.assertEqual(len(failures), 1)
        payload = failures[0].args[1]
        self.assertEqual(payload["user_id"], 1)
        self.assertEqual(payload["room_id"], "1")
        self.assertEqual(payload["error_class"], "RuntimeError")

    def test_a_failed_progress_frame_does_not_abort_the_turn(self):
        record = MagicMock()

        async def _events(**kwargs):
            yield AgentEvent("text", {"text": "hello there"})

        run_loop = MagicMock(side_effect=_events)
        send_step_event = AsyncMock(side_effect=RuntimeError("frame dropped"))

        send_chunk = self._run(run_loop, record, send_step_event=send_step_event)

        sent = "".join(str(call.args[1]) for call in send_chunk.call_args_list if len(call.args) > 1)
        self.assertIn("hello there", sent)
        run_loop.assert_called_once()

    def test_the_coordinator_source_no_longer_mentions_the_legacy_pipeline(self):
        import importlib
        import sys

        from orchestration import coordinator

        source = inspect.getsource(coordinator)
        for needle in (
            "plan_user_request",
            "parse_intent",
            "route_intent",
            "data_synthesizer",
            "conversation_mode",
            "pending_key",
        ):
            self.assertNotIn(needle, source, needle)

        sys.modules.pop("orchestration.data_synthesizer", None)
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("orchestration.data_synthesizer")
