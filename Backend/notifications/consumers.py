"""Per-user WebSocket consumer for real-time notification delivery."""
from __future__ import annotations

import logging

from channels.generic.websocket import AsyncJsonWebsocketConsumer

logger = logging.getLogger(__name__)


class NotificationConsumer(AsyncJsonWebsocketConsumer):
    """
    URL: ws/notifications/

    Separate from ChatConsumer so the chat logic stays untouched.
    Each authenticated user joins the group ``notifications_{user.id}``.
    Multiple browser tabs → same group → all receive events.
    """

    async def connect(self):
        if not self.scope["user"].is_authenticated:
            await self.close(code=4001)
            return

        self.user = self.scope["user"]
        self.group_name = f"notifications_{self.user.id}"

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        # Send initial unread count on connection
        from .services import NotificationService

        count = await NotificationService.aget_unread_count(self.user.id)
        await self.send_json({"type": "init", "unread_count": count})

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(
                self.group_name, self.channel_name
            )

    async def receive_json(self, content, **kwargs):
        """Handle client-side actions: mark_read, mark_all_read, dismiss."""
        from .services import NotificationService

        action = content.get("action")

        if action == "mark_read":
            nid = content.get("id")
            try:
                nid = int(nid)
            except (TypeError, ValueError):
                nid = None
            if nid:
                await NotificationService.amark_read(self.user.id, nid)
                unread_count = await NotificationService.aget_unread_count(self.user.id)
                await self.send_json(
                    {
                        "type": "ack",
                        "action": "mark_read",
                        "id": nid,
                        "unread_count": unread_count,
                    }
                )

        elif action == "mark_all_read":
            await NotificationService.amark_all_read(self.user.id)
            await self.send_json(
                {"type": "ack", "action": "mark_all_read", "unread_count": 0}
            )

        elif action == "dismiss":
            nid = content.get("id")
            try:
                nid = int(nid)
            except (TypeError, ValueError):
                nid = None
            if nid:
                await NotificationService.adismiss(self.user.id, nid)
                unread_count = await NotificationService.aget_unread_count(self.user.id)
                await self.send_json(
                    {
                        "type": "ack",
                        "action": "dismiss",
                        "id": nid,
                        "unread_count": unread_count,
                    }
                )

    # ------------------------------------------------------------------ #
    #  Group event handlers                                              #
    # ------------------------------------------------------------------ #

    async def notification_push(self, event):
        """Receives group_send from NotificationService._push_ws."""
        await self.send_json(
            {"type": "notification", **event["notification"]}
        )
