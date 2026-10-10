"""Adaptive task state and action registry helpers."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict

from asgiref.sync import sync_to_async
from django.core.cache import cache

logger = logging.getLogger(__name__)

_CANCEL_RE = re.compile(r"\b(cancel|nevermind|never mind|stop|forget it|drop it|not now|pause)\b", re.IGNORECASE)


def is_cancel_request(message: str) -> bool:
    if not message:
        return False
    return bool(_CANCEL_RE.search(message))


def _task_cache_key(context: Dict[str, Any]) -> str:
    user_id = context.get("user_id") or "anon"
    room_id = context.get("room_id") or "room"
    return f"adaptive_task:{user_id}:{room_id}"


def _result_cache_key(context: Dict[str, Any], suffix: str) -> str:
    user_id = context.get("user_id") or "anon"
    room_id = context.get("room_id") or "room"
    return f"adaptive_results:{suffix}:{user_id}:{room_id}"


async def clear_task_state(context: Dict[str, Any]) -> None:
    key = _task_cache_key(context)
    try:
        await sync_to_async(cache.delete)(key)
    except Exception as exc:
        logger.warning("Adaptive task state delete failed: %s", exc)


async def clear_result_sets(context: Dict[str, Any]) -> None:
    try:
        await sync_to_async(cache.delete)(_result_cache_key(context, "last"))
        await sync_to_async(cache.delete)(_result_cache_key(context, "last_search"))
    except Exception as exc:
        logger.warning("Adaptive result set delete failed: %s", exc)
