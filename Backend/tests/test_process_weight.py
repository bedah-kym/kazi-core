"""T-F5: process weight.

Covers the sidecar's no-Django entry point, environment/settings parity, the
in-process scheduler, the fail-closed reminder claim, and the lite profile.
"""
import asyncio
import base64
import json
import os
import subprocess  # nosec B404 — test infra spawns fixed argv interpreters
import sys
import threading
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import DatabaseError
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from chatbot import tasks as chatbot_tasks
from chatbot.consumers import ChatConsumer
from chatbot.models import Chatroom, Member, Message, Reminder
from django_celery_beat.models import IntervalSchedule, PeriodicTask
from orchestration.coordinator import OrchestrationResult

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
User = get_user_model()


def _clean_env(**overrides):
    prefixes = (
        "DJANGO_", "KAZI_", "CELERY_", "REDIS_", "DATABASE_URL", "TEMPORAL_",
        "WEB_CONCURRENCY", "GUNICORN_WORKERS", "UVICORN_WORKERS", "ENCRYPTION_KEY",
        "HF_API_TOKEN", "MODERATION_ENABLED",
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


class ShellExecTokenPrecedenceTests(SimpleTestCase):
    def test_process_environment_token_beats_dotenv_in_the_sidecar_loader(self):
        from orchestration.shell_exec.backends import ShellExecConfig
        from orchestration.shell_exec.server import load_environment

        def fake_dotenv(*args, **kwargs):
            os.environ["SHELL_EXEC_TOKEN"] = "stale-token"  # nosec B105 — test fixture
            os.environ["SHELL_EXEC_HOST"] = "dotenv-host"

        env = {
            "SHELL_EXEC_TOKEN": "rotated-token",  # nosec B105 — test fixture
            "SHELL_EXEC_HOST": "process-host",
        }
        with patch.dict(os.environ, env, clear=False), \
             patch("dotenv.load_dotenv", side_effect=fake_dotenv):
            load_environment()
            config = ShellExecConfig.from_env()
            loaded_token = os.environ["SHELL_EXEC_TOKEN"]
            loaded_host = os.environ["SHELL_EXEC_HOST"]

        self.assertEqual(loaded_token, "rotated-token")
        self.assertEqual(config.token, "rotated-token")
        self.assertEqual(loaded_host, "dotenv-host")

    def test_run_shell_exec_uses_the_process_environment_token(self):
        code = (
            "import os, sys, types\n"
            "os.environ['SHELL_EXEC_TOKEN'] = 'rotated-token'\n"
            "fake = types.ModuleType('dotenv')\n"
            "def _load(*args, **kwargs):\n"
            "    os.environ['SHELL_EXEC_TOKEN'] = 'stale-token'\n"
            "fake.load_dotenv = _load\n"
            "sys.modules['dotenv'] = fake\n"
            "import django\n"
            "django.setup()\n"
            "from django.core.management import call_command\n"
            "from unittest.mock import patch\n"
            "seen = {}\n"
            "def _serve(config, host=None, port=None):\n"
            "    seen['token'] = config.token\n"
            "with patch('orchestration.shell_exec.server.serve', _serve):\n"
            "    call_command('run_shell_exec')\n"
            "print('TOKEN=' + seen['token'])\n"
        )
        env = _clean_env(
            DJANGO_SETTINGS_MODULE="Backend.settings",
            DJANGO_SECRET_KEY="fake-secret-key-for-token-tests",  # nosec B106 — test fixture
            DJANGO_DEBUG="True",
            DJANGO_ALLOWED_HOSTS="localhost,127.0.0.1",
            ENCRYPTION_KEY=base64.urlsafe_b64encode(b"t" * 32).decode("ascii"),
        )
        proc = subprocess.run(  # nosec B603 — test infra spawns a fixed argv interpreter
            [sys.executable, "-c", code],
            cwd=str(BACKEND_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-4000:])
        self.assertIn("TOKEN=rotated-token", proc.stdout)


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

    def test_a_disabled_row_does_not_run_even_when_overdue(self):
        last_run_at = self.now - timedelta(seconds=120)
        self._seed(last_run_at)
        PeriodicTask.objects.filter(name="due-job").update(enabled=False)

        self.assertEqual(self.scheduler.run_due(self.now), [])
        self.assertEqual(self.ran, [])
        row = PeriodicTask.objects.get(name="due-job")
        self.assertEqual(row.last_run_at, last_run_at)

    def test_admin_disabling_a_row_keeps_it_disabled(self):
        self._seed(self.now - timedelta(seconds=120))
        row = PeriodicTask.objects.get(name="due-job")
        row.enabled = False
        row.save()
        self.assertIsNone(row.last_run_at)

        self.assertEqual(self.scheduler.run_due(self.now), [])
        self.assertEqual(
            self.scheduler.run_due(self.now + timedelta(seconds=120)), []
        )
        self.assertEqual(self.ran, [])
        row.refresh_from_db()
        self.assertFalse(row.enabled)
        self.assertIsNone(row.last_run_at)

    def test_a_real_crontab_entry_is_stored_unexpanded(self):
        from orchestration.in_process_scheduler import _schedule_fields

        entry = settings.CELERY_BEAT_SCHEDULE["check-workflow-health"]
        cron = _schedule_fields(entry["schedule"])["crontab"]
        cron.refresh_from_db()
        self.assertEqual(cron.minute, "0")
        self.assertEqual(cron.hour, "*")
        self.assertEqual(cron.day_of_week, "*")
        self.assertEqual(cron.day_of_month, "*")
        self.assertEqual(cron.month_of_year, "*")


class SchedulerThreadPathTests(SimpleTestCase):
    def test_tick_runs_the_job_through_the_connection_cleaning_path(self):
        from orchestration.in_process_scheduler import InProcessScheduler

        scheduler = InProcessScheduler(
            schedules={},
            runner=lambda entry: None,
            now=timezone.now,
        )

        with patch("channels.db.close_old_connections") as recycle:
            asyncio.run(scheduler.tick())

        self.assertEqual(recycle.call_count, 2)

    def test_stop_bounds_the_wait_for_a_running_job(self):
        from orchestration.in_process_scheduler import InProcessScheduler

        started = threading.Event()
        release = threading.Event()

        def runner(entry):
            started.set()
            release.wait(2)

        scheduler = InProcessScheduler(
            schedules={"slow": {"task": "noop", "schedule": 60.0}},
            runner=runner,
            now=timezone.now,
            tick_seconds=60.0,
            stop_timeout=0.01,
        )

        async def drive():
            with patch("channels.db.close_old_connections"), \
                 patch.object(scheduler, "_claim", return_value=True):
                scheduler.start()
                for _ in range(100):
                    if started.is_set():
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(started.is_set())
                loop = asyncio.get_running_loop()
                before = loop.time()
                with self.assertLogs(
                    "orchestration.in_process_scheduler", level="WARNING"
                ) as logs:
                    await scheduler.stop()
                elapsed = loop.time() - before
                release.set()
                await asyncio.sleep(0)
                return elapsed, logs.output

        elapsed, output = asyncio.run(drive())
        self.assertLess(elapsed, 1.0)
        self.assertIn("still running", "\n".join(output))


class BeatRefusalTests(SimpleTestCase):
    def test_beat_signal_refuses_to_start_when_the_scheduler_is_on(self):
        import Backend.celery  # noqa: F401 — connect the real receiver
        from celery.signals import beat_init

        with override_settings(IN_PROCESS_SCHEDULER=True):
            with self.assertRaises(SystemExit) as raised:
                beat_init.send(sender=object())

        self.assertIn("IN_PROCESS_SCHEDULER", str(raised.exception))

    def test_beat_signal_allows_start_when_the_scheduler_is_off(self):
        import Backend.celery  # noqa: F401 — connect the real receiver
        from celery.signals import beat_init

        with override_settings(IN_PROCESS_SCHEDULER=False):
            beat_init.send(sender=object())


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


@override_settings(
    IN_PROCESS_SCHEDULER=True,
    CHANNEL_LAYERS={
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"},
    },
)
class InProcessReminderConsumerTests(TransactionTestCase):
    def test_scheduler_reminder_wakes_a_connected_consumer(self):
        from channels.layers import get_channel_layer
        from orchestration.in_process_scheduler import InProcessScheduler

        user = User.objects.create_user(
            username="process-weight-consumer",
            email="pwc@example.com",
            password="secret",  # nosec B106 — test fixture
        )
        room = Chatroom.objects.create()
        now = timezone.now()
        reminder = Reminder.objects.create(
            user=user,
            room=room,
            content="Stretch",
            scheduled_time=now + timedelta(minutes=2),
            via_email=True,
            via_whatsapp=True,
        )
        Reminder.objects.filter(pk=reminder.pk).update(
            scheduled_time=now - timedelta(seconds=5)
        )
        interval, _ = IntervalSchedule.objects.get_or_create(
            every=30, period=IntervalSchedule.SECONDS
        )
        PeriodicTask.objects.update_or_create(
            name="check-due-reminders",
            defaults={
                "task": "chatbot.tasks.check_due_reminders",
                "enabled": True,
                "last_run_at": now - timedelta(seconds=60),
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
            now=lambda: now,
        )

        async def drive():
            layer = get_channel_layer()
            channel = await layer.new_channel()
            await layer.group_add(f"chat_{room.id}", channel)
            received = asyncio.create_task(layer.receive(channel))
            with patch("notifications.services.NotificationService.notify"), \
                 patch.object(chatbot_tasks, "_is_user_online", return_value=True):
                ran = await scheduler.tick()
            message = await asyncio.wait_for(received, timeout=1.0)
            return ran, message

        ran, message = async_to_sync(drive)()
        self.assertEqual(ran, ["check-due-reminders"])
        self.assertEqual(message["type"], "ai_message_saved")
        self.assertIn("Reminder: Stretch", message["message"]["content"])


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
        "    'moderation': settings.MODERATION_ENABLED,\n"
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
        self.assertFalse(payload["moderation"])

    def test_lite_profile_refuses_explicit_moderation(self):
        proc = self._run("import django; django.setup()\n", MODERATION_ENABLED="1")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("moderation needs the Redis profile", proc.stdout + proc.stderr)

    def test_lite_profile_disables_moderation_implied_by_hf_token(self):
        proc = self._run(self._SETTINGS_CODE, HF_API_TOKEN="fake-token")  # nosec B106 — test fixture
        self.assertEqual(proc.returncode, 0, proc.stderr[-4000:])
        line = next(
            (candidate for candidate in proc.stdout.splitlines() if candidate.startswith("LITE=")),
            None,
        )
        self.assertIsNotNone(line, proc.stdout[-2000:] + proc.stderr[-2000:])
        payload = json.loads(line[len("LITE="):])
        self.assertFalse(payload["moderation"])
        self.assertIn(
            "moderation is disabled in the lite profile",
            proc.stdout + proc.stderr,
        )

    def test_lite_profile_refuses_more_than_one_web_worker(self):
        proc = self._run("import Backend.asgi\n", WEB_CONCURRENCY="2")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("one web worker", proc.stdout + proc.stderr)

    def test_one_machine_trial_can_use_plain_http_on_loopback(self):
        code = (
            "import django\n"
            "django.setup()\n"
            "from django.conf import settings\n"
            "print('SECURE_SSL_REDIRECT=%s' % settings.SECURE_SSL_REDIRECT)\n"
            "print('SESSION_COOKIE_SECURE=%s' % settings.SESSION_COOKIE_SECURE)\n"
            "print('CSRF_COOKIE_SECURE=%s' % settings.CSRF_COOKIE_SECURE)\n"
            "print('SECURE_HSTS_SECONDS=%s' % settings.SECURE_HSTS_SECONDS)\n"
        )
        proc = self._run(
            code,
            KAZI_INSECURE_LOCALHOST="1",
            DJANGO_ALLOWED_HOSTS="localhost,127.0.0.1",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-4000:])
        self.assertIn("SECURE_SSL_REDIRECT=False", proc.stdout)
        self.assertIn("SESSION_COOKIE_SECURE=False", proc.stdout)
        self.assertIn("CSRF_COOKIE_SECURE=False", proc.stdout)
        self.assertIn("SECURE_HSTS_SECONDS=0", proc.stdout)

    def test_one_machine_trial_refuses_a_public_host(self):
        proc = self._run(
            "import django; django.setup()\n",
            KAZI_INSECURE_LOCALHOST="1",
            DJANGO_ALLOWED_HOSTS="example.com",
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("one-machine trial", proc.stderr)


class TravelOptionalDependencyTests(SimpleTestCase):
    def test_missing_aiohttp_returns_an_error_instead_of_raising(self):
        from orchestration.connectors.travel_events_connector import TravelEventsConnector

        connector = TravelEventsConnector()
        with patch.dict(os.environ, {"EVENTBRITE_API_KEY": "fake-key"}), \
             patch.dict(sys.modules, {"aiohttp": None}):
            results, error = async_to_sync(connector._search_eventbrite_api)(
                "Nairobi", None, None, "all"
            )

        self.assertEqual(results, [])
        self.assertIn("aiohttp", (error or "").lower())

    @override_settings(TRAVEL_ALLOW_FALLBACK=True)
    def test_missing_aiohttp_still_serves_fallback_events(self):
        from orchestration.connectors.travel_events_connector import TravelEventsConnector

        connector = TravelEventsConnector()
        with patch.dict(os.environ, {"EVENTBRITE_API_KEY": "fake-key"}), \
             patch.dict(sys.modules, {"aiohttp": None}):
            result = async_to_sync(connector._fetch)(
                {"location": "Nairobi", "category": "all"}, {}
            )

        self.assertGreaterEqual(len(result["results"]), 1)
        self.assertIn("aiohttp", (result["metadata"].get("warning") or "").lower())


class LiteRunDocsTests(SimpleTestCase):
    def test_nginx_example_speaks_http_1_1_to_the_upstream(self):
        text = (REPO_ROOT / "docs" / "run-lite.md").read_text(encoding="utf-8")
        block = text.split("```nginx", 1)[1].split("```", 1)[0]
        self.assertIn("proxy_http_version 1.1;", block)


@override_settings(
    DEBUG=False,
    KAZI_LITE_PROFILE=True,
    MODERATION_ENABLED=False,
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "kazi-lite-chat",
        },
    },
)
class LiteChatTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="lite-chat", email="lite@example.com", password="secret",  # nosec B106 — test fixture
        )
        self.member = Member.objects.select_related("User").get(User=self.user)
        self.chatroom = Chatroom.objects.get(participants=self.member)

    def _consumer(self):
        consumer = ChatConsumer()
        consumer.scope = {"user": self.user}
        consumer.room_name = str(self.chatroom.id)
        consumer.room_group_name = f"chat_{self.chatroom.id}"
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_send = AsyncMock()
        consumer.send_chat_message = AsyncMock()
        consumer.send_message = AsyncMock()
        consumer.encrypt_message = AsyncMock(return_value={"data": "enc", "nonce": "nonce"})
        consumer.check_user_muted = AsyncMock(return_value=False)
        consumer.check_rate_limit = AsyncMock(return_value=True)
        consumer.check_key_rotation = AsyncMock()
        consumer.get_current_chatroom = AsyncMock(return_value=self.chatroom)
        consumer.get_chatroom_participants = AsyncMock(return_value=[self.member])
        consumer.message_to_json = AsyncMock(return_value={"id": 1})
        consumer.schedule_context_summary = AsyncMock()
        consumer.schedule_idle_nudge_if_needed = AsyncMock()
        consumer.get_history_rows = AsyncMock(return_value=[])
        return consumer

    @patch("orchestration.coordinator.OrchestrationCoordinator")
    def test_new_message_completes_in_the_lite_profile_with_debug_off(self, mock_coord):
        mock_coord.return_value.handle_message = AsyncMock(
            return_value=OrchestrationResult(full_response="hello back", persist=True)
        )
        consumer = self._consumer()

        async_to_sync(consumer.new_message)(
            {"from": self.user.username, "message": "@kazi hi", "chatid": str(self.chatroom.id)}
        )

        mock_coord.return_value.handle_message.assert_awaited_once()
        self.assertTrue(Message.objects.filter(member=self.member).exists())
        self.assertTrue(Message.objects.filter(member__User__username="kazi").exists())
        consumer.channel_layer.group_send.assert_awaited_once_with(
            consumer.room_group_name,
            {"type": "ai_message_saved", "message": {"id": 1}},
        )
        sent = [
            call.args[0] if call.args else call.kwargs
            for call in consumer.send_chat_message.await_args_list
        ]
        self.assertNotIn(
            "Error processing message",
            [item.get("content") for item in sent if isinstance(item, dict)],
        )

    def test_moderation_enabled_without_redis_is_skipped_and_logged(self):
        consumer = self._consumer()

        with self.settings(MODERATION_ENABLED=True):
            with patch("chatbot.consumers._moderation_store_warned", False):
                with self.assertLogs("chatbot.consumers", level="WARNING") as logs:
                    triggered = async_to_sync(consumer.buffer_message_for_moderation)(
                        self.chatroom.id, 1
                    )

        self.assertFalse(triggered)
        self.assertIn("not Redis", "\n".join(logs.output))
