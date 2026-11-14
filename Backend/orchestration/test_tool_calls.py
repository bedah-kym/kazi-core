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
from chatbot.kazi_messages import ORIGIN_TURN, persist_kazi_message
from chatbot.models import Chatroom, Member, Message
from chatbot.transcript import build_history_messages
from orchestration.models import Turn, TurnToolCall
from orchestration.tool_calls import (
    decrypt_body,
    load_tool_calls_for_turn,
    record_from_event,
    record_tool_calls,
    shorten_for_record,
)
from orchestration.tool_record import RECORD_STRING_MAX, tool_record_input


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
        text, _truncated = shorten_for_record("a" * 6000, limit=300)

        self.assertIn("record shortened", text)
        self.assertIn("omitted of 6,000", text)

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
        return persist_kazi_message(
            self.room,
            content=model,
            origin=ORIGIN_TURN,
            turn=self.turn,
            model_text=model,
            tools=None,
            by=self.user.id,
            broadcast=False,
        )

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
        self.assertEqual(result["tool_use_id"], "c1")
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
