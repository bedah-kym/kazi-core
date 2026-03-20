"""
Central notification dispatch — the single entry point for all notification events.

Usage:
    from notifications.services import NotificationService
    NotificationService.notify(
        user=user,
        event_type='payment.deposit',
        title='Deposit Successful',
        body='500 KES credited to your wallet',
        severity='success',
        related_journal=journal,
    )
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.utils import timezone

from .models import Notification

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
#  Default preference matrix (used when user has no overrides)       #
# ------------------------------------------------------------------ #

DEFAULT_NOTIFY_MATRIX: Dict[str, Dict[str, bool]] = {
    "payment.deposit":    {"in_app": True, "email": True,  "whatsapp": False},
    "payment.withdrawal": {"in_app": True, "email": True,  "whatsapp": False},
    "payment.invoice":    {"in_app": True, "email": True,  "whatsapp": False},
    "payment.error":      {"in_app": True, "email": True,  "whatsapp": True},
    "reminder.due":       {"in_app": True, "email": True,  "whatsapp": False},
    "message.unread":     {"in_app": True, "email": False, "whatsapp": False},
    "message.mention":    {"in_app": True, "email": True,  "whatsapp": False},
    "system.info":        {"in_app": True, "email": False, "whatsapp": False},
    "system.warning":     {"in_app": True, "email": True,  "whatsapp": False},
}


class NotificationService:
    """Unified notification dispatch: preferences → DB → WebSocket → async email/WhatsApp."""

    # ------------------------------------------------------------------ #
    #  Public API                                                        #
    # ------------------------------------------------------------------ #

    @staticmethod
    def notify(
        user,
        event_type: str,
        title: str,
        body: str = "",
        severity: str = "info",
        *,
        related_invoice=None,
        related_journal=None,
        related_reminder=None,
        related_room=None,
        related_message=None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Notification]:
        """
        Central entry point.  All code paths call this instead of creating
        model instances directly.

        1. Load user's notify_matrix for *event_type*
        2. Create Notification row (if in_app enabled)
        3. Push via WebSocket
        4. Queue async email / WhatsApp delivery
        """
        prefs = NotificationService._get_channel_prefs(user, event_type)

        notification = None
        if prefs.get("in_app", True):
            notification = Notification.objects.create(
                user=user,
                event_type=event_type,
                severity=severity,
                title=title,
                body=body,
                related_invoice=related_invoice,
                related_journal=related_journal,
                related_reminder=related_reminder,
                related_room=related_room,
                related_message=related_message,
                metadata=metadata or {},
            )

        # Real-time WebSocket push
        if notification:
            NotificationService._push_ws(user, notification)

        # Queue external delivery
        if prefs.get("email", False):
            try:
                from notifications.tasks import deliver_notification_email

                deliver_notification_email.delay(
                    notification.id if notification else None,
                    user.id,
                    event_type,
                    title,
                    body,
                )
            except Exception as exc:
                logger.warning("Failed to queue notification email: %s", exc)

        if prefs.get("whatsapp", False):
            try:
                from notifications.tasks import deliver_notification_whatsapp

                deliver_notification_whatsapp.delay(
                    notification.id if notification else None,
                    user.id,
                    event_type,
                    title,
                    body,
                )
            except Exception as exc:
                logger.warning("Failed to queue notification whatsapp: %s", exc)

        return notification

    @staticmethod
    def notify_room_message(sender, room, message, room_group_name: str) -> None:
        """
        Notify offline room participants about a new message.
        Skips users who are currently online in the room (checked via Redis
        presence set) and applies a 5-minute debounce per user+room.
        """
        try:
            from django_redis import get_redis_connection

            redis = get_redis_connection("default")
        except Exception:
            return

        online_users = redis.smembers(f"online:{room_group_name}")
        online_usernames = {u.decode() if isinstance(u, bytes) else u for u in online_users}

        # Get room participants
        try:
            participants = room.participants.select_related("User").all()
        except Exception:
            return

        sender_username = sender.username if hasattr(sender, "username") else str(sender)

        for member in participants:
            member_user = member.User
            if member_user.username == sender_username:
                continue
            if member_user.username in online_usernames:
                continue

            # 5-minute debounce
            debounce_key = f"notif_debounce:msg:{member_user.id}:{room.id}"
            if redis.get(debounce_key):
                continue
            redis.setex(debounce_key, 300, "1")

            NotificationService.notify(
                user=member_user,
                event_type="message.unread",
                title=f"New message from {sender_username}",
                body="",  # Don't leak encrypted message content
                severity="info",
                related_room=room,
                related_message=message,
            )

    # ------------------------------------------------------------------ #
    #  Async helpers for the WebSocket consumer                          #
    # ------------------------------------------------------------------ #

    @staticmethod
    async def aget_unread_count(user_id: int) -> int:
        from asgiref.sync import sync_to_async

        return await sync_to_async(
            lambda: Notification.objects.filter(
                user_id=user_id, is_read=False, is_dismissed=False
            ).count()
        )()

    @staticmethod
    async def amark_read(user_id: int, notification_id: int) -> None:
        from asgiref.sync import sync_to_async

        await sync_to_async(
            lambda: Notification.objects.filter(
                id=notification_id, user_id=user_id
            ).update(is_read=True, read_at=timezone.now())
        )()

    @staticmethod
    async def amark_all_read(user_id: int) -> None:
        from asgiref.sync import sync_to_async

        await sync_to_async(
            lambda: Notification.objects.filter(
                user_id=user_id, is_read=False
            ).update(is_read=True, read_at=timezone.now())
        )()

    @staticmethod
    async def adismiss(user_id: int, notification_id: int) -> None:
        from asgiref.sync import sync_to_async

        await sync_to_async(
            lambda: Notification.objects.filter(
                id=notification_id, user_id=user_id
            ).update(is_dismissed=True)
        )()

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                  #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _get_channel_prefs(user, event_type: str) -> Dict[str, bool]:
        """Load the per-event channel matrix from the user's profile."""
        defaults = DEFAULT_NOTIFY_MATRIX.get(
            event_type, {"in_app": True, "email": False, "whatsapp": False}
        )
        try:
            profile = user.profile
            prefs = profile.notification_preferences or {}
            matrix = prefs.get("notify_matrix", {})
            event_prefs = matrix.get(event_type, {})
            return {channel: event_prefs.get(channel, default_val) for channel, default_val in defaults.items()}
        except Exception:
            return defaults

    @staticmethod
    def _push_ws(user, notification: Notification) -> None:
        """Push notification to the user's personal WebSocket channel group."""
        try:
            channel_layer = get_channel_layer()
            group_name = f"notifications_{user.id}"
            async_to_sync(channel_layer.group_send)(
                group_name,
                {
                    "type": "notification.push",
                    "notification": {
                        "id": notification.id,
                        "event_type": notification.event_type,
                        "severity": notification.severity,
                        "title": notification.title,
                        "body": notification.body,
                        "created_at": notification.created_at.isoformat(),
                        "metadata": notification.metadata,
                    },
                },
            )
            Notification.objects.filter(id=notification.id).update(delivered_ws=True)
        except Exception as exc:
            logger.debug("WebSocket push skipped (no consumer connected?): %s", exc)
