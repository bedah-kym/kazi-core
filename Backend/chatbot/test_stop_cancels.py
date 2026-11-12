"""#216b-3: an exact stop cancels the running turn before its next step.

The tests drive the real consumer (``WebsocketCommunicator``), the real runner,
real Turn rows and the in-memory channel layer. Only the model is faked, at
``orchestration.agent_loop.get_llm_client``. The mid-run stop races for real:
an event at the seam between tool A's receipt and the next model call.
"""
from __future__ import annotations

import asyncio
import json
import threading
from datetime import timedelta
from unittest import skipIf
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync, sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

import chatbot.routing
from chatbot.kazi_messages import encrypt_for_room
from chatbot.models import Chatroom, Member, Message
from chatbot.turn_runner import (
    STOP_COMMITTING_NOTICE,
    STOP_FAILED_NOTICE,
    STOP_NOTICE,
    TurnRunner,
)
from orchestration import agent_loop
from orchestration import turn_leases
from orchestration import turns as turns_mod
from orchestration.action_receipts import ActionReceipt
from orchestration.coordinator import (
    STOPPED_TURN_NOTE,
    OrchestrationCoordinator,
)
from orchestration.models import Turn
from orchestration.shell.profiles import PROFILES
from orchestration.turns import USER_CANCEL, create_turn
from workflows.models import WorkflowApprovalRecord


def _text(text):
    return {"type": "text", "text": text}


def _tool(call_id, name, tool_input):
    return {"type": "tool_use", "id": call_id, "name": name, "input": tool_input}


