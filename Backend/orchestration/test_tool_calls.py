"""The Turn's typed tool-call record (P3-5, T-D3 final).

The rows are written once, append-only, encrypted with the room key, and read
back by the same history path the next turn uses. The replay keeps every call's
result, including the calls that did not run.
"""
from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from chatbot.history import get_history_rows
from chatbot.models import Chatroom, Member, Message
from chatbot.transcript import build_history_messages
from chatbot.turn_runner import save_turn_reply
from orchestration.coordinator import OrchestrationResult
from orchestration.models import Turn, TurnToolCall
from orchestration.tool_calls import (
    MAX_REPLAYED_CALLS,
    decrypt_body,
    link_turn_calls_to_message,
    load_tool_calls_for_turn,
    record_from_event,
    record_tool_calls,
    shorten_for_record,
    status_for_result,
)
from orchestration.tool_record import (
    RECORD_STRING_LIMIT,
    RECORD_STRING_MAX,
    tool_record_input,
)


class ShortenRecordTests(SimpleTestCase):
    def test_a_short_value_is_unchanged(self):
        text, truncated = shorten_for_record("short")

        self.assertEqual(text, "short")
        self.assertFalse(truncated)

    def test_a_long_value_keeps_its_head_and_tail_with_a_marker(self):
        value = "START " + "x" * 5000 + " END"

        text, truncated = shorten_for_record(value, limit=400)

        self.assertTrue(truncated)
        self.assertTrue(text.startswith("START "))
        self.assertIn("characters omitted of", text)
        self.assertIn(" END", text)
        self.assertLessEqual(len(text), 400)

    def test_the_marker_cannot_be_mistaken_for_real_content(self):
        value = "HEAD-" + "SECRET" * 1000 + "-TAIL"

        text, _truncated = shorten_for_record(value, limit=300)

        self.assertIn("HEAD-", text)
        self.assertIn("-TAIL", text)
        self.assertNotIn("SECRET" * 1000, text)
        self.assertIn("record shortened", text)
        self.assertNotEqual(text, value)

    def test_a_161_character_input_is_kept_whole(self):
        value = "a" * 161

        recorded = tool_record_input({"content": value})["content"]

        self.assertEqual(recorded, value)

    def test_the_omitted_count_uses_the_redacted_text(self):
        from orchestration.security_policy import redact_sensitive_text

        secret = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456"  # nosec B105 — fake credential
        value = secret + " tail" * 80
        redacted = redact_sensitive_text(value)

        recorded = tool_record_input({"content": value})["content"]

        self.assertNotIn("abcdefghijklmnopqrstuvwxyz123456", recorded)
        self.assertIn(f"the call sent {len(redacted)} characters", recorded)
        self.assertLessEqual(len(recorded), RECORD_STRING_MAX)
        marker_start = recorded.find(" ... [record shortened:")
        marker_end = recorded.find("] ... ", marker_start) + len("] ... ")
        real_text = len(recorded) - (marker_end - marker_start)
        self.assertGreaterEqual(real_text, RECORD_STRING_LIMIT)

    def test_a_shortened_input_keeps_its_start_and_end(self):
        value = "START " + "y" * 900 + " END"

        recorded = tool_record_input({"content": value})["content"]

        self.assertTrue(recorded.startswith("START "))
        self.assertIn("the call sent 910 characters", recorded)
        self.assertIn(" END", recorded)
        self.assertLessEqual(len(recorded), RECORD_STRING_MAX)


class TurnToolCallRecordTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        User = get_user_model()
        self.user = User.objects.create_user(username="tool-record", password="pw")  # nosec B106 — fake test credential
        self.member = Member.objects.select_related("User").get(User=self.user)
        self.room = Chatroom.objects.get(participants=self.member)
        self.turn = Turn.objects.create(
            room=self.room, user=self.user, state="completed",
        )
        self.request = Message.objects.create(
            member=self.member, content="run it", timestamp=timezone.now(),
        )
        self.room.chats.add(self.request)

    def _reply(self, *calls, model="Done."):
        for call in calls:
            call.setdefault("iteration", 1)
            call.setdefault("provenance", "main")
        record_tool_calls(self.turn.id, self.room, list(calls))
        result = OrchestrationResult(
            full_response=model,
            persist=True,
            tools=[],
            model_text=model,
            harness="",
            tool_calls_recorded=True,
            turn_id=str(self.turn.id),
        )
        return save_turn_reply(self.room, result, user_id=self.user.id)

    def test_seq_allocation_locks_the_turn_row_first(self):
        from unittest.mock import MagicMock

        lock = MagicMock()
        with patch.object(Turn.objects, "select_for_update", return_value=lock):
            record_tool_calls(self.turn.id, self.room, [
                {"call_id": "c1", "name": "get_weather",
                 "input": {"city": "Nairobi"}, "result": {"status": "success"},
                 "shown": "sunny"},
            ])

        lock.filter.assert_called_once_with(pk=self.turn.id)

    def test_rows_survive_a_cache_flush(self):
        self._reply(
            {"call_id": "c1", "name": "run_command", "input": {"command": "ls"},
             "result": {"status": "success"}, "shown": "listed"},
        )

        cache.clear()

        rows = list(TurnToolCall.objects.filter(turn=self.turn))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, "ok")
        self.assertEqual(rows[0].seq, 1)

    def test_bodies_are_encrypted_with_the_room_key(self):
        self._reply(
            {"call_id": "c1", "name": "create_note", "input": {"content": "private"},
             "result": {"status": "success"}, "shown": "note saved"},
        )

        row = TurnToolCall.objects.get(turn=self.turn)
        self.assertNotIn("private", row.input_enc)
        self.assertNotIn("note saved", row.shown_enc)
        loaded = load_tool_calls_for_turn(self.room, self.turn.id)[0]
        self.assertEqual(loaded["input"], {"content": "private"})
        self.assertEqual(loaded["shown"], "note saved")

    def test_retrieval_is_set_for_web_search_and_search_info(self):
        record_tool_calls(self.turn.id, self.room, [
            {"call_id": "s1", "name": "web_search", "input": {"query": "x"},
             "result": {"status": "success"}, "shown": "results"},
            {"call_id": "s2", "name": "search_info", "input": {"query": "y"},
             "result": {"status": "success"}, "shown": "results"},
            {"call_id": "s3", "name": "create_note", "input": {"content": "z"},
             "result": {"status": "success"}, "shown": "saved"},
        ])

        rows = {
            row.call_id: row for row in TurnToolCall.objects.filter(turn=self.turn)
        }
        self.assertTrue(rows["s1"].retrieval)
        self.assertTrue(rows["s2"].retrieval)
        self.assertFalse(rows["s3"].retrieval)

    def test_a_delegated_call_records_its_provenance_and_parent(self):
        record_from_event(self.turn.id, self.room, {
            "id": "parent-1",
            "name": "delegate_task",
            "input": {"task": "search"},
            "result": {"status": "success", "summary": "found it"},
            "shown": "found it",
            "iteration": 1,
            "delegated_calls": [
                {"call_id": "child-1", "name": "web_search",
                 "input": {"query": "x"}, "status": "success",
                 "shown": "child result"},
            ],
        })

        child = TurnToolCall.objects.get(turn=self.turn, call_id="child-1")
        parent = TurnToolCall.objects.get(turn=self.turn, call_id="parent-1")
        self.assertEqual(child.provenance, "delegated")
        self.assertEqual(child.parent_call_id, "parent-1")
        self.assertTrue(child.retrieval)
        self.assertEqual(parent.provenance, "main")
        self.assertLess(child.seq, parent.seq)

    def test_a_row_cannot_be_updated(self):
        self._reply(
            {"call_id": "c1", "name": "create_note", "input": {"content": "x"},
             "result": {"status": "success"}, "shown": "saved"},
        )
        row = TurnToolCall.objects.get(turn=self.turn)

        row.status = "error"
        with self.assertRaises(ValueError):
            row.save()

    def test_the_next_turn_replays_a_declined_call_with_its_status(self):
        self._reply(
            {"call_id": "c1", "name": "run_command", "input": {"command": "rm x"},
             "result": {"status": "declined"},
             "shown": "not executed: you declined", "status": "declined"},
        )

        rows = get_history_rows(self.room, limit=8)
        messages = build_history_messages(rows, viewer_user_id=self.user.id)

        results = [
            block for message in messages
            if isinstance(message.get("content"), list)
            for block in message["content"]
            if block.get("type") == "tool_result"
        ]
        self.assertTrue(results)
        result = results[0]
        self.assertTrue(result["tool_use_id"].startswith("noexec-"))
        self.assertIn("not executed", result["content"])
        self.assertIn("declined", result["content"])
        self.assertTrue(result["is_error"])

    def test_memory_reads_the_turn_rows_not_a_duplicated_list(self):
        from asgiref.sync import async_to_sync

        from orchestration.memory_state import (
            load_memory_state,
            update_memory_state_batch,
        )

        self._reply(
            {"call_id": "c1", "name": "get_weather", "input": {"city": "Nairobi"},
             "result": {"status": "success"}, "shown": "sunny"},
        )
        context = {
            "user_id": self.user.id, "room_id": self.room.id,
            "turn_id": self.turn.id,
        }

        async_to_sync(update_memory_state_batch)(context, [
            {"name": "get_weather", "input": {}, "output": {}},
            {"name": "get_weather", "input": {}, "output": {}},
        ])

        state = async_to_sync(load_memory_state)(context)
        actions = [entry["action"] for entry in state.get("last_actions") or []]
        self.assertEqual(actions.count("get_weather"), 1)

    def test_memory_falls_back_to_the_passed_records_when_no_row_exists(self):
        from asgiref.sync import async_to_sync

        from orchestration.memory_state import (
            load_memory_state,
            update_memory_state_batch,
        )

        context = {
            "user_id": self.user.id, "room_id": self.room.id,
            "turn_id": self.turn.id,
        }
        async_to_sync(update_memory_state_batch)(context, [
            {"name": "get_weather", "input": {}, "output": {}},
        ])

        state = async_to_sync(load_memory_state)(context)
        actions = [entry["action"] for entry in state.get("last_actions") or []]
        self.assertEqual(actions.count("get_weather"), 1)

    def test_the_history_read_decrypts_each_body_once(self):
        self._reply(
            {"call_id": "c1", "name": "run_command", "input": {"command": "ls"},
             "result": {"status": "success"}, "shown": "listed"},
            {"call_id": "c2", "name": "run_command", "input": {"command": "pwd"},
             "result": {"status": "success"}, "shown": "path"},
        )
        calls = []
        real = decrypt_body

        def counted(*args, **kwargs):
            calls.append(kwargs.get("blob", args[1] if len(args) > 1 else None))
            return real(*args, **kwargs)

        with patch("orchestration.tool_calls.decrypt_body", side_effect=counted):
            rows = get_history_rows(self.room, limit=8)

        stored = list(TurnToolCall.objects.filter(turn=self.turn))
        self.assertEqual(len(stored), 2)
        self.assertTrue(rows)
        self.assertEqual(len(calls), 2 * len(stored))
        self.assertEqual(len(set(calls)), len(calls))

    def test_the_last_ten_calls_replay_with_a_note_and_never_decrypt_the_rest(self):
        records = [
            {"call_id": f"c{n}", "name": "get_weather",
             "input": {"city": f"city-{n}"}, "result": {"status": "success"},
             "shown": f"sunny-{n}"}
            for n in range(1, 26)
        ]
        record_tool_calls(self.turn.id, self.room, records)
        message = self._reply()
        link_turn_calls_to_message(self.turn.id, message.id)
        calls = []
        real = decrypt_body

        def counted(*args, **kwargs):
            calls.append(kwargs.get("blob", args[1] if len(args) > 1 else None))
            return real(*args, **kwargs)

        with patch("orchestration.tool_calls.decrypt_body", side_effect=counted):
            rows = get_history_rows(self.room, limit=8)

        reply = next(row for row in rows if row[0] == message.id)
        self.assertEqual(len(reply[3]["tool_calls"]), MAX_REPLAYED_CALLS)
        self.assertEqual(
            reply[3]["tool_calls"][0]["call_id"], "c16",
            "the latest ten calls are kept, not the earliest",
        )
        self.assertIn("15 earlier tool calls", reply[3]["tool_calls"][0]["note"])
        self.assertLessEqual(len(calls), 2 * MAX_REPLAYED_CALLS)

    def test_a_body_copied_to_another_row_fails_to_decrypt(self):
        record_tool_calls(self.turn.id, self.room, [
            {"call_id": "c1", "name": "get_weather", "input": {"city": "A"},
             "result": {"status": "success"}, "shown": "first"},
            {"call_id": "c2", "name": "get_weather", "input": {"city": "B"},
             "result": {"status": "success"}, "shown": "second"},
        ])
        first, second = list(TurnToolCall.objects.filter(turn=self.turn).order_by("seq"))

        TurnToolCall.objects.filter(pk=second.pk).update(shown_enc=first.shown_enc)
        second.refresh_from_db()

        self.assertIsNone(
            decrypt_body(
                self.room, second.shown_enc,
                turn_id=second.turn_id, seq=second.seq,
            ),
        )

    def test_an_unusable_room_key_stores_no_body_in_clear(self):
        Chatroom.objects.filter(pk=self.room.pk).update(encryption_key="not-a-key")
        self.room.refresh_from_db()

        record_tool_calls(self.turn.id, self.room, [
            {"call_id": "c1", "name": "create_note",
             "input": {"content": "private"}, "result": {"status": "success"},
             "shown": "note saved"},
        ])

        row = TurnToolCall.objects.get(turn=self.turn)
        self.assertIsNone(row.input_enc)
        self.assertIsNone(row.shown_enc)

    def test_the_writer_caps_the_shown_body_at_two_thousand_characters(self):
        record_tool_calls(self.turn.id, self.room, [
            {"call_id": "c1", "name": "run_command",
             "input": {"command": "ls"}, "result": {"status": "success"},
             "shown": "START " + "x" * 4000 + " END"},
        ])

        row = TurnToolCall.objects.get(turn=self.turn)
        shown = decrypt_body(
            self.room, row.shown_enc, turn_id=row.turn_id, seq=row.seq,
        )
        # Hard-coded: the plan's per-row bound is 2,000 characters, so raising
        # SHOWN_LIMIT must fail this test.
        self.assertLessEqual(len(shown), 2000)
        self.assertIn("characters omitted of", shown)
        self.assertIn(" END", shown)
        self.assertTrue(row.truncated)

    def test_an_unreadable_body_replays_an_honest_record_not_an_ok_result(self):
        record_tool_calls(self.turn.id, self.room, [
            {"call_id": "c1", "name": "get_weather", "input": {"city": "A"},
             "result": {"status": "success"}, "shown": "sunny"},
        ])
        message = self._reply()
        link_turn_calls_to_message(self.turn.id, message.id)
        TurnToolCall.objects.filter(turn=self.turn).update(shown_enc="not-a-blob")

        rows = get_history_rows(self.room, limit=8)
        messages = build_history_messages(rows, viewer_user_id=self.user.id)
        results = [
            block for item in messages
            if isinstance(item.get("content"), list)
            for block in item["content"]
            if block.get("type") == "tool_result"
        ]
        self.assertIn("[record unreadable]", results[0]["content"])

    def test_delegated_rows_are_not_replayed(self):
        self._reply(
            {"call_id": "child", "name": "run_command", "input": {"command": "ls"},
             "result": {"status": "success"}, "shown": "listed",
             "provenance": "delegated", "parent_call_id": "parent"},
        )

        rows = get_history_rows(self.room, limit=8)
        messages = build_history_messages(rows, viewer_user_id=self.user.id)
        uses = [
            block for item in messages
            if isinstance(item.get("content"), list)
            for block in item["content"]
            if block.get("type") == "tool_use"
        ]
        self.assertEqual(uses, [])

    def test_two_turns_sharing_a_provider_call_id_replay_unique_ids(self):
        second = Turn.objects.create(
            room=self.room, user=self.user, state="completed",
        )
        for turn in (self.turn, second):
            record_tool_calls(turn.id, self.room, [
                {"call_id": "call_0", "name": "get_weather",
                 "input": {"city": "Nairobi"}, "result": {"status": "success"},
                 "shown": "sunny"},
            ])
            result = OrchestrationResult(
                full_response="Done.", persist=True, tools=[], model_text="Done.",
                harness="", tool_calls_recorded=True, turn_id=str(turn.id),
            )
            save_turn_reply(self.room, result, user_id=self.user.id)

        rows = get_history_rows(self.room, limit=8)
        messages = build_history_messages(rows, viewer_user_id=self.user.id)
        uses = [
            block["id"] for item in messages
            if isinstance(item.get("content"), list)
            for block in item["content"]
            if block.get("type") == "tool_use"
        ]
        results = [
            block["tool_use_id"] for item in messages
            if isinstance(item.get("content"), list)
            for block in item["content"]
            if block.get("type") == "tool_result"
        ]
        self.assertEqual(len(uses), 2)
        self.assertEqual(len(set(uses)), 2)
        self.assertEqual(sorted(uses), sorted(results))

    def test_a_row_outside_the_history_window_is_not_replayed(self):
        old = self._reply(
            {"call_id": "old", "name": "run_command", "input": {"command": "ls"},
             "result": {"status": "success"}, "shown": "listed"},
        )
        for index in range(10):
            message = Message.objects.create(
                member=self.member, content=f"later {index}",
                timestamp=timezone.now(),
            )
            self.room.chats.add(message)

        rows = get_history_rows(self.room, limit=8)
        self.assertNotIn(old.id, [row[0] for row in rows])
        messages = build_history_messages(rows, viewer_user_id=self.user.id)
        uses = [
            block for item in messages
            if isinstance(item.get("content"), list)
            for block in item["content"]
            if block.get("type") == "tool_use"
        ]
        self.assertEqual(uses, [])

    def test_a_shared_room_does_not_replay_another_members_rows(self):
        from chatbot.history import get_history_rows as read_rows

        User = get_user_model()
        other = User.objects.create_user(username="other-member", password="pw")  # nosec B106
        other_member = Member.objects.select_related("User").get(User=other)
        self.room.participants.add(other_member)
        other_turn = Turn.objects.create(
            room=self.room, user=other, state="completed",
        )
        record_tool_calls(other_turn.id, self.room, [
            {"call_id": "secret", "name": "run_command",
             "input": {"command": "cat secret"}, "result": {"status": "success"},
             "shown": "secret"},
        ])
        result = OrchestrationResult(
            full_response="Done.", persist=True, tools=[], model_text="Done.",
            harness="", tool_calls_recorded=True, turn_id=str(other_turn.id),
        )
        other_message = save_turn_reply(self.room, result, user_id=other.id)
        link_turn_calls_to_message(other_turn.id, other_message.id)

        rows = read_rows(self.room, limit=8)
        messages = build_history_messages(rows, viewer_user_id=self.user.id)
        uses = [
            block for item in messages
            if isinstance(item.get("content"), list)
            for block in item["content"]
            if block.get("type") == "tool_use"
        ]
        self.assertEqual(uses, [])


