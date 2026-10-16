"""T-F3: the shell no longer raises a tool-call cap.

On ``main`` a session that touched the shell got a higher tool-call cap than
one that did not (D9). The single turn budget replaced that; these tests pin
the removal.
"""
from __future__ import annotations

from django.test import SimpleTestCase, override_settings

from orchestration.agent_loop import _turn_token_budget


class ShellCapRemovedTests(SimpleTestCase):
    def test_shell_session_cap_helper_is_gone(self):
        with self.assertRaises(ImportError):
            from orchestration.agent_loop import _session_tool_call_cap  # noqa: F401

    def test_shell_session_cap_constant_is_gone(self):
        with self.assertRaises(ImportError):
            from orchestration.agent_loop import SHELL_SESSION_MAX_TOOL_CALLS  # noqa: F401


class TurnBudgetMappingTests(SimpleTestCase):
    @override_settings(AGENT_TURN_TOKEN_BUDGET=30000, AGENT_TURN_TOKEN_BUDGET_MAX=700000)
    def test_one_bound_for_every_session(self):
        # Caps on: the configured bound. Caps off (a legacy install): the largest
        # bound. Neither depends on the tool the session used.
        self.assertEqual(_turn_token_budget(True), 30000)
        self.assertEqual(_turn_token_budget(False), 700000)
