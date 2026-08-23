"""Regression tests for the Celery Beat schedule.

The schedule dict was once silently replaced by a second definition later in
settings.py, dropping the reminder sweep and the deferred-workflow replay
watchdog from production with no error anywhere.
"""
from importlib import import_module

from django.conf import settings
from django.test import SimpleTestCase

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
