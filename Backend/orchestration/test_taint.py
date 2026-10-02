"""Tests for the untrusted-context taint flag (v0.7, issue #170)."""
from __future__ import annotations

from django.test import SimpleTestCase

from orchestration.agent_loop import (
    LoopState,
    _bucket_tool_calls,
    _untrusted_source,
    load_loop_state,
    save_loop_state,
)


class TaintFlagUnitTests(SimpleTestCase):
    def test_untrusted_sources_are_recognized(self):
        self.assertTrue(_untrusted_source("run_command"))
        self.assertTrue(_untrusted_source("web_search"))
        self.assertFalse(_untrusted_source("get_weather"))

    def test_tainted_run_escalates_stored_auto_override(self):
        preferences = {"approval_overrides": {"send_email": "auto"}}
        tc = {"id": "1", "name": "send_email", "input": {"to": "a@b.c"}}

        auto, pause, denied = _bucket_tool_calls([tc], preferences)
        self.assertEqual(len(auto), 1)
        self.assertEqual(pause, [])

        auto, pause, denied = _bucket_tool_calls([tc], preferences, tainted=True)
        self.assertEqual(auto, [])
        self.assertEqual(len(pause), 1)
        self.assertEqual(denied, [])

    def test_tainted_run_does_not_escalate_low_risk_calls(self):
        tc = {"id": "1", "name": "get_weather", "input": {"city": "Nairobi"}}
        auto, pause, denied = _bucket_tool_calls([tc], None, tainted=True)
        self.assertEqual(len(auto), 1)
        self.assertEqual(pause, [])
        self.assertEqual(denied, [])

    def test_loop_state_roundtrip_preserves_taint(self):
        state = LoopState(messages=[], tainted=True)
        save_loop_state(41, 42, state)
        loaded = load_loop_state(41, 42)

        self.assertIsNotNone(loaded)
        self.assertTrue(loaded.tainted)
