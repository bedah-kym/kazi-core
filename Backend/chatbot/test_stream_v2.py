"""217a-2a: the v2 turn stream, driven through the real consumer.

Every test opens a ``WebsocketCommunicator`` against the real routing, consumer,
runner, coordinator and Turn rows; only the model and the side-effect seams are
faked. A v2 socket negotiates first (hello or the kazi.v2 subprotocol) and then
reads typed frames with a turn id and a contiguous seq.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync, sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

import chatbot.routing
from chatbot.models import Chatroom, Member
from chatbot.turn_runner import TurnRunner
from orchestration import agent_loop, turn_leases
from orchestration.models import Turn
from orchestration.turns import expire_all_queued_for_key, reap_turn


def _reset_run_seqs():
    """Clear the per-run seq map; a no-op on a tree that does not have it."""
    try:
        from orchestration.stream_seq import reset_run_seqs
    except Exception:
        return
    reset_run_seqs()


TURN_FRAME_TYPES = {
    "turn.started", "turn.resumed", "part.started", "text.delta", "part.ended",
    "notice", "turn.paused", "turn.completed", "turn.failed", "turn.cancelled",
    "turn.superseded", "turn.expired",
}
TERMINAL_TYPES = {
    "turn.completed", "turn.failed", "turn.cancelled", "turn.superseded",
    "turn.expired",
}
_DEFAULT_ROOM = object()


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
    TURN_CANCEL_POLL_SECONDS=1,
    CONSENT_SKEW_SECONDS=0,
)
class StreamV2TestCase(TransactionTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        turn_leases._LIVE_TURNS.clear()
        turn_leases._FENCED_TURNS.clear()
        turn_leases._LAST_RENEWAL.clear()
        turn_leases._CANCELLED_TURNS.clear()
        agent_loop._circuit_state.clear()
        _reset_run_seqs()
        self.addCleanup(cache.clear)
        self.addCleanup(turn_leases._LIVE_TURNS.clear)
        self.addCleanup(turn_leases._FENCED_TURNS.clear)
        self.addCleanup(turn_leases._LAST_RENEWAL.clear)
        self.addCleanup(turn_leases._CANCELLED_TURNS.clear)
        self.addCleanup(agent_loop._circuit_state.clear)
        self.addCleanup(_reset_run_seqs)

        import chatbot.turn_runner as runner_mod

        self.addCleanup(
            setattr, runner_mod, "_DEFAULT_RUNNER", runner_mod._DEFAULT_RUNNER,
        )
        runner_mod._DEFAULT_RUNNER = TurnRunner()
        self.runner = runner_mod._DEFAULT_RUNNER

        User = get_user_model()
        self.user = User.objects.create_user(
            username="v2-owner", password="pw",  # nosec B106 — fake test credential
        )
        self.other = User.objects.create_user(
            username="v2-other", password="pw",  # nosec B106 — fake test credential
        )
        self.member = Member.objects.select_related("User").get(User=self.user)
        self.other_member = Member.objects.select_related("User").get(
            User=self.other,
        )
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

    def _run(self, coro):
        async def _await():
            try:
                return await coro
            finally:
                for comm in self.communicators:
                    try:
                        await comm.disconnect()
                    except BaseException:
                        pass
                self.communicators.clear()
                await turn_leases.stop_heartbeat()
                turn_leases._LIVE_TURNS.clear()
                turn_leases._FENCED_TURNS.clear()
                turn_leases._LAST_RENEWAL.clear()
                turn_leases._CANCELLED_TURNS.clear()
                _reset_run_seqs()

        return async_to_sync(_await)()

    async def _connect(self, user=None, *, hello=False, subprotocols=None,
                       room=None):
        user = user or self.user
        room = room or self.room
        app = _ForceUser(
            URLRouter(chatbot.routing.websocket_urlpatterns), user,
        )
        if subprotocols is not None:
            comm = WebsocketCommunicator(
                app, f"/ws/chat/{room.id}/", subprotocols=subprotocols,
            )
        else:
            comm = WebsocketCommunicator(app, f"/ws/chat/{room.id}/")
        connected, chosen = await comm.connect()
        self.assertTrue(connected)
        self.communicators.append(comm)
        if hello:
            await comm.send_json_to({"type": "hello", "protocols": [2, 1]})
        initial = await self._wait_for_ready(comm)
        setattr(comm, "_v2_initial", initial)
        setattr(comm, "_room_id", room.id)
        return comm, chosen

    async def _wait_for_ready(self, comm):
        """Read the connect-time frames so later drains start clean.

        ``receive_json_from`` cancels the application task when it times out,
        so this only reads frames already queued, like the ending tests do.
        """
        frames = []
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 3
        while loop.time() < deadline:
            await comm.receive_nothing(timeout=0.05)
            while not comm.output_queue.empty():
                frame = await comm.receive_json_from(timeout=1)
                if frame.get("command") in {"presence", "presence_snapshot"}:
                    if frame.get("command") == "presence_snapshot":
                        return frames
                    continue
                frames.append(frame)
                if frame.get("type") == "session.ready":
                    return frames
            await asyncio.sleep(0.01)
        return frames

    async def _send(self, comm, text, user=None):
        user = user or self.user
        await comm.send_json_to({
            "command": "new_message",
            "from": user.username,
            "message": text,
            "chatid": str(comm._room_id),
        })

    async def _wait_for(self, predicate, timeout=8.0):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if await sync_to_async(predicate)():
                return True
            await asyncio.sleep(0.02)
        return False

    async def _drain(self, comm, quiet=0.2):
        frames = []
        for _ in range(3):
            await comm.receive_nothing(timeout=quiet)
            while not comm.output_queue.empty():
                frame = await comm.receive_json_from(timeout=1)
                if frame.get("command") in {"presence", "presence_snapshot"}:
                    continue
                frames.append(frame)
            if comm.output_queue.empty():
                await asyncio.sleep(0.02)
                if comm.output_queue.empty():
                    break
        return frames

    @staticmethod
    def _turn_frames(frames, *, turn_id=None, types=None):
        selected = []
        for frame in frames:
            if frame.get("type") not in TURN_FRAME_TYPES:
                continue
            if turn_id is not None and frame.get("turn_id") != str(turn_id):
                continue
            if types is not None and frame.get("type") not in types:
                continue
            selected.append(frame)
        return selected

    @staticmethod
    def _assert_contiguous(test, frames):
        seqs = [frame["seq"] for frame in frames]
        test.assertEqual(seqs, list(range(len(seqs))), seqs)

    def _create_turn(self, state, *, user=None, room=_DEFAULT_ROOM, **extra):
        if room is _DEFAULT_ROOM:
            room = self.room
        return Turn.objects.create(
            room=room,
            user=user or self.user,
            state=state,
            **extra,
        )

    # --- negotiation -----------------------------------------------------

    def test_hello_chooses_v2_and_answers_session_ready(self):
        async def scenario():
            comm, _ = await self._connect(hello=True)
            return getattr(comm, "_v2_initial", []) + await self._drain(comm)

        frames = self._run(scenario())
        ready = [f for f in frames if f.get("type") == "session.ready"]
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0]["protocol"], 2)
        self.assertFalse(ready[0]["resumable"])

    def test_a_subprotocol_offer_is_answered_with_kazi_v2(self):
        async def scenario():
            comm, chosen = await self._connect(
                subprotocols=["kazi.v2", "kazi.v1"],
            )
            return chosen, getattr(comm, "_v2_initial", []) + await self._drain(comm)

        chosen, frames = self._run(scenario())
        self.assertEqual(chosen, "kazi.v2")
        ready = [f for f in frames if f.get("type") == "session.ready"]
        self.assertEqual([f["protocol"] for f in ready], [2])

    @override_settings(KAZI_STREAM_V2=False)
    def test_the_kill_switch_off_forces_v1_for_hello_and_subprotocol(self):
        async def scenario():
            comm, chosen = await self._connect(
                subprotocols=["kazi.v2", "kazi.v1"],
            )
            offered = getattr(comm, "_v2_initial", [])
            await comm.send_json_to({"type": "hello", "protocols": [2, 1]})
            frames = await self._drain(comm)
            return chosen, offered + frames

        chosen, frames = self._run(scenario())
        self.assertEqual(chosen, "kazi.v1")
        ready = [f for f in frames if f.get("type") == "session.ready"]
        self.assertEqual([f["protocol"] for f in ready], [1])
        self.assertFalse(any(f.get("v") == 2 and f.get("turn_id") for f in frames))

    # --- seq -------------------------------------------------------------

    def test_a_plain_turn_has_a_turn_id_and_contiguous_seq(self):
        self.llm.queue(_response([_text("Hi there.")]))

        async def scenario():
            comm, _ = await self._connect(hello=True)
            await self._send(comm, "@kazi hello")
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(state="completed").exists(),
            ))
            await self.runner.drain(timeout=15)
            return await self._drain(comm)

        frames = self._run(scenario())
        turn = Turn.objects.get(state="completed")
        turn_frames = self._turn_frames(frames, turn_id=turn.pk)
        self.assertTrue(turn_frames, "no v2 turn frames")
        self.assertEqual(turn_frames[0]["type"], "turn.started")
        self.assertEqual(turn_frames[0]["seq"], 0)
        self._assert_contiguous(self, turn_frames)
        terminals = [f for f in turn_frames if f["type"] in TERMINAL_TYPES]
        self.assertEqual([f["type"] for f in terminals], ["turn.completed"])
        self.assertEqual(turn_frames[-1]["type"], "turn.completed")

    def test_seq_and_turn_id_continue_across_an_approval_pause_and_yes(self):
        self.llm.queue(
            _response(
                [_tool("c1", "send_email", {"to": "a@b.c", "body": "hi"})],
                stop_reason="tool_use",
            ),
            _response([_text("Sent.")]),
        )

        async def scenario():
            comm, _ = await self._connect(hello=True)
            await self._send(comm, "@kazi send it")
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(state="paused").exists(),
            ))
            await self.runner.drain(timeout=15)
            paused = await sync_to_async(
                lambda: Turn.objects.get(state="paused")
            )()
            before = await self._drain(comm)
            await self._send(comm, "@kazi yes")
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(
                    pk=paused.pk, state="completed",
                ).exists(),
            ))
            await self.runner.drain(timeout=15)
            after = await self._drain(comm)
            return paused, before + after

        paused, frames = self._run(scenario())
        turn_frames = self._turn_frames(frames, turn_id=paused.pk)
        self.assertTrue(turn_frames, "no v2 turn frames")
        self.assertEqual(turn_frames[0]["type"], "turn.started")
        self._assert_contiguous(self, turn_frames)
        paused_frame = [f for f in turn_frames if f["type"] == "turn.paused"]
        self.assertEqual(len(paused_frame), 1)
        resumed = [f for f in turn_frames if f["type"] == "turn.resumed"]
        self.assertEqual(len(resumed), 1)
        self.assertEqual(resumed[0]["seq"], paused_frame[0]["seq"] + 1)
        terminals = [f for f in turn_frames if f["type"] in TERMINAL_TYPES]
        self.assertEqual([f["type"] for f in terminals], ["turn.completed"])
        # The "yes" placeholder is superseded exactly once.
        superseded = self._turn_frames(frames, types={"turn.superseded"})
        self.assertEqual(len(superseded), 1)
        self.assertEqual(superseded[0]["by_turn_id"], str(paused.pk))

    # --- terminal writers ------------------------------------------------

    def test_the_runner_completes_a_turn_with_one_terminal_frame(self):
        self.llm.queue(_response([_text("Done.")]))

        async def scenario():
            comm, _ = await self._connect(hello=True)
            await self._send(comm, "@kazi hello")
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(state="completed").exists(),
            ))
            await self.runner.drain(timeout=15)
            return await self._drain(comm)

        frames = self._run(scenario())
        terminals = self._turn_frames(frames, types=TERMINAL_TYPES)
        self.assertEqual([f["type"] for f in terminals], ["turn.completed"])

    def test_a_loop_error_ends_failed_with_one_terminal_frame(self):
        async def failing_loop(**kwargs):
            raise RuntimeError("boom")
            yield  # noqa: unreachable — keeps this an async generator

        async def scenario():
            comm, _ = await self._connect(hello=True)
            with patch(
                "orchestration.coordinator.run_agent_loop", new=failing_loop,
            ):
                await self._send(comm, "@kazi hello")
                self.assertTrue(await self._wait_for(
                    lambda: Turn.objects.filter(state="failed").exists(),
                ))
                await self.runner.drain(timeout=15)
            return await self._drain(comm)

        frames = self._run(scenario())
        terminals = self._turn_frames(frames, types=TERMINAL_TYPES)
        self.assertEqual([f["type"] for f in terminals], ["turn.failed"])
        turn = Turn.objects.get(state="failed")
        turn_frames = self._turn_frames(frames, turn_id=turn.pk)
        self._assert_contiguous(self, turn_frames)

    def test_a_stop_ends_cancelled_with_one_terminal_frame(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def before(n):
            if n == 1:
                entered.set()
                await release.wait()

        self.llm.before_call = before
        self.llm.queue(_response(
            [_tool("s1", "get_weather", {"city": "Paris"})],
            stop_reason="tool_use",
        ))

        async def scenario():
            comm, _ = await self._connect(hello=True)
            await self._send(comm, "@kazi hello")
            await asyncio.wait_for(entered.wait(), timeout=5)
            turn = await sync_to_async(lambda: Turn.objects.get(state="running"))()
            await self._send(comm, "@kazi stop")
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(
                    pk=turn.pk, cancel_requested_at__isnull=False,
                ).exists(),
            ))
            release.set()
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(
                    pk=turn.pk, state="cancelled",
                ).exists(),
            ))
            await self.runner.drain(timeout=15)
            return turn, await self._drain(comm)

        turn, frames = self._run(scenario())
        cancelled = self._turn_frames(
            frames, turn_id=turn.pk, types={"turn.cancelled"},
        )
        self.assertEqual(len(cancelled), 1)

    def test_the_reaper_ends_a_turn_with_one_terminal_frame(self):
        async def scenario():
            comm, _ = await self._connect(hello=True)
            stale = await sync_to_async(self._create_turn)(
                "running",
                lease_expires_at=timezone.now() - timedelta(seconds=5),
                owner_id="dead:1:boot",
                version=1,
            )
            await sync_to_async(reap_turn)(stale, require_lapsed=True)
            frames = await self._drain(comm)
            return stale, frames

        stale, frames = self._run(scenario())
        failed = self._turn_frames(
            frames, turn_id=stale.pk, types={"turn.failed"},
        )
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["reason_code"], "lease_lost")

    def test_a_queued_turn_expiry_emits_one_terminal_frame(self):
        async def scenario():
            comm, _ = await self._connect(hello=True)
            queued = await sync_to_async(self._create_turn)("queued")
            await sync_to_async(expire_all_queued_for_key)(
                self.room.id, self.user.id, reason="restart",
            )
            frames = await self._drain(comm)
            return queued, frames

        queued, frames = self._run(scenario())
        expired = self._turn_frames(
            frames, turn_id=queued.pk, types={"turn.expired"},
        )
        self.assertEqual(len(expired), 1)
        self.assertEqual(expired[0]["reason"], "restart")

    def test_reset_cancelling_a_pause_emits_one_terminal_frame(self):
        self.llm.queue(
            _response(
                [_tool("r1", "send_email", {"to": "a@b.c", "body": "hi"})],
                stop_reason="tool_use",
            ),
        )

        async def scenario():
            comm, _ = await self._connect(hello=True)
            await self._send(comm, "@kazi send it")
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(state="paused").exists(),
            ))
            await self.runner.drain(timeout=15)
            paused = await sync_to_async(
                lambda: Turn.objects.get(state="paused")
            )()
            await self._drain(comm)
            await self._send(comm, "@kazi reset")
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(
                    pk=paused.pk, state="cancelled",
                ).exists(),
            ))
            await self.runner.drain(timeout=15)
            return paused, await self._drain(comm)

        paused, frames = self._run(scenario())
        cancelled = self._turn_frames(
            frames, turn_id=paused.pk, types={"turn.cancelled"},
        )
        self.assertEqual(len(cancelled), 1)

    def test_a_forced_group_send_failure_never_hangs_the_turn(self):
        from channels.layers import get_channel_layer

        layer = get_channel_layer()

        async def failing_group_send(channel, message):
            raise RuntimeError("stream down")

        async def failing_loop(**kwargs):
            raise RuntimeError("boom")
            yield  # noqa: unreachable

        async def scenario():
            comm, _ = await self._connect(hello=True)
            with patch.object(
                layer, "group_send", new=failing_group_send,
            ), patch("orchestration.coordinator.run_agent_loop", new=failing_loop):
                await self._send(comm, "@kazi hello")
                ended = await self._wait_for(
                    lambda: Turn.objects.filter(
                        state__in=("completed", "failed", "cancelled"),
                    ).exists()
                    and not Turn.objects.filter(
                        state__in=("running", "committing"),
                    ).exists(),
                )
            return ended, await sync_to_async(
                lambda: Turn.objects.order_by("created_at").last()
            )()

        ended, turn = self._run(scenario())
        self.assertTrue(ended)
        self.assertEqual(turn.state, "failed")

    # --- turn.cancel -----------------------------------------------------

    def test_turn_cancel_is_refused_for_another_rooms_turn(self):
        other_room = Chatroom.objects.create()
        other_room.participants.add(self.member)

        async def scenario():
            comm, _ = await self._connect(hello=True)
            foreign = await sync_to_async(self._create_turn)(
                "queued", room=other_room,
            )
            await comm.send_json_to({
                "type": "turn.cancel", "turn_id": str(foreign.pk),
            })
            return await self._drain(comm)

        frames = self._run(scenario())
        errors = [f for f in frames if f.get("type") == "error"]
        self.assertTrue(errors, "no refusal frame")
        self.assertEqual(errors[0]["code"], "cancel_denied")
        self.assertTrue(Turn.objects.filter(state="queued").exists())

    def test_turn_cancel_is_refused_for_a_non_owner(self):
        async def scenario():
            comm, _ = await self._connect(hello=True)
            foreign = await sync_to_async(self._create_turn)(
                "queued", user=self.other,
            )
            await comm.send_json_to({
                "type": "turn.cancel", "turn_id": str(foreign.pk),
            })
            return await self._drain(comm)

        frames = self._run(scenario())
        errors = [f for f in frames if f.get("type") == "error"]
        self.assertTrue(errors, "no refusal frame")
        self.assertEqual(errors[0]["code"], "cancel_denied")
        self.assertTrue(Turn.objects.filter(state="queued").exists())

    def test_turn_cancel_is_refused_when_the_check_errors(self):
        async def scenario():
            comm, _ = await self._connect(hello=True)
            # An unparseable id makes the lookup itself raise; the contract
            # says that denies and changes nothing.
            await comm.send_json_to({
                "type": "turn.cancel", "turn_id": "not-a-turn-id",
            })
            return await self._drain(comm)

        frames = self._run(scenario())
        errors = [f for f in frames if f.get("type") == "error"]
        self.assertTrue(errors, "no refusal frame")
        self.assertEqual(errors[0]["code"], "cancel_denied")

    def test_turn_cancel_cancels_a_paused_turn(self):
        self.llm.queue(
            _response(
                [_tool("p1", "send_email", {"to": "a@b.c", "body": "hi"})],
                stop_reason="tool_use",
            ),
        )

        async def scenario():
            comm, _ = await self._connect(hello=True)
            await self._send(comm, "@kazi send it")
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(state="paused").exists(),
            ))
            await self.runner.drain(timeout=15)
            paused = await sync_to_async(
                lambda: Turn.objects.get(state="paused")
            )()
            await self._drain(comm)
            await comm.send_json_to({
                "type": "turn.cancel", "turn_id": str(paused.pk),
            })
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(
                    pk=paused.pk, state="cancelled",
                ).exists(),
            ))
            await self.runner.drain(timeout=15)
            return paused, await self._drain(comm)

        paused, frames = self._run(scenario())
        cancelled = self._turn_frames(
            frames, turn_id=paused.pk, types={"turn.cancelled"},
        )
        self.assertEqual(len(cancelled), 1)

    def test_turn_cancel_cancels_a_queued_turn(self):
        async def scenario():
            comm, _ = await self._connect(hello=True)
            queued = await sync_to_async(self._create_turn)("queued")
            await comm.send_json_to({
                "type": "turn.cancel", "turn_id": str(queued.pk),
            })
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(
                    pk=queued.pk, state="cancelled",
                ).exists(),
            ))
            return queued, await self._drain(comm)

        queued, frames = self._run(scenario())
        cancelled = self._turn_frames(
            frames, turn_id=queued.pk, types={"turn.cancelled"},
        )
        self.assertEqual(len(cancelled), 1)

    def test_turn_cancel_stops_a_running_turn(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def before(n):
            if n == 1:
                entered.set()
                await release.wait()

        self.llm.before_call = before
        self.llm.queue(_response(
            [_tool("s2", "get_weather", {"city": "Paris"})],
            stop_reason="tool_use",
        ))

        async def scenario():
            comm, _ = await self._connect(hello=True)
            await self._send(comm, "@kazi hello")
            await asyncio.wait_for(entered.wait(), timeout=5)
            turn = await sync_to_async(lambda: Turn.objects.get(state="running"))()
            await comm.send_json_to({
                "type": "turn.cancel", "turn_id": str(turn.pk),
            })
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(
                    pk=turn.pk, cancel_requested_at__isnull=False,
                ).exists(),
            ))
            release.set()
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(
                    pk=turn.pk, state="cancelled",
                ).exists(),
            ))
            await self.runner.drain(timeout=15)
            return turn, await self._drain(comm)

        turn, frames = self._run(scenario())
        cancelled = self._turn_frames(
            frames, turn_id=turn.pk, types={"turn.cancelled"},
        )
        self.assertEqual(len(cancelled), 1)

    # --- mixed room and guard -------------------------------------------

    def test_a_v1_only_room_is_unchanged_guard(self):
        """Guard: a v1 socket keeps today's frames (passes on main by design)."""
        self.llm.queue(_response([_text("Hi there.")]))

        async def scenario():
            comm, _ = await self._connect(hello=False)
            await self._send(comm, "@kazi hello")
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(state="completed").exists(),
            ))
            await self.runner.drain(timeout=15)
            return await self._drain(comm)

        frames = self._run(scenario())
        self.assertFalse(any(f.get("v") == 2 for f in frames))
        self.assertTrue(any(
            f.get("command") == "ai_stream"
            and f.get("chunk") == "Hi there."
            for f in frames
        ))
        self.assertTrue(any(
            f.get("command") == "ai_message_saved"
            and (f.get("message") or {}).get("content") == "Hi there."
            for f in frames
        ))

    def test_mixed_team_room_each_socket_gets_its_own_protocol_once(self):
        from chatbot.kazi_messages import get_kazi_member

        self.llm.queue(_response([_text("One.")]), _response([_text("Two.")]))

        async def scenario():
            team = await sync_to_async(Chatroom.objects.create)()
            kazi = await sync_to_async(get_kazi_member)()
            await sync_to_async(team.participants.add)(
                self.member, self.other_member, kazi,
            )
            v1, _ = await self._connect(
                user=self.user, hello=False, room=team,
            )
            v2, _ = await self._connect(
                user=self.other, hello=True, room=team,
            )
            await self._send(v1, "@kazi first", user=self.user)
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(state="completed").count() == 1,
            ))
            await self.runner.drain(timeout=15)
            await self._send(v2, "@kazi second", user=self.other)
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(state="completed").count() == 2,
            ))
            await self.runner.drain(timeout=15)
            return await self._drain(v1), await self._drain(v2)

        v1_frames, v2_frames = self._run(scenario())
        turns = list(Turn.objects.filter(state="completed").order_by("created_at"))
        self.assertEqual(len(turns), 2)

        self.assertFalse(any(f.get("v") == 2 for f in v1_frames))
        for turn in turns:
            v1_terminals = [
                f for f in v1_frames
                if f.get("command") == "ai_message_saved"
            ]
            self.assertTrue(v1_terminals, "v1 socket lost the saved reply")
            completions = self._turn_frames(
                v2_frames, turn_id=turn.pk, types={"turn.completed"},
            )
            self.assertEqual(len(completions), 1, turn.pk)
            started = self._turn_frames(
                v2_frames, turn_id=turn.pk, types={"turn.started"},
            )
            self.assertEqual(len(started), 1, turn.pk)
            self._assert_contiguous(
                self, self._turn_frames(v2_frames, turn_id=turn.pk),
            )

    def test_a_turn_with_a_deleted_room_gets_no_terminal_frame(self):
        async def scenario():
            comm, _ = await self._connect(hello=True)
            orphan = await sync_to_async(self._create_turn)(
                "running",
                room=None,
                lease_expires_at=timezone.now() - timedelta(seconds=5),
                owner_id="dead:1:boot",
                version=1,
            )
            await sync_to_async(reap_turn)(orphan, require_lapsed=True)
            return orphan, await self._drain(comm)

        orphan, frames = self._run(scenario())
        self.assertEqual(
            self._turn_frames(frames, turn_id=orphan.pk), [],
        )
