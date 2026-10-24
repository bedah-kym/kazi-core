"""The autopilot reply must be true in a tainted room (C4).

A tainted room escalates a network-reaching shell command even inside an armed
window, so the reply must say so instead of promising every command runs.
"""
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from orchestration.agent_loop import AgentEvent, LoopState
from orchestration.coordinator import OrchestrationCoordinator
from tests.turn_support import turn_test_patches

CLEAN_REPLY = (
    "Autopilot armed for this room. Shell commands, including root and network "
    "ones, now run on this unsandboxed host without asking until the window "
    "expires or you say **autopilot off**. Commands on the destructive list "
    "still ask."
)
TAINT_NOTE = "commands that reach the network will still ask while the room holds it"


def _confirmation_state():
    return LoopState(
        messages=[],
        pending_tool={"name": "run_command", "input": {"command": "ls"}},
        pending_tier="bounded",
    )


class AutopilotArmedWordingTests(SimpleTestCase):
    def _arm(self, taint, history_messages=None):
        """Reply to **autopilot** with a pending shell command; return sent text."""
        send_chunk = AsyncMock()

        async def _resume(**kwargs):
            yield AgentEvent("text", {"text": "ok"})
            yield AgentEvent("done", {})

        defaults = {
            **turn_test_patches(),
            "orchestration.coordinator.load_memory_summary": AsyncMock(return_value=None),
            "orchestration.coordinator.get_user_preferences": lambda user_id, profile_obj=None: {
                "shell_profile": "open",
            },
            "orchestration.coordinator.has_pending_agent_state": AsyncMock(return_value=True),
            "orchestration.coordinator.has_pending_budget_state": AsyncMock(return_value=False),
            "orchestration.coordinator.load_pending_state": AsyncMock(return_value=_confirmation_state()),
            "orchestration.coordinator.resume_after_confirmation": _resume,
            "orchestration.coordinator.cache": MagicMock(),
            "orchestration.coordinator.record_event": MagicMock(),
            "orchestration.shell.autopilot.is_armed": MagicMock(return_value=False),
            "orchestration.shell.autopilot.arm": MagicMock(return_value=True),
            "orchestration.shell.environment.get_shell_environment": AsyncMock(return_value={}),
        }
        if isinstance(taint, Exception):
            defaults["orchestration.coordinator._conversation_tainted"] = MagicMock(side_effect=taint)
        else:
            defaults["orchestration.coordinator._conversation_tainted"] = MagicMock(return_value=taint)

        async def run():
            return await OrchestrationCoordinator().handle_message(
                query="autopilot",
                user_id=1,
                room_id="1",
                username="alice",
                message_id=42,
                send_chunk=send_chunk,
                send_step_event=AsyncMock(),
                get_context_prompt=AsyncMock(return_value=""),
                bump_signals=MagicMock(),
                history_messages=history_messages,
            )

        with ExitStack() as stack:
            for target, value in defaults.items():
                stack.enter_context(patch(target, value, create=True))
            async_to_sync(run)()

        return "".join(call.args[1] for call in send_chunk.await_args_list)

    def test_a_tainted_room_is_told_network_commands_still_ask(self):
        sent = self._arm(True)

        self.assertIn(TAINT_NOTE, sent)
        self.assertIn("untrusted content", sent)
        self.assertIn("lifts on its own", sent)
        self.assertNotIn("including root and network ones", sent)

    def test_a_clean_room_keeps_todays_reply(self):
        sent = self._arm(False)

        self.assertIn(CLEAN_REPLY, sent)
        self.assertNotIn(TAINT_NOTE, sent)

    def test_an_unreadable_taint_state_uses_the_cautious_reply(self):
        sent = self._arm(RuntimeError("cache down"))

        self.assertIn(TAINT_NOTE, sent)
        self.assertIn("untrusted content", sent)
        self.assertIn("lifts on its own", sent)

    def test_replayed_untrusted_history_uses_the_tainted_reply_in_a_clean_window(self):
        history = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "h1", "name": "run_command", "input": {"command": "ls"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "h1", "content": "listed"},
            ]},
        ]

        sent = self._arm(False, history_messages=history)

        self.assertIn(TAINT_NOTE, sent)
        self.assertIn("untrusted content", sent)
        self.assertIn("lifts on its own", sent)
        self.assertNotIn("including root and network ones", sent)
