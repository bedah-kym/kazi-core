import ast

from celery.schedules import crontab as crontab_schedule
from celery.schedules import crontab_parser
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask


CRONTAB_FIELDS = (
    ("minute", 60, 0),
    ("hour", 24, 0),
    ("day_of_week", 7, 0),
    ("day_of_month", 31, 1),
    ("month_of_year", 12, 1),
)


def _cron_set(value, maximum: int, minimum: int) -> set:
    if isinstance(value, (set, frozenset, list, tuple)):
        return {int(item) for item in value}
    if isinstance(value, int):
        return {value}
    text = str(value).strip()
    if text.startswith("{") and text.endswith("}"):
        parsed = ast.literal_eval(text)
        return {int(item) for item in parsed}
    return set(crontab_parser(maximum, minimum).parse(text))


def _cron_fields_match(stored, declared) -> bool:
    try:
        return all(
            _cron_set(getattr(stored, name), maximum, minimum)
            == _cron_set(getattr(declared, name), maximum, minimum)
            for name, maximum, minimum in CRONTAB_FIELDS
        )
    except (TypeError, ValueError, SyntaxError):
        return False


def _schedule_matches(task, spec) -> bool:
    if isinstance(spec, (int, float)):
        interval = task.interval
        return (
            interval is not None
            and interval.every == int(round(float(spec)))
            and interval.period == IntervalSchedule.SECONDS
        )
    if isinstance(spec, crontab_schedule):
        cron = task.crontab
        return cron is not None and _cron_fields_match(cron, spec)
    return False


def beat_schedule_drift(desired=None) -> dict:
    """Compare the declared schedule with the database without writing.

    Return ``{"missing": [...], "extra": [...], "changed": [...]}``. A declared
    entry that exists but is disabled, points at a different task or carries a
    different schedule is ``changed``; an enabled database entry with no
    declaration is ``extra``.
    """
    desired = desired if desired is not None else settings.CELERY_BEAT_SCHEDULE
    rows = {
        row.name: row
        for row in PeriodicTask.objects.select_related("interval", "crontab").all()
    }
    missing = []
    changed = []
    for name, entry in desired.items():
        row = rows.get(name)
        if row is None:
            missing.append(name)
            continue
        if not row.enabled or row.task != entry["task"] or not _schedule_matches(row, entry["schedule"]):
            changed.append(name)
    extra = [
        name for name, row in rows.items()
        if row.enabled and name not in desired
    ]
    return {"missing": missing, "extra": extra, "changed": changed}


class Command(BaseCommand):
    help = (
        "Mirror settings.CELERY_BEAT_SCHEDULE into the django_celery_beat "
        "database scheduler. Idempotent: re-running converges to the declared "
        "schedule and disables database entries that are no longer in settings."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--check",
            action="store_true",
            help="Read-only: report drift and exit non-zero when the schedule differs.",
        )

    def handle(self, *args, **options):
        if options.get("check"):
            drift = beat_schedule_drift()
            drifted = drift["missing"] + drift["extra"] + drift["changed"]
            if drifted:
                raise CommandError(
                    "beat schedule drift: "
                    f"{len(drift['missing'])} missing, {len(drift['extra'])} extra, "
                    f"{len(drift['changed'])} changed ({', '.join(sorted(drifted))})"
                )
            self.stdout.write(self.style.SUCCESS("sync_beat_schedule: schedule is in sync."))
            return
        desired = settings.CELERY_BEAT_SCHEDULE
        seen = set()
        created = 0
        for name, entry in desired.items():
            defaults = {"task": entry["task"], "enabled": True}
            defaults.update(self._db_schedule(entry["schedule"]))
            _, was_created = PeriodicTask.objects.update_or_create(name=name, defaults=defaults)
            if was_created:
                created += 1
            seen.add(name)

        disabled = PeriodicTask.objects.exclude(name__in=seen).filter(enabled=True).update(enabled=False)

        self.stdout.write(self.style.SUCCESS(
            f"sync_beat_schedule: {len(seen)} declared entries ({created} newly created), "
            f"{disabled} undeclared entries disabled."
        ))

    def _db_schedule(self, spec):
        if isinstance(spec, (int, float)):
            every = int(round(float(spec)))
            interval, _ = IntervalSchedule.objects.get_or_create(
                every=every, period=IntervalSchedule.SECONDS,
            )
            return {"interval": interval, "crontab": None}
        if isinstance(spec, crontab_schedule):
            cron, _ = CrontabSchedule.objects.get_or_create(
                minute=str(spec.minute),
                hour=str(spec.hour),
                day_of_week=str(spec.day_of_week),
                day_of_month=str(spec.day_of_month),
                month_of_year=str(spec.month_of_year),
                timezone=settings.TIME_ZONE,
            )
            return {"crontab": cron, "interval": None}
        raise CommandError(f"Unsupported schedule type {type(spec).__name__} for beat entry")
