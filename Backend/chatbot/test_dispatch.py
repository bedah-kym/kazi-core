"""Task dispatch from async code never blocks the event loop or raises into a turn."""
import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.test import SimpleTestCase, override_settings
from kombu.exceptions import EncodeError

from chatbot import dispatch
from chatbot.dispatch import dispatch_task


def _wait_for(condition, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def _reset_backoff():
    dispatch._skip_until = 0.0
    dispatch._skipped = 0


class DispatchTaskTests(SimpleTestCase):
    def setUp(self):
        _reset_backoff()
        self.addCleanup(_reset_backoff)
        # A pool per test: nothing a test queued can run during the next one.
        self.pool = ThreadPoolExecutor(max_workers=1)
        patcher = patch.object(dispatch, "_executor", self.pool)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.pool.shutdown, wait=True)

    def test_a_slow_publish_does_not_hold_the_event_loop(self):
        seen = {}

        def slow_publish(**kwargs):
            seen["thread"] = threading.get_ident()
            time.sleep(0.4)

        task = MagicMock()
        task.apply_async.side_effect = slow_publish

        async def run():
            started = time.monotonic()
            queued = dispatch_task(task, 1, 2, flag=True)
            return threading.get_ident(), queued, time.monotonic() - started

        loop_thread, queued, returned_after = asyncio.run(run())
        self.pool.shutdown(wait=True)

        self.assertTrue(queued)
        self.assertLess(returned_after, 0.4)
        self.assertNotEqual(seen["thread"], loop_thread)
        task.apply_async.assert_called_once_with(args=(1, 2), kwargs={"flag": True}, retry=False)

    def test_an_unreachable_broker_is_logged_and_then_skipped(self):
        task = MagicMock()
        task.name = "chatbot.tasks.example"
        task.apply_async.side_effect = OSError("broker unreachable")

        async def run():
            first = dispatch_task(task, 1)
            await asyncio.to_thread(_wait_for, lambda: dispatch._skip_until > 0)
            second = dispatch_task(task, 2)
            return first, second

        with self.assertLogs("chatbot.dispatch", level="WARNING") as logs:
            first, second = asyncio.run(run())
            self.pool.shutdown(wait=True)

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(task.apply_async.call_count, 1)
        self.assertEqual(len(logs.records), 1)

    def test_publishes_queued_behind_a_failure_do_not_each_wait_for_the_broker(self):
        task = MagicMock()
        task.name = "chatbot.tasks.example"
        task.apply_async.side_effect = OSError("broker unreachable")

        with self.assertLogs("chatbot.dispatch", level="WARNING"):
            dispatch._publish(task, (1,), {})
            dispatch._publish(task, (2,), {})

        self.assertEqual(task.apply_async.call_count, 1)

    def test_the_number_of_skipped_tasks_is_reported_when_dispatch_resumes(self):
        task = MagicMock()
        task.name = "chatbot.tasks.example"
        task.apply_async.side_effect = OSError("broker unreachable")
        with self.assertLogs("chatbot.dispatch", level="WARNING"):
            dispatch_task(task, 1)
        self.assertFalse(dispatch_task(task, 2))
        self.assertFalse(dispatch_task(task, 3))

        dispatch._skip_until = 0.0
        task.apply_async.side_effect = None
        with self.assertLogs("chatbot.dispatch", level="WARNING") as logs:
            self.assertTrue(dispatch_task(task, 4))

        self.assertIn("2 task(s) were skipped", logs.output[0])

    def test_a_publish_that_succeeds_ends_the_back_off(self):
        def another_publish_fails_meanwhile(**kwargs):
            dispatch._skip_until = time.monotonic() + 60

        task = MagicMock()
        task.apply_async.side_effect = another_publish_fails_meanwhile

        dispatch._publish(task, (1,), {})

        self.assertTrue(dispatch_task(MagicMock(), 2))

    def test_a_task_that_cannot_be_encoded_does_not_silence_the_others(self):
        task = MagicMock()
        task.name = "chatbot.tasks.example"
        task.apply_async.side_effect = EncodeError("not JSON")

        with self.assertLogs("chatbot.dispatch", level="WARNING"):
            dispatch_task(task, object())

        other = MagicMock()
        self.assertTrue(dispatch_task(other, 1))
        other.apply_async.assert_called_once()

    def test_dispatch_outside_an_event_loop_publishes_inline(self):
        task = MagicMock()

        self.assertTrue(dispatch_task(task, 7))

        task.apply_async.assert_called_once_with(args=(7,), kwargs={}, retry=False)

    def test_database_connections_are_closed_in_the_pool_thread_and_never_in_the_callers(self):
        task = MagicMock()

        with patch("chatbot.dispatch.close_old_connections") as close:
            dispatch_task(task, 1)
            self.assertEqual(close.call_count, 0)

            async def run():
                dispatch_task(task, 2)

            asyncio.run(run())
            self.pool.shutdown(wait=True)
            self.assertEqual(close.call_count, 1)