def _response(blocks, stop_reason="end_turn"):
    return {
        "content": blocks,
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


REMINDER_A = {"content": "a", "time": "2026-11-12 09:00"}
REMINDER_B = {"content": "b", "time": "2026-11-12 10:00"}


class _FakeLLM:
    def __init__(self):
        self.calls = []
        self._responses = []
        self.before_call = None

    def queue(self, *responses):
        self._responses.extend(responses)
        return self

    def default_provider(self, *args, **kwargs):
        return "deepseek"

    def record_work_tokens(self, *args, **kwargs):
        return None

    async def check_hourly_quota(self, *args, **kwargs):
        return None

    async def create_message(self, **kwargs):
        snapshot = dict(kwargs)
        snapshot["messages"] = list(kwargs.get("messages") or [])
        self.calls.append(snapshot)
        if self.before_call is not None:
            await self.before_call(len(self.calls))
        response = self._responses.pop(0) if self._responses else _response(
            [_text("ok")],
        )
        return await response() if callable(response) else response


class _ForceUser:
    def __init__(self, inner, user):
        self.inner = inner
        self.user = user

    async def __call__(self, scope, receive, send):
        scope = dict(scope)
        scope["user"] = self.user
        return await self.inner(scope, receive, send)


@override_settings(
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
    TURN_QUEUE_MAX=3,
    TURN_CANCEL_POLL_SECONDS=1,
    CONSENT_SKEW_SECONDS=0,
)
class StopCancelsWebsocketTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        turn_leases._LIVE_TURNS.clear()
        turn_leases._FENCED_TURNS.clear()
        turn_leases._LAST_RENEWAL.clear()
        turn_leases._CANCELLED_TURNS.clear()
        agent_loop._circuit_state.clear()
        self.addCleanup(agent_loop._circuit_state.clear)
        self.addCleanup(turn_leases._CANCELLED_TURNS.clear)

        import chatbot.turn_runner as runner_mod

        self.addCleanup(
            setattr, runner_mod, "_DEFAULT_RUNNER", runner_mod._DEFAULT_RUNNER,
        )
        runner_mod._DEFAULT_RUNNER = TurnRunner()
        self.runner = runner_mod._DEFAULT_RUNNER

        User = get_user_model()
        self.user = User.objects.create_user(username="stopper", password="pw")  # nosec B106 — fake test credential
        self.member = Member.objects.select_related("User").get(User=self.user)
        self.room = Chatroom.objects.get(participants=self.member)
        self.communicators = []

        self.llm = _FakeLLM()
        self._patches = [
            patch("orchestration.agent_loop.get_llm_client", return_value=self.llm),
            patch(
                "orchestration.agent_loop._resolve_persona",
                new=AsyncMock(return_value=None),
            ),
            patch("orchestration.agent_loop.enforce_agent_caps", return_value=True),
            patch("orchestration.agent_loop._search_backend_available", return_value=False),
            patch("orchestration.agent_loop.execute_tool", new_callable=AsyncMock),
            patch("orchestration.agent_loop.preview_tool", new_callable=AsyncMock),
            patch(
                "orchestration.shell.environment.get_shell_environment",
                new=AsyncMock(return_value={}),
            ),
        ]
        started = [p.start() for p in self._patches]
        self.addCleanup(lambda: [p.stop() for p in self._patches])
        started[4].return_value = {"status": "success", "data": {"ok": True}}
        started[5].return_value = None
        self.execute_tool = started[4]

    def _run(self, coro):
        async def _await():
            try:
                return await coro
            finally:
                for comm in self.communicators:
                    try:
                        await comm.disconnect()
                    except Exception:
                        pass
                self.communicators.clear()
                await turn_leases.stop_heartbeat()
                turn_leases._LIVE_TURNS.clear()
                turn_leases._FENCED_TURNS.clear()
                turn_leases._LAST_RENEWAL.clear()
                turn_leases._CANCELLED_TURNS.clear()

        return async_to_sync(_await)()

    async def _connect(self):
        app = _ForceUser(
            URLRouter(chatbot.routing.websocket_urlpatterns), self.user,
        )
        comm = WebsocketCommunicator(app, f"/ws/chat/{self.room.id}/")
        connected, _ = await comm.connect()
        self.assertTrue(connected)
        await comm.receive_json_from(timeout=2)  # presence snapshot
        self.communicators.append(comm)
        return comm

    async def _send(self, comm, text):
        await comm.send_json_to({
            "command": "new_message",
            "from": self.user.username,
            "message": text,
            "chatid": str(self.room.id),
        })

    async def _wait_for(self, predicate, timeout=8.0):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if await sync_to_async(predicate)():
                return True
            await asyncio.sleep(0.02)
        return False

    async def _wait_flag(self, cancel_requested, turn_id, timeout=5.0):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if await sync_to_async(cancel_requested)(turn_id):
                return True
            await asyncio.sleep(0.02)
        return False

    def _queued_request(self, text):
        content = encrypt_for_room(self.room, {"content": text})
        message = Message.objects.create(
            member=self.member,
            content=json.dumps(content),
            timestamp=timezone.now(),
        )
        self.room.chats.add(message)
        return create_turn(
            room_id=self.room.id,
            user_id=self.user.id,
            request_message_id=message.id,
        )

    def _notice_texts(self):
        from chatbot.history import decrypt_stored

        texts = []
        for message in Message.objects.filter(member__User__username="kazi"):
            content, payload = decrypt_stored(self.room, message)
            if (payload or {}).get("origin") == "turn_notice":
                texts.append(content)
        return texts

    def _running_turn(self):
        return Turn.objects.get(state="running")

    def _committing_turn(self):
        turn = Turn.objects.create(
            room=self.room, user=self.user, state="running",
            started_at=timezone.now(), attempt=1,
        )
        self.assertTrue(turns_mod.transition(
            str(turn.pk), turn.version, "committing",
            from_state="running", end_reason="finished",
        ))
        turn.refresh_from_db()
        return turn

    def _saved_reply(self, turn_id):
        from chatbot.history import decrypt_stored

        message = Message.objects.filter(
            turn_id=turn_id, member__User__username="kazi",
        ).order_by("-timestamp").first()
        if message is None:
            return "", {}
        content, payload = decrypt_stored(self.room, message)
        return content, (payload or {})

    def _arm_autopilot(self):
        from orchestration.shell import autopilot
        from users.models import UserProfile

        UserProfile.objects.get_or_create(user=self.user)
        with patch("orchestration.shell.autopilot.can_arm", return_value=True):
            self.assertTrue(
                autopilot.arm(self.user.id, self.room.id, armed_by=self.user.id),
            )

    def _is_autopilot_armed(self):
        from orchestration.shell import autopilot

        return autopilot.is_armed(self.user.id, self.room.id)

    def _open_profile(self):
        return patch(
            "orchestration.shell.profiles.resolve_profile",
            return_value=PROFILES["open"],
        )

    async def _pause_for_blocks(self, blocks, *, text="@kazi do it"):
        self.llm.queue(_response(blocks, stop_reason="tool_use"))
        comm = await self._connect()
        await self._send(comm, text)
        self.assertTrue(await self._wait_for(
            lambda: Turn.objects.filter(state="paused").exists(),
        ))
        return comm, await sync_to_async(
            lambda: Turn.objects.get(state="paused")
        )()

    # --- must-fail tests -------------------------------------------------

    def test_a_mid_run_stop_keeps_tool_a_and_never_runs_b(self):
        receipts_done = asyncio.Event()
        release = asyncio.Event()
        real_receipt = agent_loop._record_receipt

        async def receipt_wrapper(name, *args, **kwargs):
            await real_receipt(name, *args, **kwargs)
            if name == "set_reminder" and not receipts_done.is_set():
                receipts_done.set()
                await release.wait()

        self.llm.queue(
            _response(
                [_text("working"), _tool("a1", "set_reminder", REMINDER_A)],
                stop_reason="tool_use",
            ),
            _response(
                [_tool("b1", "set_reminder", REMINDER_B)],
                stop_reason="tool_use",
            ),
        )

        async def scenario():
            with patch(
                "orchestration.agent_loop._record_receipt",
                new=receipt_wrapper,
            ):
                comm = await self._connect()
                await self._send(comm, "@kazi set reminder a")
                await asyncio.wait_for(receipts_done.wait(), timeout=8)
                turn = await sync_to_async(self._running_turn)()
                await self._send(comm, "@kazi stop")
                self.assertTrue(await self._wait_for(
                    lambda: Turn.objects.filter(
                        pk=turn.pk, cancel_requested_at__isnull=False,
                    ).exists(),
                    timeout=5,
                ))
                self.assertTrue(await self._wait_for(
                    lambda: any(STOP_NOTICE in text for text in self._notice_texts()),
                    timeout=5,
                ))
                notice = next(
                    text for text in await sync_to_async(self._notice_texts)()
                    if STOP_NOTICE in text
                )
                self.assertIn("The step already running will finish", notice)
                self.assertNotIn("Stopping before the next step", notice)
                release.set()
                self.assertTrue(await self._wait_for(
                    lambda: Turn.objects.filter(
                        pk=turn.pk, state="cancelled",
                    ).exists(),
                    timeout=8,
                ))
                await self.runner.drain(timeout=15)
                return turn.pk

        turn_id = self._run(scenario())

        turn = Turn.objects.get(pk=turn_id)
        self.assertEqual(turn.state, "cancelled")
        self.assertEqual(turn.end_reason, USER_CANCEL)
        self.assertTrue(turn.partial)
        self.assertEqual(self.execute_tool.await_count, 1)
        self.assertEqual(len(self.llm.calls), 1)
        self.assertEqual(
            ActionReceipt.objects.filter(action="set_reminder").count(), 1,
        )
        self.assertFalse(Turn.objects.filter(state="completed").exists())
        content, _payload = self._saved_reply(turn_id)
        self.assertNotIn("marked interrupted", content)
        self.assertIn("you stopped this turn", content)

    def test_autopilot_off_mid_run_disarms_without_stopping_the_turn(self):
        # The stop set is is_cancel_command (rev 3, Q1). "autopilot off" only
        # disarms: the rest of the turn asks as usual, it is not cancelled.
        receipts_done = asyncio.Event()
        release = asyncio.Event()
        real_receipt = agent_loop._record_receipt

        async def receipt_wrapper(name, *args, **kwargs):
            await real_receipt(name, *args, **kwargs)
            if name == "set_reminder" and not receipts_done.is_set():
                receipts_done.set()
                await release.wait()

        self.llm.queue(
            _response(
                [_text("working"), _tool("a1", "set_reminder", REMINDER_A)],
                stop_reason="tool_use",
            ),
            _response([_text("all done")]),
        )

        async def scenario():
            with patch(
                "orchestration.agent_loop._record_receipt",
                new=receipt_wrapper,
            ):
                comm = await self._connect()
                await self._send(comm, "@kazi set reminder a")
                await asyncio.wait_for(receipts_done.wait(), timeout=8)
                turn = await sync_to_async(self._running_turn)()
                await self._send(comm, "@kazi autopilot off")
                self.assertTrue(await self._wait_for(
                    lambda: any(
                        "Autopilot was not armed" in text
                        for text in self._notice_texts()
                    ),
                    timeout=5,
                ))
                release.set()
                self.assertTrue(await self._wait_for(
                    lambda: Turn.objects.filter(
                        pk=turn.pk, state="completed",
                    ).exists(),
                    timeout=8,
                ))
                await self.runner.drain(timeout=15)
                return turn.pk

        turn_id = self._run(scenario())

        turn = Turn.objects.get(pk=turn_id)
        self.assertEqual(turn.state, "completed")
        self.assertIsNone(turn.cancel_requested_at)
        self.assertEqual(len(self.llm.calls), 2)
        self.assertFalse(any(
            STOP_NOTICE in text for text in self._notice_texts()
        ))

    def test_a_stop_notice_says_autopilot_is_off_when_it_disarmed(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def before(n):
            if n == 1:
                entered.set()
                await release.wait()

        self.llm.before_call = before
        self.llm.queue(_response([_text("working")]))
        self._arm_autopilot()

        async def scenario():
            comm = await self._connect()
            await self._send(comm, "@kazi long thing")
            await asyncio.wait_for(entered.wait(), timeout=5)
            await self._send(comm, "@kazi stop")
            self.assertTrue(await self._wait_for(
                lambda: any(
                    "Autopilot is now off" in text
                    for text in self._notice_texts()
                ),
                timeout=5,
            ))
            release.set()
            await self.runner.drain(timeout=15)

        self._run(scenario())

        self.assertFalse(self._is_autopilot_armed())
        self.assertTrue(any(
            "Autopilot is now off" in text for text in self._notice_texts()
        ))

    def test_a_stop_from_another_worker_is_honoured_within_the_poll(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def before(n):
            if n == 1:
                entered.set()
                await release.wait()

        self.llm.before_call = before
        self.llm.queue(_response(
            [_text("working"), _tool("b1", "set_reminder", REMINDER_B)],
            stop_reason="tool_use",
        ))

        async def scenario():
            comm = await self._connect()
            await self._send(comm, "@kazi set reminder b")
            await asyncio.wait_for(entered.wait(), timeout=5)
            turn = await sync_to_async(self._running_turn)()
            # Another worker stamps the flag straight in the database.
            await sync_to_async(
                lambda: Turn.objects.filter(pk=turn.pk).update(
                    cancel_requested_at=timezone.now(),
                )
            )()
            self.assertTrue(await self._wait_flag(
                turn_leases.cancel_requested, str(turn.pk), timeout=6,
            ))
            release.set()
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(
                    pk=turn.pk, state="cancelled",
                ).exists(),
                timeout=8,
            ))
            await self.runner.drain(timeout=15)
            return turn.pk

        turn_id = self._run(scenario())

        turn = Turn.objects.get(pk=turn_id)
        self.assertEqual(turn.state, "cancelled")
        self.assertEqual(turn.end_reason, USER_CANCEL)
        self.assertTrue(turn.partial)
        self.assertEqual(self.execute_tool.await_count, 0)
        self.assertEqual(len(self.llm.calls), 1)

    def test_a_stop_at_the_cap_is_not_refused_and_cancels_the_queue(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def before(n):
            if n == 1:
                entered.set()
                await release.wait()

        self.llm.before_call = before
        self.llm.queue(_response(
            [_text("working"), _tool("b1", "set_reminder", REMINDER_B)],
            stop_reason="tool_use",
        ))

        async def scenario():
            comm = await self._connect()
            await self._send(comm, "@kazi set reminder b")
            await asyncio.wait_for(entered.wait(), timeout=5)
            turn = await sync_to_async(self._running_turn)()
            # The queue is at the cap behind the running turn.
            for text in ("one", "two", "three"):
                await sync_to_async(self._queued_request)(f"@kazi {text}")
            await self._send(comm, "@kazi stop")
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(
                    state__in=("queued", "committing"),
                ).count() == 0,
                timeout=5,
            ))
            self.assertTrue(await self._wait_for(
                lambda: all(
                    reason == USER_CANCEL
                    for reason in Turn.objects.filter(
                        state="cancelled",
                    ).values_list("end_reason", flat=True)
                ) and Turn.objects.filter(state="cancelled").count() == 3,
                timeout=5,
            ))
            release.set()
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(
                    pk=turn.pk, state="cancelled",
                ).exists(),
                timeout=8,
            ))
            await self.runner.drain(timeout=15)
            return turn.pk

        turn_id = self._run(scenario())

        self.assertEqual(Turn.objects.count(), 4)
        self.assertEqual(Turn.objects.filter(state="cancelled").count(), 4)
        self.assertEqual(
            Turn.objects.filter(end_reason=USER_CANCEL).count(), 4,
        )
        self.assertFalse(Turn.objects.filter(state="completed").exists())
        notices = self._notice_texts()
        stop_notice = next(text for text in notices if STOP_NOTICE in text)
        self.assertIn("3 waiting message(s) weren't answered", stop_notice)
        self.assertFalse(any("3 messages waiting" in text for text in notices))
        self.assertEqual(Turn.objects.get(pk=turn_id).state, "cancelled")

    def test_a_stop_just_after_committing_does_not_claim_a_stop(self):
        turn = self._committing_turn()

        async def scenario():
            comm = await self._connect()
            await self._send(comm, "@kazi stop")
            self.assertTrue(await self._wait_for(
                lambda: any(
                    STOP_COMMITTING_NOTICE in text
                    for text in self._notice_texts()
                ),
                timeout=5,
            ))

        self._run(scenario())

        turn.refresh_from_db()
        self.assertEqual(turn.state, "committing")
        self.assertIsNone(turn.cancel_requested_at)
        notices = self._notice_texts()
        self.assertIn(STOP_COMMITTING_NOTICE, notices)
        self.assertNotIn(STOP_NOTICE, notices)

    def test_a_stop_also_ends_a_waiting_pause(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def before(n):
            if n == 1:
                entered.set()
                await release.wait()

        self.llm.before_call = before
        self.llm.queue(_response(
            [_text("working"), _tool("b1", "set_reminder", REMINDER_B)],
            stop_reason="tool_use",
        ))

        def _make_pause():
            return Turn.objects.create(
                room=self.room, user=self.user, state="paused",
                pause_kind="approval", started_at=timezone.now(),
                paused_at=timezone.now(),
            )

        async def scenario():
            comm = await self._connect()
            await self._send(comm, "@kazi set reminder b")
            await asyncio.wait_for(entered.wait(), timeout=5)
            turn = await sync_to_async(self._running_turn)()
            paused = await sync_to_async(_make_pause)()
            await self._send(comm, "@kazi stop")
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(
                    pk=paused.pk, state="cancelled",
                ).exists(),
                timeout=5,
            ))
            release.set()
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(
                    pk=turn.pk, state="cancelled",
                ).exists(),
                timeout=8,
            ))
            await self.runner.drain(timeout=15)

        self._run(scenario())

        self.assertFalse(Turn.objects.filter(
            state__in=("running", "committing", "queued", "paused"),
        ).exists())
        self.assertEqual(Turn.objects.filter(state="cancelled").count(), 2)

    def test_the_next_turn_sees_the_stop_line_from_the_record(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def before(n):
            if n == 1:
                entered.set()
                await release.wait()

        self.llm.before_call = before
        self.llm.queue(
            _response(
                [_text("working"), _tool("b1", "set_reminder", REMINDER_B)],
                stop_reason="tool_use",
            ),
            _response([_text("first follow-up")]),
            _response([_text("second follow-up")]),
        )

        async def scenario():
            comm = await self._connect()
            await self._send(comm, "@kazi set reminder b")
            await asyncio.wait_for(entered.wait(), timeout=5)
            turn = await sync_to_async(self._running_turn)()
            await self._send(comm, "@kazi stop")
            self.assertTrue(await self._wait_flag(
                turn_leases.cancel_requested, str(turn.pk), timeout=5,
            ))
            release.set()
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(
                    pk=turn.pk, state="cancelled",
                ).exists(),
                timeout=8,
            ))
            await self.runner.drain(timeout=15)

            await self._send(comm, "@kazi what now")
            self.assertTrue(await self._wait_for(
                lambda: len(self.llm.calls) >= 2,
                timeout=8,
            ))
            await self.runner.drain(timeout=15)

            await self._send(comm, STOPPED_TURN_NOTE)
            self.assertTrue(await self._wait_for(
                lambda: len(self.llm.calls) >= 3,
                timeout=8,
            ))
            await self.runner.drain(timeout=15)

        self._run(scenario())

        second = json.dumps(self.llm.calls[1]["messages"], default=str)
        self.assertIn(STOPPED_TURN_NOTE, second)
        self.assertEqual(second.count(STOPPED_TURN_NOTE), 1)
        third = json.dumps(self.llm.calls[2]["messages"], default=str)
        # The user's own identical words are data, not a harness line.
        self.assertEqual(third.count(STOPPED_TURN_NOTE), 1)

    def test_the_stop_path_adds_one_flag_write_and_one_bulk_cancel(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        Turn.objects.create(
            room=self.room, user=self.user, state="running",
            started_at=timezone.now(), attempt=1,
        )
        for text in ("one", "two", "three"):
            self._queued_request(f"@kazi {text}")

        with CaptureQueriesContext(connection) as queries:
            result = turns_mod.request_stop_for_key(
                self.room.id, self.user.id,
            )

        updates = [
            query["sql"] for query in queries.captured_queries
            if query["sql"].lstrip().upper().startswith("UPDATE")
        ]
        self.assertEqual(len(updates), 2, updates)
        self.assertTrue(result.flagged)
        self.assertEqual(result.queued_cancelled, 3)
        self.assertEqual(
            Turn.objects.filter(state="cancelled", end_reason=USER_CANCEL).count(),
            3,
        )

    @skipIf(
        connection.vendor == "sqlite",
        "SQLite serialises writers; the two-connection race is the Postgres "
        "guarantee. The deterministic seam test covers the logic on SQLite.",
    )
    def test_a_stop_during_a_resume_wins_when_the_stop_commits_first(self):
        entered = threading.Event()
        release = threading.Event()
        real_continue = turns_mod.continue_pause

        def blocking_continue(placeholder, paused, *, message_id=None):
            entered.set()
            release.wait(10)
            return real_continue(placeholder, paused, message_id=message_id)

        tool = _tool("c1", "send_email", {"to": "a@b.c", "body": "hi"})

        async def scenario():
            with patch(
                "chatbot.turn_runner.continue_pause_turn",
                side_effect=blocking_continue,
            ), patch(
                "chatbot.turn_runner._writes_are_thread_sensitive",
                return_value=False,
            ):
                comm, paused = await self._pause_for_blocks([tool])
                await self._send(comm, "@kazi yes")
                entered_ok = await asyncio.get_running_loop().run_in_executor(
                    None, entered.wait, 5,
                )
                self.assertTrue(entered_ok, "the pause swap never ran")
                placeholder = await sync_to_async(self._running_turn)()
                await self._send(comm, "@kazi stop")
                self.assertTrue(await self._wait_for(
                    lambda: Turn.objects.filter(
                        pk=placeholder.pk, cancel_requested_at__isnull=False,
                    ).exists(),
                    timeout=5,
                ))
                release.set()
                self.assertTrue(await self._wait_for(
                    lambda: not Turn.objects.filter(
                        state__in=("running", "committing", "paused"),
                    ).exists(),
                    timeout=8,
                ))
                await self.runner.drain(timeout=15)
                return placeholder.pk, paused.pk

        placeholder_id, paused_id = self._run(scenario())

        placeholder = Turn.objects.get(pk=placeholder_id)
        paused = Turn.objects.get(pk=paused_id)
        self.assertEqual(placeholder.state, "cancelled")
        self.assertEqual(placeholder.end_reason, USER_CANCEL)
        self.assertEqual(paused.state, "cancelled")
        self.assertEqual(self.execute_tool.await_count, 0)
        self.assertEqual(len(self.llm.calls), 1)
        self.assertFalse(
            WorkflowApprovalRecord.objects.filter(status="pending").exists(),
        )
        content, _payload = self._saved_reply(placeholder_id)
        self.assertNotIn("already resumed elsewhere", content)
        self.assertIn("you stopped this turn", content)

    @skipIf(
        connection.vendor == "sqlite",
        "SQLite serialises writers; the two-connection race is the Postgres "
        "guarantee. The deterministic seam test covers the logic on SQLite.",
    )
    def test_a_stop_during_a_resume_wins_when_the_swap_commits_first(self):
        entered = threading.Event()
        release = threading.Event()
        real_continue = turns_mod.continue_pause

        def blocking_continue(placeholder, paused, *, message_id=None):
            ref = real_continue(placeholder, paused, message_id=message_id)
            entered.set()
            release.wait(10)
            return ref

        tool = _tool("c1", "send_email", {"to": "a@b.c", "body": "hi"})

        async def scenario():
            with patch(
                "chatbot.turn_runner.continue_pause_turn",
                side_effect=blocking_continue,
            ), patch(
                "chatbot.turn_runner._writes_are_thread_sensitive",
                return_value=False,
            ):
                comm, paused = await self._pause_for_blocks([tool])
                await self._send(comm, "@kazi yes")
                entered_ok = await asyncio.get_running_loop().run_in_executor(
                    None, entered.wait, 5,
                )
                self.assertTrue(entered_ok, "the pause swap never committed")
                placeholder = await sync_to_async(
                    lambda: Turn.objects.get(state="superseded")
                )()
                await self._send(comm, "@kazi stop")
                self.assertTrue(await self._wait_for(
                    lambda: Turn.objects.filter(
                        pk=paused.pk, cancel_requested_at__isnull=False,
                    ).exists(),
                    timeout=5,
                ))
                release.set()
                self.assertTrue(await self._wait_for(
                    lambda: Turn.objects.filter(
                        pk=paused.pk, state="cancelled",
                    ).exists(),
                    timeout=8,
                ))
                await self._send(comm, "@kazi ok")
                self.assertTrue(await self._wait_for(
                    lambda: len(self.llm.calls) >= 2,
                    timeout=8,
                ))
                await self.runner.drain(timeout=15)
                return paused.pk, placeholder.pk

        paused_id, placeholder_id = self._run(scenario())

        resumed = Turn.objects.get(pk=paused_id)
        self.assertEqual(resumed.state, "cancelled")
        self.assertEqual(resumed.end_reason, USER_CANCEL)
        self.assertEqual(Turn.objects.get(pk=placeholder_id).state, "superseded")
        self.assertEqual(self.execute_tool.await_count, 0)
        self.assertEqual(len(self.llm.calls), 2)
        self.assertFalse(
            WorkflowApprovalRecord.objects.filter(status="pending").exists(),
        )
        next_call = self.llm.calls[1]
        messages = json.dumps(next_call["messages"], default=str)
        self.assertIn(STOPPED_TURN_NOTE, messages)
        self.assertEqual(messages.count(STOPPED_TURN_NOTE), 1)

    def test_the_stop_line_ignores_the_queued_rows_a_stop_dropped(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def before(n):
            if n == 1:
                entered.set()
                await release.wait()

        self.llm.before_call = before
        self.llm.queue(
            _response(
                [_text("working"), _tool("b1", "set_reminder", REMINDER_B)],
                stop_reason="tool_use",
            ),
            _response([_text("next answer")]),
        )

        async def scenario():
            comm = await self._connect()
            await self._send(comm, "@kazi set reminder b")
            await asyncio.wait_for(entered.wait(), timeout=5)
            turn = await sync_to_async(self._running_turn)()
            for text in ("one", "two", "three"):
                await sync_to_async(self._queued_request)(f"@kazi {text}")
            await self._send(comm, "@kazi stop")
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(state="queued").count() == 0,
                timeout=5,
            ))
            release.set()
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(
                    pk=turn.pk, state="cancelled",
                ).exists(),
                timeout=8,
            ))
            await self.runner.drain(timeout=15)
            await self._send(comm, "@kazi next")
            self.assertTrue(await self._wait_for(
                lambda: len(self.llm.calls) >= 2,
                timeout=8,
            ))
            await self.runner.drain(timeout=15)

        self._run(scenario())

        next_call = self.llm.calls[1]
        messages = json.dumps(next_call["messages"], default=str)
        self.assertIn(STOPPED_TURN_NOTE, messages)
        self.assertEqual(messages.count(STOPPED_TURN_NOTE), 1)

    def test_a_committing_stop_drops_the_queue_and_gives_no_stop_line(self):
        turn = self._committing_turn()
        for text in ("one", "two", "three"):
            self._queued_request(f"@kazi {text}")

        def _finish_committing():
            row = Turn.objects.get(pk=turn.pk)
            return turns_mod.transition(
                str(row.pk), row.version, "completed",
                from_state="committing",
            )

        async def scenario():
            comm = await self._connect()
            await self._send(comm, "@kazi stop")
            self.assertTrue(await self._wait_for(
                lambda: any(
                    STOP_COMMITTING_NOTICE in text
                    for text in self._notice_texts()
                ),
                timeout=5,
            ))
            notice = next(
                text for text in await sync_to_async(self._notice_texts)()
                if STOP_COMMITTING_NOTICE in text
            )
            self.assertIn("3 waiting message(s) weren't answered", notice)
            self.assertTrue(await sync_to_async(_finish_committing)())
            await self._send(comm, "@kazi next")
            self.assertTrue(await self._wait_for(
                lambda: len(self.llm.calls) >= 1,
                timeout=8,
            ))
            await self.runner.drain(timeout=15)

        self._run(scenario())

        call = next(
            call for call in self.llm.calls
            if "next" in json.dumps(call["messages"], default=str)
        )
        self.assertNotIn(
            STOPPED_TURN_NOTE, json.dumps(call["messages"], default=str),
        )
        self.assertEqual(
            Turn.objects.filter(state="cancelled", end_reason=USER_CANCEL).count(),
            3,
        )

    def test_a_stop_before_a_pause_edge_ends_cancelled_without_a_prompt(self):
        tool = _tool("c1", "send_email", {"to": "a@b.c", "body": "hi"})
        self.llm.queue(_response([tool], stop_reason="tool_use"))

        async def preview(name, tool_input, context):
            turn_id = context.get("turn_id")
            await sync_to_async(
                lambda: Turn.objects.filter(pk=turn_id).update(
                    cancel_requested_at=timezone.now(),
                )
            )()
            return None

        async def scenario():
            with patch(
                "orchestration.agent_loop.preview_tool", side_effect=preview,
            ):
                comm = await self._connect()
                await self._send(comm, "@kazi send it")
                self.assertTrue(await self._wait_for(
                    lambda: Turn.objects.filter(
                        state="cancelled", end_reason=USER_CANCEL,
                    ).exists(),
                    timeout=8,
                ))
                await self.runner.drain(timeout=15)
                return await sync_to_async(
                    lambda: Turn.objects.get(end_reason=USER_CANCEL)
                )()

        turn = self._run(scenario())

        self.assertEqual(turn.state, "cancelled")
        self.assertEqual(self.execute_tool.await_count, 0)
        self.assertFalse(
            WorkflowApprovalRecord.objects.filter(status="pending").exists(),
        )
        content, _payload = self._saved_reply(turn.pk)
        self.assertNotIn("Please confirm", content)
        self.assertIn("you stopped this turn", content)

    def test_a_fence_wins_over_a_stop_at_a_pause_edge(self):
        # A stop and a fence that both hold resolve as the fence: the row is no
        # longer this owner's to end as the person's cancel (CodeRabbit, #306).
        tool = _tool("c1", "send_email", {"to": "a@b.c", "body": "hi"})
        self.llm.queue(_response([tool], stop_reason="tool_use"))
        real_persist = agent_loop._persist_pause_state

        async def persist_then_stop_and_fence(context, *args, **kwargs):
            # After the loop's last lease check and before the coordinator
            # handles the confirmation: a stop lands and the lease is lost.
            saved = await real_persist(context, *args, **kwargs)
            turn_id = context.get("turn_id")
            await sync_to_async(
                lambda: Turn.objects.filter(pk=turn_id).update(
                    cancel_requested_at=timezone.now(),
                )
            )()
            with turn_leases._REGISTRY_LOCK:
                turn_leases._FENCED_TURNS.add(str(turn_id))
            return saved

        async def scenario():
            with patch(
                "orchestration.agent_loop._persist_pause_state",
                new=persist_then_stop_and_fence,
            ):
                comm = await self._connect()
                await self._send(comm, "@kazi send it")
                self.assertTrue(await self._wait_for(
                    lambda: not Turn.objects.filter(state="running").exists()
                    and Turn.objects.exists(),
                    timeout=8,
                ))
                await self.runner.drain(timeout=15)
                return await sync_to_async(
                    lambda: Turn.objects.order_by("-created_at").first()
                )()

        turn = self._run(scenario())

        self.assertNotEqual(turn.end_reason, USER_CANCEL)
        self.assertEqual(self.execute_tool.await_count, 0)
        content, _payload = self._saved_reply(turn.pk)
        self.assertNotIn("you stopped this turn", content)

    def test_a_lost_cancel_write_at_a_pause_edge_adds_no_stop_line(self):
        # The reaper ends the row between the stop's read and its cancel write;
        # the write loses, so this owner must not claim the stop in the reply.
        tool = _tool("c1", "send_email", {"to": "a@b.c", "body": "hi"})
        self.llm.queue(_response([tool], stop_reason="tool_use"))
        real_persist = agent_loop._persist_pause_state
        from orchestration import coordinator as coordinator_mod

        real_atransition = coordinator_mod.atransition

        async def persist_then_stop(context, *args, **kwargs):
            saved = await real_persist(context, *args, **kwargs)
            turn_id = context.get("turn_id")
            await sync_to_async(
                lambda: Turn.objects.filter(pk=turn_id).update(
                    cancel_requested_at=timezone.now(),
                )
            )()
            return saved

        async def reaper_wins(turn_id, version, to_state, **kwargs):
            if to_state == "cancelled" and kwargs.get("end_reason") == USER_CANCEL:
                await sync_to_async(
                    lambda: Turn.objects.filter(pk=turn_id).update(
                        state="failed", end_reason="lease_lost",
                        version=version + 1, ended_at=timezone.now(),
                    )
                )()
            return await real_atransition(turn_id, version, to_state, **kwargs)

        async def scenario():
            with patch(
                "orchestration.agent_loop._persist_pause_state",
                new=persist_then_stop,
            ), patch(
                "orchestration.coordinator.atransition", new=reaper_wins,
            ):
                comm = await self._connect()
                await self._send(comm, "@kazi send it")
                self.assertTrue(await self._wait_for(
                    lambda: Turn.objects.filter(
                        state="failed", end_reason="lease_lost",
                    ).exists(),
                    timeout=8,
                ))
                await self.runner.drain(timeout=15)
                return await sync_to_async(
                    lambda: Turn.objects.order_by("-created_at").first()
                )()

        turn = self._run(scenario())

        self.assertEqual(turn.end_reason, "lease_lost")
        content, _payload = self._saved_reply(turn.pk)
        self.assertNotIn("you stopped this turn", content)

    def test_a_fenced_turn_still_keeps_the_lease_lost_line(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def before(n):
            if n == 1:
                entered.set()
                await release.wait()

        self.llm.before_call = before
        self.llm.queue(_response(
            [_text("working"), _tool("b1", "set_reminder", REMINDER_B)],
            stop_reason="tool_use",
        ))

        async def scenario():
            comm = await self._connect()
            await self._send(comm, "@kazi set reminder b")
            await asyncio.wait_for(entered.wait(), timeout=5)
            turn = await sync_to_async(self._running_turn)()
            turn_leases._FENCED_TURNS.add(str(turn.pk))
            release.set()
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(
                    pk=turn.pk, state="failed", end_reason="lease_lost",
                ).exists(),
                timeout=8,
            ))
            await self.runner.drain(timeout=15)
            return turn.pk

        turn_id = self._run(scenario())

        content, _payload = self._saved_reply(turn_id)
        self.assertIn("marked interrupted", content)
        self.assertNotIn("you stopped this turn", content)
        self.assertEqual(self.execute_tool.await_count, 0)

    def test_a_stop_lookup_error_saves_a_notice_and_falls_back(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def before(n):
            if n == 1:
                entered.set()
                await release.wait()

        self.llm.before_call = before
        self.llm.queue(_response([_text("working")]), _response([_text("ok")]))
        self._arm_autopilot()

        async def scenario():
            comm = await self._connect()
            await self._send(comm, "@kazi long thing")
            await asyncio.wait_for(entered.wait(), timeout=5)
            turn = await sync_to_async(self._running_turn)()
            with patch(
                "orchestration.turns.request_stop_for_key",
                side_effect=RuntimeError("db down"),
            ):
                await self._send(comm, "@kazi stop")
                self.assertTrue(await self._wait_for(
                    lambda: any(
                        STOP_FAILED_NOTICE in text
                        for text in self._notice_texts()
                    ),
                    timeout=5,
                ))
            self.assertFalse(await sync_to_async(self._is_autopilot_armed)())
            release.set()
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(
                    pk=turn.pk, state="completed",
                ).exists(),
                timeout=8,
            ))
            await self.runner.drain(timeout=15)
            return turn.pk

        turn_id = self._run(scenario())

        notice = next(
            text for text in self._notice_texts()
            if STOP_FAILED_NOTICE in text
        )
        self.assertIn("Autopilot is now off", notice)
        self.assertEqual(Turn.objects.get(pk=turn_id).state, "completed")

    def test_a_stop_with_nothing_running_keeps_todays_behaviour(self):
        self.llm.queue(_response([_text("nothing to stop")]))

        async def scenario():
            comm = await self._connect()
            await self._send(comm, "@kazi stop")
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(state="completed").exists(),
                timeout=8,
            ))
            await self.runner.drain(timeout=15)

        self._run(scenario())

        self.assertEqual(Turn.objects.count(), 1)
        self.assertEqual(Turn.objects.filter(state="completed").count(), 1)
        self.assertEqual(len(self.llm.calls), 1)


