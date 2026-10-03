"""Human-armed autopilot for the chat shell loop (2026-10 autonomy).

Autopilot is an explicit, time-boxed human decision: inside the window, safe and
bounded-local shell commands run without a per-command prompt. Destructive and
denied commands still gate, a tainted run's egress/sensitive step still gates,
and an unallowlisted network command still gates. Stored per ``(user, room)`` in
the profile (no migration). Default OFF. Reads are lock-free; writes lock the
profile row (see ``profile_store``).
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, Optional

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .profile_store import locked_profile, profile, read_key, room_key, write_key

logger = logging.getLogger(__name__)

AUTOPILOT_KEY = "shell_autopilot"


def _default_minutes() -> int:
    try:
        from django.conf import settings

        return max(1, int(getattr(settings, "SHELL_AUTOPILOT_MINUTES", 30) or 30))
    except (TypeError, ValueError):
        return 30


def _read(profile_obj) -> Dict[str, Any]:
    return read_key(profile_obj, AUTOPILOT_KEY)


def status(user_id: Optional[int], room_id: Any) -> Optional[Dict[str, Any]]:
    profile_obj = profile(user_id)
    if not profile_obj:
        return None
    return _read(profile_obj).get(room_key(room_id))


def entry_is_armed(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    parsed = parse_datetime(str(entry.get("expires_at") or ""))
    return bool(parsed and parsed > timezone.now())


def is_armed(user_id: Optional[int], room_id: Any) -> bool:
    return entry_is_armed(status(user_id, room_id))


def all_for_user(user_id: Optional[int]) -> Dict[str, Dict[str, Any]]:
    """Return ``{room_key: entry}`` across every room (ops UI)."""
    profile_obj = profile(user_id)
    if not profile_obj:
        return {}
    return {str(room): dict(entry or {}) for room, entry in _read(profile_obj).items()}


def arm(
    user_id: Optional[int],
    room_id: Any,
    *,
    minutes: Optional[int] = None,
    armed_by: Optional[int] = None,
) -> bool:
    if not user_id:
        return False
    mins = _default_minutes() if minutes is None else max(1, int(minutes))
    now = timezone.now()
    with locked_profile(user_id) as profile_obj:
        if not profile_obj:
            return False
        store = _read(profile_obj)
        store[room_key(room_id)] = {
            "armed_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=mins)).isoformat(),
            "armed_by": armed_by,
        }
        write_key(profile_obj, AUTOPILOT_KEY, store)
    return True


def disarm(user_id: Optional[int], room_id: Any) -> bool:
    if not user_id:
        return False
    with locked_profile(user_id) as profile_obj:
        if not profile_obj:
            return False
        store = _read(profile_obj)
        key = room_key(room_id)
        if key not in store:
            return False
        store.pop(key, None)
        write_key(profile_obj, AUTOPILOT_KEY, store)
    return True
