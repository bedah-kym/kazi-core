"""The coordinator hands the agent loop the transcript it was given, untouched."""
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from orchestration.agent_loop import AgentEvent
from orchestration.coordinator import OrchestrationCoordinator


def _run_agent_turn(query, *, history_messages=None, memory_summary=None, context_prompt=""):
    """Run one loop-path turn with state mocked; returns what run_agent_loop received."""
    captured = {}

    async def _capture(**kwargs):
        captured.update(kwargs)
        yield AgentEvent("text", {"text": "ok"})

    cache = MagicMock()
    cache.get.return_value = None
    targets = {
        "orchestration.coordinator.load_memory_summary": AsyncMock(return_value=memory_summary),
        "orchestration.coordinator.get_user_preferences": lambda user_id: {},
        "orchestration.coordinator.has_pending_agent_state": AsyncMock(return_value=False),
        "orchestration.coordinator.cache": cache,
        "orchestration.coordinator.record_event": MagicMock(),
        "orchestration.coordinator.run_agent_loop": _capture,
    }

    async def run():
        return await OrchestrationCoordinator().handle_message(
            query=query,
            user_id=1,
            room_id="1",
            username="alice",
            message_id=42,
            send_chunk=AsyncMock(),
            send_step_event=AsyncMock(),
            get_context_prompt=AsyncMock(return_value=context_prompt),
            bump_signals=MagicMock(),
            history_messages=history_messages,
        )

    with ExitStack() as stack:
        for target, new in targets.items():
            stack.enter_context(patch(target, new))
        async_to_sync(run)()
    return captured


class CoordinatorHistoryTests(SimpleTestCase):
    def test_structured_history_reaches_the_loop_untouched(self):
        history = [
            {"role": "user", "content": "make a folder"},
            {"role": "assistant", "content": "Step 2: write the lines\ncommand: mkdir qa"},
        ]

        captured = _run_agent_turn("what next?", history_messages=history)

        self.assertEqual(captured["history"], history)
        self.assertEqual(captured["user_message"], "what next?")

    def test_memory_and_room_context_go_to_the_prompt_not_the_transcript(self):
        history = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]

        captured = _run_agent_turn(
            "continue",
            history_messages=history,
            memory_summary="Recent actions: run_command.",
            context_prompt="IMPORTANT NOTES:\n- [#7] [DECISION] ship on friday",
        )

        self.assertEqual(captured["history"], history)
        self.assertEqual(captured["memory_summary"], "Recent actions: run_command.")
        self.assertIn("ship on friday", captured["context_prompt"])

    def test_no_history_is_passed_as_none(self):
        captured = _run_agent_turn("hello", history_messages=[])

        self.assertIsNone(captured["history"])
