"""T-F5: process weight.

Covers the sidecar's no-Django entry point, environment/settings parity, the
in-process scheduler, the fail-closed reminder claim, and the lite profile.
"""
import base64
import json
import os
import subprocess  # nosec B404 — test infra spawns fixed argv interpreters
import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import DatabaseError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from chatbot import tasks as chatbot_tasks
from chatbot.models import Chatroom, Reminder
from django_celery_beat.models import IntervalSchedule, PeriodicTask

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
User = get_user_model()


def _clean_env(**overrides):
    prefixes = (
        "DJANGO_", "KAZI_", "CELERY_", "REDIS_", "DATABASE_URL", "TEMPORAL_",
        "WEB_CONCURRENCY", "GUNICORN_WORKERS", "UVICORN_WORKERS", "ENCRYPTION_KEY",
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(prefixes)
    }
    env.update(overrides)
    return env


def _lite_env(**overrides):
    env = _clean_env(
        DJANGO_SETTINGS_MODULE="Backend.settings",
        DJANGO_SECRET_KEY="fake-secret-key-for-process-weight-tests",  # nosec B106 — test fixture
        DJANGO_DEBUG="False",
        DJANGO_CSRF_TRUSTED_ORIGINS="http://localhost:8000",
        KAZI_LITE_PROFILE="1",
        ENCRYPTION_KEY=base64.urlsafe_b64encode(b"k" * 32).decode("ascii"),
    )
    env.update(overrides)
    return env


