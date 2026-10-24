"""Seed the Kazi AI bot user and wire it into existing rooms.

Creates the 'Kazi' user (with profile) if it doesn't exist, then ensures
every human user has at least one room with Kazi as a participant.

Idempotent — safe to run on every startup.

Usage:
    python manage.py seed_kazi
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from chatbot.kazi_messages import (
    ORIGIN_WELCOME,
    get_kazi_member,
    get_kazi_user,
    persist_kazi_message,
)
from chatbot.models import Chatroom, Member
from users.naming import account_has_owner

User = get_user_model()


class Command(BaseCommand):
    help = "Seed the Kazi AI bot and ensure all users have an AI-ready room."

    def handle(self, *args, **options):
        existing = User.objects.filter(username="kazi").first()
        if existing is not None and account_has_owner(existing):
            raise CommandError(
                "An account named 'kazi' looks like a person: it has a password, a login "
                "history or staff rights. That username is reserved for the assistant, and "
                "adopting the account would add it to other users' rooms. Rename the "
                "account, then run seed_kazi again. Nothing was changed."
            )

        _kazi_user, created = get_kazi_user()
        if created:
            self.stdout.write(self.style.SUCCESS("Created Kazi user"))
        else:
            self.stdout.write("Kazi user already exists")

        kazi_member = get_kazi_member()

        with transaction.atomic():
            users = list(
                User.objects.filter(is_active=True)
                .exclude(username="kazi")
                .select_for_update()
            )
            for user in users:
                has_room = (
                    Chatroom.objects.filter(participants__User=user)
                    .filter(participants=kazi_member)
                    .exists()
                )
                if has_room:
                    continue

                room = Chatroom.objects.create()
                user_member, _ = Member.objects.get_or_create(User=user)
                room.participants.add(user_member, kazi_member)

                persist_kazi_message(
                    room,
                    content="Hello! I'm Kazi, your AI assistant. Ask me anything!",
                    origin=ORIGIN_WELCOME,
                    broadcast=False,
                )
                self.stdout.write(
                    self.style.SUCCESS(f"Created AI room for {user.username}")
                )

        self.stdout.write(self.style.SUCCESS("Done"))
