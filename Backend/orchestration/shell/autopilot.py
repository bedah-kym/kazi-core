"""Human-armed window for the unsandboxed (`open`) shell profile.

Sandboxed profiles need no window: a command that stays inside the container
runs without asking and only an egress step prompts (see
``docs/plans/2026-10-shell-auto-mode.md``). The `open` profile has no boundary,
so its only autonomy is this explicit, time-boxed, receipted human decision.
Destructive and denied commands still gate inside the window.

Stored per ``(user, room)`` in the profile (no migration). Default OFF. Reads
are lock-free; writes lock the user row (see ``profile_store``).
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, Optional

from asgiref.sync import async_to_sync
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .profile_store import locked_profile, profile, read_key, room_key, write_key

logger = logging.getLogger(__name__)

AUTOPILOT_KEY = "shell_autopilot"
ARM_ACTION = "shell_autopilot_arm"
DISARM_ACTION = "shell_autopilot_disarm"


def _int_setting(name: str, default: int) -> int:
    try:
        from django.conf import settings

        return max(1, int(getattr(settings, name, default) or default))
    except (TypeError, ValueError):
        return default


def _default_minutes() -> int:
    return _int_setting("SHELL_AUTOPILOT_MINUTES", 30)


def _max_minutes() -> int:
    return _int_setting("SHELL_AUTOPILOT_MAX_MINUTES", 120)


def _read(profile_obj) -> Dict[str, Any]:
    return read_key(profile_obj, AUTOPILOT_KEY)


def can_arm(user_id: Optional[int], room_id: Any) -> bool:
    """Only a member of a room on the unsandboxed profile may arm it (fail closed)."""
    if not user_id or not room_id:
        return False
    try:
        from chatbot.models import Chatroom
        from orchestration.shell.profiles import resolve_profile
        from orchestration.user_preferences import get_user_preferences

        # Resolve exactly as the coordinator's risk gate does.
        if resolve_profile(room_id, get_user_preferences(user_id)).backend != "local":
            return False
        return Chatroom.objects.filter(id=room_id, participants__User_id=user_id).exists()
    except Exception as exc:
        logger.warning("Autopilot eligibility check failed for room %s: %s", room_id, exc)
        return False


def _receipt(user_id: int, room_id: Any, action: str, params: Dict[str, Any]) -> bool:
    from orchestration.action_receipts import record_action_receipt

    receipt = async_to_sync(record_action_receipt)(
        user_id=user_id,
        room_id=room_id,
        action=action,
        service="shell",
        params=params,
        result={},
        status="success",
    )
    return receipt is not None


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
    """Arm the window. Refused unless eligible and the receipt is written."""
    if not can_arm(user_id, room_id):
        return False
    try:
        mins = _default_minutes() if minutes is None else max(1, int(minutes))
    except (TypeError, ValueError):
        return False
    mins = min(mins, _max_minutes())
    now = timezone.now()
    expires_at = (now + timedelta(minutes=mins)).isoformat()
    with locked_profile(user_id) as profile_obj:
        if not profile_obj:
            return False
        if not _receipt(user_id, room_id, ARM_ACTION, {"minutes": mins, "expires_at": expires_at, "armed_by": armed_by}):
            return False
        store = _read(profile_obj)
        store[room_key(room_id)] = {
            "armed_at": now.isoformat(),
            "expires_at": expires_at,
            "armed_by": armed_by,
        }
        write_key(profile_obj, AUTOPILOT_KEY, store)
    return True


def disarm(user_id: Optional[int], room_id: Any, *, reason: str = "") -> bool:
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
    try:
        _receipt(user_id, room_id, DISARM_ACTION, {"reason": reason})
    except Exception as exc:
        logger.warning("Autopilot disarm receipt failed for room %s: %s", room_id, exc)
    return True
