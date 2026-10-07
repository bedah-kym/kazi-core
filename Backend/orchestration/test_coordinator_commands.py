"""Ordinary sentences reach the model; only whole-message commands are directives."""
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from orchestration.agent_loop import AgentEvent
from orchestration.coordinator import OrchestrationCoordinator

ORDINARY_SENTENCES = (
    "run the build in debug mode as a scheduled task",
    "what is the default dark mode setting in vscode",
    "the installer says I need to start over, what now?",
    "git revert the last commit in the workspace",
    "how do I rollback a django migration",
    "save this receipt text to receipts.txt",
    "help me understand this traceback",
    "why did the server stop responding?",
    "add a pause of 2 seconds between retries",
)


def _empty_preferences(user_id):
    return {}


class CoordinatorCommandTests(SimpleTestCase):
    def _run(self, query, patches=None):
        captured = {}

        async def _capture_loop(**kwargs):
            captured.update(kwargs)
            yield AgentEvent("text", {"text": "ok"})

        cache = MagicMock()
        cache.get.return_value = None
        defaults = {
            "orchestration.coordinator.load_memory_summary": AsyncMock(return_value=None),
            "orchestration.coordinator.get_user_preferences": _empty_preferences,
            "orchestration.coordinator.get_conversation_mode": AsyncMock(return_value="auto"),
            "orchestration.coordinator.has_pending_agent_state": AsyncMock(return_value=False),
            "orchestration.coordinator.dismiss_pending_confirmation": AsyncMock(),
            "orchestration.coordinator.load_task_state": AsyncMock(return_value=None),
            "orchestration.coordinator.cache": cache,
            "orchestration.coordinator.record_event": MagicMock(),
            "orchestration.coordinator.run_agent_loop": _capture_loop,
        }
        defaults.update(patches or {})
        send_chunk = AsyncMock()
        stack = ExitStack()
        for target, value in defaults.items():
            stack.enter_context(patch(target, value))

        async def call():
            return await OrchestrationCoordinator().handle_message(
                query=query,
                user_id=1,
                room_id="1",
                username="alice",
                message_id=42,
                history_text="",
                send_chunk=send_chunk,
                send_step_event=AsyncMock(),
                get_context_prompt=AsyncMock(return_value=""),
                bump_signals=MagicMock(),
            )

        with stack:
            result = async_to_sync(call)()
        sent = "".join(c.args[1] for c in send_chunk.await_args_list)
        return result, captured, sent

    def test_ordinary_sentences_reach_the_agent_loop(self):
        for sentence in ORDINARY_SENTENCES:
            _result, captured, _sent = self._run(sentence)
            self.assertEqual(captured.get("user_message"), sentence, sentence)

    def test_reset_command_resets(self):
        _result, captured, sent = self._run(
            "reset",
            patches={
                "orchestration.coordinator.clear_task_state": AsyncMock(),
                "orchestration.coordinator.clear_result_sets": AsyncMock(),
                "orchestration.coordinator.clear_memory": AsyncMock(),
            },
        )
        self.assertNotIn("user_message", captured)
        self.assertIn("starting fresh", sent.lower())

    def test_reset_cancels_a_pending_action_so_a_later_yes_cannot_run_it(self):
        dismiss = AsyncMock()
        _result, captured, _sent = self._run(
            "reset",
            patches={
                "orchestration.coordinator.clear_task_state": AsyncMock(),
                "orchestration.coordinator.clear_result_sets": AsyncMock(),
                "orchestration.coordinator.clear_memory": AsyncMock(),
                "orchestration.coordinator.has_pending_agent_state": AsyncMock(return_value=True),
                "orchestration.coordinator.dismiss_pending_confirmation": dismiss,
            },
        )
        dismiss.assert_awaited_once()
        self.assertNotIn("user_message", captured)

    def test_receipts_command_lists(self):
        _result, captured, sent = self._run(
            "receipts",
            patches={
                "orchestration.coordinator.fetch_recent_receipts": AsyncMock(return_value=[]),
                "orchestration.coordinator.format_receipt_list": MagicMock(return_value="No receipts."),
            },
        )
        self.assertIn("No receipts.", sent)
        self.assertNotIn("user_message", captured)

    def test_undo_command_undoes(self):
        _result, captured, sent = self._run(
            "undo",
            patches={
                "orchestration.coordinator.undo_last_action": AsyncMock(return_value={"message": "Undone."}),
            },
        )
        self.assertIn("Undone.", sent)
        self.assertNotIn("user_message", captured)

    def test_dismiss_suggestions_command_dismisses(self):
        cache = MagicMock()
        cache.get.side_effect = lambda key, *a: (
            "some reason" if key == "proactive:last_reason:1:1"
            else [] if key == "proactive:dismissed:1:1"
            else None
        )
        _result, captured, sent = self._run(
            "dismiss suggestions",
            patches={"orchestration.coordinator.cache": cache},
        )
        self.assertIn("stop showing", sent)
        self.assertNotIn("user_message", captured)

    def test_a_cancel_word_in_a_sentence_does_not_disarm_with_nothing_pending(self):
        disarm = MagicMock()
        _result, captured, _sent = self._run(
            "why did it stop?",
            patches={
                "orchestration.shell.autopilot.is_armed": MagicMock(return_value=True),
                "orchestration.shell.autopilot.disarm": disarm,
            },
        )
        disarm.assert_not_called()
        self.assertEqual(captured.get("user_message"), "why did it stop?")

    def test_a_whole_message_stop_disarms_autopilot(self):
        disarm = MagicMock(return_value=True)
        _result, _captured, _sent = self._run(
            "stop",
            patches={
                "orchestration.shell.autopilot.is_armed": MagicMock(return_value=True),
                "orchestration.shell.autopilot.disarm": disarm,
            },
        )
        disarm.assert_called_once()

    def test_a_cancel_word_with_a_pending_action_cancels_it(self):
        cancel = AsyncMock(return_value="Cancelled.")
        _result, _captured, _sent = self._run(
            "why did it stop?",
            patches={
                "orchestration.coordinator.has_pending_agent_state": AsyncMock(return_value=True),
                "orchestration.coordinator.cancel_pending_action": cancel,
                "orchestration.shell.autopilot.disarm": MagicMock(return_value=True),
            },
        )
        cancel.assert_awaited_once()