class CeleryAppWiringTests(SimpleTestCase):
    def test_tasks_are_bound_to_the_project_app_and_follow_settings(self):
        from chatbot.tasks import refresh_room_context_summary

        app = refresh_room_context_summary.app
        self.assertEqual(app.main, "Backend")
        self.assertEqual(bool(app.conf.task_always_eager), bool(settings.CELERY_TASK_ALWAYS_EAGER))

    def test_a_pool_thread_sees_the_project_app_too(self):
        from chatbot.tasks import refresh_room_context_summary

        seen = {}
        thread = threading.Thread(target=lambda: seen.update(app=refresh_room_context_summary.app.main))
        thread.start()
        thread.join(10)

        self.assertEqual(seen.get("app"), "Backend")

    def test_a_test_run_neither_reaches_a_broker_nor_runs_task_bodies(self):
        from Backend import celery_app

        self.assertTrue(celery_app.connection_for_write().as_uri().startswith("memory://"))
        self.assertFalse(celery_app.conf.task_always_eager)

    def test_with_a_broker_every_argument_reaches_celery_unchanged(self):
        from Backend import celery_app

        def probe():
            pass

        task = celery_app.task(name="chatbot.test_dispatch.passthrough")(probe)
        self.addCleanup(celery_app.tasks.pop, "chatbot.test_dispatch.passthrough", None)

        with patch("celery.app.task.Task.apply_async") as celery_apply_async:
            task.apply_async((1,), {"a": 2}, "my-id", countdown=600)

        celery_apply_async.assert_called_once_with((1,), {"a": 2}, "my-id", None, None, None, None, countdown=600)


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class EagerModeHasNoSchedulerTests(SimpleTestCase):
    """Dev mode without a broker runs tasks inline. A task due later must not run at once."""

    def setUp(self):
        from Backend import celery_app

        self.ran = []

        def probe():
            self.ran.append(1)

        self.task = celery_app.task(name="chatbot.test_dispatch.probe")(probe)
        self.addCleanup(celery_app.tasks.pop, "chatbot.test_dispatch.probe", None)

    def test_a_task_due_now_runs_inline(self):
        self.task.delay()

        self.assertEqual(self.ran, [1])

    def test_a_countdown_is_not_run_at_once(self):
        result = self.task.apply_async(countdown=600)

        self.assertEqual(self.ran, [])
        self.assertTrue(result.id)

    def test_a_time_in_the_future_is_not_run_at_once(self):
        self.task.apply_async(eta=datetime.now(timezone.utc) + timedelta(minutes=5))

        self.assertEqual(self.ran, [])

    def test_a_time_without_a_zone_is_read_as_utc_the_way_celery_reads_it(self):
        utc_now = datetime.now(timezone.utc).replace(tzinfo=None)

        self.task.apply_async(eta=utc_now + timedelta(hours=2))
        self.assertEqual(self.ran, [])

        self.task.apply_async(eta=utc_now - timedelta(hours=2))
        self.assertEqual(self.ran, [1])

    def test_a_time_already_past_runs(self):
        self.task.apply_async(eta=datetime.now(timezone.utc) - timedelta(minutes=5))

        self.assertEqual(self.ran, [1])

    def test_the_nudge_and_the_reminder_follow_the_same_rule(self):
        from Backend.celery import KaziTask
        from chatbot.tasks import send_idle_nudge, send_reminder

        self.assertIsInstance(send_idle_nudge._get_current_object(), KaziTask)
        self.assertIsInstance(send_reminder._get_current_object(), KaziTask)
