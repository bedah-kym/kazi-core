from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from chatbot.models import Chatroom, Member
from orchestration.shell import autopilot
from orchestration.shell.profiles import shell_profile_pref_key


class ShellAutonomyUiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="shellui")
        self.room = Chatroom.objects.create()
        self.room.participants.add(Member.objects.create(User=self.user))
        cache.set(shell_profile_pref_key(self.room.id), "open")
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def _arm(self, **data):
        return self.client.post(reverse("workflows:shell_autopilot_arm"), {"room_id": self.room.id, **data})

    def test_inbox_lists_the_open_room(self):
        response = self.client.get(reverse("workflows:operations_inbox"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Shell autonomy")
        self.assertContains(response, f"Room #{self.room.id}")

    def test_sandboxed_room_is_not_listed(self):
        cache.set(shell_profile_pref_key(self.room.id), "standard")
        response = self.client.get(reverse("workflows:operations_inbox"))
        self.assertNotContains(response, f"Room #{self.room.id}")

    def test_arm_then_disarm(self):
        self.assertEqual(self._arm(minutes=30).status_code, 302)
        self.assertTrue(autopilot.is_armed(self.user.id, self.room.id))
        self.assertContains(self.client.get(reverse("workflows:operations_inbox")), "Autopilot armed")

        response = self.client.post(reverse("workflows:shell_autopilot_disarm"), {"room_id": self.room.id})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(autopilot.is_armed(self.user.id, self.room.id))

    def test_get_does_not_arm(self):
        response = self.client.get(reverse("workflows:shell_autopilot_arm"), {"room_id": self.room.id})
        self.assertEqual(response.status_code, 405)
        self.assertFalse(autopilot.is_armed(self.user.id, self.room.id))

    def test_cannot_arm_someone_elses_room(self):
        other = Chatroom.objects.create()
        cache.set(shell_profile_pref_key(other.id), "open")
        self.client.post(reverse("workflows:shell_autopilot_arm"), {"room_id": other.id})
        self.assertFalse(autopilot.is_armed(self.user.id, other.id))

    def test_oversized_minutes_falls_back_to_the_default(self):
        self.assertEqual(self._arm(minutes="9" * 40).status_code, 302)
        entry = autopilot.status(self.user.id, self.room.id)
        self.assertTrue(autopilot.entry_is_armed(entry))

    def test_host_approval_is_listed_and_can_be_revoked(self):
        from orchestration.shell import host_grants

        # A host that does not appear in the template's own help text.
        self.assertEqual(host_grants.grant(self.user.id, self.room.id, "mirror.acme.org"), "mirror.acme.org")
        response = self.client.get(reverse("workflows:operations_inbox"))
        self.assertContains(response, "Shell host approvals")
        self.assertEqual([grant.host for grant in response.context["shell_host_grants"]], ["mirror.acme.org"])
        self.assertContains(response, "mirror.acme.org")

        response = self.client.post(
            reverse("workflows:shell_host_revoke"), {"room_id": self.room.id, "host": "mirror.acme.org"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(host_grants.active_hosts(self.room.id), [])

    def test_cannot_revoke_a_host_in_someone_elses_room(self):
        from orchestration.shell import host_grants

        owner = get_user_model().objects.create_user(username="roomowner")
        other = Chatroom.objects.create()
        other.participants.add(Member.objects.create(User=owner))
        host_grants.grant(owner.id, other.id, "mirror.acme.org")
        self.client.post(reverse("workflows:shell_host_revoke"), {"room_id": other.id, "host": "mirror.acme.org"})
        self.assertEqual(host_grants.active_hosts(other.id), ["mirror.acme.org"])
        response = self.client.get(reverse("workflows:operations_inbox"))
        self.assertEqual(list(response.context["shell_host_grants"]), [])
        self.assertNotContains(response, "mirror.acme.org")

    def test_post_requires_login(self):
        self.client.logout()
        response = self._arm()
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)
        self.assertFalse(autopilot.is_armed(self.user.id, self.room.id))
