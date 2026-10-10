"""Presence for chat rooms.

The only owner of presence state: callers never see Redis commands, key names
or member strings. It uses Redis when the cache backend is Redis, and an
in-process store otherwise (single-process development and the hermetic tests).
Each of ``connect``, ``beat`` and ``disconnect`` is one Redis pipeline.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Dict, Optional, Set, Tuple

from asgiref.sync import sync_to_async
from django.conf import settings
from django_redis import get_redis_connection

from orchestration.model_catalog import available_models

logger = logging.getLogger(__name__)

# Following chatbot/dispatch.py: after a store error, stop touching the store
# for a while so an unreachable Redis cannot fill the thread pool.
BACKOFF_SECONDS = 30
_backoff_lock = threading.Lock()
_skip_until = 0.0

# In-process store, used when the cache backend is not Redis.
_local_rooms: Dict[int, Dict[str, float]] = {}
_local_users: Dict[int, float] = {}
_local_lock = threading.Lock()


def agent_status() -> str:
    """``"online"`` when a message sent now would reach a model, else ``"offline"``.

    The bot never opens a socket, so the web process declares its status rather
    than inferring it from a connection. A model is reachable when at least one
    catalog provider has its key configured — the same set the model picker and
    the agent loop use.
    """
    return "online" if available_models() else "offline"


def _now() -> float:
    """The clock. Tests replace this to advance time without sleeping."""
    return time.time()


def _window_seconds() -> int:
    """The expiry window, never shorter than two beats plus slack.

    A shorter window would drop a connected user every time one beat is late.
    """
    window = int(getattr(settings, "PRESENCE_WINDOW_SECONDS", 75))
    return max(window, 2 * beat_interval_seconds() + 15)


def beat_interval_seconds() -> int:
    return int(getattr(settings, "PRESENCE_BEAT_SECONDS", 30))


def _room_key(room_id) -> str:
    return f"presence:room:{room_id}"


def _user_key(user_id) -> str:
    return f"presence:user:{user_id}"


def _member(user_id, connection_id) -> str:
    return f"{user_id}:{connection_id}"


def _member_user_id(member) -> int:
    text = member.decode() if isinstance(member, bytes) else str(member)
    try:
        return int(text.split(":", 1)[0])
    except (TypeError, ValueError):
        return -1


def _redis_configured() -> bool:
    try:
        backend = str((settings.CACHES.get("default") or {}).get("BACKEND", ""))
    except Exception:
        return False
    return "RedisCache" in backend


def _redis_client():
    return get_redis_connection("default")


def _skipping() -> bool:
    with _backoff_lock:
        return _now() < _skip_until


def _trip_backoff(exc) -> None:
    global _skip_until
    with _backoff_lock:
        _skip_until = _now() + BACKOFF_SECONDS
    logger.warning(
        "Presence store unavailable (%s); skipping store calls for %ss",
        exc, BACKOFF_SECONDS,
    )


# --------------------------------------------------------------------------- #
#  Redis store                                                                #
# --------------------------------------------------------------------------- #

def _redis_apply(room_id, user_id, connection_id, remove: bool) -> Tuple[Set[int], int]:
    """One pipeline: change the member, trim, read, refresh expiry."""
    redis = _redis_client()
    now = _now()
    cutoff = now - _window_seconds()
    member = _member(user_id, connection_id)
    room_key = _room_key(room_id)

    pipe = redis.pipeline(transaction=True)
    if remove:
        pipe.zrem(room_key, member)
    else:
        pipe.zadd(room_key, {member: now})
    pipe.zremrangebyscore(room_key, "-inf", cutoff)
    pipe.zrange(room_key, cutoff, "+inf", byscore=True)
    pipe.expire(room_key, 2 * _window_seconds())
    if not remove:
        pipe.set(_user_key(user_id), 1, ex=_window_seconds())
    results = pipe.execute()

    fresh = results[2] or []
    online_ids = {_member_user_id(m) for m in fresh}
    user_count = sum(1 for m in fresh if _member_user_id(m) == user_id)
    return online_ids, user_count


def _redis_online(room_id) -> Set[int]:
    cutoff = _now() - _window_seconds()
    fresh = _redis_client().zrange(_room_key(room_id), cutoff, "+inf", byscore=True)
    return {_member_user_id(m) for m in (fresh or [])}


def _redis_user_online(user_id) -> bool:
    return bool(_redis_client().exists(_user_key(user_id)))


# --------------------------------------------------------------------------- #
#  In-process store                                                           #
# --------------------------------------------------------------------------- #

def _trim_local(room: Dict[str, float], cutoff: float) -> None:
    for stale in [m for m, score in room.items() if score < cutoff]:
        room.pop(stale, None)


def _local_apply(room_id, user_id, connection_id, remove: bool) -> Tuple[Set[int], int]:
    now = _now()
    cutoff = now - _window_seconds()
    member = _member(user_id, connection_id)
    with _local_lock:
        room = _local_rooms.setdefault(room_id, {})
        if remove:
            room.pop(member, None)
        else:
            room[member] = now
        _trim_local(room, cutoff)
        if room:
            online_ids = {_member_user_id(m) for m in room}
            user_count = sum(1 for m in room if _member_user_id(m) == user_id)
        else:
            _local_rooms.pop(room_id, None)
            online_ids = set()
            user_count = 0
        # As in the Redis store, a disconnect leaves the user's entry to expire:
        # the user may still be connected to another room.
        if not remove:
            _local_users[user_id] = now + _window_seconds()
        return online_ids, user_count


def _local_online(room_id) -> Set[int]:
    cutoff = _now() - _window_seconds()
    with _local_lock:
        room = _local_rooms.get(room_id, {})
        _trim_local(room, cutoff)
        return {_member_user_id(m) for m in room}


def _local_user_online(user_id) -> bool:
    now = _now()
    with _local_lock:
        expires = _local_users.get(user_id)
        if expires is None:
            return False
        if expires <= now:
            _local_users.pop(user_id, None)
            return False
        return True


# --------------------------------------------------------------------------- #
#  Interface                                                                  #
# --------------------------------------------------------------------------- #

async def connect(room_id, user_id, connection_id) -> Tuple[bool, Set[int]]:
    """Add this connection. Returns ``(was_first_connection, online_user_ids)``."""
    if _skipping():
        return False, set()
    try:
        if _redis_configured():
            online, count = await sync_to_async(_redis_apply, thread_sensitive=False)(
                room_id, user_id, connection_id, False,
            )
        else:
            online, count = _local_apply(room_id, user_id, connection_id, False)
    except Exception as exc:
        _trip_backoff(exc)
        return False, set()
    return count == 1, online


async def beat(room_id, user_id, connection_id) -> Optional[Set[int]]:
    """Refresh this connection and return the room's online user ids.

    ``None`` means the store could not be read. That is "unknown", not "nobody
    is online": the caller keeps what it last showed.
    """
    if _skipping():
        return None
    try:
        if _redis_configured():
            online, _count = await sync_to_async(_redis_apply, thread_sensitive=False)(
                room_id, user_id, connection_id, False,
            )
        else:
            online, _count = _local_apply(room_id, user_id, connection_id, False)
    except Exception as exc:
        _trip_backoff(exc)
        return None
    return online


async def disconnect(room_id, user_id, connection_id) -> bool:
    """Remove this connection. True when the user has no other fresh connection."""
    if _skipping():
        return False
    try:
        if _redis_configured():
            _online, count = await sync_to_async(_redis_apply, thread_sensitive=False)(
                room_id, user_id, connection_id, True,
            )
        else:
            _online, count = _local_apply(room_id, user_id, connection_id, True)
    except Exception as exc:
        _trip_backoff(exc)
        return False
    return count == 0


def online_user_ids(room_id) -> Set[int]:
    """The room's fresh user ids. Sync: for notification and task code."""
    if room_id is None or _skipping():
        return set()
    try:
        if _redis_configured():
            return _redis_online(room_id)
        return _local_online(room_id)
    except Exception as exc:
        _trip_backoff(exc)
        return set()


def is_user_online(user_id) -> bool:
    """True while the user has beaten inside the window. Sync: for tasks."""
    if not user_id or _skipping():
        return False
    try:
        if _redis_configured():
            return _redis_user_online(user_id)
        return _local_user_online(user_id)
    except Exception as exc:
        _trip_backoff(exc)
        return False
