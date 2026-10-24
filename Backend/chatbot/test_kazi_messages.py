"""One door for every Kazi message (#219, T-A4).

Kazi-authored rows are encrypted with the room key, carry their ``origin``,
and only the helper creates them. Out-of-turn origins replay as harness notes,
never as assistant speech (D24).
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from chatbot import tasks as chatbot_tasks
from chatbot.consumers import ChatConsumer
from chatbot.models import Chatroom, Member, Message, Reminder
from chatbot.transcript import build_history_messages

ROOT = Path(__file__).resolve().parents[2]
WATCHED = (
    "Backend/chatbot/consumers.py",
    "Backend/chatbot/tasks.py",
    "Backend/chatbot/views.py",
    "Backend/users/signals.py",
    "Backend/chatbot/management/commands/seed_kazi.py",
)
HELPER = "Backend/chatbot/kazi_messages.py"


def _kazi_create_calls(path: Path):
    """Line numbers and member expressions of Kazi Message.create() calls."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if ast.unparse(node.func) != "Message.objects.create":
            continue
        member = next((kw.value for kw in node.keywords if kw.arg == "member"), None)
        if member is None:
            continue
        expression = ast.unparse(member).lower()
        if "kazi" in expression or "ai_member" in expression:
            yield node.lineno, expression


def _reader(room: Chatroom) -> ChatConsumer:
    consumer = ChatConsumer()
    consumer.aes_gcm = chatbot_tasks._get_room_cipher(room)
    return consumer


def _read(message: Message, room: Chatroom):
    return async_to_sync(_reader(room)._decrypt_stored)(message)


def _room_for(user):
    member = Member.objects.select_related("User").get(User=user)
    return Chatroom.objects.get(participants=member)


class SingleDoorTests(SimpleTestCase):
    def test_kazi_messages_are_created_only_inside_the_helper(self):
        offenders = []
        for relative in WATCHED:
            for line, expression in _kazi_create_calls(ROOT / relative):
                offenders.append(f"{relative}:{line}: member={expression}")
        self.assertEqual(offenders, [])

    def test_the_helper_is_the_one_place_that_creates_a_kazi_message(self):
        helper = ROOT / HELPER
        self.assertTrue(helper.is_file(), f"{HELPER} must exist")
        calls = list(_kazi_create_calls(helper))
        self.assertEqual(len(calls), 1, calls)

    def test_only_the_consumer_still_writes_a_human_message(self):
        writers = (
            "Backend/chatbot/tasks.py",
            "Backend/chatbot/views.py",
            "Backend/users/signals.py",
            "Backend/chatbot/management/commands/seed_kazi.py",
        )
        offenders = []
        for relative in writers:
            source = (ROOT / relative).read_text(encoding="utf-8")
            if "Message.objects.create" in source:
                offenders.append(relative)
        self.assertEqual(offenders, [])


class BroadcastFrameTests(TestCase):
    def test_a_reply_is_linked_to_its_turn(self):
        from chatbot.kazi_messages import persist_kazi_message
        from orchestration.models import Turn

        user = get_user_model().objects.create_user(username="turn-link", password="pw")  # nosec B106
        room = _room_for(user)
        turn = Turn.objects.create(room=room, user=user)

        message = persist_kazi_message(
            room,
            content="reply",
            origin="turn",
            turn=turn,
            model_text="reply",
            broadcast=False,
        )

        self.assertEqual(message.turn_id, turn.id)

    def test_the_saved_frame_is_the_standard_ai_message_saved(self):
        from chatbot.kazi_messages import persist_kazi_message

        user = get_user_model().objects.create_user(username="frame-user", password="pw")  # nosec B106
        room = _room_for(user)
        layer = MagicMock()
        layer.group_send = AsyncMock()

        message = persist_kazi_message(
            room,
            content="hello",
            origin="turn",
            model_text="hello",
            tools=[],
            by=user.id,
            channel_layer=layer,
        )

        layer.group_send.assert_awaited_once()
        group, event = layer.group_send.await_args.args
        self.assertEqual(group, f"chat_{room.id}")
        self.assertEqual(event["type"], "ai_message_saved")
        frame = event["message"]
        self.assertEqual(set(frame), {"id", "member", "content", "timestamp", "parent_id"})
        self.assertEqual(frame["id"], message.id)
        self.assertEqual(frame["member"], "kazi")
        self.assertEqual(frame["content"], "hello")
        self.assertEqual(frame["timestamp"], str(message.timestamp))
        self.assertIsNone(frame["parent_id"])


