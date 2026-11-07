"""The turn runner's must-fail tests, driven through the real socket (#216b-2).

These are the repo's first ``WebsocketCommunicator`` tests: the real consumer,
the real runner, real Turn rows and the in-memory channel layer. Only the model
is faked, at ``orchestration.agent_loop.get_llm_client``.
"""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from unittest import skipIf
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync, sync_to_async
from channels.testing import WebsocketCommunicator
from channels.routing import URLRouter
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

import chatbot.routing
from chatbot.kazi_messages import encrypt_for_room
from chatbot.models import Chatroom, Member, Message
from chatbot.turn_runner import TurnRunner
from orchestration import turn_leases
from orchestration import turns as turns_mod
from orchestration.llm_client import TokenQuotaExceeded
from orchestration.shell.profiles import PROFILES
from orchestration.models import Turn
from orchestration.turns import REAP_NOTICE, create_turn


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
)
class TurnRunnerWebsocketTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        turn_leases._LIVE_TURNS.clear()
        turn_leases._FENCED_TURNS.clear()
        turn_leases._LAST_RENEWAL.clear()
        from orchestration import agent_loop

        agent_loop._circuit_state.clear()
        self.addCleanup(agent_loop._circuit_state.clear)
        import chatbot.turn_runner as runner_mod

        runner_mod._DEFAULT_RUNNER = TurnRunner()
        self.runner = runner_mod._DEFAULT_RUNNER

        User = get_user_model()
        self.user = User.objects.create_user(username="runner", password="pw")  # nosec B106 — fake test credential
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

    def tearDown(self):
        super().tearDown()

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

        return async_to_sync(_await)()

    async def _connect(self, user=None):
        user = user or self.user
        app = _ForceUser(
            URLRouter(chatbot.routing.websocket_urlpatterns), user,
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

    async def _collect(self, comm, predicate, timeout=8.0):
        frames = []
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            try:
                frame = await comm.receive_json_from(
                    timeout=max(0.05, deadline - loop.time()),
                )
            except asyncio.TimeoutError:
                break
            frames.append(frame)
            if predicate(frame):
                return frame, frames
        return None, frames

    async def _wait_for(self, predicate, timeout=8.0):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if await self._maybe_true(predicate):
                return True
            await asyncio.sleep(0.02)
        return False

    async def _maybe_true(self, predicate):
        from asgiref.sync import sync_to_async

        return await sync_to_async(predicate)()

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

    # --- must-fail tests -------------------------------------------------

    def test_the_sender_gets_a_stream_frame_before_the_turn_completes(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def before(n):
            if n == 1:
                entered.set()
                await release.wait()

        self.llm.before_call = before
        self.llm.queue(_response([_text("live answer")]))

        async def scenario():
            comm = await self._connect()
            await self._send(comm, "@kazi hello")
            await asyncio.wait_for(entered.wait(), timeout=5)
            # The turn is still inside its model call. A free socket can read
            # the planning frame; the old in-handler turn could not.
            frame, _frames = await self._collect(
                comm,
                lambda f: f.get("command") == "orchestration_step",
                timeout=1.0,
            )
            self.assertIsNotNone(frame, "no frame arrived while the turn ran")
            release.set()
            saved, _ = await self._collect(
                comm, lambda f: f.get("command") == "ai_message_saved",
                timeout=8.0,
            )
            self.assertIsNotNone(saved)

        self._run(scenario())

    def test_a_second_message_during_a_turn_gets_a_queued_frame(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def before(n):
            if n == 1:
                entered.set()
                await release.wait()

        self.llm.before_call = before
        self.llm.queue(_response([_text("first answer")]), _response([_text("second answer")]))

        async def scenario():
            comm = await self._connect()
            await self._send(comm, "@kazi first")
            await asyncio.wait_for(entered.wait(), timeout=5)
            await self._send(comm, "@kazi second")
            queued, _ = await self._collect(
                comm,
                lambda f: f.get("command") == "ai_stream"
                and "queued (position" in (f.get("chunk") or ""),
                timeout=2.0,
            )
            self.assertIsNotNone(queued, "second message got no queued frame")
            release.set()
            await self.runner.drain(timeout=15)
            from asgiref.sync import sync_to_async

            completed = await sync_to_async(
                lambda: Turn.objects.filter(state="completed").count()
            )()
            self.assertEqual(completed, 2)

        self._run(scenario())

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

    def test_a_stop_during_a_running_autopilot_turn_disarms_before_it_ends(self):
        self._arm_autopilot()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def before(n):
            if n == 1:
                entered.set()
                await release.wait()

        self.llm.before_call = before
        self.llm.queue(_response([_text("working")]), _response([_text("ok")]))

        async def scenario():
            comm = await self._connect()
            await self._send(comm, "@kazi do the long thing")
            await asyncio.wait_for(entered.wait(), timeout=5)
            self.assertTrue(await self._maybe_true(self._is_autopilot_armed))
            await self._send(comm, "@kazi stop")
            # The running turn is still gated; the stop's side effect must have
            # landed already, not when the queued turn eventually runs.
            self.assertTrue(await self._wait_for(
                lambda: not self._is_autopilot_armed(),
            ))
            release.set()
            await self.runner.drain(timeout=15)

        self._run(scenario())

    def test_a_stop_at_the_cap_still_disarms(self):
        self._arm_autopilot()
        for text in ("one", "two", "three"):
            self._queued_request(f"@kazi {text}")
        self.assertTrue(self._is_autopilot_armed())

        async def scenario():
            comm = await self._connect()
            self.assertTrue(await self._maybe_true(self._is_autopilot_armed))
            await self._send(comm, "@kazi stop")
            self.assertTrue(await self._wait_for(
                lambda: not self._is_autopilot_armed(),
            ))
            from asgiref.sync import sync_to_async

            counts = await sync_to_async(lambda: {
                "turns": Turn.objects.count(),
                "queued": Turn.objects.filter(state="queued").count(),
            })()
            self.assertEqual(counts["turns"], 3)
            self.assertEqual(counts["queued"], 3)

        self._run(scenario())

    def test_a_failed_disarm_never_claims_autopilot_was_off(self):
        from chatbot.turn_runner import run_immediate_directives

        with patch(
            "orchestration.shell.autopilot.disarm",
            side_effect=RuntimeError("store down"),
        ):
            reply = async_to_sync(run_immediate_directives)(
                query="autopilot off", user_id=self.user.id, room_id=self.room.id,
            )
        self.assertIn("couldn't confirm", reply)
        self.assertNotIn("not armed", reply)

    def test_two_submissions_at_the_cap_create_only_one_turn(self):
        import threading

        from django.db import close_old_connections

        from chatbot.turn_runner import enqueue_turn

        for text in ("one", "two"):
            self._queued_request(f"@kazi {text}")
        extra = []
        for text in ("three", "four"):
            content = encrypt_for_room(self.room, {"content": f"@kazi {text}"})
            message = Message.objects.create(
                member=self.member,
                content=json.dumps(content),
                timestamp=timezone.now(),
            )
            self.room.chats.add(message)
            extra.append(message)

        barrier = threading.Barrier(2)
        results = []

        def worker(message):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                results.append(enqueue_turn(
                    room_id=self.room.id,
                    user_id=self.user.id,
                    request_message_id=message.id,
                    cap=3,
                ))
            finally:
                close_old_connections()

        threads = [
            threading.Thread(target=worker, args=(message,))
            for message in extra
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertEqual(sum(1 for result in results if result is not None), 1)
        self.assertEqual(sum(1 for result in results if result is None), 1)
        self.assertEqual(Turn.objects.filter(state="queued").count(), 3)

    def test_a_queued_turns_history_has_the_previous_reply_and_not_the_queued_message(self):
        now = timezone.now()
        for index in range(10):
            content = encrypt_for_room(
                self.room, {"content": f"older message {index}"},
            )
            message = Message.objects.create(
                member=self.member,
                content=json.dumps(content),
                timestamp=now - timedelta(minutes=20 - index),
            )
            self.room.chats.add(message)

        entered = asyncio.Event()
        release = asyncio.Event()

        async def before(n):
            if n == 1:
                entered.set()
                await release.wait()

        self.llm.before_call = before
        self.llm.queue(
            _response([_text("reply to first")]),
            _response([_text("reply to second")]),
            _response([_text("reply to third")]),
        )

        async def scenario():
            comm = await self._connect()
            await self._send(comm, "@kazi first")
            await asyncio.wait_for(entered.wait(), timeout=5)
            await self._send(comm, "@kazi second question three")
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(state="queued").count() == 1,
            ))
            await self._send(comm, "@kazi third message behind")
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(state="queued").count() == 2,
            ))
            # A newer harness notice about a later message must not leak in.
            from chatbot.kazi_messages import ORIGIN_TURN_NOTICE, persist_kazi_message

            await sync_to_async(persist_kazi_message)(
                self.room,
                content="3 messages waiting; this one won't be answered.",
                origin=ORIGIN_TURN_NOTICE,
            )
            release.set()
            await self.runner.drain(timeout=20)

        from asgiref.sync import sync_to_async

        self._run(scenario())

        self.assertEqual(Turn.objects.filter(state="completed").count(), 3)
        self.assertGreater(
            self.room.chats.filter(member__User=self.user).count(), 8,
        )
        second_call = self.llm.calls[1]
        history = json.dumps(second_call["messages"], default=str)
        self.assertIn("reply to first", history)
        # The current query is the last user message the loop adds itself; it
        # must not also appear from the queued-message history.
        self.assertEqual(history.count("second question three"), 1)
        self.assertNotIn("third message behind", history)
        self.assertNotIn("3 messages waiting", history)

    @override_settings(
        CHANNEL_LAYERS={
            "default": {
                "BACKEND": "channels.layers.InMemoryChannelLayer",
                "CONFIG": {"expiry": 2},
            },
        },
    )
    def test_a_slow_turn_still_delivers_the_saved_reply(self):
        async def slow():
            await asyncio.sleep(4.0)
            return _response([_text("slow answer")])

        self.llm.queue(slow)

        async def scenario():
            comm = await self._connect()
            await self._send(comm, "@kazi slow")
            saved, frames = await self._collect(
                comm, lambda f: f.get("command") == "ai_message_saved",
                timeout=10.0,
            )
            self.assertIsNotNone(saved, f"saved reply not delivered: {frames}")
            self.assertIn("slow answer", saved["message"]["content"])

        self._run(scenario())

    def test_a_submit_that_sees_the_owner_finishing_does_not_lose_a_wakeup(self):
        """The queued row is the handoff: either the submitter or the owner claims it."""
        self._queued_request("@kazi first")
        self._queued_request("@kazi second")
        entered = asyncio.Event()
        release = asyncio.Event()

        async def before(n):
            if n == 1:
                entered.set()
                await release.wait()

        self.llm.before_call = before
        self.llm.queue(_response([_text("first")]), _response([_text("second")]))

        async def scenario():
            owner = TurnRunner()
            await owner.submit(self.room.id, self.user.id)
            await asyncio.wait_for(entered.wait(), timeout=5)
            # The late submitter checks while the owner is still finishing.
            late = TurnRunner()
            await late.submit(self.room.id, self.user.id)
            await asyncio.sleep(0.05)
            self.assertFalse(late._tasks)
            release.set()
            await owner.drain(timeout=15)
            await late.drain(timeout=15)

        self._run(scenario())

        self.assertEqual(Turn.objects.filter(state="completed").count(), 2)
        self.assertEqual(len(self.llm.calls), 2)

    @skipIf(
        connection.vendor == "sqlite",
        "The room-row SELECT ... FOR UPDATE is the Postgres guarantee; "
        "SQLite serialises writers and the lite profile is one worker.",
    )
    def test_two_process_locks_compete_for_the_last_slot(self):
        import threading

        from django.db import close_old_connections

        from chatbot.turn_runner import enqueue_turn

        for text in ("one", "two"):
            self._queued_request(f"@kazi {text}")
        extra = []
        for text in ("three", "four"):
            content = encrypt_for_room(self.room, {"content": f"@kazi {text}"})
            message = Message.objects.create(
                member=self.member,
                content=json.dumps(content),
                timestamp=timezone.now(),
            )
            self.room.chats.add(message)
            extra.append(message)

        barrier = threading.Barrier(2)
        locks = [threading.Lock(), threading.Lock()]
        results = []

        def worker(message, lock):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                results.append(enqueue_turn(
                    room_id=self.room.id,
                    user_id=self.user.id,
                    request_message_id=message.id,
                    cap=3,
                    lock=lock,
                ))
            finally:
                close_old_connections()

        threads = [
            threading.Thread(target=worker, args=(message, locks[index]))
            for index, message in enumerate(extra)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertEqual(sum(1 for result in results if result is not None), 1)
        self.assertEqual(sum(1 for result in results if result is None), 1)
        self.assertEqual(Turn.objects.filter(state="queued").count(), 3)

    def test_two_runners_drain_three_queued_rows_fifo_exactly_once(self):
        for text in ("one", "two", "three"):
            self._queued_request(f"@kazi {text}")
        self.llm.queue(
            _response([_text("a")]),
            _response([_text("b")]),
            _response([_text("c")]),
        )

        async def scenario():
            first = TurnRunner()
            second = TurnRunner()
            await asyncio.gather(
                first.submit(self.room.id, self.user.id),
                second.submit(self.room.id, self.user.id),
            )
            await first.drain(timeout=15)
            await second.drain(timeout=15)

        self._run(scenario())

        from chatbot.history import decrypt_stored
        from chatbot.transcript import strip_wake_word

        def _claimed_queries():
            texts = []
            for turn in Turn.objects.order_by("started_at"):
                message = Message.objects.filter(
                    turn_id=turn.pk,
                    member__User__username=self.user.username,
                ).order_by("timestamp").first()
                if message is None:
                    continue
                content, _payload = decrypt_stored(self.room, message)
                texts.append(strip_wake_word(content))
            return texts

        self.assertEqual(_claimed_queries(), ["one", "two", "three"])
        self.assertEqual(len(self.llm.calls), 3)
        self.assertEqual(Turn.objects.filter(state="completed").count(), 3)

    @override_settings(CONSENT_SKEW_SECONDS=0)
    def test_a_queued_yes_cannot_approve_a_prompt_it_never_saw(self):
        tool_call = {"id": "c1", "name": "send_email", "input": {"to": "a@b.c", "body": "hi"}}
        entered = asyncio.Event()
        release = asyncio.Event()

        async def before(n):
            if n == 1:
                entered.set()
                await release.wait()

        self.llm.before_call = before
        self.llm.queue(
            _response([_tool("c1", "send_email", tool_call["input"])], stop_reason="tool_use"),
            _response([_text("I still need your approval.")]),
            _response([_text("Sent.")]),
        )

        async def scenario():
            comm = await self._connect()
            await self._send(comm, "@kazi send it")
            await asyncio.wait_for(entered.wait(), timeout=5)
            # The "yes" is saved before the approval prompt exists.
            await self._send(comm, "@kazi yes")
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(state="queued").count() == 1,
            ))
            release.set()
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(state="paused").exists(),
            ))
            await self.runner.drain(timeout=15)

            from asgiref.sync import sync_to_async

            paused = await sync_to_async(lambda: Turn.objects.get(state="paused"))()
            paused_id = paused.pk
            self.assertEqual(self.execute_tool.await_count, 0)

            # A yes sent after the prompt continues the pause. Wait for the
            # paused row itself to leave paused, not for any completed turn.
            await self._send(comm, "@kazi yes")
            self.assertTrue(await self._wait_for(
                lambda: not Turn.objects.filter(pk=paused_id, state="paused").exists(),
            ))
            await self.runner.drain(timeout=15)

            await sync_to_async(paused.refresh_from_db)()
            self.assertEqual(paused.state, "completed")
            self.assertEqual(self.execute_tool.await_count, 1)
            superseded = await sync_to_async(
                lambda: Turn.objects.filter(state="superseded").count()
            )()
            self.assertEqual(superseded, 1)

        self._run(scenario())

    def test_a_disconnect_mid_turn_still_saves_the_reply(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def before(n):
            if n == 1:
                entered.set()
                await release.wait()

        self.llm.before_call = before
        self.llm.queue(_response([_text("saved after disconnect")]))

        async def scenario():
            comm = await self._connect()
            await self._send(comm, "@kazi hello")
            await asyncio.wait_for(entered.wait(), timeout=5)
            await comm.disconnect()
            self.communicators.remove(comm)
            release.set()
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(state="completed").exists(),
            ))
            await self.runner.drain(timeout=10)

            reader = await self._connect()
            await reader.send_json_to({
                "command": "fetch_messages", "chatid": str(self.room.id),
            })
            frame, _frames = await self._collect(
                reader, lambda f: f.get("command") == "messages",
            )
            self.assertIsNotNone(frame)
            contents = [m.get("content") for m in frame["messages"]]
            self.assertIn("saved after disconnect", contents)

        self._run(scenario())

    def test_five_rapid_messages_cap_at_three_queued_and_refuse_the_fifth(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def before(n):
            if n == 1:
                entered.set()
                await release.wait()

        self.llm.before_call = before
        self.llm.queue(_response([_text("one")]), _response([_text("two")]),
                       _response([_text("three")]), _response([_text("four")]))

        async def scenario():
            comm = await self._connect()
            await self._send(comm, "@kazi first")
            await asyncio.wait_for(entered.wait(), timeout=5)
            for text in ("second", "third", "fourth", "fifth"):
                await self._send(comm, f"@kazi {text}")
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(state="queued").count() == 3,
            ))
            from asgiref.sync import sync_to_async

            counts = await sync_to_async(lambda: {
                "running": Turn.objects.filter(state="running").count(),
                "queued": Turn.objects.filter(state="queued").count(),
                "total": Turn.objects.count(),
            })()
            self.assertEqual(counts["running"], 1)
            self.assertEqual(counts["queued"], 3)
            self.assertEqual(counts["total"], 4)
            from chatbot.history import decrypt_stored

            def _notices():
                texts = []
                for message in Message.objects.filter(member__User__username="kazi"):
                    content, _payload = decrypt_stored(self.room, message)
                    texts.append(content)
                return texts

            self.assertTrue(await self._wait_for(
                lambda: any("3 messages waiting" in n for n in _notices()),
            ))
            notices = await sync_to_async(_notices)()
            self.assertTrue(any("3 messages waiting" in n for n in notices))
            release.set()
            await self.runner.drain(timeout=20)
            remaining = await sync_to_async(lambda: Turn.objects.exclude(
                state__in=("completed", "failed", "cancelled", "superseded", "expired"),
            ).count())()
            self.assertEqual(remaining, 0)

        self._run(scenario())

    async def _pause_for_blocks(self, blocks, *, text="@kazi do it"):
        """Drive one turn to a real approval pause and return the paused Turn."""
        self.llm.queue(_response(blocks, stop_reason="tool_use"))
        comm = await self._connect()
        await self._send(comm, text)
        self.assertTrue(await self._wait_for(
            lambda: Turn.objects.filter(state="paused").exists(),
        ))
        return comm, await sync_to_async(
            lambda: Turn.objects.get(state="paused")
        )()

    async def _assert_still_paused(self, paused):
        await sync_to_async(paused.refresh_from_db)()
        self.assertEqual(paused.state, "paused")
        self.assertIsNotNone(paused.paused_state_enc)
        state = await sync_to_async(turns_mod.get_turn_paused_state)(paused)
        self.assertIsNotNone(state)

    def _open_profile(self):
        return patch(
            "orchestration.shell.profiles.resolve_profile",
            return_value=PROFILES["open"],
        )

    @override_settings(CONSENT_SKEW_SECONDS=0)
    def test_autopilot_arm_failure_keeps_the_pause(self):
        tool = _tool("c1", "run_command", {"command": "rm scratch.txt"})

        async def scenario():
            with self._open_profile(), patch(
                "orchestration.shell.autopilot.arm", return_value=False,
            ):
                comm, paused = await self._pause_for_blocks([tool])
                await self._send(comm, "@kazi autopilot")
                self.assertTrue(await self._wait_for(
                    lambda: Turn.objects.filter(state="completed").count() >= 1,
                ))
                await self.runner.drain(timeout=15)
                await self._assert_still_paused(paused)

                # A real yes still resumes the held action once.
                self.llm.queue(_response([_text("Done.")]))
                await self._send(comm, "@kazi yes")
                self.assertTrue(await self._wait_for(
                    lambda: not Turn.objects.filter(
                        pk=paused.pk, state="paused",
                    ).exists(),
                ))
                await self.runner.drain(timeout=15)
                await sync_to_async(paused.refresh_from_db)()
                self.assertEqual(paused.state, "completed")
                self.assertEqual(self.execute_tool.await_count, 1)

        self._run(scenario())

    @override_settings(CONSENT_SKEW_SECONDS=0)
    def test_autopilot_on_a_mixed_batch_keeps_the_pause(self):
        shell = _tool(
            "c1", "run_command", {"command": "rm scratch.txt"},
        )
        email = _tool("c2", "send_email", {"to": "a@b.c", "body": "hi"})

        async def scenario():
            with self._open_profile(), patch(
                "orchestration.shell.autopilot.arm", return_value=True,
            ):
                comm, paused = await self._pause_for_blocks([shell, email])
                await self._send(comm, "@kazi autopilot")
                self.assertTrue(await self._wait_for(
                    lambda: Turn.objects.filter(state="completed").count() >= 1,
                ))
                await self.runner.drain(timeout=15)
                await self._assert_still_paused(paused)

                self.llm.queue(_response([_text("Done.")]))
                await self._send(comm, "@kazi yes")
                self.assertTrue(await self._wait_for(
                    lambda: not Turn.objects.filter(
                        pk=paused.pk, state="paused",
                    ).exists(),
                ))
                await self.runner.drain(timeout=15)
                await sync_to_async(paused.refresh_from_db)()
                self.assertEqual(paused.state, "completed")
                self.assertEqual(self.execute_tool.await_count, 2)

        self._run(scenario())

    @override_settings(CONSENT_SKEW_SECONDS=0)
    def test_quota_still_blocked_keeps_the_pause(self):
        pause_payload = {
            "messages": [],
            "awaiting_budget": True,
            "awaiting_quota": True,
            "end_reason": "quota",
            "token_budget": 100000,  # nosec B105 — a token count, not a credential
            "active_seconds": 0.0,
            "wall_started_at": timezone.now().timestamp(),
        }
        paused = Turn.objects.create(
            room=self.room, user=self.user, state="running",
            started_at=timezone.now(), attempt=1,
        )
        self.assertTrue(turns_mod.pause_turn(
            str(paused.pk), paused.version,
            state_payload=pause_payload, pause_kind="quota",
        ))
        paused.refresh_from_db()
        self.llm.queue(_response([_text("Continued.")]))

        async def scenario():
            comm = await self._connect()
            with patch(
                "orchestration.coordinator._quota_resume_still_blocked",
                new=AsyncMock(return_value=TokenQuotaExceeded(3600)),
            ):
                await self._send(comm, "@kazi continue")
                self.assertTrue(await self._wait_for(
                    lambda: Turn.objects.filter(state="completed").count() >= 1,
                ))
                await self.runner.drain(timeout=15)
                await self._assert_still_paused(paused)

            with patch(
                "orchestration.coordinator._quota_resume_still_blocked",
                new=AsyncMock(return_value=None),
            ):
                await self._send(comm, "@kazi continue")
                self.assertTrue(await self._wait_for(
                    lambda: not Turn.objects.filter(
                        pk=paused.pk, state="paused",
                    ).exists(),
                ))
                await self.runner.drain(timeout=15)

            await sync_to_async(paused.refresh_from_db)()
            self.assertEqual(paused.state, "completed")

        self._run(scenario())

    def _turn_notices(self):
        from chatbot.history import decrypt_stored

        texts = []
        for message in Message.objects.filter(member__User__username="kazi"):
            content, payload = decrypt_stored(self.room, message)
            if (payload or {}).get("origin") == "turn_notice":
                texts.append(content)
        return texts

    async def _lifespan_shutdown(self, turn_id, *, at_complete):
        from Backend.asgi import lifespan

        sent = []

        async def receive():
            return {"type": "lifespan.shutdown"}

        async def send(message):
            sent.append(message)
            if message.get("type") == "lifespan.shutdown.complete":
                at_complete["turns"] = await sync_to_async(lambda: {
                    str(pk): (state, reason, partial)
                    for pk, state, reason, partial in Turn.objects.values_list(
                        "id", "state", "end_reason", "partial",
                    )
                })()
                at_complete["notices"] = await sync_to_async(self._turn_notices)()

        await lifespan({"type": "lifespan"}, receive, send)
        self.assertEqual(sent[-1]["type"], "lifespan.shutdown.complete")
        self.assertEqual(len(sent), 1)

    @override_settings(CONSENT_SKEW_SECONDS=0, TURN_SHUTDOWN_GRACE_SECONDS=1)
    def test_a_resumed_turn_cancelled_at_shutdown_gets_an_ending(self):
        """A resumed turn cancelled mid-tool ends failed(shutdown), one notice."""
        tool_call = {"id": "c1", "name": "send_email", "input": {"to": "a@b.c", "body": "hi"}}
        tool_entered = asyncio.Event()

        async def blocking_tool(*args, **kwargs):
            tool_entered.set()
            await asyncio.Event().wait()

        self.execute_tool.side_effect = blocking_tool
        self.llm.queue(
            _response([_tool("c1", "send_email", tool_call["input"])], stop_reason="tool_use"),
            _response([_text("Sent.")]),
        )

        async def scenario():
            comm = await self._connect()
            await self._send(comm, "@kazi send it")
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(state="paused").exists(),
            ))
            paused = await sync_to_async(lambda: Turn.objects.get(state="paused"))()
            await self._send(comm, "@kazi yes")
            await asyncio.wait_for(tool_entered.wait(), timeout=5)
            # The tool is dispatched and running; no receipt exists yet (E10).
            self.assertEqual(self.execute_tool.await_count, 1)

            at_complete = {}
            await self._lifespan_shutdown(paused.pk, at_complete=at_complete)

            state, reason, partial = at_complete["turns"][str(paused.pk)]
            self.assertEqual(state, "failed")
            self.assertEqual(reason, "shutdown")
            self.assertIs(partial, True)
            self.assertEqual(at_complete["notices"].count(REAP_NOTICE), 1)
            self.assertEqual(self.execute_tool.await_count, 1)

            await sync_to_async(paused.refresh_from_db)()
            self.assertEqual(paused.state, "failed")
            self.assertEqual(paused.end_reason, "shutdown")
            self.assertIs(paused.partial, True)
            self.assertEqual(
                [n for n in await sync_to_async(self._turn_notices)() if n == REAP_NOTICE],
                [REAP_NOTICE],
            )

        self._run(scenario())

    @override_settings(TURN_SHUTDOWN_GRACE_SECONDS=1)
    def test_a_fresh_turn_cancelled_at_shutdown_gets_an_ending(self):
        """A fresh turn cancelled mid-tool gets the same honest ending."""
        tool_entered = asyncio.Event()

        async def blocking_tool(*args, **kwargs):
            tool_entered.set()
            await asyncio.Event().wait()

        self.execute_tool.side_effect = blocking_tool
        self.llm.queue(_response(
            [_tool("c1", "get_weather", {"city": "Paris"})],
            stop_reason="tool_use",
        ))

        async def scenario():
            comm = await self._connect()
            await self._send(comm, "@kazi weather")
            await asyncio.wait_for(tool_entered.wait(), timeout=5)
            turn = await sync_to_async(
                lambda: Turn.objects.get(state="running")
            )()
            self.assertEqual(self.execute_tool.await_count, 1)

            at_complete = {}
            await self._lifespan_shutdown(turn.pk, at_complete=at_complete)

            state, reason, partial = at_complete["turns"][str(turn.pk)]
            self.assertEqual(state, "failed")
            self.assertEqual(reason, "shutdown")
            self.assertIs(partial, True)
            self.assertEqual(at_complete["notices"].count(REAP_NOTICE), 1)
            self.assertEqual(self.execute_tool.await_count, 1)

        self._run(scenario())
