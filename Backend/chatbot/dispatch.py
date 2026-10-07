"""Hand Celery tasks to the broker from async code without blocking the event loop.

``task.delay()`` is a synchronous network call. Made directly from a consumer
it freezes every connection while the broker is slow or unreachable, and in
eager mode it would run the task body, ORM and all, on the loop thread.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from django.db import close_old_connections
from kombu.exceptions import OperationalError

logger = logging.getLogger(__name__)

BACKOFF_SECONDS = 60
# Its own small pool: a broker that hangs must not use up the threads the
# event loop needs for DNS lookups and other executor work.
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="kazi-dispatch")
_lock = threading.Lock()
_skip_until = 0.0
_skipped = 0


def _skipping(task) -> bool:
    global _skipped
    with _lock:
        if time.monotonic() >= _skip_until:
            return False
        _skipped += 1
    logger.debug("Task %s skipped: the broker was unreachable a moment ago", getattr(task, "name", task))
    return True


def _publish(task, args, kwargs) -> None:
    global _skip_until, _skipped
    if _skipping(task):
        return
    name = getattr(task, "name", task)
    try:
        task.apply_async(args=args, kwargs=kwargs, retry=False)
    except (OperationalError, OSError) as exc:
        with _lock:
            _skip_until = time.monotonic() + BACKOFF_SECONDS
        logger.warning("Task %s was not dispatched (%s); skipping dispatch for %ss", name, exc, BACKOFF_SECONDS)
    except Exception as exc:
        logger.warning("Task %s was not dispatched (%s)", name, exc)
    else:
        with _lock:
            skipped, _skipped, _skip_until = _skipped, 0, 0.0
        if skipped:
            logger.warning("Dispatch resumed; %s task(s) were skipped while the broker was unreachable", skipped)


def _publish_from_pool(task, args, kwargs) -> None:
    try:
        _publish(task, args, kwargs)
    finally:
        close_old_connections()


def dispatch_task(task, *args, **kwargs) -> bool:
    """Queue ``task`` and return at once. Never raises.

    False means dispatch is being skipped after a recent broker failure. True
    means the task was accepted, not that it was published: one still waiting
    in the pool when a publish fails is dropped and counted as skipped.
    """
    if _skipping(task):
        return False
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        _publish(task, args, kwargs)
        return True
    try:
        _executor.submit(_publish_from_pool, task, args, kwargs)
    except RuntimeError:
        return False
    return True
