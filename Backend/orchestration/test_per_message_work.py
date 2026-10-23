"""T-F6 per-message work: tracker off the hot path and no success claim.

Each test is written to fail on ``main``:
- the loop no longer calls ``update_memory_state`` per tool call;
- the loop does not write a ``Completed: ...`` summary;
- a tool-using turn leaves the next turn's memory summary empty;
- the pause helpers accept state the coordinator already read (including None);
- the producer-less named-entity branch of the telemetry rollup is gone.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from orchestration.agent_loop import AgentEvent, LoopState


def _make_llm_response(content_blocks, stop_reason="end_turn", usage=None):
    return {
        "content": content_blocks,
        "stop_reason": stop_reason,
        "usage": usage or {"input_tokens": 100, "output_tokens": 50},
    }


def _tool_use_block(tool_id, name, tool_input):
    return {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}


async def _collect(async_gen):
    return [event async for event in async_gen]


class _MemoryCache:
    """Small in-memory stand-in for the Django cache used by memory_state."""

    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value, timeout=None):
        self.data[key] = value

    def delete(self, key):
        self.data.pop(key, None)


class _LoopCase(SimpleTestCase):
    def _run_tool_turn(self, tool_calls=None, execute_result=None):
        from orchestration.agent_loop import run_agent_loop

        if tool_calls is None:
            tool_calls = [("get_weather", {"city": "Nairobi"})]
        if execute_result is None:
            execute_result = {"status": "success"}

        first_blocks = [
            _tool_use_block(f"t{index}", name, tool_input)
            for index, (name, tool_input) in enumerate(tool_calls)
        ]
        llm = MagicMock()
        llm.default_provider = MagicMock(return_value="deepseek")
        llm.create_message = AsyncMock(side_effect=[
            _make_llm_response(first_blocks, stop_reason="tool_use"),
            _make_llm_response([{"type": "text", "text": "Done."}]),
        ])

        context = {"user_id": 1, "room_id": 1, "username": "t"}

        async def _drive():
            return await _collect(run_agent_loop(
                user_message="weather?",
                context=context,
            ))

        with patch("orchestration.agent_loop.get_llm_client", return_value=llm), \
                patch("orchestration.agent_loop._resolve_persona", new=AsyncMock(return_value=None)), \
                patch("orchestration.agent_loop.execute_tool", new=AsyncMock(
                    return_value=execute_result,
                )), \
                patch("orchestration.agent_loop._record_receipt", new=AsyncMock()), \
                patch("orchestration.agent_loop.enforce_agent_caps", return_value=True), \
                patch("orchestration.agent_loop._search_backend_available", return_value=False), \
                patch("orchestration.agent_loop.cache") as mock_cache, \
                patch("orchestration.agent_loop.record_event"), \
                patch("orchestration.memory_state._persist_entities_to_db", new=AsyncMock()), \
                patch("orchestration.memory_state.record_event"):
            mock_cache.get.return_value = None
            return asyncio.run(_drive()), context


class TrackerOffHotPathTests(_LoopCase):
    def test_loop_does_not_update_memory_state_per_tool_call(self):
        with patch(
            "orchestration.agent_loop.update_memory_state",
            new=AsyncMock(),
            create=True,
        ) as tracker, patch(
            "orchestration.agent_loop.update_memory_state_batch",
            new=AsyncMock(),
            create=True,
        ):
            self._run_tool_turn()

        tracker.assert_not_called()

    def test_loop_tracks_the_turn_in_one_batch_with_every_call(self):
        calls = [
            ("get_weather", {"city": "Nairobi"}),
            ("get_weather", {"city": "Mombasa"}),
        ]
        with patch(
            "orchestration.agent_loop.update_memory_state_batch",
            new=AsyncMock(),
            create=True,
        ) as batch:
            self._run_tool_turn(tool_calls=calls)

        batch.assert_called_once()
        records = batch.call_args.args[1]
        self.assertEqual(
            [(record["name"], record["input"]) for record in records],
            calls,
        )

    def test_loop_does_not_write_a_completion_summary(self):
        written = []

        async def _capture(_context, summary):
            written.append(summary)

        with patch(
            "orchestration.agent_loop.save_memory_summary",
            new=AsyncMock(side_effect=_capture),
            create=True,
        ), patch(
            "orchestration.memory_state.save_memory_summary",
            new=AsyncMock(side_effect=_capture),
        ):
            self._run_tool_turn()

        self.assertFalse(any("Completed:" in summary for summary in written))


class ToolTurnLeavesNoMemorySummaryTests(_LoopCase):
    def _run_and_check_summary(self, execute_result):
        memory_cache = _MemoryCache()
        with patch("orchestration.memory_state.cache", memory_cache):
            import orchestration.memory_state as memory_state

            context = {"user_id": 1, "room_id": 1, "username": "t"}
            async_to_sync(memory_state.save_memory_summary)(
                context, "Completed: old_turn (1.0s)",
            )
            self.assertTrue(async_to_sync(memory_state.load_memory_summary)(context))

            _events, context = self._run_tool_turn(
                tool_calls=[("get_weather", {"city": "Nairobi"})],
                execute_result=execute_result,
            )

            self.assertIsNone(async_to_sync(memory_state.load_memory_summary)(context))
            state = async_to_sync(memory_state.load_memory_state)(context)
            self.assertIsNotNone(state)
            self.assertEqual(state["entities"]["city"], "Nairobi")

    def test_successful_tool_turn_leaves_the_next_summary_empty(self):
        self._run_and_check_summary({"status": "success"})

    def test_failed_tool_turn_leaves_the_next_summary_empty(self):
        self._run_and_check_summary({"status": "error", "message": "boom"})


class PauseStatePassthroughTests(SimpleTestCase):
    def test_budget_helpers_use_the_state_the_coordinator_read(self):
        from orchestration.agent_loop import has_pending_budget_state, pending_resume_kind

        state = LoopState(messages=[], awaiting_budget=True, awaiting_quota=True)

        async def _check():
            return (
                await has_pending_budget_state(1, 1, state=state),
                await pending_resume_kind(1, 1, state=state),
            )

        with patch("orchestration.agent_loop.load_loop_state") as loader:
            budget, kind = asyncio.run(_check())

        self.assertTrue(budget)
        self.assertEqual(kind, "quota")
        loader.assert_not_called()

    def test_a_passed_none_is_not_reread(self):
        from orchestration.agent_loop import has_pending_budget_state, pending_resume_kind

        async def _check():
            return (
                await has_pending_budget_state(1, 1, state=None),
                await pending_resume_kind(1, 1, state=None),
            )

        with patch("orchestration.agent_loop.load_loop_state") as loader:
            budget, kind = asyncio.run(_check())

        self.assertFalse(budget)
        self.assertEqual(kind, "")
        loader.assert_not_called()


class ProfileObjectPassthroughTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="pref-profile-user", password="pw",  # nosec B106
        )
        profile = self.user.profile
        profile.timezone = "Africa/Nairobi"
        profile.location = "Nairobi"
        profile.notification_preferences = {
            "allow_email": False,
            "assistant_locale": "fr-FR",
        }
        profile.save()

    def test_get_user_preferences_accepts_a_loaded_profile(self):
        from orchestration.user_preferences import get_user_preferences

        direct = get_user_preferences(self.user.id)
        passed = get_user_preferences(self.user.id, profile_obj=self.user.profile)

        self.assertEqual(passed, direct)
        self.assertFalse(passed["allow_email"])
        self.assertEqual(passed["locale"], "fr-FR")

    def test_is_armed_accepts_a_loaded_profile(self):
        from datetime import timedelta

        from django.utils import timezone

        from orchestration.shell.autopilot import is_armed

        prefs = dict(self.user.profile.notification_preferences or {})
        prefs["shell_autopilot"] = {
            "1": {"expires_at": (timezone.now() + timedelta(minutes=5)).isoformat()},
        }
        self.user.profile.notification_preferences = prefs
        self.user.profile.save(update_fields=["notification_preferences"])

        with patch("orchestration.shell.autopilot.profile", side_effect=AssertionError("re-read")):
            armed = is_armed(self.user.id, 1, profile_obj=self.user.profile)

        self.assertTrue(armed)


class DeadTelemetryBranchTests(SimpleTestCase):
    def test_tool_params_and_inputs_produce_no_named_entities(self):
        from orchestration.telemetry_rollups import extract_entity_facts

        facts = extract_entity_facts([
            {"event": "agent_loop_done", "params": {"city": "Nairobi"}},
            {"event": "agent_loop_done", "input": {"email": "ops@example.com"}},
        ])

        self.assertEqual(facts, {})


class ResumeReadsFreshStateTests(SimpleTestCase):
    def test_resume_helpers_cannot_take_a_stale_coordinator_copy(self):
        import inspect

        from orchestration import agent_loop

        for helper in (
            agent_loop.resume_after_confirmation,
            agent_loop.resume_after_quota,
            agent_loop.resume_with_more_budget,
        ):
            with self.subTest(helper=helper.__name__):
                self.assertNotIn("loop_state", inspect.signature(helper).parameters)

    def test_resume_reads_the_paused_state_at_resume_time(self):
        from orchestration.agent_loop import _args_hash, resume_after_confirmation

        call = {"id": "t1", "name": "run_command", "input": {"command": "ls"}}
        state = LoopState(
            messages=[],
            pending_tool=call,
            pending_tier="bounded",
            pending_args_hashes={"t1": _args_hash(call["input"])},
        )
        record = MagicMock()
        record.metadata = {"calls": [{"tool_call_id": "t1", "tool": "run_command"}]}
        turn = MagicMock(state="running", pause_expires_at=None)

        async def _fake_run(**_kwargs):
            yield AgentEvent("done", {})

        async def _run():
            return await _collect(resume_after_confirmation(
                context={"user_id": 1, "room_id": 1, "turn_id": "turn-1"},
            ))

        with (
            patch(
                "orchestration.agent_loop._claim_turn_for_resume",
                new=AsyncMock(return_value=(turn, True)),
            ),
            patch("orchestration.agent_loop._pending_approval_record", return_value=record),
            patch("orchestration.agent_loop._consume_pending_approval_sync", return_value=True),
            patch(
                "orchestration.agent_loop._load_resume_state",
                new=AsyncMock(return_value=state),
            ) as loader,
            patch("orchestration.agent_loop.clear_loop_state"),
            patch("orchestration.agent_loop.run_agent_loop", side_effect=_fake_run),
        ):
            events = asyncio.run(_run())

        loader.assert_called_once()
        self.assertTrue(any(event.kind == "done" for event in events))
