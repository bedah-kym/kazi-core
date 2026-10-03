from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from orchestration.shell import autopilot, grants
from orchestration.tool_executor import get_tool_risk_info


class FingerprintTests(SimpleTestCase):
    @override_settings(SHELL_EXEC_PROFILE="standard")
    def test_plain_command_is_grantable(self):
        self.assertEqual(grants.fingerprint("Dir /b tools", profile="standard"), "dir /b tools")

    def test_metacharacter_command_is_not_grantable(self):
        self.assertIsNone(grants.fingerprint("dir tools && rm -rf /"))

    def test_opaque_interpreter_blob_is_not_grantable(self):
        self.assertIsNone(grants.fingerprint('python -c "print(1)"'))
        self.assertIsNone(grants.fingerprint("cmd /c dir"))

    @override_settings(SHELL_EXEC_PROFILE="standard")
    def test_destructive_command_is_not_grantable(self):
        self.assertIsNone(grants.fingerprint("rm -rf /", profile="standard"))


class RiskRuleTests(SimpleTestCase):
    @override_settings(SHELL_EXEC_PROFILE="standard")
    def test_bounded_asks_without_autonomy(self):
        info = get_tool_risk_info("run_command", None, {"command": "ping 1.1.1.1"})
        self.assertTrue(info["requires_confirmation"])
        self.assertEqual(info["shell_tier"], "bounded")

    @override_settings(SHELL_EXEC_PROFILE="standard")
    def test_safe_command_auto_runs(self):
        info = get_tool_risk_info("run_command", None, {"command": "dir /b"})
        self.assertFalse(info["requires_confirmation"])

    @override_settings(SHELL_EXEC_PROFILE="standard")
    def test_autopilot_makes_bounded_auto(self):
        info = get_tool_risk_info(
            "run_command", {"_shell_autopilot": True}, {"command": "ping 1.1.1.1"},
        )
        self.assertFalse(info["requires_confirmation"])

    @override_settings(SHELL_EXEC_PROFILE="standard")
    def test_grant_makes_bounded_auto(self):
        info = get_tool_risk_info(
            "run_command",
            {"_shell_grants": {"ping 1.1.1.1": "always_allow"}},
            {"command": "ping 1.1.1.1"},
        )
        self.assertFalse(info["requires_confirmation"])

    @override_settings(SHELL_EXEC_PROFILE="standard")
    def test_autopilot_never_runs_destructive(self):
        info = get_tool_risk_info(
            "run_command", {"_shell_autopilot": True}, {"command": "rm -rf /"},
        )
        self.assertTrue(info["requires_confirmation"])
        self.assertEqual(info["shell_tier"], "destructive")

    @override_settings(SHELL_EXEC_PROFILE="open")
    def test_tainted_egress_still_asks_under_autopilot(self):
        info = get_tool_risk_info(
            "run_command",
            {"_shell_autopilot": True, "_shell_tainted": True},
            {"command": "curl http://example.test"},
        )
        self.assertTrue(info["requires_confirmation"])


class GrantStoreTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="shelluser")

    def test_create_and_read_roundtrip(self):
        fp = grants.create_grant(self.user.id, 7, "dir /b", profile="standard")
        self.assertEqual(fp, "dir /b")
        self.assertIn(fp, grants.active_grants(self.user.id, 7))

    def test_revoke_removes_grant(self):
        grants.create_grant(self.user.id, 7, "dir /b", profile="standard")
        self.assertTrue(grants.revoke_grant(self.user.id, 7, "DIR /b"))
        self.assertEqual(grants.active_grants(self.user.id, 7), {})

    def test_room_scoped(self):
        grants.create_grant(self.user.id, 7, "dir /b", profile="standard")
        self.assertEqual(grants.active_grants(self.user.id, 8), {})

    def test_expired_grant_is_inactive(self):
        grants.create_grant(self.user.id, 7, "dir /b", days=1, profile="standard")
        profile = get_user_model().objects.get(pk=self.user.id).profile
        prefs = dict(profile.notification_preferences)
        prefs[grants.GRANTS_KEY]["7"]["dir /b"]["expires_at"] = "2000-01-01T00:00:00+00:00"
        profile.notification_preferences = prefs
        profile.save(update_fields=["notification_preferences"])
        self.assertEqual(grants.active_grants(self.user.id, 7), {})

    def test_non_grantable_command_creates_nothing(self):
        self.assertIsNone(grants.create_grant(self.user.id, 7, "rm -rf /", profile="standard"))
        self.assertEqual(grants.active_grants(self.user.id, 7), {})


class AutopilotTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="autopilotuser")

    def test_arm_then_disarm(self):
        self.assertFalse(autopilot.is_armed(self.user.id, 3))
        self.assertTrue(autopilot.arm(self.user.id, 3, minutes=5))
        self.assertTrue(autopilot.is_armed(self.user.id, 3))
        self.assertTrue(autopilot.disarm(self.user.id, 3))
        self.assertFalse(autopilot.is_armed(self.user.id, 3))

    def test_expired_window_is_not_armed(self):
        autopilot.arm(self.user.id, 3, minutes=5)
        profile = get_user_model().objects.get(pk=self.user.id).profile
        prefs = dict(profile.notification_preferences)
        prefs[autopilot.AUTOPILOT_KEY]["3"]["expires_at"] = "2000-01-01T00:00:00+00:00"
        profile.notification_preferences = prefs
        profile.save(update_fields=["notification_preferences"])
        self.assertFalse(autopilot.is_armed(self.user.id, 3))

    def test_room_scoped(self):
        autopilot.arm(self.user.id, 3, minutes=5)
        self.assertFalse(autopilot.is_armed(self.user.id, 4))