class TranscriptOriginTests(SimpleTestCase):
    def test_a_turn_origin_is_still_assistant_speech(self):
        messages = build_history_messages([
            (1, "admin", "run it"),
            (2, "kazi", "Done.", {"origin": "turn", "model": "Done.", "tools": []}),
        ])

        self.assertEqual([message["role"] for message in messages], ["user", "assistant"])
        self.assertEqual(messages[1]["content"], "Done.")

    def test_a_non_turn_origin_is_a_harness_note(self):
        messages = build_history_messages([
            (1, "kazi", "Reminder: buy milk", {"origin": "reminder"}),
            (2, "admin", "what did you just tell me?"),
        ])

        self.assertEqual([message["role"] for message in messages], ["user"])
        self.assertEqual(
            messages[0]["content"],
            "[Harness: reminder: Reminder: buy milk]\nwhat did you just tell me?",
        )


class ReminderOriginTests(TestCase):
    def test_a_delivered_reminder_is_a_harness_note_not_assistant_speech(self):
        user = get_user_model().objects.create_user(username="reminder-user", password="pw")  # nosec B106
        room = _room_for(user)
        reminder = Reminder.objects.create(
            user=user,
            room=room,
            content="buy milk",
            scheduled_time=timezone.now() + timezone.timedelta(hours=1),
        )
        layer = MagicMock()
        layer.group_send = AsyncMock()

        with patch("chatbot.kazi_messages.get_channel_layer", return_value=layer):
            self.assertTrue(chatbot_tasks._deliver_reminder_to_chat(reminder))

        message = room.chats.filter(
            member__User__username="kazi"
        ).order_by("-id").first()
        content, payload = _read(message, room)
        self.assertEqual(content, "Reminder: buy milk")
        self.assertEqual(payload.get("origin"), "reminder")

        rows = async_to_sync(_reader(room).get_history_rows)(
            room.id, limit=10, room=room,
        )
        history = build_history_messages(rows)
        assistant_text = "\n".join(
            message["content"] for message in history
            if message["role"] == "assistant" and isinstance(message["content"], str)
        )
        self.assertNotIn("Reminder: buy milk", assistant_text)
        self.assertIn("[Harness:", "\n".join(
            message["content"] for message in history
            if message["role"] == "user" and isinstance(message["content"], str)
        ))


class WelcomeEncryptionTests(TestCase):
    def test_a_new_users_welcome_is_stored_encrypted_with_its_origin(self):
        user = get_user_model().objects.create_user(username="welcome-user", password="pw")  # nosec B106
        room = _room_for(user)
        message = room.chats.filter(
            member__User__username="kazi"
        ).order_by("id").first()

        self.assertIsNotNone(message)
        stored = json.loads(message.content)
        self.assertIn("data", stored)
        self.assertIn("nonce", stored)
        self.assertNotIn("Kazi", message.content)

        content, payload = _read(message, room)
        self.assertIn("Kazi", content)
        self.assertEqual(payload.get("origin"), "welcome")


class LegacyPlaintextTests(TestCase):
    def test_a_plaintext_kazi_row_still_reads(self):
        user = get_user_model().objects.create_user(username="legacy-user", password="pw")  # nosec B106
        room = _room_for(user)
        kazi_user, _ = get_user_model().objects.get_or_create(username="kazi")
        kazi_member, _ = Member.objects.get_or_create(User=kazi_user)
        message = Message.objects.create(
            member=kazi_member,
            content="plain old welcome",
            timestamp=timezone.now(),
        )
        room.chats.add(message)

        content, payload = _read(message, room)

        self.assertEqual(content, "plain old welcome")
        self.assertEqual(payload, {})
