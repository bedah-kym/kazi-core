"""Preference mining (v0.6 L2, #152).

Every approve/deny is a labeled signal: N approvals with zero denials for an
action in a room earns a learned ``auto`` override; a single denial resets the
tally. Overrides only lower friction *inside* the existing approval seam — they
can never grant access a human couldn't approve once — and they are
room-scoped, reversible, and visible.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

LEARNED_OVERRIDES_KEY = "learned_approval_overrides"
_CACHE_TTL_SECONDS = 60
_DENY_STATUSES = {"rejected", "cancelled", "timed_out"}


def _min_approvals() -> int:
    try:
        from django.conf import settings
        return int(getattr(settings, "PREFERENCE_MINING_MIN_APPROVALS", 3) or 3)
    except Exception:
        return 3


def _window_days() -> int:
    try:
        from django.conf import settings
        return int(getattr(settings, "PREFERENCE_MINING_WINDOW_DAYS", 30) or 30)
    except Exception:
        return 30


def mining_enabled() -> bool:
    try:
        from django.conf import settings
        return bool(getattr(settings, "PREFERENCE_MINING_ENABLED", True))
    except Exception:
        return True


def mine_overrides(records: Iterable[Dict[str, Any]], min_approvals: int = 3) -> Dict[str, Dict[str, Dict[str, str]]]:
    """Pure tally: records -> ``{user_id: {room: {action: "auto"}}}``.

    Each record needs ``user_id``, ``room_id``, ``action`` and ``status``.
    A single deny for an (action, room, user) cancels any auto candidate.
    """
    tally: Dict[Any, Dict[str, int]] = defaultdict(lambda: {"approved": 0, "denied": 0})
    for record in records:
        status = str(record.get("status") or "")
        if status == "pending":
            continue
        key = (record.get("user_id"), str(record.get("room_id") if record.get("room_id") is not None else ""), str(record.get("action") or ""))
        if not key[2]:
            continue
        if status == "approved":
            tally[key]["approved"] += 1
        elif status in _DENY_STATUSES:
            tally[key]["denied"] += 1

    mined: Dict[str, Dict[str, Dict[str, str]]] = {}
    for (user_id, room, action), counts in tally.items():
        if counts["denied"] == 0 and counts["approved"] >= int(min_approvals):
            mined.setdefault(str(user_id), {}).setdefault(room, {})[action] = "auto"
    return mined


def _profile(user_id: Any):
    from django.contrib.auth import get_user_model

    User = get_user_model()
    user = User.objects.select_related("profile").filter(id=user_id).first()
    return getattr(user, "profile", None) if user else None


def _write_learned(user_id: Any, rooms: Dict[str, Dict[str, str]]) -> None:
    profile = _profile(user_id)
    if not profile:
        return
    prefs = dict(profile.notification_preferences or {})
    prefs[LEARNED_OVERRIDES_KEY] = rooms
    profile.notification_preferences = prefs
    profile.save(update_fields=["notification_preferences"])
    _invalidate(user_id)


def get_learned_overrides(user_id: Optional[int]) -> Dict[str, Dict[str, str]]:
    """Return ``{room: {action: policy}}`` learned overrides for a user."""
    if not user_id:
        return {}
    from django.core.cache import cache

    cache_key = f"learned_overrides:{user_id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    profile = _profile(user_id)
    value = dict((profile.notification_preferences or {}).get(LEARNED_OVERRIDES_KEY) or {}) if profile else {}
    cache.set(cache_key, value, _CACHE_TTL_SECONDS)
    return value


def clear_learned_overrides(user_id: Optional[int]) -> bool:
    """Reversal: drop every learned override for a user. Returns whether any existed."""
    if not user_id:
        return False
    profile = _profile(user_id)
    if not profile:
        return False
    prefs = dict(profile.notification_preferences or {})
    existed = bool(prefs.get(LEARNED_OVERRIDES_KEY))
    prefs.pop(LEARNED_OVERRIDES_KEY, None)
    profile.notification_preferences = prefs
    profile.save(update_fields=["notification_preferences"])
    _invalidate(user_id)
    return existed


def _invalidate(user_id: Any) -> None:
    try:
        from django.core.cache import cache
        cache.delete(f"learned_overrides:{user_id}")
    except Exception:
        pass


def effective_approval_overrides(user_id: Optional[int], room_id: Any) -> Dict[str, str]:
    """Learned ``auto`` overrides for one room (only ``auto`` is ever learned)."""
    learned = get_learned_overrides(user_id).get(str(room_id if room_id is not None else ""), {})
    return {action: policy for action, policy in learned.items() if policy == "auto"}


def merge_learned_overrides(preferences: Optional[Dict[str, Any]], user_id: Optional[int], room_id: Any) -> Dict[str, Any]:
    """Merge learned room overrides under the user's explicit overrides.

    Explicit user-set policies win, so mining can only *add* friction relief.
    """
    prefs = dict(preferences or {})
    explicit = dict(prefs.get("approval_overrides") or {})
    merged = dict(effective_approval_overrides(user_id, room_id))
    merged.update(explicit)
    prefs["approval_overrides"] = merged
    return prefs


def mine_approval_overrides(window_days: Optional[int] = None, min_approvals: Optional[int] = None) -> Dict[str, Any]:
    """Mine ``WorkflowApprovalRecord`` history and persist learned overrides."""
    from datetime import timedelta

    from django.utils import timezone

    from workflows.models import WorkflowApprovalRecord

    if not mining_enabled():
        return {"enabled": False}

    window = int(window_days or _window_days())
    threshold = int(min_approvals or _min_approvals())
    since = timezone.now() - timedelta(days=window)

    rows = (
        WorkflowApprovalRecord.objects
        .filter(kind="agent_loop", created_at__gte=since)
        .exclude(status="pending")
        .values("requested_by_id", "room_id", "action", "status")
    )
    records: List[Dict[str, Any]] = [
        {
            "user_id": row["requested_by_id"],
            "room_id": row["room_id"],
            "action": row["action"],
            "status": row["status"],
        }
        for row in rows
    ]

    mined = mine_overrides(records, threshold)
    for user_id, rooms in mined.items():
        _write_learned(user_id, rooms)
    return {
        "enabled": True,
        "threshold": threshold,
        "window_days": window,
        "records": len(records),
        "users": len(mined),
    }
