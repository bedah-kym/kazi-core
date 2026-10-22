"""Per-message work: one read per thing before the loop starts (T-F6).

The turn counter is taken where the coordinator is entered, so it covers only
the consumer's "message received -> loop start" work. On ``main`` the same turn
re-reads the user, the room, the participants and the shell/autopilot profile,
and history carries two queries per message.
"""
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.db.backends.utils import CursorWrapper
from django.test import TransactionTestCase
from django.utils import timezone

from chatbot.consumers import ChatConsumer
from chatbot.models import Chatroom, Member, Message

# Measured on the branch for this fixture: eight history messages plus the
# message being sent, up to the coordinator entry. On main the same turn costs 30.
MAX_PRE_LOOP_QUERIES = 9


class TurnQueryCountTests(TransactionTestCase):
    def _room_with_history(self, count):
        User = get_user_model()
        user = User.objects.create_user(username=f"turn-{count}", password="pw")  # nosec B106
        member = Member.objects.select_related("User").get(User=user)
        room = Chatroom.objects.get(participants=member)
        for index in range(count):
            message = Message.objects.create(
                member=member,
                content=f"history {index}",
                timestamp=timezone.now(),
            )
            room.chats.add(message)
        return user, member, room

    def test_one_turn_reads_each_thing_once(self):
        user, member, room = self._room_with_history(8)
        seen = {"queries": 0}
        real_execute = CursorWrapper.execute

        def counting_execute(cursor, sql, params=None):
            seen["queries"] += 1
            return real_execute(cursor, sql, params)

        async def fake_handle_message(**kwargs):
            seen["pre_loop"] = seen["queries"]
            raise RuntimeError("stop before the loop")

        consumer = ChatConsumer()
        consumer.scope = {"user": user}
        consumer.room_name = str(room.id)
        consumer.room_group_name = f"chat_{room.id}"
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_send = AsyncMock()
        consumer.send_chat_message = AsyncMock()
        consumer.send_message = AsyncMock()
        consumer.encrypt_message = AsyncMock(return_value={"data": "enc", "nonce": "nonce"})
        consumer.check_key_rotation = AsyncMock()

        with patch("orchestration.coordinator.OrchestrationCoordinator") as coordinator:
            coordinator.return_value.handle_message = AsyncMock(side_effect=fake_handle_message)
            with patch.object(CursorWrapper, "execute", new=counting_execute):
                async_to_sync(consumer.new_message)(
                    {"from": user.username, "message": "@kazi hello", "chatid": str(room.id)}
                )

        self.assertIn("pre_loop", seen)
        self.assertLessEqual(seen["pre_loop"], MAX_PRE_LOOP_QUERIES)
