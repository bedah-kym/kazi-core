"""Settings-page and agent-caps toggle tests."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from orchestration.user_preferences import enforce_agent_caps

from .models import Workspace

User = get_user_model()


class AgentCapsToggleTests(TestCase):
    """Settings > Capabilities exposes the install-level budget-cap kill
    switch; enforcement reads it with a fail-safe default of ON."""

    def setUp(self):
        self.user = User.objects.create_user(username="caps-user", email="caps@example.com", password="secret")
        Workspace.objects.create(user=self.user, onboarding_completed=True)
        self.client.force_login(self.user)

    def _post_capabilities(self, **extra):
        data = {"section": "capabilities", "capability_mode": "custom"}
        data.update(extra)
        response = self.client.post(reverse("users:settings"), data)
        self.assertRedirects(response, "/accounts/settings/#capabilities", fetch_redirect_response=False)
        self.user.profile.refresh_from_db()
        return response

    def test_settings_page_renders_caps_switch_default_on(self):
        response = self.client.get(reverse("users:settings"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="capsToggle"')
        self.assertTrue(response.context["capability_prefs"]["enforce_agent_caps"])
        self.assertTrue(enforce_agent_caps(self.user.id))

    def test_unchecked_submit_disables_caps(self):
        response = self._post_capabilities(allow_payments="on")

        self.assertEqual(response.status_code, 302)
        prefs = self.user.profile.notification_preferences
        self.assertFalse(prefs["enforce_agent_caps"])
        self.assertFalse(enforce_agent_caps(self.user.id))

    def test_checked_submit_keeps_caps_enabled(self):
        response = self._post_capabilities(enforce_agent_caps="on", allow_travel="on")

        self.assertEqual(response.status_code, 302)
        prefs = self.user.profile.notification_preferences
        self.assertTrue(prefs["enforce_agent_caps"])
        self.assertTrue(enforce_agent_caps(self.user.id))

    def test_toggle_survives_preset_mode_submission(self):
        response = self._post_capabilities(capability_mode="max", enforce_agent_caps="on")

        self.assertEqual(response.status_code, 302)
        prefs = self.user.profile.notification_preferences
        self.assertEqual(prefs["capability_mode"], "max")
        self.assertTrue(prefs["enforce_agent_caps"])

    def test_helper_fails_safe_on_lookup_error(self):
        with patch("django.contrib.auth.get_user_model") as mock_get:
            mock_get.return_value.objects.get.side_effect = RuntimeError("db down")
            self.assertTrue(enforce_agent_caps(self.user.id))

    def test_helper_none_user_defaults_on(self):
        self.assertTrue(enforce_agent_caps(None))
