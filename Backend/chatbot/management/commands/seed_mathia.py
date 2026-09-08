"""Seed the Mathia AI bot user and wire it into existing rooms.

Creates the 'mathia' user (with profile) if it doesn't exist, then ensures
every human user has at least one room with mathia as a participant.

Idempotent — safe to run on every startup.

Usage:
    python manage.py seed_mathia
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from chatbot.models import Chatroom, Member, Message

User = get_user_model()


class Command(BaseCommand):
    help = "Seed the Mathia AI bot and ensure all users have an AI-ready room."

    def handle(self, *args, **options):
        mathia_user, created = User.objects.get_or_create(
            username="mathia",
            defaults={
                "first_name": "Mathia",
                "last_name": "AI",
                "is_active": True,
                "email": "mathia@kwikchat.ai",
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS("Created mathia user"))
        else:
            self.stdout.write("mathia user already exists")

        mathia_member, _ = Member.objects.get_or_create(User=mathia_user)

        with transaction.atomic():
            users = list(
                User.objects.filter(is_active=True)
                .exclude(username="mathia")
                .select_for_update()
            )
            for user in users:
                has_room = (
                    Chatroom.objects.filter(participants__User=user)
                    .filter(participants=mathia_member)
                    .exists()
                )
                if has_room:
                    continue

                room = Chatroom.objects.create()
                user_member, _ = Member.objects.get_or_create(User=user)
                room.participants.add(user_member, mathia_member)

                welcome_msg = Message.objects.create(
                    member=mathia_member,
                    content="Hello! I'm Mathia, your AI assistant. Ask me anything!",
                    timestamp=timezone.now(),
                )
                room.chats.add(welcome_msg)
                self.stdout.write(
                    self.style.SUCCESS(f"Created AI room for {user.username}")
                )

        self.stdout.write(self.style.SUCCESS("Done"))