class SidecarEntryTests(SimpleTestCase):
    def test_entry_module_imports_without_django(self):
        code = (
            "import sys\n"
            "import orchestration.shell_exec.__main__\n"
            "import orchestration.shell_exec.server\n"
            "print('django' in sys.modules)\n"
            "print('django.conf' in sys.modules)\n"
        )
        env = dict(os.environ)
        env.pop("DJANGO_SETTINGS_MODULE", None)
        proc = subprocess.run(  # nosec B603 — test infra spawns a fixed argv interpreter
            [sys.executable, "-c", code],
            cwd=str(BACKEND_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        self.assertEqual(proc.stdout.strip().splitlines(), ["False", "False"])


class ShellExecConfigParityTests(SimpleTestCase):
    @override_settings(
        SHELL_EXEC_ROOT=str(REPO_ROOT / "shell_workspaces"),
        SHELL_EXEC_TOKEN="fake-token",  # nosec B106 — test fixture
        SHELL_EXEC_HOST="127.0.0.1",
        SHELL_EXEC_PORT=9999,
        SHELL_EXEC_PROFILE="locked",
        SHELL_EXEC_PROFILES=["standard", "locked"],
        SHELL_EXEC_IMAGE="alpine:3.21",
        SHELL_EXEC_USER="1000:1000",
        SHELL_EXEC_MEMORY="512m",
        SHELL_EXEC_PIDS_LIMIT=256,
        SHELL_EXEC_TIMEOUT_DEFAULT=30,
        SHELL_EXEC_TIMEOUT_MAX=120,
        SHELL_EXEC_OUTPUT_BYTES_MAX=4096,
        SHELL_EXEC_NETWORK_ALLOWLIST=["example.com", "api.example.com"],
        SHELL_EGRESS_PROXY=True,
        SHELL_EGRESS_PROXY_IMAGE="kazi-egress-squid:2",
    )
    def test_environment_and_settings_build_the_same_config(self):
        from orchestration.shell_exec.backends import ShellExecConfig

        env = {
            "SHELL_EXEC_ROOT": str(REPO_ROOT / "shell_workspaces"),
            "SHELL_EXEC_TOKEN": "fake-token",  # nosec B105 — test fixture
            "SHELL_EXEC_HOST": "127.0.0.1",
            "SHELL_EXEC_PORT": "9999",
            "SHELL_EXEC_PROFILE": "locked",
            "SHELL_EXEC_PROFILES": "standard,locked",
            "SHELL_EXEC_IMAGE": "alpine:3.21",
            "SHELL_EXEC_USER": "1000:1000",
            "SHELL_EXEC_MEMORY": "512m",
            "SHELL_EXEC_PIDS_LIMIT": "256",
            "SHELL_EXEC_TIMEOUT_DEFAULT": "30",
            "SHELL_EXEC_TIMEOUT_MAX": "120",
            "SHELL_EXEC_OUTPUT_BYTES_MAX": "4096",
            "SHELL_EXEC_NETWORK_ALLOWLIST": "example.com,api.example.com",
            "SHELL_EGRESS_PROXY": "true",
            "SHELL_EGRESS_PROXY_IMAGE": "kazi-egress-squid:2",
        }
        with patch.dict(os.environ, env, clear=False):
            from_settings = ShellExecConfig.from_settings()
            from_env = ShellExecConfig.from_env()
        self.assertEqual(from_env, from_settings)


class InProcessSchedulerTests(TestCase):
    def setUp(self):
        from orchestration.in_process_scheduler import InProcessScheduler

        self.now = timezone.now()
        self.ran = []
        self.schedules = {
            "due-job": {
                "task": "tests.test_process_weight.noop",
                "schedule": 60.0,
            },
        }
        self.scheduler = InProcessScheduler(
            schedules=self.schedules,
            runner=lambda entry: self.ran.append(entry["task"]),
            now=lambda: self.now,
        )

    def _seed(self, last_run_at):
        interval, _ = IntervalSchedule.objects.get_or_create(
            every=60, period=IntervalSchedule.SECONDS
        )
        PeriodicTask.objects.update_or_create(
            name="due-job",
            defaults={
                "task": "tests.test_process_weight.noop",
                "enabled": True,
                "last_run_at": last_run_at,
                "interval": interval,
            },
        )

    def test_two_schedulers_ticking_together_run_the_job_once(self):
        from orchestration.in_process_scheduler import InProcessScheduler

        self._seed(self.now - timedelta(seconds=120))
        other_ran = []
        other = InProcessScheduler(
            schedules=self.schedules,
            runner=lambda entry: other_ran.append(entry["task"]),
            now=lambda: self.now,
        )
        self.scheduler.run_due(self.now)
        other.run_due(self.now)
        self.assertEqual(self.ran + other_ran, ["tests.test_process_weight.noop"])

    def test_last_run_is_durable_across_a_restart(self):
        from orchestration.in_process_scheduler import InProcessScheduler

        self._seed(self.now - timedelta(seconds=120))
        self.scheduler.run_due(self.now)
        restarted = InProcessScheduler(
            schedules=self.schedules,
            runner=lambda entry: self.ran.append(entry["task"]),
            now=lambda: self.now,
        )
        restarted.run_due(self.now)
        self.assertEqual(self.ran, ["tests.test_process_weight.noop"])

    def test_database_error_skips_the_run(self):
        with patch.object(
            PeriodicTask.objects,
            "get_or_create",
            side_effect=DatabaseError("database is down"),
        ):
            self.assertEqual(self.scheduler.run_due(self.now), [])
        self.assertEqual(self.ran, [])


@override_settings(IN_PROCESS_SCHEDULER=True)
class InProcessReminderDeliveryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="process-weight", email="pw@example.com", password="secret",  # nosec B106 — test fixture
        )
        self.room = Chatroom.objects.create()
        self.now = timezone.now()

    def test_due_reminder_is_delivered_by_the_in_process_scheduler(self):
        from orchestration.in_process_scheduler import InProcessScheduler

        reminder = Reminder.objects.create(
            user=self.user,
            room=self.room,
            content="Stretch",
            scheduled_time=self.now + timedelta(minutes=2),
            via_email=False,
            via_whatsapp=False,
        )
        Reminder.objects.filter(pk=reminder.pk).update(
            scheduled_time=self.now - timedelta(seconds=5)
        )
        interval, _ = IntervalSchedule.objects.get_or_create(
            every=30, period=IntervalSchedule.SECONDS
        )
        PeriodicTask.objects.update_or_create(
            name="check-due-reminders",
            defaults={
                "task": "chatbot.tasks.check_due_reminders",
                "enabled": True,
                "last_run_at": self.now - timedelta(seconds=60),
                "interval": interval,
            },
        )
        scheduler = InProcessScheduler(
            schedules={
                "check-due-reminders": {
                    "task": "chatbot.tasks.check_due_reminders",
                    "schedule": 30.0,
                },
            },
            now=lambda: self.now,
        )
        with patch("notifications.services.NotificationService.notify"):
            ran = scheduler.run_due(self.now)
        self.assertEqual(ran, ["check-due-reminders"])
        reminder.refresh_from_db()
        self.assertEqual(reminder.status, "sent")
        self.assertIsNotNone(reminder.sent_at)


class ReminderClaimTests(TestCase):
    def test_claim_is_granted_once(self):
        cache.delete("reminder_claim:9001")
        self.assertTrue(chatbot_tasks._claim_reminder_delivery(9001))
        self.assertFalse(chatbot_tasks._claim_reminder_delivery(9001))
        cache.delete("reminder_claim:9001")

    def test_claim_refuses_when_the_store_errors(self):
        with patch.object(chatbot_tasks.cache, "add", side_effect=RuntimeError("store down")):
            self.assertFalse(chatbot_tasks._claim_reminder_delivery(9002))

    def test_send_reminder_skips_when_the_claim_store_errors(self):
        user = User.objects.create_user(
            username="claim-store", email="cs@example.com", password="secret",  # nosec B106 — test fixture
        )
        reminder = Reminder.objects.create(
            user=user,
            content="Do not duplicate",
            scheduled_time=timezone.now() + timedelta(minutes=2),
        )
        Reminder.objects.filter(pk=reminder.pk).update(
            scheduled_time=timezone.now() - timedelta(seconds=5)
        )
        with patch.object(chatbot_tasks.cache, "add", side_effect=RuntimeError("store down")), \
             patch.object(chatbot_tasks, "_deliver_reminder") as deliver:
            result = chatbot_tasks.send_reminder.run(reminder.id)
        self.assertEqual(result, {"status": "skipped", "reason": "delivery_in_progress"})
        deliver.assert_not_called()


class LiteProfileTests(SimpleTestCase):
    _SETTINGS_CODE = (
        "import json\n"
        "import django\n"
        "django.setup()\n"
        "from django.conf import settings\n"
        "payload = {\n"
        "    'debug': settings.DEBUG,\n"
        "    'lite': settings.KAZI_LITE_PROFILE,\n"
        "    'in_process': settings.IN_PROCESS_SCHEDULER,\n"
        "    'cache': settings.CACHES['default']['BACKEND'],\n"
        "    'channels': settings.CHANNEL_LAYERS['default']['BACKEND'],\n"
        "    'temporal_disabled': settings.TEMPORAL_DISABLED,\n"
        "    'eager': settings.CELERY_TASK_ALWAYS_EAGER,\n"
        "    'reminder_sweep': settings.REMINDER_SWEEP_SECONDS,\n"
        "}\n"
        "print('LITE=' + json.dumps(payload))\n"
    )

    def _run(self, code, **overrides):
        env = _lite_env(**overrides)
        return subprocess.run(  # nosec B603 — test infra spawns a fixed argv interpreter
            [sys.executable, "-c", code],
            cwd=str(BACKEND_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )

    def test_lite_profile_is_selected_with_debug_off(self):
        proc = self._run(self._SETTINGS_CODE)
        self.assertEqual(proc.returncode, 0, proc.stderr[-4000:])
        line = next(
            (candidate for candidate in proc.stdout.splitlines() if candidate.startswith("LITE=")),
            None,
        )
        self.assertIsNotNone(line, proc.stdout[-2000:] + proc.stderr[-2000:])
        payload = json.loads(line[len("LITE="):])
        self.assertFalse(payload["debug"])
        self.assertTrue(payload["lite"])
        self.assertTrue(payload["in_process"])
        self.assertEqual(payload["cache"], "django.core.cache.backends.locmem.LocMemCache")
        self.assertEqual(payload["channels"], "channels.layers.InMemoryChannelLayer")
        self.assertTrue(payload["temporal_disabled"])
        self.assertTrue(payload["eager"])
        self.assertEqual(payload["reminder_sweep"], 30)

    def test_lite_profile_refuses_more_than_one_web_worker(self):
        proc = self._run("import Backend.asgi\n", WEB_CONCURRENCY="2")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("one web worker", proc.stdout + proc.stderr)