@override_settings(
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
)
class PauseConsentTests(TransactionTestCase):
    """#216b-3 item 6: a budget "continue" must postdate the pause."""

    def setUp(self):
        super().setUp()
        cache.clear()
        turn_leases._LIVE_TURNS.clear()
        turn_leases._FENCED_TURNS.clear()
        turn_leases._LAST_RENEWAL.clear()
        turn_leases._CANCELLED_TURNS.clear()
        agent_loop._circuit_state.clear()
        self.addCleanup(agent_loop._circuit_state.clear)
        self.addCleanup(turn_leases._CANCELLED_TURNS.clear)

        User = get_user_model()
        self.user = User.objects.create_user(username="pauser", password="pw")  # nosec B106 — fake test credential
        self.member = Member.objects.select_related("User").get(User=self.user)
        self.room = Chatroom.objects.get(participants=self.member)

        self.llm = _FakeLLM()
        self._patches = [
            patch("orchestration.agent_loop.get_llm_client", return_value=self.llm),
            patch(
                "orchestration.agent_loop._resolve_persona",
                new=AsyncMock(return_value=None),
            ),
            patch("orchestration.agent_loop.enforce_agent_caps", return_value=True),
            patch("orchestration.agent_loop._search_backend_available", return_value=False),
            patch("orchestration.agent_loop.execute_tool", new_callable=AsyncMock),
            patch("orchestration.agent_loop.preview_tool", new_callable=AsyncMock),
            patch(
                "orchestration.shell.environment.get_shell_environment",
                new=AsyncMock(return_value={}),
            ),
        ]
        [p.start() for p in self._patches]
        self.addCleanup(lambda: [p.stop() for p in self._patches])

    def _paused_turn(self, paused_at):
        payload = {
            "messages": [],
            "awaiting_budget": True,
            "token_budget": 100000,  # nosec B105 — a token count, not a credential
            "active_seconds": 0.0,
            "wall_started_at": timezone.now().timestamp(),
        }
        turn = Turn.objects.create(
            room=self.room, user=self.user, state="running",
            started_at=timezone.now(), attempt=1,
        )
        self.assertTrue(turns_mod.pause_turn(
            str(turn.pk), turn.version,
            state_payload=payload, pause_kind="budget",
        ))
        Turn.objects.filter(pk=turn.pk).update(paused_at=paused_at)
        turn.refresh_from_db()
        return turn

    def _legacy_paused_turn(self, *, expires_in=300):
        # A pause written before migration 0005 has an expiry but no stamp.
        turn = self._paused_turn(timezone.now())
        Turn.objects.filter(pk=turn.pk).update(
            paused_at=None,
            pause_expires_at=timezone.now() + timedelta(seconds=expires_in),
        )
        turn.refresh_from_db()
        return turn

    def _message(self, text, timestamp):
        message = Message.objects.create(
            member=self.member, content=text, timestamp=timestamp,
        )
        self.room.chats.add(message)
        return message

    def _drive(self, query, message_id, *, send_chunk=None):
        send_chunk = send_chunk or AsyncMock()

        async def _inner():
            coordinator = OrchestrationCoordinator()
            try:
                result = await coordinator.handle_message(
                    query=query,
                    user_id=self.user.id,
                    room_id=self.room.id,
                    username=self.user.username,
                    message_id=message_id,
                    send_chunk=send_chunk,
                    send_step_event=AsyncMock(),
                    get_context_prompt=AsyncMock(return_value=""),
                    bump_signals=MagicMock(),
                )
                if result.turn_state == "committing":
                    await coordinator.finalize_turn(result, reply_saved=True)
                return result
            finally:
                await turn_leases.stop_heartbeat()

        return async_to_sync(_inner)()

    def test_an_early_continue_does_not_resume_the_pause(self):
        base = timezone.now()
        turn = self._paused_turn(base)
        old = self._message("continue", base - timedelta(seconds=10))

        result = self._drive("continue", old.id)

        turn.refresh_from_db()
        self.assertEqual(turn.state, "paused")
        self.assertIsNotNone(turn.paused_at)
        self.assertNotEqual(result.turn_id, str(turn.pk))

    def test_a_continue_after_the_pause_resumes_it(self):
        base = timezone.now()
        turn = self._paused_turn(base)
        new = self._message("continue", base + timedelta(seconds=10))

        self._drive("continue", new.id)

        turn.refresh_from_db()
        self.assertEqual(turn.state, "completed")

    def test_a_legacy_pause_derives_its_time_from_the_expiry(self):
        turn = self._legacy_paused_turn(expires_in=300)
        new = self._message("continue", timezone.now())

        self._drive("continue", new.id)

        turn.refresh_from_db()
        self.assertEqual(turn.state, "completed")

    def test_an_old_continue_on_a_legacy_pause_tells_the_user_to_retry(self):
        turn = self._legacy_paused_turn(expires_in=300)
        old = self._message(
            "continue", timezone.now() - timedelta(seconds=400),
        )
        chunks = []

        async def _send(correlation_id, text, is_final=False):
            chunks.append(text)

        self._drive("continue", old.id, send_chunk=_send)

        turn.refresh_from_db()
        self.assertEqual(turn.state, "paused")
        self.assertTrue(any(
            "still paused waiting for more budget" in text for text in chunks
        ))


