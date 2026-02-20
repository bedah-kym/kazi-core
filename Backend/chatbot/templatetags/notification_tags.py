from django import template
from chatbot.models import Reminder
from chatbot.notification_utils import get_unread_room_count

register = template.Library()


def _get_user(context):
    request = context.get('request')
    if request and hasattr(request, 'user'):
        return request.user
    return context.get('user')


@register.simple_tag(takes_context=True)
def unread_rooms_count(context, exclude_room_id=None):
    user = _get_user(context)
    if not user or not getattr(user, "is_authenticated", False):
        return 0
    return get_unread_room_count(user, exclude_room_id=exclude_room_id)


@register.simple_tag(takes_context=True)
def pending_reminders_count(context):
    user = _get_user(context)
    if not user or not getattr(user, "is_authenticated", False):
        return 0
    return Reminder.objects.filter(user=user, status='pending').count()
