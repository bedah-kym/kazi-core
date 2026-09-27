from __future__ import annotations

import unittest
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from orchestration.shell import profiles
from orchestration.shell.classifier import classify_command
from orchestration.shell.profiles import (
    DEFAULT_PROFILE,
    PROFILES,
    get_profile,
    resolve_profile,
    shell_profile_pref_key,
    warn_if_open_profile,
)


class ProfileRegistryTests(unittest.TestCase):
    def test_three_profiles_exist(self):
        self.assertEqual(set(PROFILES), {"open", "standard", "locked"})

    def test_standard_is_default(self):
        self.assertEqual(DEFAULT_PROFILE, "standard")
        self.assertEqual(profiles.default_profile_name(), "standard")

    def test_standard_is_docker_sandboxed(self):
        profile = PROFILES["standard"]
        self.assertEqual(profile.backend, "docker")
        self.assertTrue(profile.rootfs_readonly)
        self.assertEqual(profile.writable, "workspace")
        self.assertEqual(profile.network, "none")
        self.assertEqual(profile.user, "65534:65534")

    def test_open_is_host_local(self):
        profile = PROFILES["open"]
        self.assertEqual(profile.backend, "local")
        self.assertFalse(profile.rootfs_readonly)
        self.assertEqual(profile.network, "full")
        self.assertTrue(profile.allow_root)

    def test_locked_has_no_writable_workspace(self):
        profile = PROFILES["locked"]
        self.assertEqual(profile.writable, "none")
        self.assertEqual(profile.network, "none")

    def test_unknown_profile_falls_back_to_standard(self):
        self.assertEqual(get_profile("bogus").name, "standard")
        self.assertEqual(get_profile(None).name, "standard")


class ResolutionTests(SimpleTestCase):
    @override_settings(SHELL_EXEC_PROFILE="standard")
    def test_default_when_no_override(self):
        self.assertEqual(resolve_profile(room_id=None).name, "standard")

    @override_settings(SHELL_EXEC_PROFILE="locked")
    def test_global_default_is_used(self):
        self.assertEqual(resolve_profile(room_id=None).name, "locked")

    @override_settings(SHELL_EXEC_PROFILE="standard")
    def test_preferences_override_wins(self):
        profile = resolve_profile(room_id=1, preferences={"shell_profile": "locked"})
        self.assertEqual(profile.name, "locked")

    @override_settings(SHELL_EXEC_PROFILE="standard")
    def test_room_override_from_cache(self):
        with patch("orchestration.shell.profiles.room_profile_override", return_value="locked"):
            self.assertEqual(resolve_profile(room_id=7).name, "locked")

    @override_settings(SHELL_EXEC_PROFILE="standard")
    def test_cache_error_falls_back_to_default(self):
        with patch("django.core.cache.cache.get", side_effect=RuntimeError("boom")):
            self.assertEqual(resolve_profile(room_id=7).name, "standard")

    def test_pref_key_contains_room(self):
        self.assertIn("7", shell_profile_pref_key(7))


class EscalationConsistencyTests(unittest.TestCase):
    CASES = [
        ("sudo ls", "root"),
        ("curl https://example.com", "network"),
        ("rm -rf /", "destructive"),
    ]

    def test_escalation_matches_classifier(self):
        for profile_name, profile in PROFILES.items():
            for command, category in self.CASES:
                expected = profile.escalation[category]
                actual = classify_command(command, profile=profile_name)["tier"]
                self.assertEqual(actual, expected, f"{profile_name}/{command}")


class OpenWarningTests(SimpleTestCase):
    @override_settings(SHELL_EXEC_PROFILE="open")
    def test_warns_on_open_default(self):
        with self.assertLogs("orchestration.shell.profiles", level="WARNING") as captured:
            fired = warn_if_open_profile()
        self.assertTrue(fired)
        self.assertTrue(any("UNSANDBOXED" in line for line in captured.output))

    @override_settings(SHELL_EXEC_PROFILE="standard")
    def test_no_warning_on_standard(self):
        self.assertFalse(warn_if_open_profile())
