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

from django.conf import settings
from django.db.models import F
from django.utils import timezone
from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask

logger = logging.getLogger(__name__)

DEFAULT_TICK_SECONDS = 5.0
_RUNNER = Callable[[Mapping[str, Any]], Any]
_NOW = Callable[[], Any]


def is_due(schedule: Any, last_run_at: Any, now: Any) -> bool:
    """Return whether ``schedule`` is due at ``now`` given ``last_run_at``."""
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
    """The django_celery_beat schedule fields for a settings schedule."""
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
        cron, _ = CrontabSchedule.objects.get_or_create(
            minute=str(schedule.minute),
            hour=str(schedule.hour),
            day_of_week=str(schedule.day_of_week),
            day_of_month=str(schedule.day_of_month),
            month_of_year=str(schedule.month_of_year),
            timezone=settings.TIME_ZONE,
        )
        return {"crontab": cron}
    raise ValueError(f"Unsupported schedule type {type(schedule).__name__}")


def run_task(entry: Mapping[str, Any]) -> Any:
    """Execute one scheduled task in this process, never through the broker."""
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
    ) -> None:
        self._schedules = dict(
            settings.CELERY_BEAT_SCHEDULE if schedules is None else schedules
        )
        self._runner = runner or run_task
        self._now = now or timezone.now
        self._tick_seconds = tick_seconds
        self._stopping = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    def start(self) -> asyncio.Task:
        """Start the loop and return the task; the caller must keep it alive."""
        self._task = asyncio.create_task(self.run(), name="kazi-in-process-scheduler")
        return self._task

    async def stop(self) -> None:
        """Ask the loop to stop and wait for a running job to finish."""
        self._stopping.set()
        task = self._task
        if task is not None and not task.done():
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def run(self) -> None:
        logger.info("In-process scheduler started (%d jobs)", len(self._schedules))
        while not self._stopping.is_set():
            try:
                await asyncio.to_thread(self.run_due)
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
        """Claim and run every due entry once. Returns the names that ran."""
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
        try:
            row = PeriodicTask.objects.filter(name=name).only(
                "pk", "last_run_at"
            ).first()
            if row is None:
                # PeriodicTask clears last_run_at when disabled, so the row is
                # enabled. The schedule mirrors the settings entry: if the
                # deployment later switches to beat after sync_beat_schedule,
                # the row is already correct.
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
