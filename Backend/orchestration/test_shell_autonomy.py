"""Shell auto mode: the prompt follows the sandbox boundary, not the command.

See ``docs/plans/2026-10-shell-auto-mode.md``.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

from chatbot.models import Chatroom, Member
from orchestration.agent_loop import _bucket_tool_calls, _execute_scoped_tool_calls, _taint_preferences
from orchestration.models import ActionReceipt
from orchestration.shell import autopilot
from orchestration.shell.profiles import shell_profile_pref_key
from orchestration.tool_executor import get_tool_risk_info


def _shell(command, **extra):
    return {"id": "1", "name": "run_command", "input": {"command": command, **extra}}


def _bucket(command, profile, *, tainted=False, armed=False, **extra):
    prefs = {"shell_profile": profile, "_shell_autopilot": armed}
    auto, pause, denied = _bucket_tool_calls([_shell(command, **extra)], prefs, tainted=tainted)
    return "auto" if auto else "ask" if pause else "refuse"


class SandboxedProfileTests(SimpleTestCase):
    """`standard`: a command with no way out runs; only egress asks."""

    def test_local_commands_auto_run_even_when_tainted(self):
        for command in ("ls -la", "make test", "cat README.md", "python build.py"):
            self.assertEqual(_bucket(command, "standard"), "auto", command)
            self.assertEqual(_bucket(command, "standard", tainted=True), "auto", command)

    def test_network_command_asks_tainted_or_not(self):
        for command in ("pip install requests", "curl http://unlisted.test", "git clone http://unlisted.test/r"):
            self.assertEqual(_bucket(command, "standard"), "ask", command)
            self.assertEqual(_bucket(command, "standard", tainted=True), "ask", command)

    def test_requested_bridge_asks(self):
        self.assertEqual(_bucket("python fetch.py", "standard", network="bridge"), "ask")
        self.assertEqual(_bucket("python fetch.py", "standard", tainted=True, network="bridge"), "ask")

    def test_tainted_run_asks_even_for_an_allowlisted_host(self):
        with self.settings(SHELL_EXEC_NETWORK_ALLOWLIST=["listed.test"]):
            self.assertEqual(_bucket("curl http://listed.test", "standard"), "auto")
            self.assertEqual(_bucket("curl http://listed.test", "standard", tainted=True), "ask")

    def test_destructive_asks_and_root_is_refused(self):
        self.assertEqual(_bucket("rm -rf build", "standard"), "ask")
        self.assertEqual(_bucket("sudo ls", "standard"), "refuse")

    def test_armed_window_changes_nothing_when_sandboxed(self):
        self.assertEqual(_bucket("pip install requests", "standard", armed=True), "ask")
        self.assertEqual(_bucket("curl http://unlisted.test", "standard", tainted=True, armed=True), "ask")
        self.assertEqual(_bucket("rm -rf build", "standard", armed=True), "ask")

    def test_locked_profile_still_refuses_network(self):
        self.assertEqual(_bucket("curl http://unlisted.test", "locked"), "refuse")
        self.assertEqual(_bucket("ls", "locked", tainted=True), "auto")


class OpenProfileTests(SimpleTestCase):
    """`open` has no boundary: a tainted run asks unless a human armed the window."""

    def test_tainted_run_asks_for_every_command(self):
        for command in ("ls -la", "make test", "pip install requests"):
            self.assertEqual(_bucket(command, "open"), "auto", command)
            self.assertEqual(_bucket(command, "open", tainted=True), "ask", command)

    def test_root_asks_without_the_window(self):
        self.assertEqual(_bucket("sudo ls", "open"), "ask")

    def test_armed_window_lifts_prompts(self):
        self.assertEqual(_bucket("ls -la", "open", tainted=True, armed=True), "auto")
        self.assertEqual(_bucket("sudo ls", "open", armed=True), "auto")

    def test_armed_window_never_runs_destructive(self):
        self.assertEqual(_bucket("rm -rf /", "open", armed=True), "ask")
        self.assertEqual(_bucket("rm -rf /", "open", tainted=True, armed=True), "ask")


class RiskInfoTests(SimpleTestCase):
    def test_reports_egress_and_basis(self):
        local = get_tool_risk_info("run_command", {"shell_profile": "standard"}, {"command": "ls"})
        self.assertFalse(local["egress"])
        self.assertEqual(local["approval_basis"], "sandbox")

        network = get_tool_risk_info("run_command", {"shell_profile": "standard"}, {"command": "curl http://x.test"})
        self.assertTrue(network["egress"])
        self.assertEqual(network["approval_basis"], "")

        armed = get_tool_risk_info(
            "run_command", {"shell_profile": "open", "_shell_autopilot": True}, {"command": "sudo ls"},
        )
        self.assertEqual(armed["approval_basis"], "armed_window")

    def test_unknown_profile_is_treated_as_sandboxed(self):
        info = get_tool_risk_info(
            "run_command", {"shell_profile": "nonsense", "_shell_autopilot": True}, {"command": "sudo ls"},
        )
        self.assertTrue(info["requires_confirmation"])

    def test_mined_override_still_cannot_auto_run_shell(self):
        info = get_tool_risk_info(
            "run_command",
            {"shell_profile": "standard", "approval_overrides": {"run_command": "auto"}},
            {"command": "curl http://unlisted.test"},
        )
        self.assertTrue(info["requires_confirmation"])


class NonShellTaintRegressionTests(SimpleTestCase):
    """The #170 escalation is unchanged for everything that is not the shell."""

    def test_tainted_run_still_escalates_send_email(self):
        prefs = {"approval_overrides": {"send_email": "auto"}}
        tc = {"id": "1", "name": "send_email", "input": {"to": "a@example.com"}}
        auto, pause, _ = _bucket_tool_calls([tc], prefs, tainted=True)
        self.assertEqual(auto, [])
        self.assertEqual(len(pause), 1)

    def test_taint_survives_missing_preferences(self):
        with self.settings(SHELL_EXEC_PROFILE="standard"):
            auto, pause, _ = _bucket_tool_calls([_shell("curl http://unlisted.test")], None, tainted=True)
        self.assertEqual(auto, [])
        self.assertEqual(len(pause), 1)


