"""Unit tests for the OrchestrationCoordinator routing facade.

The coordinator is the routing pipeline extracted from `ChatConsumer.new_message`.
These tests pin the two things that are easy to get wrong during extraction:

1. `persist` — reset / sensitive-refusal must NOT be persisted (the original
   `return` skipped both the final stream flush and the DB write).
2. the normal path DOES collect a full response and persist it.
"""
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from orchestration.coordinator import OrchestrationCoordinator
from orchestration.agent_loop import AgentEvent


def _empty_preferences(user_id):
    # Module-level so sync_to_async can pickle it (thread pool).
    return {}


def _make_callbacks():
    return {
        "send_chunk": AsyncMock(),
        "send_step_event": AsyncMock(),
        "get_context_prompt": AsyncMock(return_value=""),
        "bump_signals": MagicMock(),
    }


def _base_patches():
    """Patches shared by nearly every coordinator test (no Redis / no DB)."""
    return {
        "orchestration.coordinator.load_memory_summary": AsyncMock(return_value=None),
        "orchestration.coordinator.get_user_preferences": _empty_preferences,
        "orchestration.coordinator.get_conversation_mode": AsyncMock(return_value="classic"),
        "orchestration.coordinator.has_pending_agent_state": AsyncMock(return_value=False),
        "orchestration.coordinator.load_task_state": AsyncMock(return_value=None),
        "orchestration.coordinator.record_event": MagicMock(),
        "orchestration.coordinator.cache": MagicMock(),
    }


