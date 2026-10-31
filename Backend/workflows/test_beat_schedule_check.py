"""Read-only beat-schedule drift checks (`sync_beat_schedule --check`)."""
from __future__ import annotations

from io import StringIO
from types import SimpleNamespace

from celery.schedules import crontab
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django_celery_beat.models import PeriodicTask

from workflows.management.commands.sync_beat_schedule import _schedule_matches, beat_schedule_drift


def _stored_cron(**fields):
    base = {
        "minute": "*", "hour": "*", "day_of_week": "*",
        "day_of_month": "*", "month_of_year": "*",
    }
    base.update(fields)
    return SimpleNamespace(interval=None, crontab=SimpleNamespace(**base))


class CronMeaningTests(TestCase):
    def test_compact_and_expanded_forms_match(self):
        declared = crontab(hour=2, minute=0)
        stored = _stored_cron(minute="0", hour="2", day_of_week="*")
        self.assertTrue(_schedule_matches(stored, declared))

    def test_star_and_a_full_range_match(self):
        declared = crontab(hour="*", minute=0)
        stored = _stored_cron(
            minute="0", hour="0-23", day_of_week="0-6",
        )
        self.assertTrue(_schedule_matches(stored, declared))

    def test_a_real_change_is_drift(self):
        declared = crontab(hour=2, minute=0)
        stored = _stored_cron(minute="0", hour="3", day_of_week="*")
        self.assertFalse(_schedule_matches(stored, declared))

    def test_expanded_set_text_matches_a_compact_row(self):
        declared = crontab(day_of_week="mon", hour=4, minute=30)
        stored = _stored_cron(minute="30", hour="4", day_of_week="{1}")
        self.assertTrue(_schedule_matches(stored, declared))


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

    def test_a_different_timezone_is_drift(self):
        call_command("sync_beat_schedule", stdout=StringIO())
        row = PeriodicTask.objects.select_related("crontab").get(
            name="nightly_ledger_reconciliation"
        )
        row.crontab.timezone = "UTC"
        row.crontab.save()
        self.assertIn("nightly_ledger_reconciliation", beat_schedule_drift()["changed"])

    def test_a_stored_crontab_without_timezone_is_skipped(self):
        declared = crontab(hour=2, minute=0)
        stored = SimpleNamespace(
            interval=None,
            crontab=SimpleNamespace(
                minute="0", hour="2", day_of_week="*",
                day_of_month="*", month_of_year="*",
            ),
        )
        self.assertTrue(_schedule_matches(stored, declared))

    def test_a_beat_rewritten_compact_row_is_not_drift(self):
        call_command("sync_beat_schedule", stdout=StringIO())
        row = PeriodicTask.objects.select_related("crontab").get(
            name="nightly_ledger_reconciliation"
        )
        cron = row.crontab
        cron.minute = "0"
        cron.hour = "2"
        cron.day_of_week = "*"
        cron.day_of_month = "*"
        cron.month_of_year = "*"
        cron.save()
        drift = beat_schedule_drift()
        self.assertEqual(drift, {"missing": [], "extra": [], "changed": []})
