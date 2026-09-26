"""
Tests for the nightly telemetry rollup (issue #153).

Covers: entity extraction, each watch type, rotation, offset bookmarks,
window idempotency, bounded per-run work, concurrent-writer tolerance,
and the privacy boundary (no free-text scanning).
"""
import json
import os
import tempfile
from datetime import datetime, timezone
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from orchestration import telemetry_rollups as rollups


def _iso(day: int, hour: int = 12) -> str:
    return datetime(2026, 9, day, hour, 0, tzinfo=timezone.utc).isoformat()


def _line(event) -> str:
    return json.dumps(event, ensure_ascii=True) + "\n"


def _write(path, text):
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(text)


class RollupTestCase(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.telemetry_path = os.path.join(self.tmpdir.name, "orchestration.jsonl")
        self.override = override_settings(ORCHESTRATION_TELEMETRY_PATH=self.telemetry_path)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(cache.clear)

    def rotate(self, day=26):
        rotated = rollups.rotate_live_telemetry_file(datetime(2026, 9, day, tzinfo=timezone.utc))
        return rotated

    def roll(self, day=26, **kwargs):
        return rollups.run_rollup(now=datetime(2026, 9, day, 3, tzinfo=timezone.utc), **kwargs)


# ---------------------------------------------------------------------------
#  Entity extraction                                                         #
# ---------------------------------------------------------------------------

class EntityExtractionTests(RollupTestCase):
    def test_extracts_hosts_ips_macs_and_entities(self):
        events = [
            {"event": "command_result", "ts": _iso(25), "host": "printer", "ip": "192.168.1.44"},
            {"event": "command_result", "ts": _iso(25), "command": "ping nas.local -c 3"},
            {"event": "command_result", "ts": _iso(25), "command": "ssh ops@10.0.0.7"},
            {"event": "command_result", "ts": _iso(25), "command": "arp shows aa:bb:cc:dd:ee:ff"},
            {"event": "action_receipt", "ts": _iso(25), "action": "send_email", "status": "success"},
            {
                "event": "agent_loop_done",
                "ts": _iso(25),
                "transcript": [
                    {"tool": "get_weather", "input": {"city": "Nairobi"}},
                    {"tool": "send_email", "input": {"email": "ops@example.com"}},
                ],
            },
        ]
        facts = rollups.extract_entity_facts(events)

        by_value = {record["value"] for record in facts.values()}
        self.assertIn("192.168.1.44", by_value)
        self.assertIn("nas.local", by_value)
        self.assertIn("10.0.0.7", by_value)
        self.assertIn("aa:bb:cc:dd:ee:ff", by_value)
        self.assertIn("send_email", by_value)
        self.assertIn("Nairobi", by_value)
        self.assertIn("ops@example.com", by_value)
        host_facts = [r for r in facts.values() if r["type"] == "host"]
        printer = next(r for r in host_facts if r["value"] == "192.168.1.44")
        self.assertEqual(printer["occurrences"], 1)

    def test_free_text_is_never_scanned(self):
        events = [
            {"event": "chat", "ts": _iso(25), "message": "my printer is at 10.0.0.99 btw"},
            {"event": "command_result", "ts": _iso(25), "output": "latency to 10.0.0.88 is fine"},
            {"event": "command_result", "ts": _iso(25), "command": "ssh fileserver"},
        ]
        facts = rollups.extract_entity_facts(events)
        values = {record["value"] for record in facts.values()}
        self.assertNotIn("10.0.0.99", values)
        self.assertNotIn("10.0.0.88", values)
        self.assertIn("fileserver", values)

    def test_facts_are_bounded(self):
        events = []
        for i in range(300):
            events.append({"event": "command_result", "ts": _iso(25), "ip": f"10.0.{i // 250}.{i % 250}"})
        facts = rollups.extract_entity_facts(events)
        self.assertLessEqual(len(facts), rollups.MAX_FACTS)

    def test_distinct_values_for_same_entity_field_stay_separate(self):
        events = [
            {"event": "agent_loop_done", "ts": _iso(25), "transcript": [
                {"tool": "get_weather", "input": {"city": "Nairobi"}},
                {"tool": "get_weather", "input": {"city": "Mombasa"}},
            ]},
        ]
        facts = rollups.extract_entity_facts(events)
        cities = [r for r in facts.values() if r["type"] == "entity" and r["name"] == "city"]
        self.assertEqual(len(cities), 2)
        self.assertEqual({c["value"] for c in cities}, {"Nairobi", "Mombasa"})

    def test_hosts_sharing_one_ip_do_not_collide(self):
        events = [
            {"event": "command_result", "ts": _iso(25), "host": "printer", "ip": "10.0.0.9"},
            {"event": "command_result", "ts": _iso(25), "host": "nas", "ip": "10.0.0.9"},
        ]
        facts = rollups.extract_entity_facts(events)
        host_keys = [k for k in facts if k.startswith("host:")]
        self.assertEqual(sorted(host_keys), ["host:nas", "host:printer"])


# ---------------------------------------------------------------------------
#  Derived watches                                                           #
# ---------------------------------------------------------------------------

class WatchComputationTests(RollupTestCase):
    def test_action_error_rate_watch(self):
        events = [
            {"event": "action_receipt", "ts": _iso(25), "action": "send_email", "status": "success"},
            {"event": "action_receipt", "ts": _iso(25), "action": "send_email", "status": "success"},
            {"event": "action_receipt", "ts": _iso(25), "action": "send_email", "status": "error"},
            {"event": "action_receipt", "ts": _iso(25), "action": "send_email", "status": "success"},
            {"event": "action_receipt", "ts": _iso(25), "action": "send_email", "status": "error"},
            {"event": "action_receipt", "ts": _iso(25), "action": "send_email", "status": "error"},
        ]
        watches = rollups.compute_watches(events)
        watch = next(w for w in watches if w["metric"] == "action_error_rate")
        self.assertEqual(watch["current_value"], 0.5)
        self.assertEqual(watch["labels"]["action"], "send_email")
        self.assertEqual(watch["samples"], 6)
        self.assertEqual(watch["threshold"], rollups.DEFAULT_ERROR_RATE_THRESHOLD)

    def test_error_rate_watch_requires_min_samples(self):
        events = [
            {"event": "action_receipt", "ts": _iso(25), "action": "send_email", "status": "error"},
        ]
        watches = rollups.compute_watches(events)
        self.assertEqual(
            [w for w in watches if w["metric"] == "action_error_rate"], []
        )

    def test_disk_growth_watch_trend(self):
        events = [
            {"event": "command_result", "ts": _iso(23), "host": "nas", "disk_used_bytes": 1000},
            {"event": "command_result", "ts": _iso(24), "host": "nas", "disk_used_bytes": 1200},
            {"event": "command_result", "ts": _iso(25), "host": "nas", "disk_used_bytes": 1400},
        ]
        watches = rollups.compute_watches(events)
        watch = next(w for w in watches if w["metric"] == "disk_growth_bytes_per_day")
        self.assertEqual(watch["current_value"], 200.0)
        self.assertEqual(watch["trend"], "rising")
        self.assertEqual(watch["labels"]["host"], "nas")

    def test_cert_expiry_watch_min_per_host(self):
        events = [
            {"event": "command_result", "ts": _iso(25), "host": "api", "cert_expiry_days": 45},
            {"event": "command_result", "ts": _iso(25), "host": "legacy", "cert_expiry_days": 12},
        ]
        watches = rollups.compute_watches(events)
        by_host = {w["labels"]["host"]: w for w in watches if w["metric"] == "cert_expiry_days"}
        self.assertEqual(by_host["api"]["current_value"], 45.0)
        self.assertEqual(by_host["legacy"]["current_value"], 12.0)
        self.assertEqual(by_host["legacy"]["threshold"], 30)
        self.assertLess(by_host["legacy"]["current_value"], by_host["legacy"]["threshold"])

    def test_cert_expiry_trend_uses_previous_run(self):
        events = [
            {"event": "command_result", "ts": _iso(25), "host": "api", "cert_expiry_days": 30},
        ]
        previous = [{
            "metric": "cert_expiry_days",
            "labels": {"host": "api"},
            "current_value": 60,
        }]
        watches = rollups.compute_watches(events, previous)
        watch = next(w for w in watches if w["metric"] == "cert_expiry_days")
        self.assertEqual(watch["trend"], "falling")

    def test_uptime_heartbeat_watch_trend(self):
        events = [
            {"event": "progress_event", "ts": _iso(24, 8), "room_id": 1},
            {"event": "progress_event", "ts": _iso(24, 9), "room_id": 1},
            {"event": "progress_event", "ts": _iso(25, 8), "room_id": 1},
            {"event": "progress_event", "ts": _iso(25, 9), "room_id": 1},
            {"event": "progress_event", "ts": _iso(25, 10), "room_id": 1},
        ]
        watches = rollups.compute_watches(events)
        watch = next(w for w in watches if w["metric"] == "uptime_heartbeats")
        self.assertEqual(watch["current_value"], 3)
        self.assertEqual(watch["trend"], "rising")

    def test_cooccurrence_watch(self):
        events = []
        for hour in range(8, 14):
            events.append({"event": "command_result", "ts": _iso(25, hour)})
            events.append({"event": "action_receipt", "ts": _iso(25, hour)})
        watches = rollups.compute_watches(events)
        co = next(w for w in watches if w["metric"] == "event_cooccurrence")
        self.assertEqual(co["current_value"], 1.0)
        self.assertEqual(co["trend"], "correlated")

    def test_watches_are_bounded(self):
        events = []
        for i in range(100):
            events.append(
                {"event": "command_result", "ts": _iso(25), "host": f"h{i}", "disk_used_bytes": i}
            )
        watches = rollups.compute_watches(events)
        self.assertLessEqual(len(watches), rollups.MAX_WATCHES)


# ---------------------------------------------------------------------------
#  Rotation, bookmarks, idempotency, bounds                                  #
# ---------------------------------------------------------------------------

class RotationAndBookmarkTests(RollupTestCase):
    def test_rollup_consumes_rotated_file_not_live(self):
        _write(self.telemetry_path, _line({"event": "action_receipt", "ts": _iso(25), "action": "a", "status": "success"}))
        _write(self.telemetry_path, _line({"event": "action_receipt", "ts": _iso(25), "action": "a", "status": "error"}))
        summary = self.roll(day=26)
        self.assertFalse(summary["noop"])
        self.assertEqual(summary["lines_processed"], 2)
        self.assertTrue(summary["rotated"])
        self.assertFalse(os.path.exists(self.telemetry_path))

        # Live events written after rotation are untouched by the rollup.
        _write(self.telemetry_path, _line({"event": "action_receipt", "ts": _iso(26), "action": "b", "status": "success"}))
        summary = self.roll(day=27)
        self.assertEqual(summary["lines_processed"], 1)

    def test_second_run_in_same_window_is_noop(self):
        _write(self.telemetry_path, _line({"event": "progress_event", "ts": _iso(25), "room_id": 1}))
        first = self.roll(day=26)
        self.assertFalse(first["noop"])
        second = self.roll(day=26)
        self.assertTrue(second["noop"])

    def test_bookmarks_make_runs_incremental(self):
        _write(self.telemetry_path, _line({"event": "progress_event", "ts": _iso(25), "room_id": 1}))
        self.roll(day=26)
        # New events land in a new rotated file the following day.
        _write(self.telemetry_path, _line({"event": "progress_event", "ts": _iso(26), "room_id": 1}))
        _write(self.telemetry_path, _line({"event": "progress_event", "ts": _iso(26), "room_id": 1}))
        summary = self.roll(day=27)
        self.assertEqual(summary["lines_processed"], 2)

    def test_reset_bookmarks_reprocesses_everything(self):
        _write(self.telemetry_path, _line({"event": "progress_event", "ts": _iso(25), "room_id": 1}))
        _write(self.telemetry_path, _line({"event": "progress_event", "ts": _iso(25), "room_id": 1}))
        self.roll(day=26)
        self.roll(day=27)
        summary = self.roll(day=28, reset_bookmarks=True)
        self.assertEqual(summary["lines_processed"], 2)
        self.assertEqual(summary["files_processed"], 1)

    def test_per_run_work_stays_flat_as_corpus_grows(self):
        for day in range(1, 21):
            _write(self.telemetry_path, _line({"event": "progress_event", "ts": _iso(day), "room_id": 0}))
            rotated = self.rotate(day=day)
            for i in range(9):
                _write(rotated, _line({"event": "progress_event", "ts": _iso(day), "room_id": i + 1}))
        # Warm-up consumes the whole 20-file corpus once (backfill).
        first = self.roll(day=21)
        self.assertEqual(first["lines_processed"], 200)
        # Corpus is now 200 lines; a new day with 1 new event processes
        # exactly that one — per-run work stays flat as the corpus grows.
        _write(self.telemetry_path, _line({"event": "progress_event", "ts": _iso(21), "room_id": 0}))
        second = self.roll(day=22)
        self.assertEqual(second["lines_processed"], 1)

    def test_max_lines_cap_halts_early(self):
        for i in range(30):
            _write(self.telemetry_path, _line({"event": "progress_event", "ts": _iso(25), "room_id": i}))
        summary = self.roll(day=26, max_lines=10)
        self.assertEqual(summary["lines_processed"], 10)
        # Bookmark lands mid-file; the next window continues where it stopped.
        _write(self.telemetry_path, _line({"event": "progress_event", "ts": _iso(26), "room_id": 99}))
        summary = self.roll(day=27, max_lines=100)
        self.assertEqual(summary["lines_processed"], 21)

    def test_concurrent_writer_partial_lines_are_tolerated(self):
        _write(self.telemetry_path, _line({"event": "progress_event", "ts": _iso(25), "room_id": 1}))
        _write(self.telemetry_path, '{"event": "progress_event", "ts": "2026-09-25T12:00:00+00:00", "room_')
        _write(self.telemetry_path, "not json at all\n")
        _write(self.telemetry_path, _line({"event": "progress_event", "ts": _iso(25), "room_id": 1}))
        summary = self.roll(day=26)
        self.assertEqual(summary["lines_processed"], 2)
        watches = rollups.load_watches()
        heartbeat = next(w for w in watches if w["metric"] == "uptime_heartbeats")
        self.assertEqual(heartbeat["current_value"], 2)

    def test_facts_and_watches_persist_and_merge_across_runs(self):
        _write(self.telemetry_path, _line({"event": "command_result", "ts": _iso(25), "host": "printer", "ip": "192.168.1.44"}))
        self.roll(day=26)
        _write(self.telemetry_path, _line({"event": "command_result", "ts": _iso(26), "host": "printer", "ip": "192.168.1.44"}))
        self.roll(day=27)
        facts = rollups.load_facts()
        printer = next(r for r in facts["facts"].values() if r["type"] == "host")
        self.assertEqual(printer["occurrences"], 2)

    def test_memory_state_surfaces_facts_and_watches(self):
        from orchestration.memory_state import load_derived_watches, load_entity_facts

        _write(self.telemetry_path, _line({"event": "command_result", "ts": _iso(25), "host": "nas", "ip": "10.0.0.2"}))
        self.roll(day=26)
        facts = load_entity_facts()
        self.assertIn("facts", facts)
        self.assertTrue(any(r["type"] == "host" for r in facts["facts"].values()))
        self.assertIsInstance(load_derived_watches(), list)

    def test_storage_failure_does_not_advance_bookmarks_or_window(self):
        _write(self.telemetry_path, _line({"event": "progress_event", "ts": _iso(25), "room_id": 1}))
        with patch("orchestration.telemetry_rollups._store_facts", return_value=False), \
             patch("orchestration.telemetry_rollups._store_watches", return_value=False):
            summary = self.roll(day=26)
        self.assertEqual(summary.get("error"), "storage_unavailable")
        self.assertEqual(summary["files_processed"], 0)
        self.assertEqual(
            rollups.load_watches(), []
        )
        # Window marker was not set, so the next run reprocesses the file
        # from the unadvanced bookmark instead of silently skipping.
        retry = self.roll(day=27)
        self.assertEqual(retry["lines_processed"], 1)
        self.assertFalse(retry["noop"])


class RotationEdgeTests(RollupTestCase):
    def test_rotation_returns_none_when_no_live_file(self):
        self.assertIsNone(self.rotate(day=26))

    def test_rotation_avoids_collisions(self):
        _write(self.telemetry_path, _line({"event": "progress_event", "ts": _iso(25), "room_id": 1}))
        first = self.rotate(day=26)
        _write(self.telemetry_path, _line({"event": "progress_event", "ts": _iso(26), "room_id": 2}))
        second = self.rotate(day=26)
        self.assertNotEqual(first, second)
        self.assertTrue(os.path.exists(first))
        self.assertTrue(os.path.exists(second))

    def test_reset_bookmarks_helper(self):
        _write(self.telemetry_path, _line({"event": "progress_event", "ts": _iso(25), "room_id": 1}))
        self.roll(day=26)
        self.assertGreater(rollups.reset_bookmarks(), 0)
        self.assertEqual(rollups.reset_bookmarks(), 0)