class StopLineNeedsTheFlagTests(TransactionTestCase):
    """#216b-3 item 8: only a Turn a person really flagged earns the stop line.

    A Turn can end ``cancelled(user_cancel)`` without carrying the flag (the
    queued rows a stop drops, or any other writer of that reason). The line
    must come from the stop itself, never from the end reason alone.
    """

    def setUp(self):
        super().setUp()
        User = get_user_model()
        self.user = User.objects.create_user(username="flagless", password="pw")  # nosec B106 — fake test credential
        member = Member.objects.select_related("User").get(User=self.user)
        self.room = Chatroom.objects.get(participants=member)

    def _pair(self, *, flagged):
        now = timezone.now()
        earlier = Turn.objects.create(
            room=self.room, user=self.user, state="cancelled",
            end_reason=USER_CANCEL, started_at=now - timedelta(seconds=30),
            ended_at=now - timedelta(seconds=20),
            cancel_requested_at=(now - timedelta(seconds=25)) if flagged else None,
        )
        current = Turn.objects.create(
            room=self.room, user=self.user, state="running", started_at=now,
        )
        # Make the order explicit; two creates can share a clock tick.
        Turn.objects.filter(pk=earlier.pk).update(created_at=now - timedelta(seconds=30))
        Turn.objects.filter(pk=current.pk).update(created_at=now)
        return str(current.pk)

    def _note(self, current_id):
        from orchestration.coordinator import _stopped_turn_note

        return async_to_sync(_stopped_turn_note)(self.room.id, self.user.id, current_id)

    def test_a_flagged_stop_earns_the_line(self):
        self.assertEqual(self._note(self._pair(flagged=True)), STOPPED_TURN_NOTE)

    def test_user_cancel_without_the_flag_earns_no_line(self):
        self.assertEqual(self._note(self._pair(flagged=False)), "")


