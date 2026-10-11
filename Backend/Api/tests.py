"""API surface tests: plan-aware throttling (v0.6 stress-test fix)."""
import contextlib
import io
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

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


class CalendlyLogRedactionTests(SimpleTestCase):
    """The Calendly OAuth paths must not write URLs or provider bodies to logs."""

    FAKE = "fake-token-otter-4471"

    def _request(self, method, path, data=None):
        factory = APIRequestFactory()
        request = getattr(factory, method)(path, data)
        user = SimpleNamespace(
            id=7, pk=7, is_authenticated=True, is_active=True, is_anonymous=False,
        )
        force_authenticate(request, user=user)
        return request

    @override_settings(CALENDLY_CLIENT_ID="fake-token-otter-4471")
    def test_connect_does_not_log_the_auth_url(self):
        from Api.views import calendly_connect

        # The leak this replaced was a print(), which log capture alone cannot see.
        printed = io.StringIO()
        with self.assertLogs(level="DEBUG") as captured, contextlib.redirect_stdout(printed):
            response = calendly_connect(self._request("post", "/api/calendly/connect/"))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(self.FAKE, "\n".join(captured.output))
        self.assertEqual(printed.getvalue(), "")

    def test_callback_does_not_log_the_provider_body(self):
        from Api.views import calendly_callback

        fake_response = httpx.Response(500, content=self.FAKE.encode())
        with patch("Api.views.requests.post", return_value=fake_response):
            with self.assertLogs(level="DEBUG") as captured:
                response = calendly_callback(
                    self._request("get", "/api/calendly/callback/", {"code": "abc"})
                )

        self.assertEqual(response.status_code, 500)
        self.assertNotIn(self.FAKE, "\n".join(captured.output))


class KaziReplyPermissionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.client = APIClient()
        from Api.models import KaziReply
        KaziReply.objects.create(message="private", sender="kazi", command="cmd", chatid=1)

    def _user(self, username, perms=()):
        from django.contrib.auth.models import Permission
        user = User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="secret",  # nosec B106 — test fixture — fake credential
        )
        for codename in perms:
            user.user_permissions.add(Permission.objects.get(codename=codename))
        return user

    def test_anonymous_cannot_list_replies(self):
        response = self.client.get("/api/newreply/")
        self.assertIn(response.status_code, (401, 403))

    def test_regular_user_cannot_list(self):
        self.client.force_authenticate(self._user("reply-regular"))
        self.assertEqual(self.client.get("/api/newreply/").status_code, 403)
        self.assertEqual(self.client.head("/api/newreply/").status_code, 403)

    def test_regular_user_cannot_create(self):
        self.client.force_authenticate(self._user("reply-regular"))
        payload = {"message": "m", "sender": "s", "command": "c", "chatid": 1}
        self.assertEqual(self.client.post("/api/newreply/", payload).status_code, 403)

    def test_user_with_model_perms_can_list_and_create(self):
        self.client.force_authenticate(self._user("reply-staff", perms=("view_kazireply", "add_kazireply")))
        self.assertEqual(self.client.get("/api/newreply/").status_code, 200)
        self.assertEqual(self.client.head("/api/newreply/").status_code, 200)
        payload = {"message": "m", "sender": "s", "command": "c", "chatid": 1}
        self.assertEqual(self.client.post("/api/newreply/", payload).status_code, 201)
