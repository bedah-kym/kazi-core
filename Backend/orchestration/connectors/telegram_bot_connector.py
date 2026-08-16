"""
Telegram Bot Connector — full-featured Telegram Bot API integration.

Set TELEGRAM_BOT_TOKEN in .env (get one from @BotFather on Telegram).
Then set the webhook:
  https://api.telegram.org/bot<TOKEN>/setWebhook?url=<YOUR_DOMAIN>/api/telegram/webhook/

Capabilities: send text, send media (photo/video/document/audio),
inline keyboards (callback + URL buttons), edit messages, delete messages,
check bot health.

Auto-discovered by connector_registry. No manual routing needed.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from ..base_connector import BaseConnector
from orchestration.contracts import build_orchestration_result

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"

# ---------------------------------------------------------------------------
# Connector class
# ---------------------------------------------------------------------------


class TelegramBotConnector(BaseConnector):
    """Send and receive messages via Telegram Bot API."""

    name = "telegram_bot"
    version = "2.0.0"
    actions = [
        "send_telegram_message",
        "send_telegram_media",
        "send_telegram_keyboard",
        "edit_telegram_message",
        "delete_telegram_message",
        "telegram_health",
    ]
    required_credentials: list[str] = ["TELEGRAM_BOT_TOKEN"]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __init__(self):
        super().__init__()
        self.token: Optional[str] = None
        self._client: Optional[httpx.AsyncClient] = None

    def _ensure_client(self) -> bool:
        """Lazy-init the HTTP client. Returns False if no token configured."""
        import os
        from django.conf import settings as django_settings

        if self._client is not None:
            return True

        self.token = (
            getattr(django_settings, 'TELEGRAM_BOT_TOKEN', None)
            or os.environ.get('TELEGRAM_BOT_TOKEN', '')
        )
        if not self.token:
            logger.warning(
                "TelegramBotConnector: TELEGRAM_BOT_TOKEN not set — cannot send messages"
            )
            return False

        self._client = httpx.AsyncClient(timeout=httpx.Timeout(20.0))
        return True

    # ------------------------------------------------------------------
    # Catalog entries (auto-registered by connector_registry)
    # ------------------------------------------------------------------

    def get_action_catalog_entries(self) -> list[dict]:
        return [
            {
                "action": "send_telegram_message",
                "aliases": ["telegram_send", "tg_message"],
                "service": "telegram",
                "description": (
                    "Send a text message via Telegram Bot API. Supports MarkdownV2 "
                    "formatting (bold, italic, code, links)."
                ),
                "params": {
                    "chat_id": {
                        "type": "string",
                        "required": True,
                        "description": "Telegram chat ID or @username",
                    },
                    "message": {
                        "type": "string",
                        "required": True,
                        "description": "Text content of the message (MarkdownV2 supported)",
                    },
                    "parse_mode": {
                        "type": "string",
                        "required": False,
                        "description": "One of: MarkdownV2, HTML, Markdown. Defaults to MarkdownV2.",
                    },
                },
                "return_description": "Returns message_id, chat_id, and status",
                "risk_level": "low",
                "confirmation_policy": "never",
            },
            {
                "action": "send_telegram_media",
                "aliases": ["tg_media", "telegram_photo", "telegram_document"],
                "service": "telegram",
                "description": (
                    "Send a photo, video, document, or audio file via Telegram Bot API. "
                    "Accepts a public URL or file_id."
                ),
                "params": {
                    "chat_id": {
                        "type": "string",
                        "required": True,
                        "description": "Telegram chat ID or @username",
                    },
                    "media_url": {
                        "type": "string",
                        "required": True,
                        "description": "Public URL of the media file, or a Telegram file_id",
                    },
                    "media_type": {
                        "type": "string",
                        "required": False,
                        "description": "One of: photo, video, document, audio. Defaults to photo.",
                    },
                    "caption": {
                        "type": "string",
                        "required": False,
                        "description": "Optional caption (MarkdownV2 supported)",
                    },
                },
                "return_description": "Returns message_id, chat_id, media_type, and status",
                "risk_level": "low",
                "confirmation_policy": "never",
            },
            {
                "action": "send_telegram_keyboard",
                "aliases": ["tg_keyboard", "telegram_buttons"],
                "service": "telegram",
                "description": (
                    "Send a message with an inline keyboard. Supports callback buttons "
                    "(return data to your webhook) and URL buttons (open a link)."
                ),
                "params": {
                    "chat_id": {
                        "type": "string",
                        "required": True,
                        "description": "Telegram chat ID or @username",
                    },
                    "text": {
                        "type": "string",
                        "required": True,
                        "description": "Message text above the keyboard",
                    },
                    "buttons": {
                        "type": "array",
                        "required": True,
                        "description": (
                            "Array of button rows. Each row is an array of button objects: "
                            '[{"text":"Label","callback_data":"action"} or '
                            '{"text":"Label","url":"https://..."}]'
                        ),
                    },
                },
                "return_description": "Returns message_id, chat_id, and status",
                "risk_level": "low",
                "confirmation_policy": "never",
            },
            {
                "action": "edit_telegram_message",
                "aliases": ["tg_edit"],
                "service": "telegram",
                "description": "Edit an existing message sent by the bot.",
                "params": {
                    "chat_id": {
                        "type": "string",
                        "required": True,
                        "description": "Telegram chat ID",
                    },
                    "message_id": {
                        "type": "integer",
                        "required": True,
                        "description": "ID of the message to edit",
                    },
                    "text": {
                        "type": "string",
                        "required": True,
                        "description": "New text for the message",
                    },
                },
                "return_description": "Returns message_id, chat_id, and status",
                "risk_level": "low",
                "confirmation_policy": "never",
            },
            {
                "action": "delete_telegram_message",
                "aliases": ["tg_delete"],
                "service": "telegram",
                "description": "Delete a message sent by the bot.",
                "params": {
                    "chat_id": {
                        "type": "string",
                        "required": True,
                        "description": "Telegram chat ID",
                    },
                    "message_id": {
                        "type": "integer",
                        "required": True,
                        "description": "ID of the message to delete",
                    },
                },
                "return_description": "Returns success status",
                "risk_level": "medium",
                "confirmation_policy": "high_risk",
            },
            {
                "action": "telegram_health",
                "aliases": ["tg_health", "tg_status"],
                "service": "telegram",
                "description": "Check whether the Telegram bot is configured and reachable.",
                "params": {},
                "return_description": "Returns bot username, can_read, can_send status",
                "risk_level": "low",
                "confirmation_policy": "never",
            },
        ]

    # ------------------------------------------------------------------
    # Config validation
    # ------------------------------------------------------------------

    def validate_config(self) -> tuple[bool, str]:
        """Check that the bot token is available."""
        import os
        from django.conf import settings as django_settings

        token = (
            getattr(django_settings, 'TELEGRAM_BOT_TOKEN', None)
            or os.environ.get('TELEGRAM_BOT_TOKEN', '')
        )
        if not token:
            return False, "TELEGRAM_BOT_TOKEN is not set"
        return True, "ok"

    # ------------------------------------------------------------------
    # Execute — main entry point
    # ------------------------------------------------------------------

    async def execute(
        self, parameters: Dict[str, Any], context: Dict[str, Any]
    ) -> Dict[str, Any]:
        if not self._ensure_client():
            return build_orchestration_result(
                status="error",
                action=parameters.get("action", "send_telegram_message"),
                clarification_prompt="TELEGRAM_BOT_TOKEN not configured",
            )

        action = parameters.get("action", "send_telegram_message")

        try:
            if action == "send_telegram_message":
                return await self._send_message(parameters)
            elif action == "send_telegram_media":
                return await self._send_media(parameters)
            elif action == "send_telegram_keyboard":
                return await self._send_keyboard(parameters)
            elif action == "edit_telegram_message":
                return await self._edit_message(parameters)
            elif action == "delete_telegram_message":
                return await self._delete_message(parameters)
            elif action == "telegram_health":
                return await self._health_check()
            else:
                return build_orchestration_result(
                    status="error",
                    action=action,
                    clarification_prompt=f"Unsupported Telegram action: {action}",
                )
        except httpx.HTTPError as exc:
            logger.error("Telegram API call failed for action=%s: %s", action, exc)
            return build_orchestration_result(
                status="error",
                action=action,
                clarification_prompt=f"Telegram API error: {exc}",
            )
        except Exception as exc:
            logger.error(
                "TelegramBotConnector execute failed: %s", exc, exc_info=True
            )
            return build_orchestration_result(
                status="error",
                action=action,
                clarification_prompt=str(exc),
            )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def _send_message(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Send a text message via sendMessage."""
        chat_id = params.get("chat_id")
        text = params.get("message") or params.get("text", "")
        parse_mode = params.get("parse_mode", "MarkdownV2")

        if not chat_id or not text:
            return build_orchestration_result(
                status="error",
                action="send_telegram_message",
                clarification_prompt="chat_id and message are required",
            )

        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": str(text)[:4096],
            "parse_mode": parse_mode,
        }

        data = await self._tg_post("sendMessage", payload)
        if not data.get("ok"):
            return build_orchestration_result(
                status="error",
                action="send_telegram_message",
                clarification_prompt=data.get("description", "Unknown Telegram error"),
            )

        msg = data["result"]
        return build_orchestration_result(
            status="success",
            action="send_telegram_message",
            data={
                "message_id": msg["message_id"],
                "chat_id": chat_id,
                "status": "sent",
            },
        )

    async def _send_media(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Send a photo, video, document, or audio file."""
        chat_id = params.get("chat_id")
        media_url = params.get("media_url")
        media_type = params.get("media_type", "photo")
        caption = params.get("caption", "")

        if not chat_id or not media_url:
            return build_orchestration_result(
                status="error",
                action="send_telegram_media",
                clarification_prompt="chat_id and media_url are required",
            )

        # Map media_type to Telegram method + file parameter
        method_map = {
            "photo": ("sendPhoto", "photo"),
            "video": ("sendVideo", "video"),
            "document": ("sendDocument", "document"),
            "audio": ("sendAudio", "audio"),
        }
        if media_type not in method_map:
            return build_orchestration_result(
                status="error",
                action="send_telegram_media",
                clarification_prompt=(
                    f"Unsupported media_type: {media_type}. "
                    "Use photo, video, document, or audio."
                ),
            )

        method, file_param = method_map[media_type]

        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            file_param: media_url,
        }
        if caption:
            payload["caption"] = str(caption)[:1024]
            payload["parse_mode"] = "MarkdownV2"

        data = await self._tg_post(method, payload)
        if not data.get("ok"):
            return build_orchestration_result(
                status="error",
                action="send_telegram_media",
                clarification_prompt=data.get("description", "Unknown Telegram error"),
            )

        msg = data["result"]
        return build_orchestration_result(
            status="success",
            action="send_telegram_media",
            data={
                "message_id": msg["message_id"],
                "chat_id": chat_id,
                "media_type": media_type,
                "status": "sent",
            },
        )

    async def _send_keyboard(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Send a message with an inline keyboard."""
        chat_id = params.get("chat_id")
        text = params.get("text", "")
        buttons = params.get("buttons") or params.get("inline_keyboard", [])

        if not chat_id or not text:
            return build_orchestration_result(
                status="error",
                action="send_telegram_keyboard",
                clarification_prompt="chat_id and text are required",
            )
        if not buttons or not isinstance(buttons, list):
            return build_orchestration_result(
                status="error",
                action="send_telegram_keyboard",
                clarification_prompt="buttons must be a non-empty array of button rows",
            )

        # Validate and normalize buttons
        inline_keyboard: List[List[Dict[str, str]]] = []
        for row in buttons:
            if not isinstance(row, list):
                continue
            normalized_row: List[Dict[str, str]] = []
            for btn in row:
                if not isinstance(btn, dict):
                    continue
                normalized: Dict[str, str] = {
                    "text": str(btn.get("text", ""))[:64],
                }
                if "callback_data" in btn:
                    normalized["callback_data"] = str(btn["callback_data"])[:64]
                elif "url" in btn:
                    normalized["url"] = str(btn["url"])
                else:
                    continue  # skip invalid buttons
                normalized_row.append(normalized)
            if normalized_row:
                inline_keyboard.append(normalized_row)

        if not inline_keyboard:
            return build_orchestration_result(
                status="error",
                action="send_telegram_keyboard",
                clarification_prompt="No valid buttons after normalization",
            )

        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": str(text)[:4096],
            "parse_mode": "MarkdownV2",
            "reply_markup": {"inline_keyboard": inline_keyboard},
        }

        data = await self._tg_post("sendMessage", payload)
        if not data.get("ok"):
            return build_orchestration_result(
                status="error",
                action="send_telegram_keyboard",
                clarification_prompt=data.get("description", "Unknown Telegram error"),
            )

        msg = data["result"]
        return build_orchestration_result(
            status="success",
            action="send_telegram_keyboard",
            data={
                "message_id": msg["message_id"],
                "chat_id": chat_id,
                "button_count": sum(len(row) for row in inline_keyboard),
                "status": "sent",
            },
        )

    async def _edit_message(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Edit an existing message (text only)."""
        chat_id = params.get("chat_id")
        message_id = params.get("message_id")
        text = params.get("text") or params.get("message", "")

        if not chat_id or not message_id or not text:
            return build_orchestration_result(
                status="error",
                action="edit_telegram_message",
                clarification_prompt="chat_id, message_id, and text are required",
            )

        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": int(message_id),
            "text": str(text)[:4096],
            "parse_mode": "MarkdownV2",
        }

        data = await self._tg_post("editMessageText", payload)
        if not data.get("ok"):
            return build_orchestration_result(
                status="error",
                action="edit_telegram_message",
                clarification_prompt=data.get("description", "Unknown Telegram error"),
            )

        msg = data["result"]
        return build_orchestration_result(
            status="success",
            action="edit_telegram_message",
            data={
                "message_id": msg["message_id"],
                "chat_id": chat_id,
                "status": "edited",
            },
        )

    async def _delete_message(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Delete a message."""
        chat_id = params.get("chat_id")
        message_id = params.get("message_id")

        if not chat_id or not message_id:
            return build_orchestration_result(
                status="error",
                action="delete_telegram_message",
                clarification_prompt="chat_id and message_id are required",
            )

        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": int(message_id),
        }

        data = await self._tg_post("deleteMessage", payload)
        if not data.get("ok"):
            return build_orchestration_result(
                status="error",
                action="delete_telegram_message",
                clarification_prompt=data.get("description", "Unknown Telegram error"),
            )

        return build_orchestration_result(
            status="success",
            action="delete_telegram_message",
            data={
                "message_id": int(message_id),
                "chat_id": chat_id,
                "status": "deleted",
            },
        )

    async def _health_check(self) -> Dict[str, Any]:
        """Check bot health via getMe."""
        if not self._ensure_client():
            return build_orchestration_result(
                status="error",
                action="telegram_health",
                clarification_prompt="TELEGRAM_BOT_TOKEN not configured",
            )

        try:
            data = await self._tg_post("getMe", {})
            if not data.get("ok"):
                return build_orchestration_result(
                    status="error",
                    action="telegram_health",
                    clarification_prompt=data.get("description", "Bot not reachable"),
                )

            bot = data["result"]
            return build_orchestration_result(
                status="success",
                action="telegram_health",
                data={
                    "bot_username": bot.get("username"),
                    "bot_name": bot.get("first_name"),
                    "bot_id": bot.get("id"),
                    "can_join_groups": bot.get("can_join_groups", False),
                    "can_read_all_group_messages": bot.get(
                        "can_read_all_group_messages", False
                    ),
                    "status": "healthy",
                },
            )
        except Exception as exc:
            return build_orchestration_result(
                status="error",
                action="telegram_health",
                clarification_prompt=str(exc),
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _tg_post(self, method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Call a Telegram Bot API method via POST."""
        url = f"{TELEGRAM_API}/bot{self.token}/{method}"
        resp = await self._client.post(url, json=payload)  # type: ignore[union-attr]
        resp.raise_for_status()
        return resp.json()

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