class StopLineFollowsEndOrderTests(TransactionTestCase):
    """#216b-3 item 8: "the previous request" is the Turn that ended last.

    A paused Turn keeps its early ``created_at`` when it resumes, so turns that
    ran while it waited must not outrank it (CodeRabbit, #306).
    """

    def setUp(self):
        super().setUp()
        User = get_user_model()
        self.user = User.objects.create_user(username="orderly", password="pw")  # nosec B106 — fake test credential
        member = Member.objects.select_related("User").get(User=self.user)
        self.room = Chatroom.objects.get(participants=member)
        self.t0 = timezone.now() - timedelta(minutes=10)

    def _turn(self, *, created, ended, stopped):
        at = self.t0 + timedelta(minutes=created)
        turn = Turn.objects.create(
            room=self.room, user=self.user,
            state="cancelled" if stopped else "completed",
            end_reason=USER_CANCEL if stopped else None,
            started_at=at,
            ended_at=self.t0 + timedelta(minutes=ended),
            cancel_requested_at=(self.t0 + timedelta(minutes=ended)) if stopped else None,
        )
        Turn.objects.filter(pk=turn.pk).update(created_at=at)
        return turn

    def _note_for_next(self):
        from orchestration.coordinator import _stopped_turn_note

        now = timezone.now()
        current = Turn.objects.create(
            room=self.room, user=self.user, state="running", started_at=now,
        )
        Turn.objects.filter(pk=current.pk).update(created_at=now)
        return async_to_sync(_stopped_turn_note)(
            self.room.id, self.user.id, str(current.pk),
        )

    def test_a_resumed_pause_that_completed_last_gives_no_line(self):
        self._turn(created=0, ended=3, stopped=False)  # P: paused early, resumed, completed
        self._turn(created=1, ended=2, stopped=True)   # Q: ran while P waited, stopped
        self.assertEqual(self._note_for_next(), "")

    def test_a_resumed_pause_that_was_stopped_last_gives_the_line(self):
        self._turn(created=0, ended=3, stopped=True)   # P: resumed, then stopped
        self._turn(created=1, ended=2, stopped=False)  # Q: ran while P waited, completed
        self.assertEqual(self._note_for_next(), STOPPED_TURN_NOTE)
