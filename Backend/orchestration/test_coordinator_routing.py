"""Branch coverage for OrchestrationCoordinator routing.

Directives and the agent-loop consent machinery, plus the loop path itself.
All external services/LLM calls are mocked.
"""
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from orchestration.agent_loop import AgentEvent
from orchestration.coordinator import OrchestrationCoordinator


def _empty_prefs(user_id):
    return {}


async def _agent_events(**kwargs):
    """Yield every event kind the handler maps."""
    yield AgentEvent("text", {"text": "hi"})
    yield AgentEvent("text_delta", {"text": " there"})
    yield AgentEvent("thinking", {"text": ""})
    yield AgentEvent("tool_start", {"name": "get_weather"})
    yield AgentEvent("tool_result", {"name": "get_weather", "result": {"status": "success"}})
    yield AgentEvent("confirmation", {"message": "Confirm?"})
    yield AgentEvent("error", {"message": "oops"})
    yield AgentEvent("done", {})


class RoutingBranchTests(SimpleTestCase):
    def _run(self, query, mocks=None):
        """Run handle_message with the common plumbing mocked.

        ``mocks`` maps a dotted target to the object that replaces it.
        Returns ``(result, chunks)``.
        """
        chunks = AsyncMock()
        cache = MagicMock()
        cache.get.return_value = None  # no pending confirmation by default
        spec = {
            "orchestration.coordinator.load_memory_summary": AsyncMock(return_value=None),
            "orchestration.coordinator.get_user_preferences": _empty_prefs,
            "orchestration.coordinator.has_pending_agent_state": AsyncMock(return_value=False),
            "orchestration.coordinator.cache": cache,
            "orchestration.coordinator.record_event": MagicMock(),
        }
        if mocks:
            spec.update(mocks)

        stack = contextlib.ExitStack()
        for target, value in spec.items():
            stack.enter_context(patch(target, value))
        try:
            async def call():
                return await OrchestrationCoordinator().handle_message(
                    query=query,
                    user_id=1,
                    room_id="1",
                    username="alice",
                    message_id=1,
                    send_chunk=chunks,
                    send_step_event=AsyncMock(),
                    get_context_prompt=AsyncMock(return_value=""),
                    bump_signals=MagicMock(),
                )

            result = async_to_sync(call)()
            return result, chunks
        finally:
            stack.close()

    def _broadcast(self, chunks, needle):
        return any(needle in call.args[1] for call in chunks.call_args_list)

    # -- directives ----------------------------------------------------- #

    def test_dismiss_directive(self):
        result, chunks = self._run("dismiss suggestions please")
        self.assertTrue(result.persist)
        self.assertTrue(self._broadcast(chunks, "stop showing"))

    def test_receipt_directive(self):
        result, chunks = self._run("receipts", mocks={
            "orchestration.coordinator.fetch_recent_receipts": AsyncMock(return_value=[]),
            "orchestration.coordinator.format_receipt_list": MagicMock(return_value="No receipts yet."),
        })
        self.assertTrue(result.persist)
        self.assertTrue(self._broadcast(chunks, "No receipts"))

    def test_undo_directive(self):
        result, chunks = self._run("undo", mocks={
            "orchestration.coordinator.undo_last_action": AsyncMock(return_value={"message": "Undone."}),
        })
        self.assertTrue(result.persist)
        self.assertTrue(self._broadcast(chunks, "Undone."))

    # -- agent-loop consent --------------------------------------------- #

    def test_agent_loop_resume_confirm(self):
        result, chunks = self._run("yes", mocks={
            "orchestration.coordinator.has_pending_agent_state": AsyncMock(return_value=True),
            "orchestration.coordinator.resume_after_confirmation": _agent_events,
        })
        self.assertTrue(result.persist)
        self.assertTrue(self._broadcast(chunks, "hi"))

    def test_agent_loop_resume_cancel(self):
        cancel = AsyncMock(return_value="Cancelled the pending action.")
        result, chunks = self._run("cancel", mocks={
            "orchestration.coordinator.has_pending_agent_state": AsyncMock(return_value=True),
            "orchestration.coordinator.cancel_pending_action": cancel,
        })
        self.assertTrue(result.persist)
        cancel.assert_awaited_once()

    def test_a_non_approval_reply_dismisses_then_starts_a_new_turn(self):
        dismiss = AsyncMock()
        captured = {}

        async def _capture(**kwargs):
            captured.update(kwargs)
            yield AgentEvent("text", {"text": "new turn"})

        result, chunks = self._run("hello there", mocks={
            "orchestration.coordinator.has_pending_agent_state": AsyncMock(return_value=True),
            "orchestration.coordinator.dismiss_pending_confirmation": dismiss,
            "orchestration.coordinator.run_agent_loop": _capture,
        })
        self.assertTrue(result.persist)
        dismiss.assert_awaited_once()
        self.assertEqual(captured.get("user_message"), "hello there")

    # -- agent loop path ------------------------------------------------- #

    def test_agent_loop_path(self):
        result, chunks = self._run("hi", mocks={
            "orchestration.coordinator.run_agent_loop": _agent_events,
        })
        self.assertTrue(result.persist)
        self.assertTrue(self._broadcast(chunks, "hi"))
