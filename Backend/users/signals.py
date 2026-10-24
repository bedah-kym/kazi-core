"""
Django signals to auto-create related models
"""
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from .models import UserProfile

User = get_user_model()


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    """Auto-create UserProfile when User is created"""
    if created:
        # Set default values for Kazi bot
        if instance.username == 'kazi':
            UserProfile.objects.create(
                user=instance,
                bio="I'm Kazi, your AI assistant. I can help you with scheduling, payments, WhatsApp messages, and more! Just mention me with @Kazi.",
                location="Cloud ☁️",
                user_type='personal'
            )
        else:
            UserProfile.objects.create(user=instance)

            # Auto-create General Room
            from chatbot.kazi_messages import (
                ORIGIN_WELCOME,
                get_kazi_member,
                persist_kazi_message,
            )
            from chatbot.models import Chatroom, Member

            # Create Member for new user
            user_member, _ = Member.objects.get_or_create(User=instance)

            # Create the General Room
            general_room = Chatroom.objects.create()
            general_room.participants.add(user_member)

            # Ensure the Kazi bot exists, then add it so the General room is
            # AI-ready even on a fresh database (bot created before first user).
            try:
                kazi_member = get_kazi_member()
                general_room.participants.add(kazi_member)
                persist_kazi_message(
                    general_room,
                    content=(
                        "Hello! I'm Kazi, your AI assistant. This is your General room "
                        "where you can ask me anything."
                    ),
                    origin=ORIGIN_WELCOME,
                    broadcast=False,
                )
            except Exception as e:
                # Log error but don't fail user creation
                print(f"Error adding Kazi to room: {e}")


@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    """Save UserProfile when User is saved"""
    if hasattr(instance, 'profile'):
        instance.profile.save()
