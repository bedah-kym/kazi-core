"""Read-only beat-schedule drift checks (`sync_beat_schedule --check`)."""
from __future__ import annotations

from io import StringIO

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django_celery_beat.models import PeriodicTask

from workflows.management.commands.sync_beat_schedule import beat_schedule_drift


class SyncBeatScheduleCheckTests(TestCase):
    def _check(self):
        out = StringIO()
        call_command("sync_beat_schedule", "--check", stdout=out)
        return out.getvalue()

    def test_check_passes_after_a_sync(self):
        call_command("sync_beat_schedule", stdout=StringIO())
        self.assertIn("in sync", self._check())

    def test_check_is_read_only(self):
        call_command("sync_beat_schedule", stdout=StringIO())
        before = set(PeriodicTask.objects.values_list("name", "task", "enabled"))
        self._check()
        after = set(PeriodicTask.objects.values_list("name", "task", "enabled"))
        self.assertEqual(before, after)

    def test_a_missing_declared_entry_is_drift(self):
        call_command("sync_beat_schedule", stdout=StringIO())
        name = next(iter(settings.CELERY_BEAT_SCHEDULE))
        PeriodicTask.objects.filter(name=name).delete()
        self.assertIn(name, beat_schedule_drift()["missing"])
        with self.assertRaises(CommandError):
            self._check()

    def test_a_disabled_declared_entry_is_drift_without_writing(self):
        call_command("sync_beat_schedule", stdout=StringIO())
        name = next(iter(settings.CELERY_BEAT_SCHEDULE))
        PeriodicTask.objects.filter(name=name).update(enabled=False)
        self.assertIn(name, beat_schedule_drift()["changed"])
        with self.assertRaises(CommandError):
            self._check()
        self.assertFalse(PeriodicTask.objects.get(name=name).enabled)

    def test_an_enabled_rogue_entry_is_extra(self):
        call_command("sync_beat_schedule", stdout=StringIO())
        from django_celery_beat.models import IntervalSchedule

        interval = IntervalSchedule.objects.create(every=60, period=IntervalSchedule.SECONDS)
        PeriodicTask.objects.create(name="rogue-task", task="no.such.task", interval=interval)
        self.assertIn("rogue-task", beat_schedule_drift()["extra"])
        with self.assertRaises(CommandError):
            self._check()

    def test_a_changed_task_path_is_drift(self):
        call_command("sync_beat_schedule", stdout=StringIO())
        name = next(iter(settings.CELERY_BEAT_SCHEDULE))
        PeriodicTask.objects.filter(name=name).update(task="no.such.task")
        self.assertIn(name, beat_schedule_drift()["changed"])
