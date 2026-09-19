"""v0.6 success metrics as executable tests (issue #128).

The v0.6 brief's §8 success metrics are pinned here as assertions that
were written BEFORE the features exist, so the features are forced to
meet the metric. Two of the three are marked `expectedFailure`: they
must stay red until the pinned feature actually lands, and when one
starts failing with "unexpected success" the decorator should be
removed in the same PR that ships the feature.

All tests are hermetic: no network, no real LLM, no database.
"""
import asyncio

from django.test import SimpleTestCase

from unittest import expectedFailure
from unittest.mock import AsyncMock, MagicMock, patch


class V06SuccessMetricTests(SimpleTestCase):
    """Executable expressions of the v0.6 brief §8 success metrics."""

    @expectedFailure
    def test_ping_request_resolves_to_typed_connector_not_shell(self):
        """Metric: "ping my ISP" answers via a TYPED network-diagnostics
        connector (`ping_host` / `dns_lookup`) — the shell (`run_command`)
        and delegate paths must not be the resolution for typed asks.

        Fails red until a typed diagnostics connector registers one of
        ping_host/dns_lookup in the action catalog.
        """
        from orchestration.action_catalog import get_supported_actions

        typed_actions = set(get_supported_actions(include_aliases=False))
        self.assertTrue(
            typed_actions & {"ping_host", "dns_lookup"},
            '"ping my ISP" has no typed resolution path: '
            "register a ping_host/dns_lookup connector action",
        )
        self.assertNotIn(
            "run_command",
            typed_actions,
            "run_command is a typed catalog action; the shell must not "
            "become the typed path for diagnostics requests",
        )

    @expectedFailure
    def test_voice_request_resolves_to_registered_voice_tool(self):
        """Metric: "sing a lullaby" resolves to a voice note on a
        voice-capable channel without the user asking for voice (#136).

        Fails red until a voice/TTS tool is registered AND advertised to
        the planner.
        """
        from orchestration.action_catalog import get_supported_actions
        from workflows.capabilities import SYSTEM_CAPABILITIES

        typed_actions = set(get_supported_actions(include_aliases=False))
        voice_actions = {
            action for action in typed_actions
            if "voice" in action or "tts" in action
        }
        self.assertTrue(voice_actions, "no voice/TTS tool registered")

        advertised = {
            integration.get("service")
            for integration in SYSTEM_CAPABILITIES["integrations"]
            if "voice" in str(integration.get("service") or "")
            or "tts" in str(integration.get("service") or "")
        }
        self.assertTrue(advertised, "voice tool not advertised to the planner")

    def test_uncovered_request_does_not_dead_end(self):
        """Metric: an open-ended request the planner cannot act on must not
        dead-end — it either delegates or asks for help.

        Currently satisfied: the planner falls back to `single` (which the
        agent loop routes to general chat) and the agent loop advertises
        `delegate_task`. This test locks that behavior against regressions.
        """
        from orchestration.agent_loop import _META_TOOL_NAMES
        from orchestration.intent_parser import IntentParser
        from orchestration.workflow_planner import plan_user_request

        message = "help me figure out why my laptop battery drains so fast"

        empty_plan_llm = MagicMock()
        empty_plan_llm.generate_json = AsyncMock(
            return_value={"mode": "single", "steps": None, "confidence": 0.1}
        )
        crashing_llm = MagicMock()
        crashing_llm.generate_json = AsyncMock(side_effect=RuntimeError("llm down"))

        for mock_llm in (empty_plan_llm, crashing_llm):
            with patch("orchestration.workflow_planner.get_llm_client", return_value=mock_llm):
                plan = asyncio.run(plan_user_request(message, history_text=""))
            self.assertIn(
                plan.get("mode"),
                ("single", "needs_clarification"),
                "uncovered request dead-ended with mode %r" % plan.get("mode"),
            )
            if plan.get("mode") == "needs_clarification":
                self.assertTrue(plan.get("assistant_message"))

        self.assertIn("delegate_task", _META_TOOL_NAMES)
        self.assertIn("general_chat", IntentParser.SUPPORTED_ACTIONS)
