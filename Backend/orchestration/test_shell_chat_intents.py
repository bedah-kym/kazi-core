from __future__ import annotations

from django.test import SimpleTestCase

from orchestration.agent_prompts import build_confirmation_prompt
from orchestration.shell.chat_intents import (
    is_approval_reply,
    is_cancel_command,
    is_decline_reply,
    is_dismiss_suggestions_command,
    is_receipts_command,
    is_reset_command,
    is_undo_command,
    is_autopilot_disarm_request,
    is_autopilot_request,
)


class ApprovalReplyTests(SimpleTestCase):
    def test_whole_message_approvals(self):
        for phrase in (
            "yes", "Yes.", "y", "ok", "OK!", "okay", "sure", "yep", "approve", "confirmed",
            "proceed", "go ahead", "sure, go ahead", "yes, do it", "yes please", "ok thanks",
            "go ahead please", "please go ahead", "  run it  ", "yes please go ahead",
            "sure, please go ahead", "ok please proceed", "yes,", "ok thank you", "yes yes yes",
            ", yes", ": yes", "\U0001F44D",
        ):
            self.assertTrue(is_approval_reply(phrase), phrase)

    def test_an_approval_word_followed_by_anything_else_is_not_consent(self):
        for phrase in (
            "ok but use a different folder",
            "yes, but only the first part",
            "ok what does that command do?",
            "yes?",
            "sure, after you show me the file",
            "okay so what happens if it fails",
            "yesterday was fine",
            "no",
            "please",
            "please confirm",
            "yes no",
            "ok no",
            "yes\n\nbut wait",
            "what does that do?",
            "",
            None,
            "yes" + "!" * 5000,
        ):
            self.assertFalse(is_approval_reply(phrase), phrase)

    def test_a_plain_no_declines(self):
        for phrase in ("no", "No.", "n", "nope", "no thanks", "no thank you", "don't"):
            self.assertTrue(is_decline_reply(phrase), phrase)
        for phrase in ("no idea what that does", "not yet, show me first", "yes", ""):
            self.assertFalse(is_decline_reply(phrase), phrase)


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


class ExactCommandTests(SimpleTestCase):
    def test_reset_commands(self):
        for phrase in (
            "reset", "Reset.", "start over", "new conversation", "/reset", "please reset",
            "fresh start", "clear context", "reset context", "forget context",
        ):
            self.assertTrue(is_reset_command(phrase), phrase)
        for phrase in (
            "the installer says I need to start over, what now?",
            "reset everything",
            "how do I reset my password?",
            "start over from the beginning for me",
            "",
            None,
        ):
            self.assertFalse(is_reset_command(phrase), phrase)

    def test_receipts_commands(self):
        for phrase in (
            "receipts", "show receipts", "/receipts", "show receipts please",
            "audit trail", "action log", "show actions", "show activity",
        ):
            self.assertTrue(is_receipts_command(phrase), phrase)
        for phrase in (
            "save this receipt text to receipts.txt",
            "show me my receipt",
            "what receipts do I have from last week?",
            "",
            None,
        ):
            self.assertFalse(is_receipts_command(phrase), phrase)

    def test_undo_commands(self):
        for phrase in ("undo", "undo that", "/undo", "undo please", "take that back", "undo last action"):
            self.assertTrue(is_undo_command(phrase), phrase)
        for phrase in (
            "git revert the last commit in the workspace",
            "how do I rollback a django migration",
            "take that back to the shop",
            "please undo the last two things",
            "",
            None,
        ):
            self.assertFalse(is_undo_command(phrase), phrase)

    def test_dismiss_suggestions_commands(self):
        for phrase in (
            "stop suggestions",
            "dismiss suggestions",
            "no more suggestions",
            "dismiss suggestions please",
        ):
            self.assertTrue(is_dismiss_suggestions_command(phrase), phrase)
        for phrase in ("dismiss the nudge", "stop", "what suggestions do you have?", "", None):
            self.assertFalse(is_dismiss_suggestions_command(phrase), phrase)

    def test_cancel_commands(self):
        for phrase in (
            "stop",
            "cancel",
            "pause",
            "never mind",
            "forget it",
            "autopilot off",
            "Stop.",
            "cancel please",
        ):
            self.assertTrue(is_cancel_command(phrase), phrase)
        for phrase in (
            "why did the server stop responding?",
            "add a pause of 2 seconds between retries",
            "stop for now",
            "the build did not stop in time",
            "cancel, and never ask me again",
            "",
            None,
        ):
            self.assertFalse(is_cancel_command(phrase), phrase)


class ConfirmationPromptTests(SimpleTestCase):
    def test_shell_prompt_does_not_advertise_shortcuts(self):
        prompt = build_confirmation_prompt("run_command", {"command": "curl http://example.test"})
        self.assertNotIn("always allow", prompt)
        self.assertNotIn("autopilot", prompt)
