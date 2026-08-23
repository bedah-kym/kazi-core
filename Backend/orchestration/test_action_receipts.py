from django.contrib.auth import get_user_model
from django.test import TransactionTestCase

from chatbot.models import Chatroom
from orchestration.models import ActionReceipt
from orchestration.action_receipts import fetch_recent_receipts, record_action_receipt


def _receipt_count():
    return ActionReceipt.objects.count()


class AppendOnlyReceiptTests(TransactionTestCase):
    """MR-1: receipts are append-only — repeat actions must not overwrite the
    audit trail, and a cancelled receipt must never be resurrected."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="receipt-user", email="example@example.com", password="fake-token",
        )
        self.room = Chatroom.objects.create()
        self.room_id = self.room.id

    async def _record(self, status="success"):
        return await record_action_receipt(
            user_id=self.user.id,
            room_id=self.room_id,
            action="set_reminder",
            service="reminder",
            params={"content": "call back"},
            result={"reminder_id": 42},
            status=status,
        )

    async def test_repeat_action_appends_rows(self):
        first = await self._record()
        second = await self._record()
        from asgiref.sync import sync_to_async
        self.assertNotEqual(first.id, second.id)
        count = await sync_to_async(_receipt_count)()
        self.assertEqual(count, 2)

    async def test_cancelled_receipt_is_not_resurrected_by_repeat(self):
        receipt = await self._record(status="success")

        def _cancel():
            receipt.status = "cancelled"
            receipt.save(update_fields=["status"])

        from asgiref.sync import sync_to_async
        await sync_to_async(_cancel)()

        await self._record()

        def _rows():
            return list(ActionReceipt.objects.order_by("id").values_list("status", flat=True))

        statuses = await sync_to_async(_rows)()
        self.assertEqual(statuses, ["cancelled", "success"])

    async def test_undo_fetch_still_targets_latest_reversible_success(self):
        await self._record(status="error")
        latest = await self._record()

        receipts = await fetch_recent_receipts(
            user_id=self.user.id, room_id=self.room_id, limit=3, reversible_only=True,
        )
        self.assertEqual([r.id for r in receipts], [latest.id])
