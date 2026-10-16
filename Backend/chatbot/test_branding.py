"""The Kazi brand (Lango mark, favicons, avatar) is what the app renders."""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.templatetags.static import static
from django.test import TestCase
from django.urls import reverse

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

    def test_ai_room_avatar_is_the_kazi_avatar(self):
        response = self.client.get(reverse("chatbot:bot-home", kwargs={"room_name": self.room.id}))
        self.assertEqual(response.context["room_avatar"], static("img/kazi-avatar.svg"))

    def test_app_shell_uses_kazi_brand(self):
        self.assert_branded(self.client.get(reverse("users:personas")))

    def test_marketing_page_uses_kazi_brand(self):
        self.client.logout()
        self.assert_branded(self.client.get(reverse("users:login")))
