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
            
            # Auto-create General Room
            from chatbot.models import Chatroom, Member, Message
            import django.utils.timezone

            # Create Member for new user
            user_member, _ = Member.objects.get_or_create(User=instance)
            
            # Create the General Room
            general_room = Chatroom.objects.create()
            general_room.participants.add(user_member)
            
            # Try to add Mathia
            try:
                mathia_user = User.objects.get(username='mathia')
                mathia_member, _ = Member.objects.get_or_create(User=mathia_user)
                
                general_room.participants.add(mathia_member)
                
                # Add a welcome message
                welcome_msg = Message.objects.create(
                    member=mathia_member,
                    content="Hello! I'm Mathia, your AI assistant. This is your General room where you can ask me anything.",
                    timestamp=django.utils.timezone.now()
                )
                general_room.chats.add(welcome_msg)
                
            except User.DoesNotExist:
                # Mathia doesn't exist, but user still gets their room
                print(f"Warning: Mathia user not found. General room created for {instance.username} without bot.")
            except Exception as e:
                # Log error but don't fail user creation
                print(f"Error adding Mathia to room: {e}")



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
