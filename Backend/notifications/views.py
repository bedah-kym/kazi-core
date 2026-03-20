"""REST API views for the unified notification system."""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Notification


@login_required
def notification_list(request):
    """
    GET /notifications/api/
    Query params: page, per_page (max 50), event_type, unread_only
    """
    qs = Notification.objects.filter(user=request.user, is_dismissed=False)

    event_type = request.GET.get("event_type")
    if event_type:
        qs = qs.filter(event_type=event_type)

    if request.GET.get("unread_only") == "true":
        qs = qs.filter(is_read=False)

    try:
        page = max(1, int(request.GET.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = min(max(1, int(request.GET.get("per_page", 20))), 50)
    except (TypeError, ValueError):
        per_page = 20

    start = (page - 1) * per_page
    notifications = list(qs[start : start + per_page])

    return JsonResponse(
        {
            "notifications": [
                {
                    "id": n.id,
                    "event_type": n.event_type,
                    "severity": n.severity,
                    "title": n.title,
                    "body": n.body,
                    "is_read": n.is_read,
                    "created_at": n.created_at.isoformat(),
                    "metadata": n.metadata,
                }
                for n in notifications
            ],
            "page": page,
            "has_more": qs.count() > start + per_page,
        }
    )


@login_required
def notification_counts(request):
    """
    GET /notifications/api/counts/
    Returns unified count + legacy counts for backward compat with notifications.js.
    """
    from chatbot.models import Reminder
    from chatbot.notification_utils import get_unread_room_count

    exclude_room_id = request.GET.get("exclude_room_id")
    try:
        exclude_room_id = int(exclude_room_id) if exclude_room_id else None
    except (TypeError, ValueError):
        exclude_room_id = None

    unread_notifications = Notification.objects.filter(
        user=request.user, is_read=False, is_dismissed=False
    ).count()

    return JsonResponse(
        {
            # Legacy fields (backward compat)
            "unread_rooms": get_unread_room_count(
                request.user, exclude_room_id=exclude_room_id
            ),
            "pending_reminders": Reminder.objects.filter(
                user=request.user, status="pending"
            ).count(),
            # New unified count
            "unread_notifications": unread_notifications,
        }
    )


@login_required
@require_POST
def mark_read(request, pk):
    """POST /notifications/api/<pk>/read/"""
    Notification.objects.filter(id=pk, user=request.user).update(
        is_read=True, read_at=timezone.now()
    )
    return JsonResponse({"status": "ok"})


@login_required
@require_POST
def mark_all_read(request):
    """POST /notifications/api/read-all/"""
    Notification.objects.filter(user=request.user, is_read=False).update(
        is_read=True, read_at=timezone.now()
    )
    return JsonResponse({"status": "ok"})


@login_required
@require_POST
def dismiss(request, pk):
    """POST /notifications/api/<pk>/dismiss/"""
    Notification.objects.filter(id=pk, user=request.user).update(is_dismissed=True)
    return JsonResponse({"status": "ok"})
