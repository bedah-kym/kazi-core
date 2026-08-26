from celery.schedules import crontab as crontab_schedule
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask


class Command(BaseCommand):
    help = (
        "Mirror settings.CELERY_BEAT_SCHEDULE into the django_celery_beat "
        "database scheduler. Idempotent: re-running converges to the declared "
        "schedule and disables database entries that are no longer in settings."
    )

    def handle(self, *args, **options):
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
