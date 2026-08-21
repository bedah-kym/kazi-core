"""Branch coverage for OrchestrationCoordinator (Phase 1 extraction follow-up).

The coordinator was mechanically moved out of `ChatConsumer.new_message`; these
tests exercise the routing branches the core tests don't reach, so the moved
code is genuinely covered (and so the Codecov patch gate stays green).
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


async def _chunk_stream(*args, **kwargs):
    yield "chunk"


class CoordinatorBranchTests(SimpleTestCase):
    def _run(self, query, mode="classic", patches=None, cache_get=None):
        """Run handle_message with common state deps mocked.

        `patches` maps "orchestration.coordinator.<name>" -> new value.
        `cache_get` is the value `cache.get(...)` returns (None by default).
        Returns (result, mocks, send_chunk).
        """
        send_chunk = AsyncMock()
        send_step = AsyncMock()
        stack = ExitStack()
        mocks = {}

        defaults = {
            "orchestration.coordinator.load_memory_summary": AsyncMock(return_value=None),
            "orchestration.coordinator.get_user_preferences": _empty_preferences,
            "orchestration.coordinator.get_conversation_mode": AsyncMock(return_value=mode),
            "orchestration.coordinator.has_pending_agent_state": AsyncMock(return_value=False),
            "orchestration.coordinator.load_task_state": AsyncMock(return_value=None),
            "orchestration.coordinator.cache": MagicMock(),
            "orchestration.coordinator.record_event": MagicMock(),
        }
        defaults.update(patches or {})
        for target, new in defaults.items():
            mocks[target.rsplit(".", 1)[-1]] = stack.enter_context(patch(target, new))

        mocks["cache"].get.return_value = cache_get

        async def run():
            return await OrchestrationCoordinator().handle_message(
                query=query,
                user_id=1,
                room_id="1",
                username="alice",
                message_id=42,
                history_text="",
                send_chunk=send_chunk,
                send_step_event=send_step,
                get_context_prompt=AsyncMock(return_value=""),
                bump_signals=MagicMock(),
            )

        with stack:
            result = async_to_sync(run)()

        return result, mocks, send_chunk

    # -- directives -------------------------------------------------------- #

    def test_mode_command_sets_conversation_mode(self):
        result, mocks, send_chunk = self._run(
            "/focus",
            patches={
                "orchestration.coordinator.detect_mode_command": lambda q: "focus",
                "orchestration.coordinator.set_conversation_mode": AsyncMock(),
            },
        )
        mocks["set_conversation_mode"].assert_awaited_once()
        self.assertTrue(send_chunk.await_count >= 1)

    def test_dismiss_directive(self):
        result, mocks, send_chunk = self._run("dismiss the nudge")
        texts = [c.args[1] for c in send_chunk.await_args_list]
        self.assertTrue(any("stop showing" in t for t in texts))

    def test_receipt_directive(self):
        result, mocks, send_chunk = self._run(
            "show receipts",
            patches={
                "orchestration.coordinator.is_receipt_request": lambda q: True,
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
                "orchestration.coordinator.is_undo_request": lambda q: True,
                "orchestration.coordinator.undo_last_action": AsyncMock(return_value={"message": "Undone."}),
            },
        )
        texts = [c.args[1] for c in send_chunk.await_args_list]
        self.assertIn("Undone.", texts)

    def test_pause_directive_sets_social_mode(self):
        result, mocks, send_chunk = self._run(
            "stop for now",
            patches={"orchestration.coordinator.set_conversation_mode": AsyncMock()},
        )
        mocks["set_conversation_mode"].assert_awaited_once()

    def test_capabilities_directive_lists_catalog(self):
        result, mocks, send_chunk = self._run("what can you do")
        texts = [c.args[1] for c in send_chunk.await_args_list]
        self.assertTrue(any("help with" in t for t in texts))

    # -- pending confirmations -------------------------------------------- #

    def test_pending_workflow_confirmation_executes(self):
        result, mocks, send_chunk = self._run(
            "yes",
            cache_get={
                "kind": "workflow",
                "workflow_definition": {"steps": [{"action": "send_email"}]},
                "user_message": "send it",
            },
            patches={
                "orchestration.coordinator.looks_like_confirmation": lambda q: True,
                "orchestration.coordinator.execute_adhoc_workflow": AsyncMock(
                    return_value={"status": "completed", "result": {}}
                ),
                "orchestration.coordinator.synthesize_workflow_response_stream": _chunk_stream,
            },
        )
        mocks["execute_adhoc_workflow"].assert_awaited_once()

    def test_pending_intent_confirmation_executes(self):
        result, mocks, send_chunk = self._run(
            "yes",
            cache_get={
                "kind": "intent",
                "intent": {"action": "get_weather", "parameters": {"city": "Nairobi"}, "confidence": 0.9},
            },
            patches={
                "orchestration.coordinator.looks_like_confirmation": lambda q: True,
                "orchestration.coordinator.needs_option_context": AsyncMock(return_value=None),
                "orchestration.coordinator.requires_confirmation": lambda a: False,
                "orchestration.coordinator.route_intent": AsyncMock(return_value={"status": "success", "data": {}}),
                "orchestration.coordinator.should_record_receipt": lambda a: False,
                "orchestration.coordinator.should_include_receipt": lambda a: False,
                "orchestration.coordinator.synthesize_response": AsyncMock(return_value="summary"),
                "orchestration.coordinator.synthesize_response_stream": _chunk_stream,
                "orchestration.coordinator.clear_task_state": AsyncMock(),
            },
        )
        mocks["route_intent"].assert_awaited_once()

    # -- agent loop -------------------------------------------------------- #

    def test_agent_loop_resume_after_confirmation(self):
        result, mocks, send_chunk = self._run(
            "yes",
            patches={
                "orchestration.coordinator.looks_like_confirmation": lambda q: True,
                "orchestration.coordinator.has_pending_agent_state": AsyncMock(return_value=True),
                "orchestration.coordinator.resume_after_confirmation": _event_stream,
            },
        )
        self.assertTrue(any(c.args[1] == "ok" for c in send_chunk.await_args_list))

    def test_agent_loop_start_in_focus_mode(self):
        result, mocks, send_chunk = self._run(
            "do something",
            mode="focus",
            patches={"orchestration.coordinator.run_agent_loop": _event_stream},
        )
        self.assertTrue(any(c.args[1] == "ok" for c in send_chunk.await_args_list))

    # -- planner branches -------------------------------------------------- #

    def test_planner_needs_confirmation_caches_pending(self):
        result, mocks, send_chunk = self._run(
            "book it",
            patches={
                "orchestration.coordinator.plan_user_request": AsyncMock(
                    return_value={
                        "mode": "needs_confirmation",
                        "workflow_definition": {},
                        "assistant_message": "Confirm?",
                    }
                ),
            },
        )
        self.assertTrue(mocks["cache"].set.called)

    def test_planner_adhoc_workflow_executes(self):
        result, mocks, send_chunk = self._run(
            "run it",
            patches={
                "orchestration.coordinator.plan_user_request": AsyncMock(
                    return_value={"mode": "adhoc_workflow", "workflow_definition": {"steps": [{"action": "get_weather"}]}}
                ),
                "orchestration.coordinator.execute_adhoc_workflow": AsyncMock(
                    return_value={"status": "completed", "result": {}}
                ),
                "orchestration.coordinator.synthesize_workflow_response_stream": _chunk_stream,
            },
        )
        mocks["execute_adhoc_workflow"].assert_awaited_once()

    def test_planner_needs_clarification(self):
        result, mocks, send_chunk = self._run(
            "help",
            patches={
                "orchestration.coordinator.plan_user_request": AsyncMock(
                    return_value={"mode": "needs_clarification", "assistant_message": "More detail?"}
                ),
            },
        )
        texts = [c.args[1] for c in send_chunk.await_args_list]
        self.assertIn("More detail?", texts)

    def test_planner_automation_request(self):
        result, mocks, send_chunk = self._run(
            "automate it",
            patches={
                "orchestration.coordinator.plan_user_request": AsyncMock(return_value={"mode": "automation_request"}),
                "workflows.workflow_agent.handle_workflow_message": AsyncMock(return_value="drafted"),
            },
        )
        texts = [c.args[1] for c in send_chunk.await_args_list]
        self.assertIn("drafted", texts)

    # -- intent dispatch branches ----------------------------------------- #

    def test_intent_execute_error_branch(self):
        result, mocks, send_chunk = self._run(
            "weather",
            patches={
                "orchestration.coordinator.plan_user_request": AsyncMock(return_value={"mode": "intent"}),
                "orchestration.coordinator.parse_intent": AsyncMock(
                    return_value={"action": "get_weather", "parameters": {}, "confidence": 0.9}
                ),
                "orchestration.coordinator.init_task_state": lambda i: {"status": "ready"},
                "orchestration.coordinator.needs_option_context": AsyncMock(return_value=None),
                "orchestration.coordinator.requires_confirmation": lambda a: False,
                "orchestration.coordinator.route_intent": AsyncMock(return_value={"status": "error", "message": "boom"}),
                "orchestration.coordinator.should_record_receipt": lambda a: False,
            },
        )
        texts = [c.args[1] for c in send_chunk.await_args_list]
        self.assertTrue(any("Error: boom" in t for t in texts))

    def test_intent_requires_confirmation_caches_pending(self):
        result, mocks, send_chunk = self._run(
            "weather",
            patches={
                "orchestration.coordinator.plan_user_request": AsyncMock(return_value={"mode": "intent"}),
                "orchestration.coordinator.parse_intent": AsyncMock(
                    return_value={"action": "get_weather", "parameters": {}, "confidence": 0.9}
                ),
                "orchestration.coordinator.init_task_state": lambda i: {"status": "ready"},
                "orchestration.coordinator.needs_option_context": AsyncMock(return_value=None),
                "orchestration.coordinator.requires_confirmation": lambda a: True,
            },
        )
        self.assertTrue(mocks["cache"].set.called)

    def test_intent_needs_option_context_prompts(self):
        result, mocks, send_chunk = self._run(
            "weather",
            patches={
                "orchestration.coordinator.plan_user_request": AsyncMock(return_value={"mode": "intent"}),
                "orchestration.coordinator.parse_intent": AsyncMock(
                    return_value={"action": "get_weather", "parameters": {}, "confidence": 0.9}
                ),
                "orchestration.coordinator.init_task_state": lambda i: {"status": "ready"},
                "orchestration.coordinator.needs_option_context": AsyncMock(return_value="Which city?"),
            },
        )
        texts = [c.args[1] for c in send_chunk.await_args_list]
        self.assertIn("Which city?", texts)

    def test_create_workflow_intent(self):
        result, mocks, send_chunk = self._run(
            "make a workflow",
            patches={
                "orchestration.coordinator.plan_user_request": AsyncMock(return_value={"mode": "intent"}),
                "orchestration.coordinator.parse_intent": AsyncMock(
                    return_value={"action": "create_workflow", "parameters": {}, "confidence": 0.9}
                ),
                "orchestration.coordinator.init_task_state": lambda i: {"status": "ready"},
                "workflows.workflow_agent.handle_workflow_message": AsyncMock(return_value="draft ready"),
            },
        )
        texts = [c.args[1] for c in send_chunk.await_args_list]
        self.assertIn("draft ready", texts)
