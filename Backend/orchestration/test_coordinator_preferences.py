"""A chat turn's capability gate denies when the preference lookup fails."""
import asyncio

from django.test import SimpleTestCase

from orchestration.test_coordinator_history import _run_agent_turn
from orchestration.user_preferences import conservative_default_preferences


class CoordinatorCapabilityLookupFailsClosedTests(SimpleTestCase):

    def test_failed_lookup_sends_conservative_preferences_to_the_loop(self):
        def _boom(user_id):
            raise RuntimeError("db down")

        _result, captured = _run_agent_turn("hello", load_preferences=_boom)

        prefs = captured["preferences"]
        self.assertFalse(prefs.get("allow_email", True))
        self.assertFalse(prefs.get("allow_payments", True))
        self.assertFalse(prefs.get("allow_whatsapp", True))
        self.assertTrue(prefs.get("allow_web_search"))

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

        names = {t["name"] for t in get_tool_definitions(user_capabilities=conservative_default_preferences())}

        self.assertNotIn("send_email", names)
        self.assertIn("get_weather", names)
