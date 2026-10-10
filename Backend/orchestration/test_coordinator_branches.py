"""Branch coverage for OrchestrationCoordinator.

Directives and the agent-loop consent machinery are answered by the
coordinator; everything else reaches the loop. All external services/LLM calls
are mocked.
"""
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from orchestration.agent_loop import AgentEvent
from orchestration.coordinator import OrchestrationCoordinator


def _empty_preferences(user_id):
    return {}


async def _event_stream(**kwargs):
    yield AgentEvent("text", {"text": "ok"})


class CoordinatorBranchTests(SimpleTestCase):
    def _run(self, query, patches=None):
        """Run handle_message with common state deps mocked.

        `patches` maps "orchestration.coordinator.<name>" -> new value.
        Returns (result, mocks, send_chunk).
        """
        send_chunk = AsyncMock()
        send_step = AsyncMock()
        stack = ExitStack()
        mocks = {}

        defaults = {
            "orchestration.coordinator.load_memory_summary": AsyncMock(return_value=None),
            "orchestration.coordinator.get_user_preferences": _empty_preferences,
            "orchestration.coordinator.has_pending_agent_state": AsyncMock(return_value=False),
            "orchestration.coordinator.cache": MagicMock(),
            "orchestration.coordinator.record_event": MagicMock(),
        }
        defaults.update(patches or {})
        for target, new in defaults.items():
            mocks[target.rsplit(".", 1)[-1]] = stack.enter_context(patch(target, new))

        mocks["cache"].get.return_value = None

        async def run():
            return await OrchestrationCoordinator().handle_message(
                query=query,
                user_id=1,
                room_id="1",
                username="alice",
                message_id=42,
                send_chunk=send_chunk,
                send_step_event=send_step,
                get_context_prompt=AsyncMock(return_value=""),
                bump_signals=MagicMock(),
            )

        with stack:
            result = async_to_sync(run)()

        return result, mocks, send_chunk

    # -- directives -------------------------------------------------------- #

    def test_dismiss_directive(self):
        result, mocks, send_chunk = self._run("dismiss suggestions")
        texts = [c.args[1] for c in send_chunk.await_args_list]
        self.assertTrue(any("stop showing" in t for t in texts))

    def test_receipt_directive(self):
        result, mocks, send_chunk = self._run(
            "show receipts",
            patches={
                "orchestration.coordinator.fetch_recent_receipts": AsyncMock(return_value=[]),
                "orchestration.coordinator.format_receipt_list": lambda receipts: "No recent actions.",
            },
        )
        texts = [c.args[1] for c in send_chunk.await_args_list]
        self.assertIn("No recent actions.", texts)

    def test_undo_directive(self):
        result, mocks, send_chunk = self._run(
            "undo that",
            patches={
                "orchestration.coordinator.undo_last_action": AsyncMock(return_value={"message": "Undone."}),
            },
        )
        texts = [c.args[1] for c in send_chunk.await_args_list]
        self.assertIn("Undone.", texts)

    # -- agent loop consent ------------------------------------------------ #

    def test_agent_loop_resume_after_confirmation(self):
        result, mocks, send_chunk = self._run(
            "yes",
            patches={
                "orchestration.coordinator.has_pending_agent_state": AsyncMock(return_value=True),
                "orchestration.coordinator.resume_after_confirmation": _event_stream,
            },
        )
        self.assertTrue(any(c.args[1] == "ok" for c in send_chunk.await_args_list))

    def _pending_reply(self, query):
        resume = MagicMock(side_effect=AssertionError("must not resume"))
        return self._run(
            query,
            patches={
                "orchestration.coordinator.has_pending_agent_state": AsyncMock(return_value=True),
                "orchestration.coordinator.resume_after_confirmation": resume,
                "orchestration.coordinator.dismiss_pending_confirmation": AsyncMock(),
                "orchestration.coordinator.cancel_pending_action": AsyncMock(return_value="Cancelled."),
                "orchestration.coordinator.run_agent_loop": _event_stream,
            },
        )

    def test_approval_word_followed_by_more_does_not_run_the_pending_action(self):
        for query in (
            "ok but use a different folder",
            "yes, but only the first part",
            "ok what does that command do?",
        ):
            result, mocks, send_chunk = self._pending_reply(query)
            mocks["resume_after_confirmation"].assert_not_called()
            mocks["dismiss_pending_confirmation"].assert_awaited_once()
            sent = "".join(c.args[1] for c in send_chunk.await_args_list)
            self.assertIn("did not run the pending action", sent, query)

    def test_cancel_is_checked_before_approval(self):
        # Force both matchers true: only the order of the checks decides.
        resume = MagicMock(side_effect=AssertionError("must not resume"))
        result, mocks, send_chunk = self._run(
            "cancel",
            patches={
                "orchestration.coordinator.is_approval_reply": lambda q: True,
                "orchestration.coordinator.has_pending_agent_state": AsyncMock(return_value=True),
                "orchestration.coordinator.resume_after_confirmation": resume,
                "orchestration.coordinator.cancel_pending_action": AsyncMock(return_value="Cancelled."),
            },
        )
        mocks["resume_after_confirmation"].assert_not_called()
        mocks["cancel_pending_action"].assert_awaited_once()

    def test_a_plain_no_cancels_the_pending_action(self):
        result, mocks, send_chunk = self._pending_reply("no")
        mocks["resume_after_confirmation"].assert_not_called()
        mocks["cancel_pending_action"].assert_awaited_once()
        mocks["dismiss_pending_confirmation"].assert_not_called()

    def test_a_directive_reply_does_not_leave_the_pending_action_armed(self):
        for query in ("receipts", "undo", "dismiss suggestions"):
            patches = {
                "orchestration.coordinator.has_pending_agent_state": AsyncMock(return_value=True),
                "orchestration.coordinator.resume_after_confirmation": MagicMock(
                    side_effect=AssertionError("must not resume")
                ),
                "orchestration.coordinator.dismiss_pending_confirmation": AsyncMock(),
                "orchestration.coordinator.fetch_recent_receipts": AsyncMock(return_value=[]),
                "orchestration.coordinator.format_receipt_list": lambda r: "No receipts.",
                "orchestration.coordinator.undo_last_action": AsyncMock(return_value={"message": "Undone."}),
            }
            result, mocks, send_chunk = self._run(query, patches=patches)
            mocks["dismiss_pending_confirmation"].assert_awaited_once()
            sent = "".join(c.args[1] for c in send_chunk.await_args_list)
            self.assertIn("did not run the pending action", sent, query)
