"""API surface tests: plan-aware throttling (v0.6 stress-test fix)."""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from users.models import Workspace

User = get_user_model()

FREE_CAP = 60


class PlanAwareThrottleTests(TestCase):
    def setUp(self):
        # Throttle history lives in the cache and user PKs restart at 1 on
        # every test rollback, so leftover history would throttle fresh users.
        cache.clear()
        self.addCleanup(cache.clear)
        self.client = APIClient()

    def _user(self, username, plan=None):
        user = User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="secret",  # nosec B106 — test fixture — fake credential
        )
        if plan:
            Workspace.objects.create(user=user, name="Stress Lab", plan=plan)
        return user

    def test_free_plan_caps_at_60_per_minute(self):
        self.client.force_authenticate(self._user("throttle-free"))
        statuses = [
            self.client.get("/api/workflows/").status_code
            for _ in range(FREE_CAP + 10)
        ]
        self.assertEqual(statuses.count(200), FREE_CAP)
        self.assertEqual(statuses.count(429), 10)

    def test_agency_plan_gets_10000_per_minute(self):
        self.client.force_authenticate(self._user("throttle-agency", plan="agency"))
        statuses = [
            self.client.get("/api/workflows/").status_code
            for _ in range(FREE_CAP + 10)
        ]
        self.assertEqual(statuses.count(200), FREE_CAP + 10)
        self.assertNotIn(429, statuses)

    def test_no_workspace_falls_back_to_free_cap(self):
        self.client.force_authenticate(self._user("throttle-nows"))
        statuses = [
            self.client.get("/api/workflows/").status_code
            for _ in range(FREE_CAP + 5)
        ]
        self.assertEqual(statuses.count(429), 5)
