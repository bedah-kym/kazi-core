"""Aggregate dashboard API.

Single DRF endpoint that returns everything the live dashboard widgets need:
workspace stats, wallet balance, quota usage, workflow run statuses and a
user-scoped activity feed. Consumed by dashboard.js via polling.
"""
from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from chatbot.models import Chatroom, Message, Reminder
from chatbot.notification_utils import get_unread_room_count
from notifications.models import Notification
from users.quota_service import QuotaService
from workflows.models import UserWorkflow, WorkflowExecution

ACTIVITY_LIMIT = 12


def _wallet_balance(user: User) -> dict:
    try:
        wallet = user.workspace.wallet
        return {
            "balance": str(wallet.balance),
            "currency": wallet.currency,
        }
    except Exception:
        return {"balance": "0.00", "currency": "KES"}


def _workflow_overview(user: User) -> dict:
    workflows = UserWorkflow.objects.filter(user=user)
    total = workflows.count()
    active = workflows.filter(status="active").count()
    executions = WorkflowExecution.objects.filter(workflow__user=user)

    recent = []
    for execution in executions.order_by("-started_at")[:5]:
        recent.append({
            "id": execution.id,
            "workflow": execution.workflow.name,
            "status": execution.status,
            "current_step": execution.current_step,
            "started_at": execution.started_at.isoformat() if execution.started_at else None,
        })

    return {
        "total": total,
        "active": active,
        "running": executions.filter(status__in=["pending", "running", "waiting"]).count(),
        "recent": recent,
    }


def _activity_feed(user: User) -> list:
    feed = []

    for message in Message.objects.filter(member__User=user).order_by("-timestamp")[:ACTIVITY_LIMIT]:
        feed.append({
            "kind": "message",
            "text": "New message in chatroom",
            "ts": message.timestamp.isoformat(),
        })

    for reminder in Reminder.objects.filter(user=user).order_by("-created_at")[:5]:
        feed.append({
            "kind": "reminder",
            "text": f"Reminder: {reminder.content[:60]}",
            "ts": reminder.created_at.isoformat(),
            "status": reminder.status,
        })

    for execution in WorkflowExecution.objects.filter(workflow__user=user).order_by("-started_at")[:5]:
        feed.append({
            "kind": "workflow",
            "text": f"Workflow \"{execution.workflow.name}\" {execution.status}",
            "ts": execution.started_at.isoformat() if execution.started_at else None,
            "status": execution.status,
        })

    feed = [item for item in feed if item.get("ts")]
    feed.sort(key=lambda item: item["ts"], reverse=True)
    return feed[:ACTIVITY_LIMIT]


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def dashboard_overview(request):
    user = request.user
    week_ago = timezone.now() - timedelta(days=7)

    unread_notifications = Notification.objects.filter(
        user=user, is_read=False, is_dismissed=False
    ).count()

    stats = {
        "total_messages": Message.objects.filter(member__User=user).count(),
        "active_rooms": Chatroom.objects.filter(participants__User=user).count(),
        "unread_rooms": get_unread_room_count(user),
        "pending_reminders": Reminder.objects.filter(user=user, status="pending").count(),
        "upcoming_today": Reminder.objects.filter(
            user=user, status="pending", scheduled_time__date=timezone.now().date()
        ).count(),
        "unread_notifications": unread_notifications,
        "messages_this_week": Message.objects.filter(
            member__User=user, timestamp__gte=week_ago
        ).count(),
    }

    return Response({
        "stats": stats,
        "wallet": _wallet_balance(user),
        "quota": QuotaService().get_user_quotas(user.id),
        "workflows": _workflow_overview(user),
        "activity": _activity_feed(user),
    })
