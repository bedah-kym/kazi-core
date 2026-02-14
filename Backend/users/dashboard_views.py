"""
Dashboard view for workspace overview
"""
from django.shortcuts import render
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.db.models import Max
from django.utils import timezone
from django.core.cache import cache
from django.http import JsonResponse
from datetime import timedelta
from chatbot.models import Chatroom, Message, Reminder
from users.models import Wallet
from users.decorators import workspace_required


@workspace_required
def dashboard(request):
    """
    Main dashboard view showing workspace stats and recent activity
    """
    user = request.user
    
    # Get user's chatrooms
    user_rooms = Chatroom.objects.filter(participants__User=user)
    
    # Calculate stats
    total_messages = Message.objects.filter(
        member__User=user
    ).count()
    
    active_rooms = user_rooms.count()
    
    # Reminders
    pending_reminders = Reminder.objects.filter(
        user=user,
        status='pending'
    ).count()
    
    upcoming_today = Reminder.objects.filter(
        user=user,
        status='pending',
        scheduled_time__date=timezone.now().date()
    ).count()
    
    # Wallet balance
    try:
        workspace = user.workspace
        wallet = workspace.wallet
        wallet_balance = wallet.balance
    except:
        wallet_balance = 0
    
    # Recent activity (last 7 days)
    week_ago = timezone.now() - timedelta(days=7)
    recent_messages = Message.objects.filter(
        member__User=user,
        timestamp__gte=week_ago
    ).order_by('-timestamp')[:10]
    
    recent_activity = []
    for msg in recent_messages:
        recent_activity.append({
            'icon': 'fas fa-comment',
            'icon_class': 'stat-icon-info',
            'text': f'New message in chatroom',
            'timestamp': msg.timestamp
        })
    
    # Add reminders to activity
    recent_reminders = Reminder.objects.filter(
        user=user,
        created_at__gte=week_ago
    ).order_by('-created_at')[:5]
    
    for reminder in recent_reminders:
        recent_activity.append({
            'icon': 'fas fa-bell',
            'icon_class': 'stat-icon-warning',
            'text': f'Reminder created: {reminder.content[:50]}...',
            'timestamp': reminder.created_at
        })
    
    # Sort by timestamp
    recent_activity.sort(key=lambda x: x['timestamp'], reverse=True)
    recent_activity = recent_activity[:10]
    
    context = {
        'total_messages': total_messages,
        'active_rooms': active_rooms,
        'pending_reminders': pending_reminders,
        'upcoming_today': upcoming_today,
        'wallet_balance': wallet_balance,
        'recent_activity': recent_activity,
    }
    
    return render(request, 'users/dashboard.html', context)


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
        display += f" +{len(other_members)-2}"
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
