"""
Linked Rooms API Views
Endpoints for bidirectional room linking (shared context).
"""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from chatbot.models import Chatroom, RoomContext


def _get_room_name(chatroom):
    """Return a human-readable room name."""
    participants = chatroom.participants.all()
    names = [m.User.username for m in participants[:3]]
    if len(names) > 3:
        names.append("...")
    return ", ".join(names) if names else f"Room #{chatroom.id}"


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def list_or_link_rooms(request, room_id):
    """
    GET  /api/rooms/<room_id>/linked/  — List linked rooms + linkable rooms
    POST /api/rooms/<room_id>/linked/  — Link a room (body: {target_room_id})
    """
    # Verify user is participant
    if not Chatroom.objects.filter(id=room_id, participants__User=request.user).exists():
        return Response({"error": "Access denied"}, status=status.HTTP_403_FORBIDDEN)

    ctx, _ = RoomContext.objects.get_or_create(chatroom_id=room_id)

    if request.method == 'GET':
        # Linked rooms
        linked = []
        for related_ctx in ctx.related_rooms.select_related('chatroom').all():
            linked.append({
                "id": related_ctx.chatroom_id,
                "name": _get_room_name(related_ctx.chatroom),
            })

        # Linkable: user's other rooms minus already-linked and self
        linked_ids = {r['id'] for r in linked}
        linked_ids.add(room_id)

        user_rooms = Chatroom.objects.filter(
            participants__User=request.user
        ).exclude(id__in=linked_ids).distinct()

        linkable = [
            {"id": r.id, "name": _get_room_name(r)}
            for r in user_rooms
        ]

        return Response({"linked": linked, "linkable": linkable})

    # POST — link a room
    target_room_id = request.data.get('target_room_id')
    if not target_room_id:
        return Response({"error": "target_room_id is required"}, status=status.HTTP_400_BAD_REQUEST)

    # Verify user is participant in target room
    if not Chatroom.objects.filter(id=target_room_id, participants__User=request.user).exists():
        return Response(
            {"error": "You must be a participant in both rooms to link them"},
            status=status.HTTP_403_FORBIDDEN,
        )

    if int(target_room_id) == room_id:
        return Response({"error": "Cannot link a room to itself"}, status=status.HTTP_400_BAD_REQUEST)

    target_ctx, _ = RoomContext.objects.get_or_create(chatroom_id=target_room_id)

    # Bidirectional link
    ctx.related_rooms.add(target_ctx)
    target_ctx.related_rooms.add(ctx)

    return Response({"success": True, "linked_room_id": target_room_id}, status=status.HTTP_201_CREATED)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def unlink_room(request, room_id, target_room_id):
    """
    DELETE /api/rooms/<room_id>/linked/<target_room_id>/  — Unlink a room
    """
    if not Chatroom.objects.filter(id=room_id, participants__User=request.user).exists():
        return Response({"error": "Access denied"}, status=status.HTTP_403_FORBIDDEN)

    try:
        ctx = RoomContext.objects.get(chatroom_id=room_id)
        target_ctx = RoomContext.objects.get(chatroom_id=target_room_id)
    except RoomContext.DoesNotExist:
        return Response({"error": "Room context not found"}, status=status.HTTP_404_NOT_FOUND)

    # Bidirectional unlink
    ctx.related_rooms.remove(target_ctx)
    target_ctx.related_rooms.remove(ctx)

    return Response({"success": True})
