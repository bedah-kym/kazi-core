"""
Dashboard view for workspace overview
"""
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.utils import timezone
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
