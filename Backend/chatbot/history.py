"""Shared decrypt and history reads for chat turns (#216b-2, E12).

The consumer and the turn runner both need the same transcript: decrypt a
stored message with the room key, then slice history *after* excluding rows
that belong to other queued messages. Keeping that in one sync module means a
queued turn sees the same history whether it runs on the socket's thread or in
the runner.
"""
from __future__ import annotations

import json
import logging
from base64 import b64decode
from typing import Any, Dict, List, Optional, Tuple

from .kazi_messages import KAZI_USERNAME, room_cipher

logger = logging.getLogger(__name__)

DECRYPT_FAILED = "Error: Could not decrypt message."


def _normalize_base64(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip().replace(" ", "+")
    padding = 4 - (len(value) % 4)
    if padding < 4:
        value += "=" * padding
    return value


def decrypt_stored(room, message) -> Tuple[str, Dict[str, Any]]:
    """Return ``(content, payload)`` for a stored message; payload is ``{}`` when none.

    The result is cached on the instance so a loaded message is decrypted once.
    """
    cached = getattr(message, "_stored_parts", None)
    if cached is not None:
        return cached
    db_content = message.content
    parts: Tuple[str, Dict[str, Any]] = (db_content, {})
    try:
        parsed = json.loads(db_content)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    if isinstance(parsed, dict) and "data" in parsed and "nonce" in parsed:
        cipher = room_cipher(room)
        if cipher is None:
            parts = (DECRYPT_FAILED, {})
        else:
            try:
                nonce = _normalize_base64(parsed.get("nonce"))
                data = _normalize_base64(parsed.get("data"))
                if not nonce or not data:
                    raise ValueError("invalid payload")
                payload = json.loads(
                    cipher.decrypt(b64decode(nonce), b64decode(data), None).decode("utf-8")
                )
                if isinstance(payload, dict) and "content" in payload:
                    parts = (payload["content"], payload)
                else:
                    parts = (DECRYPT_FAILED, {})
            except Exception:
                parts = (DECRYPT_FAILED, {})
    message._stored_parts = parts
    return parts


def message_to_dict(room, message) -> Dict[str, Any]:
    content, _payload = decrypt_stored(room, message)
    return {
        "id": message.id,
        "member": message.member.User.username if message.member else "Unknown",
        "content": content,
        "timestamp": str(message.timestamp),
        "parent_id": message.parent_id,
    }


def get_history_rows(
    room,
    *,
    limit: int = 8,
    request_message_id: Optional[int] = None,
    exclude_message_id: Optional[int] = None,
) -> List[Tuple]:
    """Last ``limit`` rows for a turn, oldest first, as ``(id, name, content, extra)``.

    When ``request_message_id`` is given the exclusion happens in the query,
    before the slice: every row except a human message newer than the request
    is eligible. That keeps the previous turn's reply (a Kazi row, higher id)
    while dropping messages queued behind this one (human, higher id).
    """
    from django.db.models import Q

    if room is None:
        return []
    query = room.chats.select_related("member__User")
    if request_message_id is not None:
        # Humans only up to the request. Kazi's turn replies stay (the earlier
        # turn's answer has a higher id), but a newer harness notice about a
        # message queued behind this one does not (#216b-2 r2, item 12).
        query = query.filter(
            Q(id__lte=request_message_id)
            | Q(
                member__User__username=KAZI_USERNAME,
                turn_id__isnull=False,
            )
        )
    if exclude_message_id is not None:
        query = query.exclude(id=exclude_message_id)
    # Message ids break a timestamp tie so the newest row of one instant is the
    # last one after the reverse, never the first.
    messages = list(query.order_by("-timestamp", "-id")[:limit])
    messages.reverse()

    # Each message carries only the rows of its own segment, decrypted once, in
    # one metadata query and one kept-row query (P3-5). A Turn that paused has
    # several messages; attaching all of its rows to each would duplicate the
    # tool_use ids. Old messages have no rows and keep their reply payload.
    from orchestration.tool_calls import load_tool_calls_for_messages

    tool_calls_by_message = (
        load_tool_calls_for_messages(room, [msg.id for msg in messages])
        if any(msg.turn_id for msg in messages)
        else {}
    )

    rows: List[Tuple] = []
    for msg in messages:
        content, payload = decrypt_stored(room, msg)
        member = msg.member.User.username if msg.member else "Unknown"
        if not content or content == DECRYPT_FAILED or member == "system":
            continue
        payload = payload if isinstance(payload, dict) else {}
        origin = payload.get("origin") or ""
        tools = payload.get("tools")
        tool_calls = tool_calls_by_message.get(msg.id) or []
        extra = None
        if origin or isinstance(tools, list) or tool_calls:
            extra = {
                "tools": tools if isinstance(tools, list) else [],
                "tool_calls": tool_calls,
                "by": payload.get("by"),
                "model": payload.get("model"),
                "harness": payload.get("harness") or "",
                "origin": origin,
            }
        rows.append((msg.id, member, content, extra))
    return rows
