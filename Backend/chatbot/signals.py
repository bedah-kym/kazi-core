# Signal handlers for the chatbot app are registered here.
# chatbot/apps.py imports this module on startup so any @receiver definitions
# land in one obvious place.
from django.db.models.signals import m2m_changed, post_delete
from django.dispatch import receiver

from chatbot.models import Chatroom, Member


@receiver(m2m_changed, sender=Chatroom.participants.through)
def _invalidate_room_access_on_membership_change(sender, instance, action, pk_set, **kwargs):
    if action == "post_clear":
        # Rows are already gone; we cannot enumerate the affected users.
        from orchestration.security_policy import bump_room_access_epoch

        bump_room_access_epoch()
        return
    if action not in ("post_add", "post_remove"):
        return

    from orchestration.security_policy import invalidate_room_access_cache

    user_ids = list(Member.objects.filter(id__in=pk_set or set()).values_list("User_id", flat=True))
    invalidate_room_access_cache(user_ids, [instance.id])


@receiver(post_delete, sender=Member)
def _bump_room_access_epoch_on_member_delete(sender, instance, **kwargs):
    # Cascading deletes bypass m2m_changed, so a removed member's room rows can
    # disappear without a precise invalidation signal — nuke the epoch instead.
    from orchestration.security_policy import bump_room_access_epoch

    bump_room_access_epoch()


@receiver(post_delete, sender=Chatroom)
def _bump_room_access_epoch_on_room_delete(sender, instance, **kwargs):
    from orchestration.security_policy import bump_room_access_epoch

    bump_room_access_epoch()
