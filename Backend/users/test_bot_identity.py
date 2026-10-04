"""The assistant's username must never be bound to a person's account.

Upgrades from a release where ``kazi`` was not reserved can meet an existing
human account with that name. The rename migration and ``seed_kazi`` both
refuse to adopt it.
"""
import importlib

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from chatbot.models import Chatroom, Member
from users.naming import account_has_owner

User = get_user_model()


def _person(username):
    user = User.objects.create(username=username)
    user.set_password("not-a-real-secret")
    user.save()
    return user


class AccountHasOwnerTests(TestCase):
    def test_account_without_password_or_login_has_no_owner(self):
        self.assertFalse(account_has_owner(User(username="kazi")))

    def test_unusable_password_marker_is_not_an_owner(self):
        marked = User(username="kazi")
        marked.set_unusable_password()
        self.assertFalse(account_has_owner(marked))

    def test_password_login_or_staff_means_an_owner(self):
        self.assertTrue(account_has_owner(_person("kazi")))
        self.assertTrue(account_has_owner(User(username="kazi", last_login=timezone.now())))
        self.assertTrue(account_has_owner(User(username="kazi", is_staff=True)))


class SeedKaziGuardTests(TestCase):
    def test_refuses_to_adopt_a_persons_account(self):
        person = _person("kazi")
        rooms_before = Chatroom.objects.count()

        with self.assertRaises(CommandError):
            call_command("seed_kazi")

        self.assertEqual(Chatroom.objects.count(), rooms_before)
        self.assertFalse(Chatroom.objects.filter(participants__User=person).exists())

    def test_seeds_normally_when_the_bot_account_has_no_owner(self):
        User.objects.create(username="kazi", email="kazi@kwikchat.ai")
        jon = _person("jon")

        call_command("seed_kazi")

        self.assertTrue(
            Chatroom.objects.filter(participants__User=jon)
            .filter(participants__User__username="kazi")
            .exists()
        )


class RenameMigrationGuardTests(TestCase):
    def _rename_bot_user(self):
        module = importlib.import_module("users.migrations.0018_rename_mathia_to_kazi")
        module.rename_bot_user(django_apps, None)

    def _legacy_ai_room(self):
        mathia = User.objects.create(username="mathia", email="mathia@kwikchat.ai")
        jon = _person("jon")
        room = Chatroom.objects.create()
        room.participants.add(
            Member.objects.get_or_create(User=mathia)[0],
            Member.objects.get_or_create(User=jon)[0],
        )
        return mathia, room

    def test_refuses_to_merge_the_bot_into_a_persons_account(self):
        person = _person("kazi")
        mathia, room = self._legacy_ai_room()

        with self.assertRaises(RuntimeError):
            self._rename_bot_user()

        self.assertFalse(room.participants.filter(User=person).exists())
        mathia.refresh_from_db()
        self.assertEqual(mathia.username, "mathia")
        self.assertTrue(mathia.is_active)

    def test_merges_into_an_existing_bot_account(self):
        bot = User.objects.create(username="kazi", email="kazi@kwikchat.ai")
        mathia, room = self._legacy_ai_room()

        self._rename_bot_user()

        self.assertTrue(room.participants.filter(User=bot).exists())
        mathia.refresh_from_db()
        self.assertFalse(mathia.is_active)
        self.assertTrue(mathia.username.startswith("mathia-legacy-"))

    def test_renames_the_legacy_bot_when_no_kazi_account_exists(self):
        mathia = User.objects.create(username="mathia", email="mathia@kwikchat.ai")
        User.objects.filter(username="kazi").delete()

        self._rename_bot_user()

        mathia.refresh_from_db()
        self.assertEqual(mathia.username, "kazi")
