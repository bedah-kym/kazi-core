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
            "orchestration.coordinator.has_pending_budget_state": AsyncMock(return_value=False),
            "orchestration.coordinator.cache": MagicMock(),
            "orchestration.coordinator.record_event": MagicMock(),
            # A quota pause defaults to the hour having freed, so tests that do
            # not care about the check resume as before (T-F3b).
            "orchestration.coordinator._quota_resume_still_blocked": AsyncMock(return_value=None),
        }
        defaults.update(patches or {})
        for target, new in defaults.items():
            # create=True lets a target added after a test was written still be
            # patched on an older revision; the assertion, not a setup error, is
            # then what fails there.
            mocks[target.rsplit(".", 1)[-1]] = stack.enter_context(patch(target, new, create=True))

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

    # -- agent loop budget (T-F3) ----------------------------------------- #

    def test_budget_extend_reply_resumes_the_turn(self):
        resume = MagicMock(side_effect=_event_stream)
        result, mocks, send_chunk = self._run(
            "more budget",
            patches={
                "orchestration.coordinator.has_pending_budget_state": AsyncMock(return_value=True),
                "orchestration.coordinator.resume_with_more_budget": resume,
                "orchestration.coordinator.clear_loop_state": MagicMock(),
            },
        )
        resume.assert_called_once()
        self.assertTrue(any(c.args[1] == "ok" for c in send_chunk.await_args_list))

    def test_a_whole_message_yes_during_a_budget_pause_resumes_the_turn(self):
        for query in ("yes", "ok", "continue", "go on", "carry on"):
            resume = MagicMock(side_effect=_event_stream)
            result, mocks, send_chunk = self._run(
                query,
                patches={
                    "orchestration.coordinator.has_pending_budget_state": AsyncMock(return_value=True),
                    "orchestration.coordinator.resume_with_more_budget": resume,
                    "orchestration.coordinator.clear_loop_state": MagicMock(),
                },
            )
            resume.assert_called_once()
            self.assertTrue(any(c.args[1] == "ok" for c in send_chunk.await_args_list), query)

    def test_a_quota_pause_resumes_without_granting_more_budget(self):
        budget_resume = MagicMock(side_effect=AssertionError("must not grant budget"))
        quota_resume = MagicMock(side_effect=_event_stream)
        result, mocks, send_chunk = self._run(
            "continue",
            patches={
                "orchestration.coordinator.has_pending_budget_state": AsyncMock(return_value=True),
                "orchestration.coordinator.pending_resume_kind": AsyncMock(return_value="quota"),
                "orchestration.coordinator.resume_with_more_budget": budget_resume,
                "orchestration.coordinator.resume_after_quota": quota_resume,
                "orchestration.coordinator.clear_loop_state": MagicMock(),
            },
        )
        quota_resume.assert_called_once()
        budget_resume.assert_not_called()
        sent = "".join(c.args[1] for c in send_chunk.await_args_list)
        self.assertIn("hourly", sent.lower())
        self.assertNotIn("more budget", sent.lower())

    def test_a_quota_resume_while_still_full_never_claims_it_freed(self):
        from orchestration.llm_client import TokenQuotaExceeded

        # The resume path must not run at all while the hour is still full.
        quota_resume = MagicMock(side_effect=_event_stream)
        result, mocks, send_chunk = self._run(
            "continue",
            patches={
                "orchestration.coordinator.has_pending_budget_state": AsyncMock(return_value=True),
                "orchestration.coordinator.pending_resume_kind": AsyncMock(return_value="quota"),
                "orchestration.coordinator.resume_after_quota": quota_resume,
                "orchestration.coordinator.clear_loop_state": MagicMock(),
                # The check says the hour is still full.
                "orchestration.coordinator._quota_resume_still_blocked": AsyncMock(
                    return_value=TokenQuotaExceeded(1800, reason="limit"),
                ),
            },
        )
        quota_resume.assert_not_called()
        mocks["clear_loop_state"].assert_not_called()
        sent = "".join(c.args[1] for c in send_chunk.await_args_list).lower()
        self.assertNotIn("freed up", sent)
        self.assertIn("still reached", sent)
        self.assertIn("hour", sent)

    def test_a_whole_message_stop_ends_the_budget_pause(self):
        for query in ("no", "stop"):
            resume = MagicMock(side_effect=AssertionError("must not resume"))
            result, mocks, send_chunk = self._run(
                query,
                patches={
                    "orchestration.coordinator.has_pending_budget_state": AsyncMock(return_value=True),
                    "orchestration.coordinator.resume_with_more_budget": resume,
                    "orchestration.coordinator.clear_loop_state": MagicMock(),
                    "orchestration.coordinator.run_agent_loop": _event_stream,
                },
            )
            resume.assert_not_called()
            mocks["clear_loop_state"].assert_called_once()
            sent = "".join(c.args[1] for c in send_chunk.await_args_list)
            self.assertIn("stopped", sent.lower(), query)

    def test_a_different_message_discards_the_paused_budget_turn(self):
        resume = MagicMock(side_effect=AssertionError("must not resume"))
        result, mocks, send_chunk = self._run(
            "what is the weather?",
            patches={
                "orchestration.coordinator.has_pending_budget_state": AsyncMock(return_value=True),
                "orchestration.coordinator.resume_with_more_budget": resume,
                "orchestration.coordinator.clear_loop_state": MagicMock(),
                "orchestration.coordinator.run_agent_loop": _event_stream,
            },
        )
        resume.assert_not_called()
        mocks["clear_loop_state"].assert_called_once()
        # The reply says once that the paused work was dropped, then the new turn
        # runs as today.
        sent = "".join(c.args[1] for c in send_chunk.await_args_list)
        self.assertIn("dropped", sent.lower())
        self.assertTrue(any(c.args[1] == "ok" for c in send_chunk.await_args_list))

    def test_a_directive_reply_discards_the_paused_budget_turn(self):
        result, mocks, send_chunk = self._run(
            "receipts",
            patches={
                "orchestration.coordinator.has_pending_budget_state": AsyncMock(return_value=True),
                "orchestration.coordinator.clear_loop_state": MagicMock(),
                "orchestration.coordinator.fetch_recent_receipts": AsyncMock(return_value=[]),
                "orchestration.coordinator.format_receipt_list": lambda r: "No receipts.",
            },
        )
        mocks["clear_loop_state"].assert_called_once()

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
