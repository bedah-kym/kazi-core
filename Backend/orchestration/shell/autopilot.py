"""Human-armed autopilot for the chat shell loop (2026-10 autonomy).

Autopilot is an explicit, time-boxed human decision: inside the window, safe and
bounded-local shell commands run without a per-command prompt. Destructive and
denied commands still gate, and a tainted run's egress/sensitive step still
gates. Stored per ``(user, room)`` in the profile (no migration). Default OFF.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, Optional

from django.utils import timezone
from django.utils.dateparse import parse_datetime

logger = logging.getLogger(__name__)

AUTOPILOT_KEY = "shell_autopilot"


def _default_minutes() -> int:
    try:
        from django.conf import settings

        return max(1, int(getattr(settings, "SHELL_AUTOPILOT_MINUTES", 30) or 30))
    except (TypeError, ValueError):
        return 30


def _profile(user_id: Optional[int]):
    if not user_id:
        return None
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.select_related("profile").filter(id=user_id).first()
    return getattr(user, "profile", None) if user else None


def _room_key(room_id: Any) -> str:
    return str(room_id if room_id is not None else "")


def _read(profile) -> Dict[str, Any]:
    return dict((profile.notification_preferences or {}).get(AUTOPILOT_KEY) or {}) if profile else {}


def _write(profile, store: Dict[str, Any]) -> None:
    prefs = dict(profile.notification_preferences or {})
    prefs[AUTOPILOT_KEY] = store
    profile.notification_preferences = prefs
    profile.save(update_fields=["notification_preferences"])


def status(user_id: Optional[int], room_id: Any) -> Optional[Dict[str, Any]]:
    profile = _profile(user_id)
    if not profile:
        return None
    return _read(profile).get(_room_key(room_id))


def entry_is_armed(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    parsed = parse_datetime(str(entry.get("expires_at") or ""))
    return bool(parsed and parsed > timezone.now())


def is_armed(user_id: Optional[int], room_id: Any) -> bool:
    return entry_is_armed(status(user_id, room_id))


def all_for_user(user_id: Optional[int]) -> Dict[str, Dict[str, Any]]:
    """Return ``{room_key: entry}`` across every room (ops UI)."""
    profile = _profile(user_id)
    if not profile:
        return {}
    return {str(room): dict(entry or {}) for room, entry in _read(profile).items()}


def arm(
    user_id: Optional[int],
    room_id: Any,
    *,
    minutes: Optional[int] = None,
    armed_by: Optional[int] = None,
) -> bool:
    profile = _profile(user_id)
    if not profile:
        return False
    mins = _default_minutes() if minutes is None else max(1, int(minutes))
    now = timezone.now()
    store = _read(profile)
    store[_room_key(room_id)] = {
        "armed_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=mins)).isoformat(),
        "armed_by": armed_by,
    }
    _write(profile, store)
    return True


def disarm(user_id: Optional[int], room_id: Any) -> bool:
    profile = _profile(user_id)
    if not profile:
        return False
    store = _read(profile)
    key = _room_key(room_id)
    if key not in store:
        return False
    store.pop(key, None)
    _write(profile, store)
    return True
