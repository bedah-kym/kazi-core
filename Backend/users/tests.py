"""Settings-page, dashboard and agent-caps tests."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from chatbot.models import Chatroom, Member, Message, Reminder
from orchestration.user_preferences import enforce_agent_caps

from .models import Wallet, Workspace

User = get_user_model()


class AgentCapsSwitchRemovedTests(TestCase):
    """T-F3: the all-or-nothing caps switch is gone from Settings >
    Capabilities. The legacy enforcement helper still reads its stored value
    (the tool router uses it), and a stored off value survives a save so it can
    still map to the largest turn bound."""

    def setUp(self):
        self.user = User.objects.create_user(username="caps-user", email="caps@example.com", password="secret")  # nosec B106 — test fixture — fake credential
        Workspace.objects.create(user=self.user, onboarding_completed=True)
        self.client.force_login(self.user)

    def tearDown(self):
        cache.delete(f"agent_caps_enforced:{self.user.id}")

    def _post_capabilities(self, **extra):
        data = {"section": "capabilities", "capability_mode": "custom"}
        data.update(extra)
        response = self.client.post(reverse("users:settings"), data)
        self.assertRedirects(response, "/accounts/settings/#capabilities", fetch_redirect_response=False)
        self.user.profile.refresh_from_db()
        return response

    def test_settings_page_has_no_caps_switch(self):
        response = self.client.get(reverse("users:settings"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="capsToggle"')

    def test_saving_capabilities_leaves_a_legacy_off_value_untouched(self):
        profile = self.user.profile
        prefs = dict(profile.notification_preferences or {})
        prefs["enforce_agent_caps"] = False
        profile.notification_preferences = prefs
        profile.save(update_fields=["notification_preferences"])

        self._post_capabilities(allow_travel="on")

        prefs = self.user.profile.notification_preferences
        self.assertEqual(prefs["capability_mode"], "custom")
        self.assertFalse(prefs["enforce_agent_caps"])
        self.assertFalse(enforce_agent_caps(self.user.id))

    def test_helper_fails_safe_on_lookup_error(self):
        with patch("django.contrib.auth.get_user_model") as mock_get:
            mock_get.return_value.objects.get.side_effect = RuntimeError("db down")
            self.assertTrue(enforce_agent_caps(self.user.id))

    def test_helper_none_user_defaults_on(self):
        self.assertTrue(enforce_agent_caps(None))


class DashboardViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="dash-user", email="dash@example.com", password="secret")  # nosec B106 — test fixture — fake credential
        self.workspace = Workspace.objects.create(user=self.user, onboarding_completed=True)
        self.client.force_login(self.user)

    def test_dashboard_page_renders_shell(self):
        response = self.client.get(reverse("users:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Command center")
        self.assertContains(response, 'id="statTotalMessages"')
        self.assertContains(response, 'id="activityFeed"')
        self.assertContains(response, reverse("workflows:workflows_list"))
        self.assertContains(response, reverse("notifications:notification-center"))


class DashboardApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="dash-api", email="dash-api@example.com", password="secret")  # nosec B106 — test fixture — fake credential
        self.workspace = Workspace.objects.create(user=self.user, onboarding_completed=True)
        self.member = Member.objects.create(User=self.user)
        self.client.force_login(self.user)

    def test_unauthenticated_is_forbidden(self):
        self.client.logout()
        response = self.client.get(reverse("botApi:dashboard_overview"))
        self.assertEqual(response.status_code, 403)

    def test_overview_returns_expected_shape(self):
        response = self.client.get(reverse("botApi:dashboard_overview"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("stats", payload)
        self.assertIn("wallet", payload)
        self.assertIn("quota", payload)
        self.assertIn("workflows", payload)
        self.assertIn("activity", payload)
        for key in (
            "total_messages", "active_rooms", "unread_rooms",
            "pending_reminders", "unread_notifications",
        ):
            self.assertIn(key, payload["stats"])

    def test_overview_reflects_user_data(self):
        room = Chatroom.objects.create()
        room.participants.add(self.member)
        Message.objects.create(member=self.member, content="hello", timestamp=timezone.now())
        Reminder.objects.create(
            user=self.user, content="call John", scheduled_time=timezone.now() + timedelta(hours=1), status="pending"
        )
        Wallet.objects.create(workspace=self.workspace, balance="1200.00")

        response = self.client.get(reverse("botApi:dashboard_overview"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertGreaterEqual(payload["stats"]["active_rooms"], 1)
        self.assertEqual(payload["stats"]["pending_reminders"], 1)
        self.assertEqual(payload["wallet"]["balance"], "1200.00")
        self.assertEqual(payload["workflows"]["total"], 0)
        self.assertTrue(any(item["kind"] == "reminder" for item in payload["activity"]))


class ReservedUsernameTests(TestCase):
    """The kazi identity can never be claimed by registration."""

    def test_is_reserved_username(self):
        from .naming import is_reserved_username

        self.assertTrue(is_reserved_username("kazi"))
        self.assertTrue(is_reserved_username("  Kazi "))
        self.assertFalse(is_reserved_username("kasuku"))

    @patch("users.auth_views._resolve_invite_token", return_value=(object(), "invite"))
    def test_register_rejects_reserved_username(self, _mock_invite):
        response = self.client.post(
            reverse("users:register"),
            {
                "full_name": "Kazi Wannabe",
                "email": "wannabe@example.com",
                "username": "kazi",
                "password1": "Str0ng!Pass99",  # nosec B105 — test fixture — fake credential
                "password2": "Str0ng!Pass99",  # nosec B105 — test fixture — fake credential
                "invite_token": "fake-token",  # nosec B105 — test fixture — fake credential
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "reserved")
        self.assertFalse(User.objects.filter(username__iexact="kazi").exists())
