"""Run the Celery beat schedule inside the web process.

The lite profile has no Celery beat and no worker. This scheduler reads the same
``settings.CELERY_BEAT_SCHEDULE`` dict, claims each due run in the database, and
runs the task locally. The claim is one conditional ``UPDATE``, so it is atomic
on SQLite and Postgres: with several web workers exactly one of them lands the
update and runs the job. If the claim cannot be written (database error, missing
table) the run is skipped, never guessed.

Last-run times live in ``django_celery_beat.PeriodicTask.last_run_at``, so a
restart does not replay a daily job. The rows use the same names as the beat
entries, so switching a deployment between beat and this scheduler keeps its
history. Never run this alongside ``celery beat``: ``Backend.celery`` refuses to
start beat when the setting is on.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from importlib import import_module
from typing import Any, Callable, List, Mapping, Optional

from channels.db import database_sync_to_async
from django.conf import settings
from django.db.models import F
from django.utils import timezone
from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask

logger = logging.getLogger(__name__)

DEFAULT_TICK_SECONDS = 5.0
DEFAULT_STOP_TIMEOUT_SECONDS = 30.0
_RUNNER = Callable[[Mapping[str, Any]], Any]
_NOW = Callable[[], Any]


def is_due(schedule: Any, last_run_at: Any, now: Any) -> bool:
    """Return whether an interval has elapsed, including the exact boundary.

    Numeric schedules are seconds; timedeltas are also accepted. Other
    schedules use their own ``is_due`` clock rather than ``now``. Return
    False without a last-run time or if that schedule evaluation raises.
    """
    if last_run_at is None:
        return False
    seconds = None
    if isinstance(schedule, (int, float)):
        seconds = float(schedule)
    elif isinstance(schedule, timedelta):
        seconds = schedule.total_seconds()
    if seconds is not None:
        return now - last_run_at >= timedelta(seconds=seconds)
    try:
        return bool(schedule.is_due(last_run_at)[0])
    except Exception:
        logger.exception("In-process scheduler could not evaluate a schedule")
        return False


def _schedule_fields(schedule: Any) -> dict:
    """Persist a beat schedule and return its interval or crontab field dict.

    Numeric seconds and timedeltas are rounded to whole seconds. Raise
    ValueError for unsupported schedule types; database errors propagate.
    """
    seconds = None
    if isinstance(schedule, (int, float)):
        seconds = float(schedule)
    elif isinstance(schedule, timedelta):
        seconds = schedule.total_seconds()
    if seconds is not None:
        interval, _ = IntervalSchedule.objects.get_or_create(
            every=int(round(seconds)), period=IntervalSchedule.SECONDS
        )
        return {"interval": interval}
    if hasattr(schedule, "minute") and hasattr(schedule, "hour"):
        # Celery expands the cronspec sets (minute=0 becomes "{0}"), but beat
        # reads the original fields back from the row. from_schedule() stores
        # the unexpanded strings, so a row written here stays readable by beat.
        cron = CrontabSchedule.from_schedule(schedule)
        cron.save()
        return {"crontab": cron}
    raise ValueError(f"Unsupported schedule type {type(schedule).__name__}")


def run_task(entry: Mapping[str, Any]) -> Any:
    """Execute one scheduled task in this process, never through the broker.

    ``entry['task']`` is a dotted task path; optional args and kwargs default
    to empty collections. Return the eager result, which holds task failures.
    Missing task keys, module import errors, and attribute errors propagate.
    """
    module_path, _, attr = str(entry["task"]).rpartition(".")
    module = import_module(module_path)
    task = getattr(module, attr)
    return task.apply(
        args=entry.get("args", ()) or (),
        kwargs=entry.get("kwargs", {}) or {},
        throw=False,
    )


class InProcessScheduler:
    """A small asyncio loop that owns the beat schedule for one web process."""

    def __init__(
        self,
        schedules: Optional[Mapping[str, Mapping[str, Any]]] = None,
        *,
        runner: Optional[_RUNNER] = None,
        now: Optional[_NOW] = None,
        tick_seconds: float = DEFAULT_TICK_SECONDS,
        stop_timeout: float = DEFAULT_STOP_TIMEOUT_SECONDS,
    ) -> None:
        """Use the supplied schedules or a copy of CELERY_BEAT_SCHEDULE.

        Runner receives one schedule entry; now supplies the current datetime.
        They default to run_task and timezone.now. tick_seconds is the delay
        between ticks; stop_timeout bounds the shutdown wait in seconds.
        """
        self._schedules = dict(
            settings.CELERY_BEAT_SCHEDULE if schedules is None else schedules
        )
        self._runner = runner or run_task
        self._now = now or timezone.now
        self._tick_seconds = tick_seconds
        self._stop_timeout = stop_timeout
        self._stopping = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    def start(self) -> asyncio.Task:
        """Start the loop and return the task; the caller must keep it alive."""
        self._task = asyncio.create_task(self.run(), name="kazi-in-process-scheduler")
        return self._task

    async def stop(self) -> None:
        """Ask the loop to stop and wait a bounded time for a running job.

        A timeout returns without cancelling the job, which may finish later.
        """
        self._stopping.set()
        task = self._task
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=self._stop_timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "In-process scheduler job still running after %.1fs; "
                "shutdown continues without it",
                self._stop_timeout,
            )
        except asyncio.CancelledError:
            pass

    async def tick(self) -> List[str]:
        """Run one tick on the server's event loop.

        ``database_sync_to_async`` runs the ORM work in asgiref's
        thread-sensitive executor: it records this loop for nested
        ``async_to_sync`` calls (reminder group sends) and recycles database
        connections at both edges of the thread, like a request does.
        """
        return await database_sync_to_async(self.run_due)()

    async def run(self) -> None:
        """Tick until stop is requested, continuing after ordinary tick errors.

        Wait tick_seconds after each attempt; cancellation propagates.
        """
        logger.info("In-process scheduler started (%d jobs)", len(self._schedules))
        while not self._stopping.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("In-process scheduler tick failed")
            try:
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=self._tick_seconds
                )
            except asyncio.TimeoutError:
                pass
        logger.info("In-process scheduler stopped")

    def run_due(self, now: Optional[Any] = None) -> List[str]:
        """Claim due entries and return names attempted, including runner failures.

        Synchronous on purpose: the connection hygiene and event-loop binding
        live in ``tick()``, the background-thread path. Calling this directly
        (tests, an operator's one-off) uses the caller's connection untouched.
        ``now`` defaults to the configured clock. Claim failures skip the entry;
        runner errors are suppressed after the last-run time has advanced.
        """
        now = now or self._now()
        ran: List[str] = []
        for name, entry in self._schedules.items():
            if not self._claim(name, entry, now):
                continue
            try:
                self._runner(entry)
            except Exception:
                logger.exception("In-process scheduled task %s failed", name)
            ran.append(name)
        return ran

    def _claim(self, name: str, entry: Mapping[str, Any], now: Any) -> bool:
        """Return True only after atomically claiming an enabled, due entry.

        New rows and rows without a last-run time are initialized at now
        without running. A successful claim advances last_run_at and the run
        count before execution. Disabled or not-due entries, lost races, and
        claim errors return False.
        """
        try:
            row = PeriodicTask.objects.filter(name=name).only(
                "pk", "last_run_at", "enabled"
            ).first()
            if row is None:
                # No row yet: create one so a later switch to beat after
                # sync_beat_schedule keeps the history. The schedule mirrors
                # the settings entry; the first tick only records the start
                # time and does not run the job.
                defaults = {
                    "task": entry["task"],
                    "enabled": True,
                    "last_run_at": now,
                }
                defaults.update(_schedule_fields(entry["schedule"]))
                row, created = PeriodicTask.objects.get_or_create(
                    name=name, defaults=defaults
                )
                if created:
                    return False
            if not row.enabled:
                # An operator disabled this row in the admin; beat skips it,
                # so this scheduler must too. Saving a disabled row clears
                # last_run_at, and claiming it here would set it back and run
                # the job on the next tick.
                return False
            if row.last_run_at is None:
                PeriodicTask.objects.filter(
                    pk=row.pk, last_run_at__isnull=True
                ).update(last_run_at=now)
                return False
            if not is_due(entry["schedule"], row.last_run_at, now):
                return False
            updated = PeriodicTask.objects.filter(
                pk=row.pk, last_run_at=row.last_run_at
            ).update(last_run_at=now, total_run_count=F("total_run_count") + 1)
            return updated == 1
        except Exception:
            logger.exception(
                "In-process scheduler could not claim %s; skipping the run", name
            )
            return False
