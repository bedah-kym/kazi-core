"""Exact-command standing grants for the chat shell loop (2026-10 autonomy).

A grant is created only from an explicit human decision ("always allow") and
auto-runs the *identical* normalized command. It never covers a destructive or
denied command, and it is ignored on a tainted run's egress step. Grants live in
the user profile's ``notification_preferences`` (no new migration); the approval
history remains the audit trail. Reads are lock-free; writes lock the profile
row (see ``profile_store``).
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Any, Dict, Optional

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .profile_store import locked_profile, profile, read_key, room_key, write_key

logger = logging.getLogger(__name__)

GRANTS_KEY = "shell_command_grants"

# A command we can safely remember must be a single, plain command line.
_METACHAR_RE = re.compile(r"[;&|><`$()%\n\r\\]")
# Opaque interpreter wrappers (optionally path-qualified) can do anything, so
# they are never grantable regardless of the trailing flags.
_OPAQUE_RE = re.compile(
    r"(?:^|\s)(?:\S*/)?(?:python\d?|py|cmd(?:\.exe)?|powershell(?:\.exe)?|pwsh|bash|sh|zsh)"
    r"\s+(?:-c|/c|-e|--eval)\b",
    re.IGNORECASE,
)


def normalize_command(command: Any) -> str:
    return " ".join(str(command or "").strip().split())


def _lifetime_days() -> int:
    try:
        from django.conf import settings

        return max(1, int(getattr(settings, "SHELL_COMMAND_GRANT_LIFETIME_DAYS", 7) or 7))
    except (TypeError, ValueError):
        return 7


def fingerprint(command: Any, profile_name: str = "standard") -> Optional[str]:
    """Stable grant key for a command, or ``None`` when it is not grantable.

    ``None`` means "always ask": metacharacters or an opaque interpreter blob
    make the command's scope impossible to pin, so it can never be auto-run.
    Case is preserved on purpose — a grant for ``/Private`` must not auto-run
    ``/private``.
    """
    text = normalize_command(command)
    if not text:
        return None
    if _METACHAR_RE.search(text) or _OPAQUE_RE.search(text):
        return None
    from orchestration.shell.classifier import TIER_BOUNDED, TIER_SAFE, classify_command

    tier = classify_command(text, profile=profile_name).get("tier")
    if tier not in (TIER_SAFE, TIER_BOUNDED):
        return None
    return text


def _read_store(profile_obj) -> Dict[str, Any]:
    return read_key(profile_obj, GRANTS_KEY)


def _expired(entry: Dict[str, Any]) -> bool:
    raw = entry.get("expires_at")
    if not raw:
        return False
    parsed = parse_datetime(str(raw))
    return bool(parsed and parsed <= timezone.now())


def active_grants(user_id: Optional[int], room_id: Any) -> Dict[str, str]:
    """Return active ``{fingerprint: decision}`` for one room (fail-closed)."""
    profile_obj = profile(user_id)
    if not profile_obj:
        return {}
    room = _read_store(profile_obj).get(room_key(room_id)) or {}
    active: Dict[str, str] = {}
    for fp, entry in room.items():
        if isinstance(entry, dict) and not _expired(entry):
            active[str(fp)] = str(entry.get("decision") or "always_allow")
    return active


def create_grant(
    user_id: Optional[int],
    room_id: Any,
    command: Any,
    *,
    decision: str = "always_allow",
    days: Optional[int] = None,
    profile_name: str = "standard",
) -> Optional[str]:
    """Create or refresh a grant for one command. Returns the fingerprint."""
    fp = fingerprint(command, profile_name=profile_name)
    if fp is None or not user_id:
        return None
    lifetime = _lifetime_days() if days is None else max(1, int(days))
    now = timezone.now()
    with locked_profile(user_id) as profile_obj:
        if not profile_obj:
            return None
        store = _read_store(profile_obj)
        room = dict(store.get(room_key(room_id)) or {})
        room[fp] = {
            "decision": decision,
            "command": normalize_command(command),
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(days=lifetime)).isoformat(),
        }
        store[room_key(room_id)] = room
        write_key(profile_obj, GRANTS_KEY, store)
    return fp


def revoke_fingerprint(user_id: Optional[int], room_id: Any, fp: Optional[str]) -> bool:
    if not fp or not user_id:
        return False
    with locked_profile(user_id) as profile_obj:
        if not profile_obj:
            return False
        store = _read_store(profile_obj)
        room = dict(store.get(room_key(room_id)) or {})
        if fp not in room:
            return False
        room.pop(fp)
        if room:
            store[room_key(room_id)] = room
        else:
            store.pop(room_key(room_id), None)
        write_key(profile_obj, GRANTS_KEY, store)
    return True


def revoke_grant(user_id: Optional[int], room_id: Any, command: Any) -> bool:
    return revoke_fingerprint(user_id, room_id, normalize_command(command) or None)


def list_grants(user_id: Optional[int], room_id: Any) -> Dict[str, Dict[str, Any]]:
    profile_obj = profile(user_id)
    if not profile_obj:
        return {}
    return dict(_read_store(profile_obj).get(room_key(room_id)) or {})


def all_grants_for_user(user_id: Optional[int]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Return ``{room_key: {fingerprint: entry}}`` across every room (ops UI)."""
    profile_obj = profile(user_id)
    if not profile_obj:
        return {}
    return {
        str(room): dict(entries or {})
        for room, entries in _read_store(profile_obj).items()
    }


def entry_is_active(entry: Any) -> bool:
    return isinstance(entry, dict) and not _expired(entry)


def sweep_expired_grants() -> int:
    """Drop every expired grant for every user. Returns the count removed."""
    from django.contrib.auth import get_user_model

    removed = 0
    user_ids = list(get_user_model().objects.values_list("id", flat=True))
    for user_id in user_ids:
        with locked_profile(user_id) as profile_obj:
            if not profile_obj:
                continue
            store = _read_store(profile_obj)
            if not store:
                continue
            changed = False
            for rkey in list(store.keys()):
                room = dict(store.get(rkey) or {})
                keep = {fp: e for fp, e in room.items() if not (isinstance(e, dict) and _expired(e))}
                if len(keep) != len(room):
                    removed += len(room) - len(keep)
                    changed = True
                    if keep:
                        store[rkey] = keep
                    else:
                        store.pop(rkey, None)
            if changed:
                write_key(profile_obj, GRANTS_KEY, store)
    return removed
