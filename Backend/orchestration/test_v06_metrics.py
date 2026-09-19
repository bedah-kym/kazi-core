"""v0.6 success metrics as executable tests (issue #128).

The v0.6 brief's §8 success metrics are pinned here as assertions that
were written BEFORE the features exist, so the features are forced to
meet the metric. The tests follow the current shell-first direction
(`docs/v0.6-brief.md` §2–§3): the shell (`run_command`) is the reach
story, typed sysops connectors are retired (#129), and modality is a
connector choice (`generate_speech` + channel flags), not a pipeline
stage. Issue #128's original "typed ping_host/dns_lookup connector"
framing predates the shell-first rewrite and is deliberately NOT
pinned here.

Tests marked `expectedFailure` must stay red until the pinned feature
actually lands; when one starts failing with "unexpected success" the
decorator should be removed in the same PR that ships the feature.

All tests are hermetic: no network, no real LLM, no database.
"""
import asyncio

from django.test import SimpleTestCase

from unittest import expectedFailure
from unittest.mock import AsyncMock, MagicMock, patch


class V06SuccessMetricTests(SimpleTestCase):
    """Executable expressions of the v0.6 brief §8 success metrics."""

    def _advertised_actions(self):
        from workflows.capabilities import SYSTEM_CAPABILITIES

        return {
            entry.get("name")
            for integration in SYSTEM_CAPABILITIES["integrations"]
            for entry in (integration.get("actions") or [])
        }

    @expectedFailure
    def test_ping_isp_answered_by_sandboxed_allowlisted_shell_no_prompt_on_safe_tier(self):
        """Metric: "ping my ISP" / "why is the DNS failing" answers by running
        sandboxed, allowlisted `run_command` commands — no prompt on the safe
        tier (brief §8.1, §4.1, §4.3).

        Red until Phase 1 lands (#130, #131): `run_command` registered and
        advertised, and the dynamic risk gate (which gains `tool_input`) tiers
        a safe command as "safe" with no blanket high-risk/confirmation flag.
        """
        from orchestration.action_catalog import get_supported_actions
        from orchestration.tool_executor import get_tool_risk_info

        self.assertIn(
            "run_command",
            set(get_supported_actions(include_aliases=False)),
            "run_command is not registered; the shell has no reach",
        )
        self.assertIn(
            "run_command",
            self._advertised_actions(),
            "run_command is not advertised to the planner",
        )

        safe_ping = get_tool_risk_info(
            "run_command", {}, {"command": "ping 8.8.8.8"}
        )
        self.assertEqual(
            safe_ping.get("tier"),
            "safe",
            "safe-tier commands must not require a prompt",
        )
        self.assertFalse(
            get_tool_risk_info("run_command", {}).get("is_high_risk"),
            "run_command must not be blanket high-risk; risk is per-command",
        )

    @expectedFailure
    def test_high_risk_shell_action_pauses_for_human_with_diff_and_receipt(self):
        """Metric: a high-risk action (restart a service, destructive change)
        pauses for a human with a diff, and produces a receipt (brief §8.2).

        Red until the dynamic gate and receipt coverage land (#131, #133):
        destructive commands tier as destructive/denied, and `run_command`
        is audited with a receipt. The diff mechanism lands with the
        snapshot/rollback workspace (#135).
        """
        from orchestration.action_receipts import _AUDITED_ACTIONS
        from orchestration.tool_executor import get_tool_risk_info

        self.assertIn(
            "run_command",
            _AUDITED_ACTIONS,
            "run_command must produce a receipt",
        )
        destructive = get_tool_risk_info(
            "run_command", {}, {"command": "rm -rf /var/www"}
        ).get("tier")
        self.assertIn(
            destructive,
            ("destructive", "denied"),
            "destructive commands must gate behind a human approval",
        )

    @expectedFailure
    def test_lullaby_resolves_to_voice_note_on_voice_capable_channel(self):
        """Metric: "sing a lullaby" resolves to a voice note on a voice-capable
        channel without the user asking for voice (brief §8.3, §4.2).

        Red until Phase 3 lands (#136): the `generate_speech` TTS connector
        registered and advertised, with per-channel capability flags
        (`supports_voice`, `max_audio_bytes`) on the notifications module —
        a connector + hint + flags, NOT a modality-selection pipeline stage.
        """
        from orchestration.action_catalog import get_supported_actions

        self.assertIn(
            "generate_speech",
            set(get_supported_actions(include_aliases=False)),
            "generate_speech TTS connector is not registered",
        )
        self.assertIn(
            "generate_speech",
            self._advertised_actions(),
            "generate_speech is not advertised to the planner",
        )

    def test_uncovered_request_does_not_dead_end(self):
        """Metric: an uncovered open-ended request must not dead-end — it
        either reaches the shell tail (run_command, once Phase 1 lands) or
        asks for help (brief §4.4: "the shell covers the open-ended tail").

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
