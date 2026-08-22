"""Branch coverage for OrchestrationCoordinator routing.

Fills the Codecov patch gap on the extracted coordinator: directives,
pending confirmations, agent-loop resume, planner branches, and the intent
dispatch branches. All external services/LLM calls are mocked.
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


async def _workflow_stream(*args, **kwargs):
    yield "Workflow ran."


async def _synthesize_stream(intent, result, use_llm):
    yield "Synthesized."


class RoutingBranchTests(SimpleTestCase):
    def _run(self, query, mocks=None, mode="classic"):
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
            "orchestration.coordinator.get_conversation_mode": AsyncMock(return_value=mode),
            "orchestration.coordinator.has_pending_agent_state": AsyncMock(return_value=False),
            "orchestration.coordinator.load_task_state": AsyncMock(return_value=None),
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
                    history_text="",
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

    def test_mode_command_short_circuits(self):
        set_mode = AsyncMock()
        result, chunks = self._run("focus mode", mocks={
            "orchestration.coordinator.set_conversation_mode": set_mode,
        })
        self.assertTrue(result.persist)
        set_mode.assert_awaited_once()

    def test_dismiss_directive(self):
        result, chunks = self._run("dismiss the nudge please")
        self.assertTrue(result.persist)
        self.assertTrue(self._broadcast(chunks, "stop showing"))

    def test_receipt_directive(self):
        result, chunks = self._run("what did you do", mocks={
            "orchestration.coordinator.fetch_recent_receipts": AsyncMock(return_value=[]),
            "orchestration.coordinator.format_receipt_list": MagicMock(return_value="No receipts yet."),
        })
        self.assertTrue(result.persist)
        self.assertTrue(self._broadcast(chunks, "No receipts"))

    def test_undo_directive(self):
        result, chunks = self._run("undo last action", mocks={
            "orchestration.coordinator.undo_last_action": AsyncMock(return_value={"message": "Undone."}),
        })
        self.assertTrue(result.persist)
        self.assertTrue(self._broadcast(chunks, "Undone."))

    def test_pause_directive(self):
        set_mode = AsyncMock()
        result, chunks = self._run("stop for now", mocks={
            "orchestration.coordinator.set_conversation_mode": set_mode,
        })
        self.assertTrue(result.persist)
        set_mode.assert_awaited_once()
        self.assertTrue(self._broadcast(chunks, "pause tasks"))

    def test_capabilities_directive(self):
        result, chunks = self._run("what can you do")
        self.assertTrue(result.persist)
        self.assertTrue(self._broadcast(chunks, "help with"))

    # -- pending confirmations ------------------------------------------ #

    def test_pending_workflow_confirmation(self):
        cache = MagicMock()
        cache.get.return_value = {
            "kind": "workflow",
            "workflow_definition": {"steps": []},
            "user_message": "do the thing",
        }
        execute = AsyncMock(return_value={"status": "completed", "mode": "inline", "result": {}})
        result, chunks = self._run("yes", mocks={
            "orchestration.coordinator.cache": cache,
            "orchestration.coordinator.execute_adhoc_workflow": execute,
            "orchestration.coordinator.synthesize_workflow_response_stream": _workflow_stream,
        })
        self.assertTrue(result.persist)
        execute.assert_awaited_once()

    def test_pending_intent_confirmation(self):
        cache = MagicMock()
        cache.get.return_value = {
            "kind": "intent",
            "intent": {"action": "get_weather", "parameters": {"city": "Nairobi"}, "confidence": 0.9},
        }
        route = AsyncMock(return_value={"status": "success", "data": {}})
        result, chunks = self._run("yes", mocks={
            "orchestration.coordinator.cache": cache,
            "orchestration.coordinator.needs_option_context": AsyncMock(return_value=None),
            "orchestration.coordinator.requires_confirmation": MagicMock(return_value=False),
            "orchestration.coordinator.route_intent": route,
            "orchestration.coordinator.should_record_receipt": MagicMock(return_value=False),
            "orchestration.coordinator.should_include_receipt": MagicMock(return_value=False),
            "orchestration.coordinator.synthesize_response": AsyncMock(return_value="summary"),
            "orchestration.coordinator.synthesize_response_stream": _synthesize_stream,
            "orchestration.coordinator.clear_task_state": AsyncMock(),
        })
        self.assertTrue(result.persist)
        route.assert_awaited_once()

    def test_pending_cancel(self):
        cache = MagicMock()
        cache.get.return_value = {"kind": "workflow", "workflow_definition": {}, "user_message": "x"}
        result, chunks = self._run("cancel", mocks={"orchestration.coordinator.cache": cache})
        self.assertTrue(result.persist)
        self.assertTrue(self._broadcast(chunks, "cancelled"))

    # -- agent-loop resume ---------------------------------------------- #

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

    def test_agent_loop_dismiss_then_planner_clarification(self):
        dismiss = AsyncMock()
        plan = AsyncMock(return_value={"mode": "needs_clarification", "assistant_message": "Tell me more."})
        result, chunks = self._run("hello there", mocks={
            "orchestration.coordinator.has_pending_agent_state": AsyncMock(return_value=True),
            "orchestration.coordinator.dismiss_pending_confirmation": dismiss,
            "orchestration.coordinator.plan_user_request": plan,
        })
        self.assertTrue(result.persist)
        dismiss.assert_awaited_once()
        self.assertTrue(self._broadcast(chunks, "Tell me more."))

    # -- agent loop path ------------------------------------------------- #

    def test_agent_loop_path(self):
        result, chunks = self._run("hi", mode="focus", mocks={
            "orchestration.coordinator.run_agent_loop": _agent_events,
        })
        self.assertTrue(result.persist)
        self.assertTrue(self._broadcast(chunks, "hi"))

    # -- planner branches ------------------------------------------------ #

    def test_planner_automation_request(self):
        plan = AsyncMock(return_value={"mode": "automation_request"})
        workflow_agent = AsyncMock(return_value="Workflow draft ready.")
        result, chunks = self._run("automate a weekly email", mocks={
            "orchestration.coordinator.plan_user_request": plan,
            "workflows.workflow_agent.handle_workflow_message": workflow_agent,
        })
        self.assertTrue(result.persist)
        workflow_agent.assert_awaited_once()

    def test_planner_needs_confirmation(self):
        plan = AsyncMock(return_value={
            "mode": "needs_confirmation",
            "workflow_definition": {"steps": []},
            "assistant_message": "Shall I proceed?",
        })
        cache = MagicMock()
        result, chunks = self._run("set up a workflow", mocks={
            "orchestration.coordinator.plan_user_request": plan,
            "orchestration.coordinator.cache": cache,
        })
        self.assertTrue(result.persist)
        self.assertTrue(self._broadcast(chunks, "proceed"))

    def test_planner_adhoc_workflow(self):
        plan = AsyncMock(return_value={
            "mode": "adhoc_workflow",
            "workflow_definition": {"steps": []},
        })
        execute = AsyncMock(return_value={"status": "completed", "mode": "inline", "result": {}})
        result, chunks = self._run("run a workflow now", mocks={
            "orchestration.coordinator.plan_user_request": plan,
            "orchestration.coordinator.execute_adhoc_workflow": execute,
            "orchestration.coordinator.synthesize_workflow_response_stream": _workflow_stream,
        })
        self.assertTrue(result.persist)
        execute.assert_awaited_once()

    # -- intent dispatch branches --------------------------------------- #

    def test_intent_create_workflow(self):
        plan = AsyncMock(return_value={"mode": "intent"})
        parse = AsyncMock(return_value={"action": "create_workflow", "parameters": {}, "confidence": 0.9})
        workflow_agent = AsyncMock(return_value="Draft created.")
        result, chunks = self._run("create a workflow", mocks={
            "orchestration.coordinator.plan_user_request": plan,
            "orchestration.coordinator.parse_intent": parse,
            "workflows.workflow_agent.handle_workflow_message": workflow_agent,
        })
        self.assertTrue(result.persist)
        workflow_agent.assert_awaited_once()

    def test_intent_missing_slots_asks(self):
        plan = AsyncMock(return_value={"mode": "intent"})
        parse = AsyncMock(return_value={"action": "get_weather", "parameters": {}, "confidence": 0.9})
        result, chunks = self._run("weather please", mocks={
            "orchestration.coordinator.plan_user_request": plan,
            "orchestration.coordinator.parse_intent": parse,
            "orchestration.coordinator.save_task_state": AsyncMock(),
        })
        self.assertTrue(result.persist)
        self.assertTrue(self._broadcast(chunks, "still need"))

    def test_intent_confirm_branch(self):
        plan = AsyncMock(return_value={"mode": "intent"})
        parse = AsyncMock(return_value={"action": "get_weather", "parameters": {"city": "Nairobi"}, "confidence": 0.7})
        cache = MagicMock()
        result, chunks = self._run("weather", mocks={
            "orchestration.coordinator.plan_user_request": plan,
            "orchestration.coordinator.parse_intent": parse,
            "orchestration.coordinator.init_task_state": MagicMock(return_value={"status": "ready"}),
            "orchestration.coordinator.cache": cache,
            "orchestration.coordinator.clear_task_state": AsyncMock(),
        })
        self.assertTrue(result.persist)
        self.assertTrue(self._broadcast(chunks, "I think you want me to"))

    def test_execute_intent_error(self):
        plan = AsyncMock(return_value={"mode": "intent"})
        parse = AsyncMock(return_value={"action": "get_weather", "parameters": {"city": "Nairobi"}, "confidence": 0.9})
        route = AsyncMock(return_value={"status": "error", "message": "boom"})
        result, chunks = self._run("weather", mocks={
            "orchestration.coordinator.plan_user_request": plan,
            "orchestration.coordinator.parse_intent": parse,
            "orchestration.coordinator.init_task_state": MagicMock(return_value={"status": "ready"}),
            "orchestration.coordinator.needs_option_context": AsyncMock(return_value=None),
            "orchestration.coordinator.requires_confirmation": MagicMock(return_value=False),
            "orchestration.coordinator.route_intent": route,
            "orchestration.coordinator.should_record_receipt": MagicMock(return_value=False),
            "orchestration.coordinator.clear_task_state": AsyncMock(),
        })
        self.assertTrue(result.persist)
        self.assertTrue(self._broadcast(chunks, "boom"))

    def test_execute_intent_needs_clarification(self):
        plan = AsyncMock(return_value={"mode": "intent"})
        parse = AsyncMock(return_value={"action": "get_weather", "parameters": {"city": "Nairobi"}, "confidence": 0.9})
        route = AsyncMock(return_value={"status": "needs_clarification", "message": "Which city?"})
        result, chunks = self._run("weather", mocks={
            "orchestration.coordinator.plan_user_request": plan,
            "orchestration.coordinator.parse_intent": parse,
            "orchestration.coordinator.init_task_state": MagicMock(return_value={"status": "ready"}),
            "orchestration.coordinator.needs_option_context": AsyncMock(return_value=None),
            "orchestration.coordinator.requires_confirmation": MagicMock(return_value=False),
            "orchestration.coordinator.route_intent": route,
            "orchestration.coordinator.should_record_receipt": MagicMock(return_value=False),
            "orchestration.coordinator.clear_task_state": AsyncMock(),
        })
        self.assertTrue(result.persist)
        self.assertTrue(self._broadcast(chunks, "Which city?"))
