from __future__ import annotations

from django.test import SimpleTestCase

from orchestration.agent_prompts import build_confirmation_prompt
from orchestration.shell.chat_intents import is_autopilot_disarm_request, is_autopilot_request


class AutopilotIntentTests(SimpleTestCase):
    def test_exact_replies_arm(self):
        for phrase in (
            "autopilot", "Autopilot.", "  auto pilot ", "autopilot on", "arm autopilot!",
            "sure, autopilot", "yes autopilot", "ok, autopilot please", "autopilot please",
        ):
            self.assertTrue(is_autopilot_request(phrase), phrase)

    def test_anything_longer_does_not_arm(self):
        for phrase in (
            "what does autopilot do?",
            "no thanks. autopilot sounds scary",
            "no autopilot",
            "please no autopilot",
            "yes but not autopilot",
            "sure, what is autopilot",
            "ok autopilot off",
            "don't take over",
            "I'll take over from here, cancel",
            "no, stop asking me",
            "cancel, and never ask me again",
            "always allow",
            "yes",
            "",
        ):
            self.assertFalse(is_autopilot_request(phrase), phrase)

    def test_disarm_replies(self):
        for phrase in ("autopilot off", "Disarm autopilot.", "stop autopilot"):
            self.assertTrue(is_autopilot_disarm_request(phrase), phrase)
            self.assertFalse(is_autopilot_request(phrase), phrase)
        self.assertFalse(is_autopilot_disarm_request("is autopilot off?"))


class ConfirmationPromptTests(SimpleTestCase):
    def test_shell_prompt_does_not_advertise_shortcuts(self):
        prompt = build_confirmation_prompt("run_command", {"command": "curl http://example.test"})
        self.assertNotIn("always allow", prompt)
        self.assertNotIn("autopilot", prompt)
