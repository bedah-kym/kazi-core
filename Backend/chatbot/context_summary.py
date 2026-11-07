"""Decide whether a room's context summary should be refreshed now.

The consumer calls this when a human message is saved; the turn runner calls
it again after the AI reply is persisted. Both need the same counter, so the
rule lives here rather than in either caller.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)


def schedule_context_summary(room_id, message_id) -> bool:
    """Queue a summary refresh when the room has crossed the threshold."""
    min_messages = getattr(settings, "CONTEXT_SUMMARY_MIN_MESSAGES", 6)
    min_minutes = getattr(settings, "CONTEXT_SUMMARY_MIN_MINUTES", 10)
    counter_key = f"context_summary:count:{room_id}"
    last_key = f"context_summary:last:{room_id}"
    now = timezone.now()

    count = (cache.get(counter_key) or 0) + 1
    cache.set(counter_key, count, timeout=60 * 60)

    should_schedule = count >= min_messages
    if not should_schedule:
        last_iso = cache.get(last_key)
        if last_iso:
            try:
                last_seen = timezone.datetime.fromisoformat(last_iso)
                if (now - last_seen).total_seconds() >= min_minutes * 60:
                    should_schedule = True
            except Exception:
                pass

    if not should_schedule:
        return False

    from .dispatch import dispatch_task
    from .tasks import refresh_room_context_summary

    if not dispatch_task(refresh_room_context_summary, room_id, message_id, count):
        return False

    cache.set(last_key, now.isoformat(), timeout=60 * 60 * 6)
    cache.delete(counter_key)
    return True
