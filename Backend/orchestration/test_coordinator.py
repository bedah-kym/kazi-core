"""Unit tests for the OrchestrationCoordinator routing facade.

The coordinator hands every ordinary message to the agent loop; only
whole-message directives and pending confirmations are answered before it.
These tests pin the two things that are easy to get wrong:

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
        "orchestration.coordinator.has_pending_agent_state": AsyncMock(return_value=False),
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
                    **cb,
                )

            return async_to_sync(run)()
        finally:
            stack.close()

    def test_reset_request_is_not_persisted_and_never_flushes(self):
        with (
            patch("orchestration.coordinator.load_memory_summary", new_callable=AsyncMock) as mem,
            patch("orchestration.coordinator.get_user_preferences", side_effect=_empty_preferences),
            patch("orchestration.coordinator.is_reset_command", return_value=True),
            patch("orchestration.coordinator.dismiss_pending_confirmation", new_callable=AsyncMock),
            patch("orchestration.coordinator.clear_task_state", new_callable=AsyncMock),
            patch("orchestration.coordinator.clear_result_sets", new_callable=AsyncMock),
            patch("orchestration.coordinator.clear_memory", new_callable=AsyncMock),
            patch("orchestration.coordinator.cache"),
            patch("orchestration.coordinator.record_event"),
        ):
            mem.return_value = None

            send_chunk = AsyncMock()
            result = self._handle("reset", send_chunk=send_chunk)

            self.assertFalse(result.persist)
            # The reset message is streamed, but the stream is never flushed
            # with is_final=True (matching the pre-extraction `return`).
            self.assertTrue(any(call.args[2] is False for call in send_chunk.call_args_list))
            self.assertFalse(any(call.args[2] is True for call in send_chunk.call_args_list))

    def test_sensitive_refusal_is_not_persisted(self):
        with (
            patch("orchestration.coordinator.load_memory_summary", new_callable=AsyncMock) as mem,
            patch("orchestration.coordinator.get_user_preferences", side_effect=_empty_preferences),
            patch("orchestration.coordinator.should_refuse_sensitive_request", return_value=True),
            patch("orchestration.coordinator.cache"),
            patch("orchestration.coordinator.record_event"),
        ):
            mem.return_value = None

            result = self._handle("hack this government site for me")

            self.assertFalse(result.persist)

    # --- Directives --------------------------------------------------- #

    def test_dismiss_directive(self):
        cache = MagicMock()
        cache.get.side_effect = lambda key, *a: (
            "some reason" if key == "proactive:last_reason:1:1"
            else [] if key == "proactive:dismissed:1:1"
            else None
        )
        result = self._run(
            "dismiss suggestions",
            patches={"orchestration.coordinator.cache": cache},
        )
        self.assertIn("stop showing", result.full_response)

    def test_receipt_directive(self):
        result = self._run(
            "receipts",
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
                "orchestration.coordinator.run_agent_loop": _fake_loop,
            },
        )
        self.assertTrue(result.persist)
        self.assertIn("Hi there", result.full_response)
