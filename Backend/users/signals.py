"""
Django signals to auto-create related models
"""
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from .models import UserProfile, Workspace, GoalProfile

User = get_user_model()


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    """Auto-create UserProfile when User is created"""
    if created:
        # Set default values for Mathia bot
        if instance.username == 'mathia':
            UserProfile.objects.create(
                user=instance,
                bio="I'm Mathia, your AI assistant. I can help you with scheduling, payments, WhatsApp messages, and more! Just mention me with @mathia.",
                location="Cloud ☁️",
                user_type='personal'
            )
        else:
            UserProfile.objects.create(user=instance)


@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    """Save UserProfile when User is saved"""
    if hasattr(instance, 'profile'):
        instance.profile.save()


@receiver(post_save, sender=Workspace)
def create_goal_profile(sender, instance, created, **kwargs):
    """Auto-create GoalProfile when Workspace is created"""
    if created:
        GoalProfile.objects.create(workspace=instance)


@receiver(post_save, sender=Workspace)
def save_goal_profile(sender, instance, **kwargs):
    """Save GoalProfile when Workspace is saved"""
    if hasattr(instance, 'goals'):
        instance.goals.save()
