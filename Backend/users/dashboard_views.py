"""
Dashboard view for workspace overview.

The dashboard page renders a shell + widget skeletons; live data is fetched
client-side from the /api/dashboard/ aggregate endpoint (dashboard_api.py).
"""
from django.shortcuts import render
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.db.models import Max
from django.core.cache import cache
from django.http import JsonResponse
from chatbot.models import Chatroom
from users.decorators import workspace_required


@workspace_required
def dashboard(request):
    """Render the dashboard command center (data hydrates via the API)."""
    return render(request, 'users/dashboard.html')


def _room_display_name(room, members, current_user):
    """
    Generate a safe display name for the room list.
    Mirrors the sidebar logic used in chatbot.views.home.
    """
    mathia_member = next((m for m in members if m.User.username == 'mathia'), None)
    other_members = [m for m in members if m.User != current_user]

    if mathia_member and len(members) <= 2:
        return "General (AI)"
    if len(other_members) == 0:
        return "Private Room (You)"
    if len(other_members) == 1:
        return other_members[0].User.username

    display = ", ".join([m.User.username for m in other_members[:2]])
    if len(other_members) > 2:
        display += f" +{len(other_members) - 2}"
    return display


@login_required
def list_rooms(request):
    """
    Lightweight JSON endpoint to list the user's rooms.
    Uses a short cache window to avoid duplicate queries on rapid clicks.
    """
    if request.method != 'GET':
        return JsonResponse({"error": "Method not allowed"}, status=405)

    cache_key = f"user_rooms:{request.user.id}"
    force_refresh = request.GET.get('refresh') == '1'

    if force_refresh:
        cache.delete(cache_key)

    rooms_payload = cache.get(cache_key)

    if rooms_payload is None:
        rooms_qs = (
            Chatroom.objects.filter(participants__User=request.user)
            .annotate(last_message_at=Max('chats__timestamp'))
            .prefetch_related('participants__User')
            .order_by('-last_message_at', '-id')
        )

        rooms_payload = []
        for room in rooms_qs:
            members = list(room.participants.all())
            rooms_payload.append({
                "id": room.id,
                "name": _room_display_name(room, members, request.user),
                "participant_count": len(members),
                "last_message_at": room.last_message_at.isoformat() if room.last_message_at else None,
                "url": request.build_absolute_uri(
                    reverse('chatbot:bot-home', kwargs={"room_name": room.id})
                ),
                "has_ai": any(m.User.username == 'mathia' for m in members),
            })

        cache.set(cache_key, rooms_payload, 60)  # 1 minute cache to reduce DB hits

    return JsonResponse({"rooms": rooms_payload, "count": len(rooms_payload)})