class OrchestrationCoordinatorTests(SimpleTestCase):
    def _handle(self, query, **overrides):
        async def run():
            callbacks = _make_callbacks()
            callbacks.update(overrides)
            return await OrchestrationCoordinator().handle_message(
                query=query,
                user_id=1,
                room_id="1",
                username="alice",
                message_id=42,
                history_text="",
                **callbacks,
            )

        return async_to_sync(run)()

    def _run(self, query, patches=None, callbacks=None):
        """Run handle_message with the base patches plus extras (returns result)."""
        combined = _base_patches()
        if patches:
            combined.update(patches)

        stack = ExitStack()
        for target, mock in combined.items():
            stack.enter_context(patch(target, new=mock))

        try:
            cb = _make_callbacks()
            if callbacks:
                cb.update(callbacks)

            async def run():
                return await OrchestrationCoordinator().handle_message(
                    query=query,
                    user_id=1,
                    room_id="1",
                    username="alice",
                    message_id=42,
                    history_text="",
                    **cb,
                )

            return async_to_sync(run)()
        finally:
            stack.close()

    def test_reset_request_is_not_persisted_and_never_flushes(self):
        with (
            patch("orchestration.coordinator.load_memory_summary", new_callable=AsyncMock) as mem,
            patch("orchestration.coordinator.get_user_preferences", side_effect=_empty_preferences),
            patch("orchestration.coordinator.get_conversation_mode", new_callable=AsyncMock) as mode,
            patch("orchestration.coordinator.is_reset_request", return_value=True),
            patch("orchestration.coordinator.clear_task_state", new_callable=AsyncMock),
            patch("orchestration.coordinator.clear_result_sets", new_callable=AsyncMock),
            patch("orchestration.coordinator.clear_memory", new_callable=AsyncMock),
            patch("orchestration.coordinator.cache"),
            patch("orchestration.coordinator.record_event"),
        ):
            mem.return_value = None
            mode.return_value = "classic"

            send_chunk = AsyncMock()
            result = self._handle("reset everything", send_chunk=send_chunk)

            self.assertFalse(result.persist)
            # The reset message is streamed, but the stream is never flushed
            # with is_final=True (matching the pre-extraction `return`).
            self.assertTrue(any(call.args[2] is False for call in send_chunk.call_args_list))
            self.assertFalse(any(call.args[2] is True for call in send_chunk.call_args_list))

    def test_sensitive_refusal_is_not_persisted(self):
        with (
            patch("orchestration.coordinator.load_memory_summary", new_callable=AsyncMock) as mem,
            patch("orchestration.coordinator.get_user_preferences", side_effect=_empty_preferences),
            patch("orchestration.coordinator.get_conversation_mode", new_callable=AsyncMock) as mode,
            patch("orchestration.coordinator.should_refuse_sensitive_request", return_value=True),
            patch("orchestration.coordinator.cache"),
            patch("orchestration.coordinator.record_event"),
        ):
            mem.return_value = None
            mode.return_value = "classic"

            result = self._handle("hack this government site for me")

            self.assertFalse(result.persist)

    def test_general_chat_persists_and_collects_streamed_text(self):
        async def _fake_stream(system_prompt, user_prompt, **kwargs):
            yield "Hello"
            yield " there"

        fake_llm = MagicMock()
        fake_llm.stream_text = _fake_stream

        with (
            patch("orchestration.coordinator.load_memory_summary", new_callable=AsyncMock) as mem,
            patch("orchestration.coordinator.get_user_preferences", side_effect=_empty_preferences),
            patch("orchestration.coordinator.get_conversation_mode", new_callable=AsyncMock) as mode,
            patch("orchestration.coordinator.has_pending_agent_state", new=AsyncMock(return_value=False)),
            patch("orchestration.coordinator.load_task_state", new_callable=AsyncMock) as load_task,
            patch("orchestration.coordinator.plan_user_request", new_callable=AsyncMock) as plan,
            patch("orchestration.coordinator.parse_intent", new_callable=AsyncMock) as parse,
            patch("orchestration.coordinator.cache"),
            patch("orchestration.coordinator.record_event"),
            patch("orchestration.llm_client.get_llm_client", return_value=fake_llm),
        ):
            mem.return_value = None
            mode.return_value = "classic"
            load_task.return_value = None
            plan.return_value = {"mode": "intent"}
            parse.return_value = {
                "action": "general_chat",
                "parameters": {},
                "confidence": 0.1,
            }

            send_chunk = AsyncMock()
            result = self._handle("hi", send_chunk=send_chunk)

            self.assertTrue(result.persist)
            self.assertEqual(result.full_response, "Hello there")
            self.assertTrue(any(call.args[2] is True for call in send_chunk.call_args_list))

    def test_intent_success_caches_summary(self):
        """Regression: `nonlocal` makes the intent path cache its summary."""
        async def _fake_stream(intent, result, use_llm):
            yield "Response"

        with (
            patch("orchestration.coordinator.load_memory_summary", new_callable=AsyncMock) as mem,
            patch("orchestration.coordinator.get_user_preferences", side_effect=_empty_preferences),
            patch("orchestration.coordinator.get_conversation_mode", new_callable=AsyncMock) as mode,
            patch("orchestration.coordinator.has_pending_agent_state", new=AsyncMock(return_value=False)),
            patch("orchestration.coordinator.load_task_state", new_callable=AsyncMock) as load_task,
            patch("orchestration.coordinator.plan_user_request", new_callable=AsyncMock) as plan,
            patch("orchestration.coordinator.parse_intent", new_callable=AsyncMock) as parse,
            patch("orchestration.coordinator.init_task_state", return_value={"status": "ready"}),
            patch("orchestration.coordinator.needs_option_context", new_callable=AsyncMock) as needs_ctx,
            patch("orchestration.coordinator.requires_confirmation", return_value=False),
            patch("orchestration.coordinator.route_intent", new_callable=AsyncMock) as route,
            patch("orchestration.coordinator.should_record_receipt", return_value=False),
            patch("orchestration.coordinator.should_include_receipt", return_value=False),
            patch("orchestration.coordinator.synthesize_response", new_callable=AsyncMock) as synthesize,
            patch("orchestration.coordinator.synthesize_response_stream", side_effect=_fake_stream),
            patch("orchestration.coordinator.clear_task_state", new_callable=AsyncMock),
            patch("orchestration.coordinator.cache") as cache_mock,
            patch("orchestration.coordinator.record_event"),
        ):
            mem.return_value = None
            mode.return_value = "classic"
            load_task.return_value = None
            plan.return_value = {"mode": "intent"}
            parse.return_value = {
                "action": "get_weather",
                "parameters": {"city": "Nairobi"},
                "confidence": 0.9,
            }
            needs_ctx.return_value = None
            route.return_value = {"status": "success", "data": {}}
            synthesize.return_value = "summary text"

            result = self._handle("weather in nairobi")

            self.assertTrue(result.persist)
            cache_mock.set.assert_called_once_with(
                "orchestration:last_summary:1:1",
                "summary text",
                timeout=3600,
            )

    # --- Directives --------------------------------------------------- #

    def test_dismiss_directive(self):
        cache = MagicMock()
        cache.get.side_effect = lambda key, *a: (
            "some reason" if key == "proactive:last_reason:1:1"
            else [] if key == "proactive:dismissed:1:1"
            else None
        )
        result = self._run(
            "dismiss the nudge",
            patches={"orchestration.coordinator.cache": cache},
        )
        self.assertIn("stop showing", result.full_response)

    def test_receipt_directive(self):
        result = self._run(
            "show me my receipt",
            patches={
                "orchestration.coordinator.fetch_recent_receipts": AsyncMock(return_value=[]),
                "orchestration.coordinator.format_receipt_list": MagicMock(return_value="No receipts."),
            },
        )
        self.assertIn("No receipts.", result.full_response)

    def test_undo_directive(self):
        result = self._run(
            "undo that",
            patches={
                "orchestration.coordinator.undo_last_action": AsyncMock(return_value={"message": "Undone."}),
            },
        )
        self.assertIn("Undone.", result.full_response)

    def test_pause_directive(self):
        result = self._run(
            "stop for now",
            patches={"orchestration.coordinator.set_conversation_mode": AsyncMock()},
        )
        self.assertIn("pause tasks", result.full_response)

    def test_capabilities_directive(self):
        result = self._run("what can you do")
        self.assertIn("I can also", result.full_response)

    # --- Pending confirmations ---------------------------------------- #

    def test_pending_workflow_confirmation(self):
        async def _fake_wf_stream(message, definition, result, status, error, preferences=None):
            yield "Workflow executed."

        cache = MagicMock()
        cache.get.side_effect = lambda key, *a: (
            {"kind": "workflow", "workflow_definition": {"steps": []}, "user_message": "do it"}
            if key == "orchestration:pending:1:1" else None
        )
        result = self._run(
            "yes",
            patches={
                "orchestration.coordinator.cache": cache,
                "orchestration.coordinator.execute_adhoc_workflow": AsyncMock(
                    return_value={"status": "completed", "result": {}, "mode": "inline"}
                ),
                "orchestration.coordinator.synthesize_workflow_response_stream": _fake_wf_stream,
            },
        )
        self.assertIn("Workflow executed.", result.full_response)

    def test_pending_intent_confirmation(self):
        async def _fake_stream(intent, result, use_llm):
            yield "Done."

        cache = MagicMock()
        cache.get.side_effect = lambda key, *a: (
            {"kind": "intent", "intent": {"action": "get_weather", "parameters": {"city": "Nairobi"}, "confirmed": False}}
            if key == "orchestration:pending:1:1" else None
        )
        result = self._run(
            "yes",
            patches={
                "orchestration.coordinator.cache": cache,
                "orchestration.coordinator.needs_option_context": AsyncMock(return_value=None),
                "orchestration.coordinator.requires_confirmation": MagicMock(return_value=False),
                "orchestration.coordinator.route_intent": AsyncMock(return_value={"status": "success", "data": {}}),
                "orchestration.coordinator.should_record_receipt": MagicMock(return_value=False),
                "orchestration.coordinator.should_include_receipt": MagicMock(return_value=False),
                "orchestration.coordinator.synthesize_response": AsyncMock(return_value="summary"),
                "orchestration.coordinator.synthesize_response_stream": _fake_stream,
                "orchestration.coordinator.clear_task_state": AsyncMock(),
            },
        )
        self.assertIn("Done.", result.full_response)

    # --- Agent loop path ----------------------------------------------- #

    def test_agent_loop_path(self):
        async def _fake_loop(**kwargs):
            yield AgentEvent("text", {"text": "Hi there"})
            yield AgentEvent("thinking", {})
            yield AgentEvent("tool_start", {"name": "get_weather"})
            yield AgentEvent("tool_result", {"name": "get_weather", "result": {"status": "success"}})
            yield AgentEvent("confirmation", {"message": "Proceed?"})
            yield AgentEvent("error", {"message": "oops"})
            yield AgentEvent("done", {})

        result = self._run(
            "hello",
            patches={
                "orchestration.coordinator.get_conversation_mode": AsyncMock(return_value="auto"),
                "orchestration.coordinator.run_agent_loop": _fake_loop,
            },
        )
        self.assertTrue(result.persist)
        self.assertIn("Hi there", result.full_response)

    # --- Planner branches ---------------------------------------------- #

    def test_planner_automation_request(self):
        result = self._run(
            "automate this",
            patches={
                "orchestration.coordinator.plan_user_request": AsyncMock(return_value={"mode": "automation_request"}),
                "workflows.workflow_agent.handle_workflow_message": AsyncMock(return_value="Draft ready."),
            },
        )
        self.assertIn("Draft ready.", result.full_response)

    def test_planner_needs_clarification(self):
        result = self._run(
            "book a trip",
            patches={
                "orchestration.coordinator.plan_user_request": AsyncMock(
                    return_value={"mode": "needs_clarification", "assistant_message": "Which city?"}
                ),
            },
        )
        self.assertIn("Which city?", result.full_response)

    def test_planner_needs_confirmation(self):
        result = self._run(
            "book a trip",
            patches={
                "orchestration.coordinator.plan_user_request": AsyncMock(
                    return_value={
                        "mode": "needs_confirmation",
                        "workflow_definition": {"steps": []},
                        "assistant_message": "Confirm to proceed?",
                    }
                ),
            },
        )
        self.assertIn("Confirm to proceed?", result.full_response)

    def test_planner_adhoc_workflow(self):
        async def _fake_wf_stream(message, definition, result, status, error, preferences=None):
            yield "Ran workflow."

        result = self._run(
            "book a trip",
            patches={
                "orchestration.coordinator.plan_user_request": AsyncMock(
                    return_value={"mode": "adhoc_workflow", "workflow_definition": {"steps": []}}
                ),
                "orchestration.coordinator.execute_adhoc_workflow": AsyncMock(
                    return_value={"status": "completed", "result": {}, "mode": "inline"}
                ),
                "orchestration.coordinator.synthesize_workflow_response_stream": _fake_wf_stream,
            },
        )
        self.assertIn("Ran workflow.", result.full_response)

    # --- Intent sub-branches ------------------------------------------- #

    def test_intent_create_workflow(self):
        result = self._run(
            "create a workflow",
            patches={
                "orchestration.coordinator.plan_user_request": AsyncMock(return_value={"mode": "intent"}),
                "orchestration.coordinator.parse_intent": AsyncMock(
                    return_value={"action": "create_workflow", "parameters": {}, "confidence": 0.8}
                ),
                "orchestration.coordinator.init_task_state": MagicMock(return_value={"status": "ready"}),
                "workflows.workflow_agent.handle_workflow_message": AsyncMock(return_value="Workflow created."),
            },
        )
        self.assertIn("Workflow created.", result.full_response)

    def test_intent_awaiting_slots(self):
        result = self._run(
            "weather",
            patches={
                "orchestration.coordinator.plan_user_request": AsyncMock(return_value={"mode": "intent"}),
                "orchestration.coordinator.parse_intent": AsyncMock(
                    return_value={"action": "get_weather", "parameters": {}, "confidence": 0.9}
                ),
                "orchestration.coordinator.init_task_state": MagicMock(
                    return_value={"status": "awaiting_slots", "missing_slots": ["city"], "action": "get_weather"}
                ),
                "orchestration.coordinator.get_action_definition": MagicMock(return_value=None),
                "orchestration.coordinator.format_missing_prompt": MagicMock(return_value="Which city?"),
                "orchestration.coordinator.save_task_state": AsyncMock(),
            },
        )
        self.assertIn("Which city?", result.full_response)

    def test_intent_confirm(self):
        result = self._run(
            "email john",
            patches={
                "orchestration.coordinator.plan_user_request": AsyncMock(return_value={"mode": "intent"}),
                "orchestration.coordinator.parse_intent": AsyncMock(
                    return_value={"action": "send_email", "parameters": {"to": "a@b.c"}, "confidence": 0.7}
                ),
                "orchestration.coordinator.init_task_state": MagicMock(return_value={"status": "ready"}),
                "orchestration.coordinator.clear_task_state": AsyncMock(),
            },
        )
        self.assertIn("I think you want me to send email", result.full_response)

    # --- _execute_intent sub-branches ---------------------------------- #

    def test_execute_intent_requires_confirmation(self):
        result = self._run(
            "send email to a@b.c",
            patches={
                "orchestration.coordinator.plan_user_request": AsyncMock(return_value={"mode": "intent"}),
                "orchestration.coordinator.parse_intent": AsyncMock(
                    return_value={"action": "send_email", "parameters": {"to": "a@b.c"}, "confidence": 0.9}
                ),
                "orchestration.coordinator.init_task_state": MagicMock(return_value={"status": "ready"}),
                "orchestration.coordinator.needs_option_context": AsyncMock(return_value=None),
                "orchestration.coordinator.requires_confirmation": MagicMock(return_value=True),
                "orchestration.coordinator.build_confirmation_prompt": MagicMock(return_value="Confirm sending email?"),
            },
        )
        self.assertIn("Confirm sending email?", result.full_response)

    def test_execute_intent_error(self):
        result = self._run(
            "weather in nairobi",
            patches={
                "orchestration.coordinator.plan_user_request": AsyncMock(return_value={"mode": "intent"}),
                "orchestration.coordinator.parse_intent": AsyncMock(
                    return_value={"action": "get_weather", "parameters": {"city": "Nairobi"}, "confidence": 0.9}
                ),
                "orchestration.coordinator.init_task_state": MagicMock(return_value={"status": "ready"}),
                "orchestration.coordinator.needs_option_context": AsyncMock(return_value=None),
                "orchestration.coordinator.requires_confirmation": MagicMock(return_value=False),
                "orchestration.coordinator.route_intent": AsyncMock(return_value={"status": "error", "message": "Boom"}),
                "orchestration.coordinator.should_record_receipt": MagicMock(return_value=False),
            },
        )
        self.assertIn("Boom", result.full_response)

    def test_execute_intent_needs_option_context(self):
        result = self._run(
            "weather",
            patches={
                "orchestration.coordinator.plan_user_request": AsyncMock(return_value={"mode": "intent"}),
                "orchestration.coordinator.parse_intent": AsyncMock(
                    return_value={"action": "get_weather", "parameters": {}, "confidence": 0.9}
                ),
                "orchestration.coordinator.init_task_state": MagicMock(return_value={"status": "ready"}),
                "orchestration.coordinator.needs_option_context": AsyncMock(return_value="Which city?"),
                "orchestration.coordinator.save_task_state": AsyncMock(),
            },
        )
        self.assertIn("Which city?", result.full_response)
