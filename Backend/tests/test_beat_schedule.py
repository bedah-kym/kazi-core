"""Regression tests for the Celery Beat schedule.

The schedule dict was once silently replaced by a second definition later in
settings.py, dropping the reminder sweep and the deferred-workflow replay
watchdog from production with no error anywhere.

Since the DatabaseScheduler adoption, settings.CELERY_BEAT_SCHEDULE is mirrored
into django_celery_beat by `manage.py sync_beat_schedule`; these tests also pin
that mirror (every enabled DB entry must exist in the settings dict).
"""
from importlib import import_module
from io import StringIO

from django.conf import settings
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django_celery_beat.models import PeriodicTask

_OPTIONAL_DEPS = ("intasend",)


class BeatScheduleTests(SimpleTestCase):
    def test_every_scheduled_task_imports(self):
        for name, entry in settings.CELERY_BEAT_SCHEDULE.items():
            task_path = entry["task"]
            module_path, _, attr = task_path.rpartition(".")
            try:
                module = import_module(module_path)
            except ImportError as exc:
                if any(dep in str(exc).lower() for dep in _OPTIONAL_DEPS):
                    self.skipTest(f"optional dependency missing for {name}: {exc}")
                raise
            self.assertTrue(
                hasattr(module, attr),
                f"Beat entry '{name}' points at missing task {task_path}",
            )

    def test_reminder_sweep_is_scheduled(self):
        self.assertIn("check-due-reminders", settings.CELERY_BEAT_SCHEDULE)

    def test_replay_watchdog_is_scheduled_when_temporal_enabled(self):
        if settings.TEMPORAL_DISABLED:
            self.skipTest("TEMPORAL_DISABLED")
        self.assertIn("replay-deferred-workflows", settings.CELERY_BEAT_SCHEDULE)

    def test_no_duplicate_task_paths(self):
        tasks = [entry["task"] for entry in settings.CELERY_BEAT_SCHEDULE.values()]
        self.assertEqual(len(tasks), len(set(tasks)))


class SyncBeatScheduleTests(TestCase):
    def _sync(self):
        out = StringIO()
        call_command("sync_beat_schedule", stdout=out)
        return out.getvalue()

    def test_sync_creates_every_declared_entry_enabled(self):
        self._sync()
        for name, entry in settings.CELERY_BEAT_SCHEDULE.items():
            task = PeriodicTask.objects.get(name=name)
            self.assertEqual(task.task, entry["task"])
            self.assertTrue(task.enabled)
            self.assertTrue(task.interval or task.crontab)

    def test_sync_is_idempotent(self):
        self._sync()
        snapshot = {
            (t.name, t.task, t.enabled) for t in PeriodicTask.objects.all()
        }
        output = self._sync()
        self.assertEqual(
            snapshot,
            {(t.name, t.task, t.enabled) for t in PeriodicTask.objects.all()},
        )
        self.assertIn("0 undeclared entries disabled", output)

    def test_undeclared_db_entries_get_disabled(self):
        from django_celery_beat.models import IntervalSchedule

        interval = IntervalSchedule.objects.create(every=60, period=IntervalSchedule.SECONDS)
        rogue = PeriodicTask.objects.create(
            name="rogue-ghost-task", task="no.such.task", interval=interval,
        )
        self._sync()
        rogue.refresh_from_db()
        self.assertFalse(rogue.enabled)

    def test_enabled_db_tasks_are_subset_of_settings_dict(self):
        self._sync()
        declared = set(settings.CELERY_BEAT_SCHEDULE)
        enabled = set(
            PeriodicTask.objects.filter(enabled=True).values_list("name", flat=True)
        )
        self.assertLessEqual(enabled, declared)