class TaintFlagOwnershipTests(SimpleTestCase):
    """Only the loop decides taint; a flag arriving in preferences is not trusted."""

    def test_stray_flag_is_stripped_on_an_untainted_run(self):
        prefs = {"shell_profile": "open", "_shell_tainted": True}
        self.assertNotIn("_shell_tainted", _taint_preferences(prefs, False))
        self.assertIn("_shell_tainted", prefs)

    def test_flag_is_set_even_without_preferences(self):
        self.assertEqual(_taint_preferences(None, True), {"_shell_tainted": True})
        self.assertIsNone(_taint_preferences(None, False))

    def test_autopilot_flag_is_ignored_on_sandboxed_profiles(self):
        for profile in ("standard", "locked", "nonsense"):
            info = get_tool_risk_info(
                "run_command", {"shell_profile": profile, "_shell_autopilot": True}, {"command": "sudo ls"},
            )
            self.assertTrue(info["requires_confirmation"], profile)


class SubAgentTaintTests(SimpleTestCase):
    """Delegation is not a way around the tainted-egress gate."""

    def _run(self, calls, prefs, log=None):
        with patch(
            "orchestration.agent_loop._execute_with_timeout",
            new=AsyncMock(return_value={"status": "success"}),
        ) as executed, patch("orchestration.agent_loop.update_memory_state", new=AsyncMock()):
            blocks, _ = async_to_sync(_execute_scoped_tool_calls)(calls, {}, prefs, log if log is not None else [])
        return blocks, executed

    def test_parent_taint_blocks_open_profile_shell(self):
        prefs = {"shell_profile": "open", "_shell_tainted": True}
        blocks, executed = self._run([_shell("ls")], prefs)
        executed.assert_not_called()
        self.assertIn("requires explicit user confirmation", blocks[0]["content"])

    def test_sub_agent_taints_itself(self):
        prefs = {"shell_profile": "open"}
        first = _shell("ls")
        second = {"id": "2", "name": "run_command", "input": {"command": "cat notes.txt"}}
        blocks, executed = self._run([first, second], prefs)
        self.assertEqual(executed.call_count, 1)
        self.assertIn("requires explicit user confirmation", blocks[1]["content"])

    def test_tainted_sub_agent_cannot_reach_even_an_allowlisted_host(self):
        prefs = {"shell_profile": "standard", "_shell_tainted": True}
        with self.settings(SHELL_EXEC_NETWORK_ALLOWLIST=["listed.test"]):
            _, executed = self._run([_shell("curl http://listed.test")], prefs)
        executed.assert_not_called()

    def test_sandboxed_local_commands_still_run_when_tainted(self):
        prefs = {"shell_profile": "standard", "_shell_tainted": True}
        _, executed = self._run([_shell("ls"), _shell("make test")], prefs)
        self.assertEqual(executed.call_count, 2)


class AutopilotWindowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="autopilotuser")
        self.member = Member.objects.create(User=self.user)
        self.room = Chatroom.objects.create()
        self.room.participants.add(self.member)
        self.other_room = Chatroom.objects.create()
        cache.set(shell_profile_pref_key(self.room.id), "open")
        cache.set(shell_profile_pref_key(self.other_room.id), "open")

    def tearDown(self):
        cache.clear()

    def test_arm_then_disarm_writes_receipts(self):
        self.assertTrue(autopilot.arm(self.user.id, self.room.id, minutes=5, armed_by=self.user.id))
        self.assertTrue(autopilot.is_armed(self.user.id, self.room.id))
        self.assertTrue(autopilot.disarm(self.user.id, self.room.id, reason="test"))
        self.assertFalse(autopilot.is_armed(self.user.id, self.room.id))
        actions = list(ActionReceipt.objects.filter(user=self.user).order_by("id").values_list("action", flat=True))
        self.assertEqual(actions, [autopilot.ARM_ACTION, autopilot.DISARM_ACTION])

    def test_cannot_arm_a_sandboxed_room(self):
        cache.set(shell_profile_pref_key(self.room.id), "standard")
        self.assertFalse(autopilot.arm(self.user.id, self.room.id, minutes=5))
        self.assertFalse(autopilot.is_armed(self.user.id, self.room.id))

    def test_cannot_arm_a_room_you_are_not_in(self):
        self.assertFalse(autopilot.arm(self.user.id, self.other_room.id, minutes=5))
        self.assertFalse(autopilot.arm(self.user.id, 999999, minutes=5))

    def test_window_is_capped(self):
        with self.settings(SHELL_AUTOPILOT_MAX_MINUTES=60):
            self.assertTrue(autopilot.arm(self.user.id, self.room.id, minutes=10 ** 9))
        receipt = ActionReceipt.objects.get(user=self.user, action=autopilot.ARM_ACTION)
        self.assertEqual(receipt.params["minutes"], 60)

    def test_no_receipt_means_not_armed(self):
        with patch("orchestration.shell.autopilot._receipt", return_value=False):
            self.assertFalse(autopilot.arm(self.user.id, self.room.id, minutes=5))
        self.assertFalse(autopilot.is_armed(self.user.id, self.room.id))

    def test_expired_window_is_not_armed(self):
        autopilot.arm(self.user.id, self.room.id, minutes=5)
        profile = get_user_model().objects.get(pk=self.user.id).profile
        prefs = dict(profile.notification_preferences)
        prefs[autopilot.AUTOPILOT_KEY][str(self.room.id)]["expires_at"] = "2000-01-01T00:00:00+00:00"
        profile.notification_preferences = prefs
        profile.save(update_fields=["notification_preferences"])
        self.assertFalse(autopilot.is_armed(self.user.id, self.room.id))
