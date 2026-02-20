from django.db.models import Max
from .models import Chatroom, RoomReadState


def get_unread_room_count(user, exclude_room_id=None):
    """
    Count rooms with messages newer than the user's last_read_at.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return 0

    rooms = Chatroom.objects.filter(participants__User=user)
    if exclude_room_id:
        rooms = rooms.exclude(id=exclude_room_id)

    rooms = rooms.annotate(last_message_at=Max('chats__timestamp'))
    read_states = RoomReadState.objects.filter(user=user, room__in=rooms).values('room_id', 'last_read_at')
    last_read_map = {entry['room_id']: entry['last_read_at'] for entry in read_states}

    unread_rooms = 0
    for room in rooms:
        last_message_at = room.last_message_at
        if not last_message_at:
            continue
        last_read_at = last_read_map.get(room.id)
        if not last_read_at or last_message_at > last_read_at:
            unread_rooms += 1

    return unread_rooms
