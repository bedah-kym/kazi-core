"""Taint outlives the run that picked it up.

See ``docs/plans/2026-10-taint-across-turns.md``.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from orchestration.agent_loop import (
    LoopState,
    _conversation_tainted,
    _taint_run,
    _taint_ttl,
    run_agent_loop,
)


def _tool_use(name, tool_input, call_id="t1"):
    return {
        "content": [{"type": "tool_use", "id": call_id, "name": name, "input": tool_input}],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def _shell(command, call_id="t1"):
    return _tool_use("run_command", {"command": command}, call_id)


_DONE = {
    "content": [{"type": "text", "text": "done"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 1, "output_tokens": 1},
}


class RoomTaintTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_taint_is_remembered_for_the_room(self):
        self.assertFalse(_conversation_tainted(71))
        state = LoopState(messages=[])
        _taint_run(state, 71)
        self.assertTrue(state.tainted)
        self.assertTrue(_conversation_tainted(71))
        self.assertFalse(_conversation_tainted(72))

    def test_unscoped_run_is_tainted_but_not_remembered(self):
        state = LoopState(messages=[])
        _taint_run(state, None)
        self.assertTrue(state.tainted)
        self.assertFalse(_conversation_tainted(None))

    @override_settings(AGENT_TAINT_TTL_SECONDS=0)
    def test_zero_ttl_restores_per_run_taint(self):
        state = LoopState(messages=[])
        _taint_run(state, 71)
        self.assertTrue(state.tainted)
        self.assertFalse(_conversation_tainted(71))

    def test_junk_or_negative_ttl_falls_back_to_the_default(self):
        for value in (-1, "soon", None):
            with override_settings(AGENT_TAINT_TTL_SECONDS=value):
                self.assertEqual(_taint_ttl(), 900, value)

    def test_cache_read_failure_fails_closed(self):
        with patch("orchestration.agent_loop.cache") as broken:
            broken.get.side_effect = RuntimeError("cache down")
            self.assertTrue(_conversation_tainted(71))

    def test_cache_write_failure_is_reported_and_run_stays_tainted(self):
        state = LoopState(messages=[])
        with (
            patch("orchestration.agent_loop.cache") as broken,
            patch("orchestration.agent_loop.record_event") as recorded,
        ):
            broken.set.side_effect = RuntimeError("cache full")
            _taint_run(state, 71)
        self.assertTrue(state.tainted)
        recorded.assert_called_once_with("taint_persist_failed", {"room_id": 71})


class NextTurnStartsTaintedTests(SimpleTestCase):
    """A new message must not reset the gate a tainted run just hit."""

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def _turn(self, responses, room_id, *, user_id=9, preferences=None):
        llm = MagicMock()
        llm.create_message = AsyncMock(side_effect=responses)
        executed = AsyncMock(return_value={"status": "success", "data": {"stdout": "ok"}})

        async def collect():
            return [
                event async for event in run_agent_loop(
                    user_message="go",
                    context={"user_id": user_id, "room_id": room_id, "username": "test"},
                    preferences=preferences or {"shell_profile": "open"},
                )
            ]

        with (
            patch("orchestration.agent_loop.get_llm_client", return_value=llm),
            patch("orchestration.agent_loop._execute_with_timeout", new=executed),
            patch("orchestration.agent_loop.update_memory_state", new=AsyncMock()),
            patch("orchestration.agent_loop._record_receipt", new=AsyncMock()),
            patch("orchestration.agent_loop._resolve_persona", new=AsyncMock(return_value=None)),
            patch("orchestration.agent_loop.save_pending_confirmation", new=AsyncMock()),
            patch("orchestration.agent_loop.preview_tool", new=AsyncMock(return_value=None)),
            patch("orchestration.agent_loop.record_event"),
        ):
            events = async_to_sync(collect)()
        return [event.kind for event in events], executed

    def test_first_command_of_the_next_turn_asks(self):
        kinds, executed = self._turn([_shell("dir"), _DONE], room_id=81)
        self.assertEqual(executed.call_count, 1)
        self.assertNotIn("confirmation", kinds)

        kinds, executed = self._turn([_shell("dir", "t2"), _DONE], room_id=81)
        executed.assert_not_called()
        self.assertIn("confirmation", kinds)

    def test_another_member_of_the_room_starts_tainted_too(self):
        self._turn([_shell("dir"), _DONE], room_id=81, user_id=9)
        kinds, executed = self._turn([_shell("dir", "t2"), _DONE], room_id=81, user_id=10)
        executed.assert_not_called()
        self.assertIn("confirmation", kinds)

    def test_another_room_is_unaffected(self):
        self._turn([_shell("dir"), _DONE], room_id=81)
        kinds, executed = self._turn([_shell("dir", "t2"), _DONE], room_id=82)
        self.assertEqual(executed.call_count, 1)
        self.assertNotIn("confirmation", kinds)

    def test_sandboxed_local_command_still_runs_on_a_tainted_next_turn(self):
        prefs = {"shell_profile": "standard"}
        self._turn([_shell("ls"), _DONE], room_id=81, preferences=prefs)
        self.assertTrue(_conversation_tainted(81))
        kinds, executed = self._turn([_shell("make test", "t2"), _DONE], room_id=81, preferences=prefs)
        self.assertEqual(executed.call_count, 1)
        self.assertNotIn("confirmation", kinds)

    def test_armed_window_still_runs_on_a_tainted_next_turn(self):
        self._turn([_shell("dir"), _DONE], room_id=81)
        prefs = {"shell_profile": "open", "_shell_autopilot": True}
        kinds, executed = self._turn([_shell("dir", "t2"), _DONE], room_id=81, preferences=prefs)
        self.assertEqual(executed.call_count, 1)
        self.assertNotIn("confirmation", kinds)

    def test_stored_auto_rule_is_escalated_on_the_next_turn(self):
        self._turn([_shell("dir"), _DONE], room_id=81)
        prefs = {"shell_profile": "open", "approval_overrides": {"send_email": "auto"}}
        email = _tool_use("send_email", {"to": "a@example.com", "subject": "s", "body": "b"}, "t2")
        kinds, executed = self._turn([email, _DONE], room_id=81, preferences=prefs)
        executed.assert_not_called()
        self.assertIn("confirmation", kinds)
