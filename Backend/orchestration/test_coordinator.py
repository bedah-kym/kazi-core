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
