from __future__ import annotations

import unittest

from notifications.capabilities import channel_capabilities, plan_voice_delivery


class NotificationCapabilityTests(unittest.TestCase):
    def test_whatsapp_supports_voice(self):
        caps = channel_capabilities("whatsapp")
        self.assertTrue(caps["supports_voice"])
        self.assertGreater(caps["max_audio_bytes"], 0)

    def test_email_does_not_support_voice(self):
        caps = channel_capabilities("email")
        self.assertFalse(caps["supports_voice"])
        self.assertEqual(caps["max_audio_bytes"], 0)

    def test_unknown_channel_defaults_to_no_audio(self):
        self.assertFalse(channel_capabilities("smoke-signal")["supports_voice"])

    def test_voice_channel_plans_voice(self):
        plan = plan_voice_delivery("whatsapp", audio_bytes=1000, text="la")
        self.assertEqual(plan["mode"], "voice")

    def test_text_only_channel_degrades_with_note(self):
        plan = plan_voice_delivery("email", audio_bytes=1000, text="la la")
        self.assertEqual(plan["mode"], "text")
        self.assertTrue(plan["note"])
        self.assertEqual(plan["text"], "la la")

    def test_oversize_audio_degrades_with_note(self):
        plan = plan_voice_delivery("in_app", audio_bytes=999_000_000, text="la")
        self.assertEqual(plan["mode"], "text")
        self.assertTrue(plan["note"])
