"""The Kazi brand (Lango mark, favicons, avatar) is what the app renders."""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.templatetags.static import static
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.html import escapejs

from chatbot.models import Chatroom, Member

User = get_user_model()


class BrandingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="brand-owner", email="example@example.com",
            password="fake-token",  # nosec B106 — test fixture — fake credential
            is_staff=True,
        )
        self.client.force_login(self.user)
        kazi, _ = User.objects.get_or_create(username="kazi", defaults={"email": "kazi@example.com"})
        self.room = Chatroom.objects.create()
        for account in (self.user, kazi):
            member, _ = Member.objects.get_or_create(User=account)
            self.room.participants.add(member)

    def assert_branded(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, static("favicon.svg"))
        self.assertContains(response, static("site.webmanifest"))
        self.assertContains(response, 'class="kazi-mark"')
        self.assertNotContains(response, "fa-robot")

    def test_chat_page_uses_kazi_brand(self):
        response = self.client.get(reverse("chatbot:bot-home", kwargs={"room_name": self.room.id}))
        self.assert_branded(response)
        self.assertContains(response, static("img/kazi-avatar.svg"))
        self.assertNotContains(response, "robohash.org")

    @override_settings(STATIC_URL="/cdn/")
    def test_ai_room_avatar_follows_static_url(self):
        response = self.client.get(reverse("chatbot:bot-home", kwargs={"room_name": self.room.id}))
        self.assertEqual(response.context["room_avatar"], "/cdn/img/kazi-avatar.svg")
        self.assertContains(response, f'window.KAZI_AVATAR_URL = "{escapejs("/cdn/img/kazi-avatar.svg")}"')

    def test_app_shell_uses_kazi_brand(self):
        self.assert_branded(self.client.get(reverse("users:personas")))

    def test_marketing_page_uses_kazi_brand(self):
        self.client.logout()
        self.assert_branded(self.client.get(reverse("users:login")))


class LogoutControlTests(TestCase):
    """Django 5 logs out on POST only, so every logout control must submit a form."""

    setUp = BrandingTests.setUp

    def assert_logout_posts(self, response):
        logout_url = reverse("users:logout")
        self.assertContains(response, f'method="post" action="{logout_url}"')
        self.assertNotContains(response, f'href="{logout_url}"')

    def test_chat_page_logout_posts(self):
        self.assert_logout_posts(
            self.client.get(reverse("chatbot:bot-home", kwargs={"room_name": self.room.id})))

    def test_app_shell_logout_posts(self):
        self.assert_logout_posts(self.client.get(reverse("users:personas")))

    def test_logout_by_post_ends_the_session(self):
        self.client.post(reverse("users:logout"))
        self.assertNotIn("_auth_user_id", self.client.session)
