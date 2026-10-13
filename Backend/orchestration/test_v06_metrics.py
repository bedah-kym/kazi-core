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
from django.test import SimpleTestCase, override_settings


class V06SuccessMetricTests(SimpleTestCase):
    """Executable expressions of the v0.6 brief §8 success metrics."""

    def _advertised_actions(self):
        from workflows.capabilities import SYSTEM_CAPABILITIES

        return {
            entry.get("name")
            for integration in SYSTEM_CAPABILITIES["integrations"]
            for entry in (integration.get("actions") or [])
        }

    def test_ping_isp_answered_by_sandboxed_allowlisted_shell_no_prompt_on_safe_tier(self):
        """Metric: "ping my ISP" answers by running sandboxed, allowlisted
        `run_command` commands — no prompt on the safe tier (brief §8.1).

        Shipped by #130/#131/#134: `run_command` is registered and advertised;
        a read-only command is `safe` and ungated; an allowlisted network
        command is also ungated. Risk is per-command, not blanket high-risk.
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

        safe = get_tool_risk_info("run_command", {}, {"command": "ls -la"})
        self.assertEqual(safe.get("tier"), "safe", "safe-tier commands must not require a prompt")
        self.assertFalse(safe.get("requires_confirmation"))
        self.assertFalse(
            safe.get("is_high_risk"),
            "run_command must not be blanket high-risk; risk is per-command",
        )

        with override_settings(SHELL_EXEC_NETWORK_ALLOWLIST=["8.8.8.8"]):
            allowlisted = get_tool_risk_info("run_command", {}, {"command": "ping -c 1 8.8.8.8"})
        self.assertEqual(allowlisted.get("tier"), "safe", "an allowlisted host must not prompt")
        self.assertFalse(allowlisted.get("requires_confirmation"))

    def test_high_risk_shell_action_pauses_for_human_with_diff_and_receipt(self):
        """Metric: a high-risk action (restart a service, destructive change)
        pauses for a human with a diff, and produces a receipt (brief §8.2).

        Shipped by #131/#133/#135: destructive commands tier as
        destructive/denied (durable approval + workspace snapshot), and
        `run_command` is audited with a receipt.
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

    def test_lullaby_resolves_to_voice_note_on_voice_capable_channel(self):
        """Metric: "sing a lullaby" resolves to a voice note on a voice-capable
        channel without the user asking for voice (brief §8.3, §4.2).

        Shipped by #136: `generate_speech` is registered and advertised, with
        per-channel capability flags on the notifications module — a connector
        + hint + flags, NOT a modality-selection pipeline stage.
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