class StatusMappingTests(SimpleTestCase):
    def test_the_status_table_separates_successes_from_failures(self):
        cases = {
            "success": "ok", "ok": "ok", "completed": "ok",
            "sent": "ok", "delivered": "ok", "healthy": "ok",
            "deleted": "ok", "edited": "ok", "restored": "ok",
            "pending": "ok", "planned": "ok", "active": "ok",
            "booked": "ok",
            "error": "error", "failed": "error", "failure": "error",
            "FAILED": "error",
            "denied": "denied", "skipped": "skipped",
            "declined": "declined", "expired": "expired",
            "superseded": "superseded",
            "budget_skipped": "budget_skipped",
            "policy_stopped": "policy_stopped",
        }
        for status, expected in cases.items():
            with self.subTest(status=status):
                self.assertEqual(status_for_result({"status": status}), expected)

    def test_an_error_key_without_a_success_status_is_a_failure(self):
        self.assertEqual(status_for_result({"error": "boom"}), "error")
        self.assertEqual(
            status_for_result({"status": "", "error": "boom"}), "error",
        )
        self.assertEqual(
            status_for_result({"status": "sent", "error": "x"}), "ok",
        )

    def test_outcome_unknown_and_policy_stop_stay_their_own_status(self):
        self.assertEqual(
            status_for_result({"status": "error", "outcome_unknown": True}),
            "outcome_unknown",
        )
        self.assertEqual(
            status_for_result(
                {"status": "error", "message": "Blocked by the safety policy."},
            ),
            "policy_stopped",
        )
