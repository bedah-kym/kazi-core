"""History loads in a fixed number of queries, whatever the message count (T-F6).

On ``main`` each message costs two extra queries for its ``member.User``; this
asserts the count is flat for 2 and 10 messages.
"""
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from chatbot.consumers import ChatConsumer
from chatbot.models import Chatroom, Member, Message


class HistoryQueryCountTests(TransactionTestCase):
    def _room_with_history(self, count):
        User = get_user_model()
        user = User.objects.create_user(username=f"history-{count}", password="pw")  # nosec B106
        member = Member.objects.select_related("User").get(User=user)
        room = Chatroom.objects.get(participants=member)
        for index in range(count):
            message = Message.objects.create(
                member=member,
                content=f"hello {index}",
                timestamp=timezone.now(),
            )
            room.chats.add(message)
        return user, room

    def _count(self, user, room, limit=8):
        consumer = ChatConsumer()
        consumer.scope = {"user": user}
        with CaptureQueriesContext(connection) as context:
            async_to_sync(consumer.get_history_rows)(room.id, limit=limit)
        return len(context.captured_queries)

    def test_history_query_count_does_not_grow_with_message_count(self):
        small_user, small_room = self._room_with_history(2)
        large_user, large_room = self._room_with_history(10)

        small = self._count(small_user, small_room)
        large = self._count(large_user, large_room)

        self.assertEqual(small, large)
        self.assertLessEqual(large, 2)
