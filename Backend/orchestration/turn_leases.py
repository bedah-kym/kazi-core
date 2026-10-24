"""Turn ownership leases and the one heartbeat per process (#216b-1).

A worker claims a Turn by writing its process owner id and a database-clock
lease. The heartbeat renews only the Turn ids this process still holds in
memory, so a leaked or hung row lapses instead of being kept alive forever.
The owner also fences itself before the lease can lapse, which stops it from
dispatching more work or touching a newer pause.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import threading
import time
import uuid
from datetime import timedelta

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db.models.functions import Now

from orchestration.models import Turn

logger = logging.getLogger(__name__)

# Mirrors orchestration.agent_loop.HARD_TURN_TIMEOUT_SECONDS. It is imported
# lazily at the one place that needs it so this module has no import cycle.
HARD_TURN_TIMEOUT_FALLBACK_SECONDS = 1800
HARD_TURN_MARGIN_SECONDS = 60

PROCESS_OWNER_ID = (
    f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex}"
)[:128]

_LIVE_TURNS: dict[str, float] = {}
_FENCED_TURNS: set[str] = set()
_REGISTRY_LOCK = threading.Lock()
_LAST_RENEWAL = 0.0
_HEARTBEAT_TASK: asyncio.Task | None = None
_MONOTONIC = time.monotonic


def _int_setting(name: str, default: int) -> int:
    try:
        value = int(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def lease_seconds() -> int:
    return _int_setting("TURN_LEASE_SECONDS", 90)


def heartbeat_seconds() -> int:
    return _int_setting("TURN_HEARTBEAT_SECONDS", 30)


def fence_margin_seconds() -> int:
    return _int_setting("TURN_LEASE_FENCE_MARGIN_SECONDS", 30)


def shutdown_grace_seconds() -> int:
    return _int_setting("TURN_SHUTDOWN_GRACE_SECONDS", 20)


def hard_timeout_seconds() -> int:
    try:
        from orchestration.agent_loop import HARD_TURN_TIMEOUT_SECONDS

        return int(HARD_TURN_TIMEOUT_SECONDS)
    except Exception:
        return HARD_TURN_TIMEOUT_FALLBACK_SECONDS


def lease_expiry():
    """DB ``Now()`` plus one lease; never the application host's clock."""
    return Now() + timedelta(seconds=lease_seconds())


def register_live_turn(turn_id) -> None:
    """Add this process's Turn to the heartbeat registry and start the beat."""
    tid = str(turn_id or "")
    if not tid:
        return
    with _REGISTRY_LOCK:
        _LIVE_TURNS[tid] = _MONOTONIC()
        _FENCED_TURNS.discard(tid)
        global _LAST_RENEWAL
        # A fresh registration starts a fresh fence clock, even if a previous
        # turn in this process left a stale renewal time behind.
        _LAST_RENEWAL = _MONOTONIC()
    ensure_heartbeat()


def unregister_live_turn(turn_id) -> None:
    tid = str(turn_id or "")
    if not tid:
        return
    with _REGISTRY_LOCK:
        _LIVE_TURNS.pop(tid, None)
        _FENCED_TURNS.discard(tid)


def live_turn_ids() -> list[str]:
    with _REGISTRY_LOCK:
        return list(_LIVE_TURNS)


def _renew_one(turn_id: str) -> bool:
    """Renew one registry Turn, never past the hard timeout. One UPDATE."""
    hard = hard_timeout_seconds()
    try:
        updated = (
            Turn.objects.filter(
                pk=turn_id,
                state__in=("running", "committing"),
                started_at__isnull=False,
                started_at__gt=Now() - timedelta(
                    seconds=hard + HARD_TURN_MARGIN_SECONDS,
                ),
            ).update(
                owner_id=PROCESS_OWNER_ID,
                lease_expires_at=lease_expiry(),
            )
        )
    except (ValueError, TypeError):
        return False
    return updated == 1


def beat_once(*, monotonic_now: float | None = None) -> int:
    """Renew every registered live Turn once and record the fence state.

    A Turn that is no longer active is dropped from the registry (it ended or
    paused). An active Turn that could not be renewed is fenced: the owner must
    stop before the lease lapses.
    """
    snapshot = live_turn_ids()
    if not snapshot:
        return 0
    now = _MONOTONIC() if monotonic_now is None else monotonic_now
    renewed = 0
    for turn_id in snapshot:
        if _renew_one(turn_id):
            renewed += 1
            continue
        active = Turn.objects.filter(
            pk=turn_id, state__in=("running", "committing"),
        ).exists()
        if active:
            with _REGISTRY_LOCK:
                _FENCED_TURNS.add(turn_id)
        else:
            with _REGISTRY_LOCK:
                _LIVE_TURNS.pop(turn_id, None)
                _FENCED_TURNS.discard(turn_id)
    if renewed:
        global _LAST_RENEWAL
        with _REGISTRY_LOCK:
            _LAST_RENEWAL = now
    return renewed


def _beat_and_close() -> int:
    """Run one beat and close this thread's DB connection (E2, E13)."""
    try:
        return beat_once()
    finally:
        try:
            from django.db import connection

            connection.close()
        except Exception:
            pass


def turn_live(turn_id) -> bool:
    """Whether this process may still act for ``turn_id``.

    Fail closed: an unregistered, fenced, or lapsed owner is not live.
    """
    tid = str(turn_id or "")
    if not tid:
        return False
    with _REGISTRY_LOCK:
        if tid not in _LIVE_TURNS:
            return False
        if tid in _FENCED_TURNS:
            return False
        last = _LAST_RENEWAL
    margin = fence_margin_seconds()
    budget = max(1, lease_seconds() - margin)
    return (_MONOTONIC() - last) <= budget


async def _heartbeat_loop() -> None:
    interval = max(1, heartbeat_seconds())
    while True:
        await asyncio.sleep(interval)
        if not live_turn_ids():
            continue
        try:
            await sync_to_async(_beat_and_close, thread_sensitive=False)()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Turn heartbeat beat failed", exc_info=True)


def ensure_heartbeat() -> asyncio.Task | None:
    """Start the one heartbeat task for the running event loop, if needed."""
    global _HEARTBEAT_TASK
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    current = _HEARTBEAT_TASK
    if current is not None and not current.done() and current.get_loop() is loop:
        return current
    _HEARTBEAT_TASK = loop.create_task(
        _heartbeat_loop(), name="kazi-turn-heartbeat",
    )
    return _HEARTBEAT_TASK


async def stop_heartbeat() -> None:
    global _HEARTBEAT_TASK
    task = _HEARTBEAT_TASK
    _HEARTBEAT_TASK = None
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def wait_for_idle(grace_seconds: int | None = None) -> bool:
    """Wait for this process's live turns to finish, bounded by the grace.

    Returns True when the registry emptied, False on timeout. The reaper is the
    backstop for anything still running after the grace.
    """
    grace = shutdown_grace_seconds() if grace_seconds is None else grace_seconds
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0, grace)
    while live_turn_ids() and loop.time() < deadline:
        await asyncio.sleep(0.1)
    return not live_turn_ids()
