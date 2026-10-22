"""A chat turn's capability gate denies when the preference lookup fails."""
import asyncio
from unittest.mock import patch

from django.test import SimpleTestCase

from orchestration.test_coordinator_history import _run_agent_turn
from orchestration.user_preferences import conservative_default_preferences


class CoordinatorCapabilityLookupFailsClosedTests(SimpleTestCase):

    def test_failed_lookup_sends_conservative_preferences_to_the_loop(self):
        def _boom(user_id, profile_obj=None):
            raise RuntimeError("db down")

        _result, captured = _run_agent_turn("hello", load_preferences=_boom)

        prefs = captured["preferences"]
        self.assertFalse(prefs.get("allow_email", True))
        self.assertFalse(prefs.get("allow_payments", True))
        self.assertFalse(prefs.get("allow_whatsapp", True))
        self.assertTrue(prefs.get("allow_web_search"))

    def test_failed_lookup_applies_no_learned_approval_shortcut(self):
        def _boom(user_id, profile_obj=None):
            raise RuntimeError("db down")

        def _learned(preferences, user_id, room_id):
            merged = dict(preferences or {})
            merged["approval_overrides"] = {"run_command": "auto"}
            return merged

        with patch("orchestration.preference_mining.merge_learned_overrides", side_effect=_learned) as merge:
            _result, captured = _run_agent_turn("hello", load_preferences=_boom)

        merge.assert_not_called()
        self.assertFalse(captured["preferences"].get("approval_overrides"))

    def test_successful_lookup_still_gets_learned_shortcuts(self):
        def _learned(preferences, user_id, room_id):
            merged = dict(preferences or {})
            merged["approval_overrides"] = {"run_command": "auto"}
            return merged

        with patch("orchestration.preference_mining.merge_learned_overrides", side_effect=_learned):
            _result, captured = _run_agent_turn("hello", load_preferences=lambda user_id, profile_obj=None: {"allow_email": True})

        self.assertEqual(captured["preferences"]["approval_overrides"], {"run_command": "auto"})

    def test_execute_tool_still_denies_email_with_conservative_preferences(self):
        from orchestration.tool_executor import execute_tool

        context = {
            "user_id": 1,
            "room_id": 1,
            "preferences": conservative_default_preferences(),
        }
        result = asyncio.run(
            execute_tool(
                "send_email",
                {"to": "example@example.com", "subject": "Hi", "text": "Hello"},
                context,
            )
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["message"], "The send email capability is disabled in your settings.")

    def test_tool_definitions_hide_email_but_keep_weather(self):
        from orchestration.tool_schemas import get_tool_definitions

        available = {"send_email", "get_weather"}
        names = {
            t["name"] for t in get_tool_definitions(
                user_capabilities=conservative_default_preferences(),
                available_actions=available,
            )
        }

        self.assertNotIn("send_email", names)
        self.assertIn("get_weather", names)
