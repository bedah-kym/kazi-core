from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from orchestration.shell import autopilot, grants


class ShellAutonomyUiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="shellui")
        self.client.force_login(self.user)

    def test_inbox_shows_shell_autonomy_panel(self):
        response = self.client.get(reverse("workflows:operations_inbox"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Shell autonomy")

    def test_arm_then_disarm_autopilot_via_ui(self):
        response = self.client.post(
            reverse("workflows:shell_autopilot_arm"),
            {"room_id": 5, "minutes": 30},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(autopilot.is_armed(self.user.id, 5))

        response = self.client.post(
            reverse("workflows:shell_autopilot_disarm"), {"room_id": 5},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(autopilot.is_armed(self.user.id, 5))

    def test_revoke_grant_via_ui(self):
        fp = grants.create_grant(self.user.id, 5, "dir /b", profile_name="standard")
        self.assertIsNotNone(fp)

        response = self.client.post(
            reverse("workflows:shell_grant_revoke"),
            {"room_id": 5, "fingerprint": fp},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(grants.active_grants(self.user.id, 5), {})

    def test_inbox_lists_configured_room(self):
        autopilot.arm(self.user.id, 5, minutes=30)
        response = self.client.get(reverse("workflows:operations_inbox"))
        self.assertContains(response, "Room #5")
        self.assertContains(response, "Autopilot armed")

    def test_post_requires_login(self):
        self.client.logout()
        response = self.client.post(reverse("workflows:shell_autopilot_arm"), {"room_id": 5})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)
        self.assertFalse(autopilot.is_armed(self.user.id, 5))
