"""#216b-3: an exact stop cancels the running turn before its next step.

The tests drive the real consumer (``WebsocketCommunicator``), the real runner,
real Turn rows and the in-memory channel layer. Only the model is faked, at
``orchestration.agent_loop.get_llm_client``. The mid-run stop races for real:
an event at the seam between tool A's receipt and the next model call.
"""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync, sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

import chatbot.routing
from chatbot.kazi_messages import encrypt_for_room
from chatbot.models import Chatroom, Member, Message
from chatbot.turn_runner import (
    STOP_COMMITTING_NOTICE,
    STOP_NOTICE,
    TurnRunner,
)
from orchestration import agent_loop
from orchestration import turn_leases
from orchestration import turns as turns_mod
from orchestration.action_receipts import ActionReceipt
from orchestration.coordinator import STOPPED_TURN_NOTE, OrchestrationCoordinator
from orchestration.models import Turn
from orchestration.turns import USER_CANCEL, create_turn


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
        self.assertFalse(any(
            "marked interrupted" in text for text in self._notice_texts()
        ))

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
        self.assertTrue(any(STOP_NOTICE in text for text in notices))
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

    def _message(self, text, timestamp):
        message = Message.objects.create(
            member=self.member, content=text, timestamp=timestamp,
        )
        self.room.chats.add(message)
        return message

    def _drive(self, query, message_id):
        async def _inner():
            coordinator = OrchestrationCoordinator()
            result = await coordinator.handle_message(
                query=query,
                user_id=self.user.id,
                room_id=self.room.id,
                username=self.user.username,
                message_id=message_id,
                send_chunk=AsyncMock(),
                send_step_event=AsyncMock(),
                get_context_prompt=AsyncMock(return_value=""),
                bump_signals=MagicMock(),
            )
            if result.turn_state == "committing":
                await coordinator.finalize_turn(result, reply_saved=True)
            return result

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
