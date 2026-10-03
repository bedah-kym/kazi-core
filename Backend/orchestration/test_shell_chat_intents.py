from __future__ import annotations

from django.test import SimpleTestCase

from orchestration.agent_prompts import build_confirmation_prompt
from orchestration.shell.chat_intents import is_always_allow_request, is_autopilot_request


class IntentDetectionTests(SimpleTestCase):
    def test_always_allow_phrases(self):
        for phrase in ("always allow", "Always allow please", "don't ask me again", "stop asking", "auto-approve"):
            self.assertTrue(is_always_allow_request(phrase), phrase)

    def test_autopilot_phrases(self):
        for phrase in ("autopilot", "auto pilot", "take over", "go autonomous", "run on your own"):
            self.assertTrue(is_autopilot_request(phrase), phrase)

    def test_plain_confirmation_is_neither(self):
        for phrase in ("yes", "go ahead", "sure", "ok"):
            self.assertFalse(is_always_allow_request(phrase), phrase)
            self.assertFalse(is_autopilot_request(phrase), phrase)

    def test_negated_phrases_do_not_match(self):
        self.assertFalse(is_always_allow_request("no don't always allow"))
        self.assertFalse(is_autopilot_request("no autopilot"))


class ConfirmationPromptHintTests(SimpleTestCase):
    def test_shell_prompt_advertises_the_shortcuts(self):
        prompt = build_confirmation_prompt("run_command", {"command": "dir /b"})
        self.assertIn("always allow", prompt)
        self.assertIn("autopilot", prompt)

    def test_non_shell_prompt_has_no_shell_hint(self):
        prompt = build_confirmation_prompt("send_email", {"to": "a@b.c"})
        self.assertNotIn("autopilot", prompt)
