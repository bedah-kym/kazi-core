"""The one door every Kazi-authored message goes through (#219, T-A4).

A message the assistant writes is persisted with the room key, labelled with
where it came from, linked to its Turn when there is one, and broadcast as
``ai_message_saved``. Callers never build a Kazi ``Message`` themselves.

``origin`` tells the transcript how to replay the row: ``turn`` is the
assistant speaking, everything else (a reminder, an idle nudge, an ``@admin``
acknowledgement, a welcome) is a note the harness wrote.
"""
from __future__ import annotations

import json
import logging
import os
from base64 import b64decode, b64encode
from typing import Any, Dict, Optional

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.contrib.auth import get_user_model
from django.utils import timezone

from users.encryption import TokenEncryption

from .models import Chatroom, Member, Message

logger = logging.getLogger(__name__)
User = get_user_model()

KAZI_USERNAME = "kazi"
ORIGIN_TURN = "turn"
ORIGIN_REMINDER = "reminder"
ORIGIN_NUDGE = "nudge"
ORIGIN_ADMIN_ACK = "admin_ack"
ORIGIN_WELCOME = "welcome"
ORIGINS = frozenset({
    ORIGIN_TURN,
    ORIGIN_REMINDER,
    ORIGIN_NUDGE,
    ORIGIN_ADMIN_ACK,
    ORIGIN_WELCOME,
})

_KAZI_DEFAULTS = {
    "first_name": "Kazi",
    "last_name": "AI",
    "is_active": True,
    "email": "kazi@kwikchat.ai",
}


class KaziMessageError(Exception):
    """The message could not be persisted (the room key is unavailable)."""


def get_kazi_user():
    """The Kazi User, created with its bot defaults when missing."""
    return User.objects.get_or_create(username=KAZI_USERNAME, defaults=_KAZI_DEFAULTS)


def get_kazi_member() -> Member:
    """The Kazi Member, tolerating legacy duplicate Member rows."""
    kazi_user, _ = get_kazi_user()
    member = Member.objects.filter(User=kazi_user).first()
    if member is None:
        member = Member.objects.create(User=kazi_user)
    return member


def _encode_base64(data: bytes) -> str:
    return b64encode(data).decode("utf-8").rstrip("=") + "=" * (-len(data) % 4)


def room_cipher(room: Chatroom) -> Optional[AESGCM]:
    """The AES-GCM cipher for ``room``'s key, or None when the key is unusable."""
    key = getattr(room, "encryption_key", "") or ""
    if key.startswith("enc:"):
        key = TokenEncryption.safe_decrypt(key[4:], default=None)
    if not key:
        return None
    try:
        return AESGCM(b64decode(key.encode("utf-8")))
    except Exception as exc:
        logger.warning("Kazi message: room key could not be used (%s)", exc)
        return None


def _ensure_room_key(room: Chatroom) -> None:
    if not getattr(room, "encryption_key", ""):
        room.save()


def encrypt_for_room(room: Chatroom, payload: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Encrypt ``payload`` with ``room``'s key as the stored ``data``/``nonce`` pair."""
    cipher = room_cipher(room)
    if cipher is None:
        _ensure_room_key(room)
        cipher = room_cipher(room)
    if cipher is None:
        return None
    nonce = os.urandom(12)
    encrypted = cipher.encrypt(nonce, json.dumps(payload).encode("utf-8"), None)
    return {"data": _encode_base64(encrypted), "nonce": _encode_base64(nonce)}


def encrypt_content_for_room(room: Chatroom, content: str) -> Optional[Dict[str, str]]:
    """Encrypt a plain content string the way reminders and nudges always did."""
    return encrypt_for_room(room, {"content": content, "timestamp": str(timezone.now())})


def _default_frame(message: Message, content: str) -> Dict[str, Any]:
    return {
        "id": message.id,
        "member": KAZI_USERNAME,
        "content": content,
        "timestamp": str(message.timestamp),
        "parent_id": message.parent_id,
    }


def persist_kazi_message(
    room: Chatroom,
    *,
    content: str,
    origin: str,
    turn: Any = None,
    model_text: str = "",
    harness_text: str = "",
    tools: Any = None,
    by: Optional[int] = None,
    broadcast: bool = True,
    channel_layer: Any = None,
) -> Message:
    """Persist and broadcast one Kazi-authored message.

    ``origin`` is stored with the message so the transcript can decide whether
    it was the assistant speaking (``turn``) or a harness note. ``turn`` may be
    a Turn instance or its id. For a turn reply, ``model_text``, ``harness_text``,
    ``tools`` and ``by`` are stored exactly as the transcript expects them.
    """
    if origin not in ORIGINS:
        raise ValueError(f"unknown Kazi message origin: {origin!r}")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("a Kazi message needs content")

    payload: Dict[str, Any] = {
        "content": content,
        "timestamp": str(timezone.now()),
        "origin": origin,
    }
    if origin == ORIGIN_TURN:
        payload["model"] = model_text or ""
        if harness_text:
            payload["harness"] = harness_text
        if tools is not None:
            payload["tools"] = tools
        if by is not None:
            payload["by"] = by
    else:
        if harness_text:
            payload["harness"] = harness_text

    encrypted = encrypt_for_room(room, payload)
    if encrypted is None:
        raise KaziMessageError("the room has no usable encryption key")

    message = Message.objects.create(
        member=get_kazi_member(),
        content=json.dumps(encrypted),
        timestamp=timezone.now(),
        turn_id=getattr(turn, "pk", turn) or None,
    )
    room.chats.add(message)

    if broadcast:
        try:
            layer = channel_layer if channel_layer is not None else get_channel_layer()
            if layer is not None:
                async_to_sync(layer.group_send)(
                    f"chat_{room.id}",
                    {"type": "ai_message_saved", "message": _default_frame(message, content)},
                )
        except Exception as exc:
            logger.warning("Kazi message realtime push failed: %s", exc)
    return message
