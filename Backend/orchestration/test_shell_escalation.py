from __future__ import annotations

import asyncio
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from orchestration.agent_loop import (
    LoopState,
    _bucket_tool_calls,
    resume_after_confirmation,
)


def _tc(name: str, tool_input: dict) -> dict:
    return {"id": f"id-{name}", "name": name, "input": tool_input}


async def _collect(agen):
    return [event async for event in agen]


class BucketTests(SimpleTestCase):
    @override_settings(SHELL_EXEC_PROFILE="standard")
    def test_safe_command_is_auto(self):
        auto, pause, denied = _bucket_tool_calls([_tc("run_command", {"command": "ls"})], None)
        self.assertEqual(len(auto), 1)
        self.assertEqual(pause, [])
        self.assertEqual(denied, [])

    @override_settings(SHELL_EXEC_PROFILE="standard")
    def test_bounded_and_destructive_pause_with_tier(self):
        auto, pause, denied = _bucket_tool_calls([
            _tc("run_command", {"command": "ping -c 1 1.1.1.1"}),
            _tc("run_command", {"command": "rm -rf /"}),
        ], None)
        self.assertEqual([tier for _tc_, tier in pause], ["bounded", "destructive"])
        self.assertEqual(auto, [])
        self.assertEqual(denied, [])

    @override_settings(SHELL_EXEC_PROFILE="standard")
    def test_denied_command_is_refused_not_paused(self):
        auto, pause, denied = _bucket_tool_calls([_tc("run_command", {"command": "sudo ls"})], None)
        self.assertEqual(len(denied), 1)
        self.assertEqual(pause, [])
        self.assertEqual(auto, [])

    def test_non_shell_high_risk_uses_durable_pause(self):
        auto, pause, denied = _bucket_tool_calls([_tc("send_email", {"to": "a@b.c"})], None)
        self.assertEqual(len(pause), 1)
        self.assertIsNone(pause[0][1])  # no shell tier -> durable path


class BoundedResumeTests(SimpleTestCase):
    def _state(self, tier: str) -> LoopState:
        return LoopState(
            messages=[],
            pending_tool={"id": "t1", "name": "run_command", "input": {"command": "ls"}},
            pending_tier=tier,
        )

    def test_bounded_pause_resumes_without_durable_record(self):
        state = self._state("bounded")

        async def fake_loop(*args, **kwargs):
            if False:
                yield None

        with patch("orchestration.agent_loop._pending_approval_record", return_value=None), \
                patch("orchestration.agent_loop.load_loop_state", return_value=state), \
                patch("orchestration.agent_loop.clear_loop_state") as cleared, \
                patch("orchestration.agent_loop.run_agent_loop", side_effect=fake_loop) as run:
            events = asyncio.run(_collect(resume_after_confirmation(
                context={"room_id": 1, "user_id": 2})))
        self.assertEqual(events, [])
        self.assertTrue(cleared.called)
        self.assertTrue(run.called)

    def test_missing_record_and_not_bounded_errors(self):
        state = self._state("destructive")
        with patch("orchestration.agent_loop._pending_approval_record", return_value=None), \
                patch("orchestration.agent_loop.load_loop_state", return_value=state), \
                patch("orchestration.agent_loop.clear_loop_state"), \
                patch("orchestration.agent_loop.run_agent_loop") as run:
            events = asyncio.run(_collect(resume_after_confirmation(
                context={"room_id": 1, "user_id": 2})))
        self.assertTrue(events)
        self.assertEqual(events[0].kind, "error")
        self.assertFalse(run.called)
