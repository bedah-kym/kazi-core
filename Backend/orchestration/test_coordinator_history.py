"""The coordinator hands the agent loop the transcript it was given, untouched."""
import json
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from chatbot.transcript import build_history_messages
from orchestration.agent_loop import AgentEvent
from orchestration.coordinator import OrchestrationCoordinator
from orchestration.testsupport import turn_test_patches
from orchestration.tool_record import RECORD_STRING_MAX


def _run_agent_turn(query, *, history_messages=None, memory_summary=None, context_prompt="", events=None, load_preferences=None):
    """Run one loop-path turn with state mocked; returns (result, what run_agent_loop received)."""
    captured = {}
    scripted = events if events is not None else [AgentEvent("text", {"text": "ok"})]

    async def _capture(**kwargs):
        captured.update(kwargs)
        for event in scripted:
            yield event

    cache = MagicMock()
    cache.get.return_value = None
    targets = {
        "orchestration.coordinator.load_memory_summary": AsyncMock(return_value=memory_summary),
        "orchestration.coordinator.get_user_preferences": load_preferences if load_preferences is not None else (lambda user_id, profile_obj=None: {}),
        "orchestration.coordinator.has_pending_agent_state": AsyncMock(return_value=False),
        "orchestration.coordinator.cache": cache,
        "orchestration.coordinator.record_event": MagicMock(),
        "orchestration.coordinator.run_agent_loop": _capture,
        **turn_test_patches(),
    }

    async def run():
        return await OrchestrationCoordinator().handle_message(
            query=query,
            user_id=1,
            room_id="1",
            username="alice",
            message_id=42,
            send_chunk=AsyncMock(),
            send_step_event=AsyncMock(),
            get_context_prompt=AsyncMock(return_value=context_prompt),
            bump_signals=MagicMock(),
            history_messages=history_messages,
        )

    with ExitStack() as stack:
        for target, new in targets.items():
            stack.enter_context(patch(target, new))
        result = async_to_sync(run)()
    return result, captured


def _replayed_input(tool_input):
    """The input a later turn sees for one tool call, after the record is replayed."""
    events = [AgentEvent("tool_result", {
        "name": "create_note",
        "result": {"status": "success"},
        "input": tool_input,
        "shown": "note saved",
    })]
    result, _captured = _run_agent_turn("write the note", events=events)
    rows = [
        (1, "alice", "write the note"),
        (2, "kazi", "Done.", {"tools": result.tools, "by": 1, "model": "Done.", "harness": ""}),
    ]
    messages = build_history_messages(rows, viewer_user_id=1)
    use = messages[1]["content"][0]
    return use["input"]


class ToolRecordMarkerTests(SimpleTestCase):
    def test_a_long_input_replays_with_a_shortened_marker_and_its_original_length(self):
        content = "x" * 900

        replayed = _replayed_input({"content": content})

        self.assertIn("shortened", replayed["content"])
        self.assertIn("the call sent 900 characters", replayed["content"])
        self.assertNotEqual(replayed["content"], content)
        self.assertLessEqual(len(replayed["content"]), RECORD_STRING_MAX)

    def test_restricted_keys_and_secret_values_stay_out_of_the_record(self):
        secret = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456"  # nosec B105 — test fixture — fake credential

        replayed = _replayed_input({
            "content": "summarise this",
            "token": "fake-token",  # nosec B105 — test fixture — fake credential
            "api_key": "fake-api-key",  # nosec B105 — test fixture — fake credential
            "note": secret,
        })
        stored = json.dumps(replayed)

        self.assertNotIn("token", replayed)
        self.assertNotIn("api_key", replayed)
        self.assertNotIn("fake-token", stored)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz123456", stored)
        self.assertNotIn("shortened", stored)

    def test_a_short_input_replays_unchanged_with_no_marker(self):
        replayed = _replayed_input({"content": "a short note"})

        self.assertEqual(replayed["content"], "a short note")
        self.assertNotIn("shortened", json.dumps(replayed))


class CoordinatorHistoryTests(SimpleTestCase):
    def test_structured_history_reaches_the_loop_untouched(self):
        history = [
            {"role": "user", "content": "make a folder"},
            {"role": "assistant", "content": "Step 2: write the lines\ncommand: mkdir qa"},
        ]

        _result, captured = _run_agent_turn("what next?", history_messages=history)

        self.assertEqual(captured["history"], history)
        self.assertEqual(captured["user_message"], "what next?")

    def test_memory_and_room_context_go_to_the_prompt_not_the_transcript(self):
        history = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]

        _result, captured = _run_agent_turn(
            "continue",
            history_messages=history,
            memory_summary="Recent actions: run_command.",
            context_prompt="IMPORTANT NOTES:\n- [#7] [DECISION] ship on friday",
        )

        self.assertEqual(captured["history"], history)
        self.assertEqual(captured["memory_summary"], "Recent actions: run_command.")
        self.assertIn("ship on friday", captured["context_prompt"])

    def test_no_history_is_passed_as_none(self):
        _result, captured = _run_agent_turn("hello", history_messages=[])

        self.assertIsNone(captured["history"])

    def test_a_tool_turn_carries_its_records_and_splits_harness_text(self):
        events = [
            AgentEvent("text", {"text": "Running it now. "}),
            AgentEvent("tool_result", {
                "name": "run_command",
                "result": {"status": "success", "stdout": "hi"},
                "input": {"command": "echo hi"},
                "shown": '{"status": "success", "stdout": "hi"}',
            }),
            AgentEvent("confirmation", {"message": "Should I go ahead? (yes / no)"}),
        ]

        result, _captured = _run_agent_turn("run echo hi", events=events)

        self.assertEqual(result.tools, [{
            "name": "run_command",
            "input": {"command": "echo hi"},
            "status": "success",
            "shown": '{"status": "success", "stdout": "hi"}',
        }])
        self.assertEqual(result.model_text, "Running it now. ")
        self.assertEqual(result.harness, "Should I go ahead? (yes / no)")
        self.assertEqual(result.full_response, "Running it now. Should I go ahead? (yes / no)")

    def test_history_that_replays_untrusted_output_starts_the_turn_tainted(self):
        history = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "h1", "name": "run_command", "input": {"command": "ls"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "h1", "content": "listed"},
            ]},
        ]

        _result, captured = _run_agent_turn("next", history_messages=history)

        self.assertTrue(captured["history_tainted"])

    def test_history_without_untrusted_output_does_not_start_tainted(self):
        history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]

        _result, captured = _run_agent_turn("next", history_messages=history)

        self.assertFalse(captured["history_tainted"])
