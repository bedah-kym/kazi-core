"""Shared atomic access to the shell-autonomy profile store.

Grants and autopilot both live in ``notification_preferences`` under different
keys, and are written by the chat loop, the ops UI and the nightly sweep. A
read-modify-write must lock the profile row or the later save discards the
earlier change. SQLite (dev/tests) serializes writers; Postgres locks the row
with ``select_for_update``.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Optional

from django.db import transaction


def profile(user_id: Optional[int]):
    if not user_id:
        return None
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.select_related("profile").filter(id=user_id).first()
    return getattr(user, "profile", None) if user else None


def room_key(room_id: Any) -> str:
    return str(room_id if room_id is not None else "")


@contextmanager
def locked_profile(user_id: Optional[int]):
    """Yield the profile inside a transaction holding its user row lock."""
    if not user_id:
        yield None
        return
    from django.contrib.auth import get_user_model

    User = get_user_model()
    with transaction.atomic():
        # Lock only the user row: `select_for_update` cannot target the nullable
        # side of an outer join, so the profile is loaded separately below.
        user = User.objects.select_for_update().filter(id=user_id).first()
        yield getattr(user, "profile", None) if user else None


def read_key(profile_obj, key: str) -> dict:
    if not profile_obj:
        return {}
    return dict((profile_obj.notification_preferences or {}).get(key) or {})


def write_key(profile_obj, key: str, value: dict) -> None:
    if not profile_obj:
        return
    prefs = dict(profile_obj.notification_preferences or {})
    prefs[key] = value
    profile_obj.notification_preferences = prefs
    profile_obj.save(update_fields=["notification_preferences"])
