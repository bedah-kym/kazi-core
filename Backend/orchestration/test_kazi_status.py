"""Tests for the effective-settings bridge used by scripts/kazi.py."""
from __future__ import annotations

import json
from io import StringIO
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase

from orchestration.models import Turn
from orchestration.tasks import LAST_REAPER_CACHE_KEY

User = get_user_model()


class KaziStatusTests(TestCase):
    def _status(self):
        out = StringIO()
        call_command("kazi_status", "--json", stdout=out)
        return json.loads(out.getvalue())

    def test_json_reports_the_effective_profile_without_secrets(self):
        payload = self._status()
        self.assertEqual(payload["profile"]["lite"], settings.KAZI_LITE_PROFILE)
        self.assertEqual(
            payload["profile"]["in_process_scheduler"], settings.IN_PROCESS_SCHEDULER
        )
        self.assertEqual(payload["profile"]["temporal_disabled"], settings.TEMPORAL_DISABLED)
        self.assertIn("host", payload["database"])
        self.assertIn("port", payload["database"])
        text = json.dumps(payload)
        password = settings.DATABASES["default"].get("PASSWORD") or ""
        if password:
            self.assertNotIn(password, text)
        self.assertNotIn("DJANGO_SECRET_KEY", text)

    def test_json_counts_open_and_paused_turns(self):
        user = User.objects.create_user(username="status-user", password="x")  # nosec B106 - throwaway test password
        Turn.objects.create(user=user, state="running")
        Turn.objects.create(user=user, state="paused")
        payload = self._status()
        self.assertEqual(payload["turns"], {"open": 1, "paused": 1})

    def test_json_includes_the_last_reaper_result(self):
        cache.set(LAST_REAPER_CACHE_KEY, {"reaped": 2, "orphaned": 0, "room_gone": 0})
        payload = self._status()
        self.assertEqual(payload["last_reaper"]["result"]["reaped"], 2)

    def test_json_reports_pending_migrations_as_a_list(self):
        payload = self._status()
        self.assertIsInstance(payload["pending_migrations"]["items"], list)

    def test_json_reports_beat_drift_keys(self):
        payload = self._status()
        self.assertEqual(
            set(payload["beat_drift"]), {"missing", "extra", "changed"}
        )

    def test_text_mode_prints_the_database_host_only(self):
        out = StringIO()
        call_command("kazi_status", stdout=out)
        text = out.getvalue()
        self.assertIn("Database host:", text)
        password = settings.DATABASES["default"].get("PASSWORD") or ""
        if password:
            self.assertNotIn(password, text)


class KaziStatusFailureTests(TestCase):
    def test_unreadable_migration_and_drift_do_not_break_the_command(self):
        with patch(
            "orchestration.management.commands.kazi_status._pending_migrations",
            return_value=None,
        ), patch(
            "orchestration.management.commands.kazi_status._beat_drift",
            return_value=None,
        ), patch(
            "orchestration.management.commands.kazi_status._turn_counts",
            return_value={},
        ):
            out = StringIO()
            call_command("kazi_status", "--json", stdout=out)
            payload = json.loads(out.getvalue())
        self.assertIsNone(payload["pending_migrations"]["items"])
        self.assertIsNone(payload["beat_drift"])
        self.assertEqual(payload["turns"], {})
