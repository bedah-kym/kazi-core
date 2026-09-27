from __future__ import annotations

import unittest

from orchestration.agent_loop import (
    HARD_CAP_TOOL_CALLS,
    MAX_TOOL_CALLS,
    SHELL_SESSION_MAX_TOOL_CALLS,
    LoopState,
    _session_tool_call_cap,
)


def _state(names):
    return LoopState(messages=[], tool_call_log=[{"name": name} for name in names])


class SessionToolCapTests(unittest.TestCase):
    def test_caps_off_uses_the_hard_backstop(self):
        self.assertEqual(
            _session_tool_call_cap(_state(["run_command"]), caps_enforced=False),
            HARD_CAP_TOOL_CALLS,
        )

    def test_no_shell_uses_the_default_cap(self):
        self.assertEqual(
            _session_tool_call_cap(_state(["get_weather"]), caps_enforced=True),
            MAX_TOOL_CALLS,
        )

    def test_shell_session_uses_the_raised_cap(self):
        self.assertEqual(
            _session_tool_call_cap(_state(["run_command"]), caps_enforced=True),
            SHELL_SESSION_MAX_TOOL_CALLS,
        )

    def test_alias_counts_as_shell(self):
        self.assertEqual(
            _session_tool_call_cap(_state(["run_shell"]), caps_enforced=True),
            SHELL_SESSION_MAX_TOOL_CALLS,
        )
