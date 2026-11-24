"""217a-0: every chat turn ends honestly on today's (v1) stream.

These tests drive the real consumer, the real runner, real Turn rows and the
in-memory channel layer through a ``WebsocketCommunicator``. Only the model is
faked, at ``orchestration.agent_loop.get_llm_client``. They record the exact
frames the browser receives, in order, after the consumer handlers, with the
volatile ids and timestamps normalised. Later 217a PRs must keep these
sequences passing.

These sequences characterise the endings on ``origin/main``. The honesty
fixes (one ``done`` per turn, ``is_final`` on every exit, and a failed
``send_chunk`` that never aborts a turn) update the changed expectations here
and add their own must-fail tests.
"""
from __future__ import annotations

import asyncio
import json
import re
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync, sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

import chatbot.routing
from chatbot.models import Chatroom, Member, Message
from chatbot.turn_runner import STOP_NOTICE, TurnRunner
from orchestration import agent_loop, turn_leases
from orchestration.agent_loop import LEASE_LOST_NOTE
from orchestration.coordinator import STOP_ENDED_REPLY_LINE
from orchestration.llm_client import TokenQuotaExceeded
from orchestration.models import Turn


_CONFIRM_PROMPT = """I'd like to `send email` with the following details:
`to` (text) — 5 characters, 1 line:
```text
a@b.c
```

`body` (text) — 2 characters, 1 line:
```text
hi
```

Should I go ahead? (yes / no)

(approval id <id>)"""


_BUDGET_PAUSE = (
    "\n\nI've paused this turn because it needs more budget than it has "
    "left. Reply **more budget** and I'll continue from where I stopped."
)
_QUOTA_PAUSE = (
    "I've reached this hour's token limit: no tool calls had run yet. It "
    "frees up in about an hour. Reply **continue** then and I'll pick up "
    "from where it stopped."
)


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


_ID_KEYS = {"id", "parent_id", "turn_id", "step_id", "approval_id", "short_id"}
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T[0-9:.]+")
_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
)
_TURN_STEP = re.compile(r"turn_\d+")
_APPROVAL = re.compile(r"\(approval id [0-9a-f]{8}\)")


def normalise(value, key=None):
    """Replace volatile ids and timestamps, keeping order and every key."""
    if key in _ID_KEYS:
        return None if value is None else "<id>"
    if key == "timestamp":
        return "<timestamp>"
    if isinstance(value, dict):
        return {k: normalise(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [normalise(item) for item in value]
    if isinstance(value, str):
        text = _APPROVAL.sub("(approval id <id>)", value)
        text = _TURN_STEP.sub("turn_<id>", text)
        text = _UUID.sub("<uuid>", text)
        text = _ISO.sub("<timestamp>", text)
        return text
    return value


@override_settings(
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
    TURN_QUEUE_MAX=3,
    TURN_CANCEL_POLL_SECONDS=1,
    CONSENT_SKEW_SECONDS=0,
)
class StreamEndingFramesTests(TransactionTestCase):
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
        self.user = User.objects.create_user(username="streamer", password="pw")  # nosec B106 — fake test credential
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

    async def _drain_frames(self, comm, quiet=0.2):
        """Read every queued frame after the runner has finished the turn.

        ``receive_json_from`` cancels the application task on timeout, so the
        quiet check only inspects the queue; frames are read while it is not
        empty, which never times out.
        """
        frames = []
        for _ in range(3):
            await comm.receive_nothing(timeout=quiet)
            while not comm.output_queue.empty():
                frame = await comm.receive_json_from(timeout=1)
                if frame.get("command") in {"presence", "presence_snapshot"}:
                    continue
                frames.append(normalise(frame))
            if comm.output_queue.empty():
                await asyncio.sleep(0.02)
                if comm.output_queue.empty():
                    break
        return frames

    def _echo(self, content):
        return {
            "command": "new_message",
            "message": {
                "id": "<id>",
                "member": "streamer",
                "content": content,
                "timestamp": "<timestamp>",
                "parent_id": None,
            },
        }

    def _step(self, phase, state, message):
        return {
            "command": "orchestration_step",
            "event": {
                "step_id": "<id>",
                "phase": phase,
                "state": state,
                "message": message,
                "timestamp": "<timestamp>",
            },
        }

    def _stream(self, chunk, is_final=False):
        return {"command": "ai_stream", "chunk": chunk, "is_final": is_final}

    def _saved(self, content):
        return {
            "command": "ai_message_saved",
            "message": {
                "id": "<id>",
                "member": "kazi",
                "content": content,
                "timestamp": "<timestamp>",
                "parent_id": None,
            },
        }

    def _done(self, message="Request complete."):
        return self._step("done", "completed", message)

    def _thinking(self):
        return self._step("planning", "started", "Thinking\u2026")

    def _reasoning(self):
        return self._step("thinking", "started", "Reasoning\u2026")

    def _assert_frames(self, label, actual, expected):
        self.assertEqual(
            actual, expected,
            f"{label}\nexpected: {json.dumps(expected, indent=2)}\n"
            f"actual: {json.dumps(actual, indent=2)}",
        )

    # --- scenarios -------------------------------------------------------

    def test_a_plain_reply(self):
        self.llm.queue(_response([_text("Hi there.")]))

        async def scenario():
            comm = await self._connect()
            await self._send(comm, "@kazi hello")
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(state="completed").exists(),
            ))
            await self.runner.drain(timeout=15)
            return await self._drain_frames(comm)

        self._assert_frames("plain reply", self._run(scenario()), [
            self._echo("@kazi hello"),
            self._thinking(),
            self._reasoning(),
            self._stream("Hi there."),
            self._done(),
            self._done(),
            self._stream("", is_final=True),
            self._saved("Hi there."),
        ])

    def test_a_one_tool_turn(self):
        self.llm.queue(
            _response(
                [_tool("t1", "get_weather", {"city": "Paris"})],
                stop_reason="tool_use",
            ),
            _response([_text("It is sunny.")]),
        )

        async def scenario():
            comm = await self._connect()
            await self._send(comm, "@kazi weather")
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(state="completed").exists(),
            ))
            await self.runner.drain(timeout=15)
            return await self._drain_frames(comm)

        self._assert_frames("one tool", self._run(scenario()), [
            self._echo("@kazi weather"),
            self._thinking(),
            self._reasoning(),
            self._step("executing", "started", "Running get weather\u2026"),
            self._step("executing", "completed", "get weather: success"),
            self._reasoning(),
            self._stream("It is sunny."),
            self._done(),
            self._done(),
            self._stream("", is_final=True),
            self._saved("It is sunny."),
        ])

    def test_a_two_call_parallel_batch(self):
        self.llm.queue(
            _response(
                [
                    _tool("p1", "get_weather", {"city": "Paris"}),
                    _tool("p2", "get_weather", {"city": "Rome"}),
                ],
                stop_reason="tool_use",
            ),
            _response([_text("Both are sunny.")]),
        )

        async def scenario():
            comm = await self._connect()
            await self._send(comm, "@kazi compare weather")
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(state="completed").exists(),
            ))
            await self.runner.drain(timeout=15)
            return await self._drain_frames(comm)

        self._assert_frames("two-call batch", self._run(scenario()), [
            self._echo("@kazi compare weather"),
            self._thinking(),
            self._reasoning(),
            self._step("executing", "started", "Running get weather\u2026"),
            self._step("executing", "completed", "get weather: success"),
            self._step("executing", "started", "Running get weather\u2026"),
            self._step("executing", "completed", "get weather: success"),
            self._reasoning(),
            self._stream("Both are sunny."),
            self._done(),
            self._done(),
            self._stream("", is_final=True),
            self._saved("Both are sunny."),
        ])

    def test_an_approval_pause_then_yes(self):
        self.llm.queue(
            _response(
                [_tool("c1", "send_email", {"to": "a@b.c", "body": "hi"})],
                stop_reason="tool_use",
            ),
            _response([_text("Sent.")]),
        )

        async def scenario():
            comm = await self._connect()
            await self._send(comm, "@kazi send it")
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(state="paused").exists(),
            ))
            await self.runner.drain(timeout=15)
            paused = await sync_to_async(
                lambda: Turn.objects.get(state="paused")
            )()
            await self._send(comm, "@kazi yes")
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(
                    pk=paused.pk, state="completed",
                ).exists(),
            ))
            await self.runner.drain(timeout=15)
            return await self._drain_frames(comm)

        self._assert_frames("approval then yes", self._run(scenario()), [
            self._echo("@kazi send it"),
            self._thinking(),
            self._reasoning(),
            self._stream(_CONFIRM_PROMPT),
            self._step("validating", "completed", "Waiting for confirmation."),
            self._done(),
            self._stream("", is_final=True),
            self._saved(_CONFIRM_PROMPT),
            self._echo("@kazi yes"),
            self._step("executing", "completed", "send email: success"),
            self._reasoning(),
            self._stream("Sent."),
            self._done(),
            self._done(),
            self._stream("", is_final=True),
            self._saved("Sent."),
        ])

    def test_a_budget_pause(self):
        self.llm.queue(_response(
            [_tool("b1", "request_more_budget", {"reason": "need more"})],
            stop_reason="tool_use",
        ))

        async def scenario():
            comm = await self._connect()
            await self._send(comm, "@kazi keep going")
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(state="paused").exists(),
            ))
            await self.runner.drain(timeout=15)
            return await self._drain_frames(comm)

        self._assert_frames("budget pause", self._run(scenario()), [
            self._echo("@kazi keep going"),
            self._thinking(),
            self._reasoning(),
            self._stream("need more"),
            self._stream(_BUDGET_PAUSE),
            self._step("validating", "completed", "Waiting for more budget."),
            self._done(),
            self._stream("", is_final=True),
            self._saved("need more" + _BUDGET_PAUSE),
        ])

    def test_a_quota_pause(self):
        async def quota():
            raise TokenQuotaExceeded(3600)

        self.llm.queue(quota)

        async def scenario():
            comm = await self._connect()
            await self._send(comm, "@kazi keep going")
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(state="paused").exists(),
            ))
            await self.runner.drain(timeout=15)
            return await self._drain_frames(comm)

        self._assert_frames("quota pause", self._run(scenario()), [
            self._echo("@kazi keep going"),
            self._thinking(),
            self._reasoning(),
            self._stream(_QUOTA_PAUSE),
            self._step("executing", "completed", "Error encountered."),
            self._done(),
            self._stream("", is_final=True),
            self._saved(_QUOTA_PAUSE),
        ])

    def test_an_exact_stop_mid_turn(self):
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
            comm = await self._connect()
            await self._send(comm, "@kazi weather")
            await asyncio.wait_for(entered.wait(), timeout=5)
            turn = await sync_to_async(
                lambda: Turn.objects.get(state="running")
            )()
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
            return await self._drain_frames(comm)

        self._assert_frames("stop mid-turn", self._run(scenario()), [
            self._echo("@kazi weather"),
            self._thinking(),
            self._reasoning(),
            self._echo("@kazi stop"),
            self._saved(STOP_NOTICE),
            self._stream(STOP_ENDED_REPLY_LINE.lstrip()),
            self._done(),
            self._done(),
            self._stream("", is_final=True),
            self._saved(STOP_ENDED_REPLY_LINE),
        ])

    def test_a_stop_at_the_pause_edge(self):
        real_persist = agent_loop._persist_pause_state

        async def persist_then_stop(context, *args, **kwargs):
            saved = await real_persist(context, *args, **kwargs)
            turn_id = context.get("turn_id")
            await sync_to_async(
                lambda: Turn.objects.filter(pk=turn_id).update(
                    cancel_requested_at=timezone.now(),
                )
            )()
            return saved

        self.llm.queue(_response(
            [_tool("e1", "send_email", {"to": "a@b.c", "body": "hi"})],
            stop_reason="tool_use",
        ))

        async def scenario():
            comm = await self._connect()
            with patch(
                "orchestration.agent_loop._persist_pause_state",
                new=persist_then_stop,
            ):
                await self._send(comm, "@kazi send it")
                self.assertTrue(await self._wait_for(
                    lambda: Turn.objects.filter(state="cancelled").exists(),
                ))
                await self.runner.drain(timeout=15)
            return await self._drain_frames(comm)

        self._assert_frames("stop at pause edge", self._run(scenario()), [
            self._echo("@kazi send it"),
            self._thinking(),
            self._reasoning(),
            self._stream(STOP_ENDED_REPLY_LINE.lstrip()),
            self._done(),
            self._stream("", is_final=True),
            self._saved(STOP_ENDED_REPLY_LINE),
        ])

    def test_a_reset(self):
        async def scenario():
            comm = await self._connect()
            await self._send(comm, "@kazi reset")
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(
                    state__in=("completed", "failed"),
                ).exists(),
            ))
            await self.runner.drain(timeout=15)
            return await self._drain_frames(comm)

        self._assert_frames("reset", self._run(scenario()), [
            self._echo("@kazi reset"),
            self._stream("Okay, starting fresh. What would you like to do?"),
            self._done("Context reset."),
        ])

    def test_a_fresh_loop_error(self):
        async def failing_loop(**kwargs):
            raise RuntimeError("boom")
            yield  # noqa: unreachable — keeps this an async generator

        async def scenario():
            comm = await self._connect()
            with patch(
                "orchestration.coordinator.run_agent_loop",
                new=failing_loop,
            ):
                await self._send(comm, "@kazi hello")
                self.assertTrue(await self._wait_for(
                    lambda: Turn.objects.filter(state="failed").exists(),
                ))
                await self.runner.drain(timeout=15)
            return await self._drain_frames(comm)

        self._assert_frames("fresh-loop error", self._run(scenario()), [
            self._echo("@kazi hello"),
            self._thinking(),
            self._stream("Something went wrong on my side. Nothing further was run."),
            self._done(),
            self._stream("", is_final=True),
            self._saved("Something went wrong on my side. Nothing further was run."),
        ])

    def _pause_for_blocks(self, blocks):
        self.llm.queue(_response(blocks, stop_reason="tool_use"))

        async def pause(comm):
            await self._send(comm, "@kazi send it")
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(state="paused").exists(),
            ))
            await self.runner.drain(timeout=15)
            return await sync_to_async(
                lambda: Turn.objects.get(state="paused")
            )()

        return pause

    def test_a_resume_path_error(self):
        async def failing_resume(**kwargs):
            raise RuntimeError("resume boom")

        async def scenario():
            comm = await self._connect()
            paused = await self._pause_for_blocks([
                _tool("r1", "send_email", {"to": "a@b.c", "body": "hi"}),
            ])(comm)
            with patch(
                "orchestration.coordinator.resume_after_confirmation",
                new=failing_resume,
            ):
                await self._send(comm, "@kazi yes")
                self.assertTrue(await self._wait_for(
                    lambda: Turn.objects.filter(
                        pk=paused.pk, state="failed",
                    ).exists(),
                ))
                await self.runner.drain(timeout=15)
            return await self._drain_frames(comm)

        self._assert_frames("resume-path error", self._run(scenario()), [
            self._echo("@kazi send it"),
            self._thinking(),
            self._reasoning(),
            self._stream(_CONFIRM_PROMPT),
            self._step("validating", "completed", "Waiting for confirmation."),
            self._done(),
            self._stream("", is_final=True),
            self._saved(_CONFIRM_PROMPT),
            self._echo("@kazi yes"),
        ])

    def test_an_error_in_pending_approval_prompt(self):
        async def scenario():
            comm = await self._connect()
            await self._pause_for_blocks([
                _tool("q1", "send_email", {"to": "a@b.c", "body": "hi"}),
            ])(comm)
            with patch(
                "orchestration.coordinator.pending_approval_prompt",
                new=AsyncMock(side_effect=RuntimeError("prompt boom")),
            ):
                await self._send(comm, "@kazi what does that do?")
                self.assertTrue(await self._wait_for(
                    lambda: Turn.objects.filter(state="failed").exists(),
                ))
                await self.runner.drain(timeout=15)
            return await self._drain_frames(comm)

        self._assert_frames(
            "pending_approval_prompt error", self._run(scenario()), [
                self._echo("@kazi send it"),
                self._thinking(),
                self._reasoning(),
                self._stream(_CONFIRM_PROMPT),
                self._step("validating", "completed", "Waiting for confirmation."),
                self._done(),
                self._stream("", is_final=True),
                self._saved(_CONFIRM_PROMPT),
                self._echo("@kazi what does that do?"),
            ],
        )

    def test_a_queued_second_message(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def before(n):
            if n == 1:
                entered.set()
                await release.wait()

        self.llm.before_call = before
        self.llm.queue(
            _response([_text("first answer")]),
            _response([_text("second answer")]),
        )

        async def scenario():
            comm = await self._connect()
            await self._send(comm, "@kazi first")
            await asyncio.wait_for(entered.wait(), timeout=5)
            await self._send(comm, "@kazi second")
            release.set()
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(state="completed").count() == 2,
            ))
            await self.runner.drain(timeout=15)
            return await self._drain_frames(comm)

        self._assert_frames("queued second message", self._run(scenario()), [
            self._echo("@kazi first"),
            self._thinking(),
            self._reasoning(),
            self._echo("@kazi second"),
            self._stream("first answer"),
            self._done(),
            self._done(),
            self._stream("", is_final=True),
            self._saved("first answer"),
            self._thinking(),
            self._reasoning(),
            self._stream("second answer"),
            self._done(),
            self._done(),
            self._stream("", is_final=True),
            self._saved("second answer"),
        ])

    @override_settings(TURN_QUEUE_MAX=1)
    def test_a_full_queue(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def before(n):
            if n == 1:
                entered.set()
                await release.wait()

        self.llm.before_call = before
        self.llm.queue(
            _response([_text("first answer")]),
            _response([_text("second answer")]),
        )

        async def scenario():
            comm = await self._connect()
            await self._send(comm, "@kazi first")
            await asyncio.wait_for(entered.wait(), timeout=5)
            await self._send(comm, "@kazi second")  # waits in the one slot
            await self._send(comm, "@kazi third")   # queue is full
            self.assertTrue(await self._wait_for(
                lambda: any(
                    "won't be answered" in text
                    for text in self._notice_texts()
                ),
            ))
            release.set()
            self.assertTrue(await self._wait_for(
                lambda: Turn.objects.filter(state="completed").count() == 2,
            ))
            await self.runner.drain(timeout=15)
            return await self._drain_frames(comm)

        self._assert_frames("queue full", self._run(scenario()), [
            self._echo("@kazi first"),
            self._thinking(),
            self._reasoning(),
            self._echo("@kazi second"),
            self._stream("[Harness: queued (position 1).]\n\n"),
            self._echo("@kazi third"),
            self._saved(
                "You already have 1 messages waiting; this one won't be "
                "answered. Send it again after the current reply."
            ),
            self._stream("first answer"),
            self._done(),
            self._done(),
            self._stream("", is_final=True),
            self._saved("first answer"),
            self._thinking(),
            self._reasoning(),
            self._stream("second answer"),
            self._done(),
            self._done(),
            self._stream("", is_final=True),
            self._saved("second answer"),
        ])

    def _notice_texts(self):
        from chatbot.history import decrypt_stored

        texts = []
        for message in Message.objects.filter(member__User__username="kazi"):
            content, payload = decrypt_stored(self.room, message)
            if (payload or {}).get("origin") == "turn_notice":
                texts.append(content)
        return texts

    def test_a_record_failure(self):
        async def scenario():
            comm = await self._connect()
            with patch(
                "orchestration.turns.link_message", return_value=False,
            ):
                await self._send(comm, "@kazi hello")
                self.assertTrue(await self._wait_for(
                    lambda: any(
                        "I couldn't record this message" in text
                        for text in self._notice_texts()
                    ),
                ))
            return await self._drain_frames(comm)

        self._assert_frames("record failed", self._run(scenario()), [
            self._echo("@kazi hello"),
            self._saved(
                "I couldn't record this message, so nothing was run. "
                "Please try again."
            ),
        ])

    def test_an_early_fail_room_gone(self):
        self.llm.queue(_response([_text("Hi.")]))

        async def scenario():
            comm = await self._connect()
            with patch(
                "orchestration.coordinator._room_exists",
                return_value=False,
            ):
                await self._send(comm, "@kazi hello")
                self.assertTrue(await self._wait_for(
                    lambda: Turn.objects.filter(
                        state="failed", end_reason="room_gone",
                    ).exists(),
                ))
                await self.runner.drain(timeout=15)
            return await self._drain_frames(comm)

        self._assert_frames("early fail room_gone", self._run(scenario()), [
            self._echo("@kazi hello"),
            self._thinking(),
            self._reasoning(),
            self._stream("Hi."),
            self._done(),
            self._done(),
            self._stream("", is_final=True),
            self._saved("Hi."),
        ])

    def test_the_reaper_ending_a_stale_turn(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def before(n):
            if n == 1:
                entered.set()
                await release.wait()

        self.llm.before_call = before
        self.llm.queue(_response(
            [_tool("z1", "get_weather", {"city": "Paris"})],
            stop_reason="tool_use",
        ))

        async def scenario():
            comm = await self._connect()
            await self._send(comm, "@kazi weather")
            await asyncio.wait_for(entered.wait(), timeout=5)
            turn = await sync_to_async(
                lambda: Turn.objects.get(state="running")
            )()
            await sync_to_async(
                lambda: Turn.objects.filter(pk=turn.pk).update(
                    state="failed", end_reason="lease_lost",
                    version=turn.version + 1, ended_at=timezone.now(),
                )
            )()
            with turn_leases._REGISTRY_LOCK:
                turn_leases._FENCED_TURNS.add(str(turn.pk))
            release.set()
            self.assertTrue(await self._wait_for(
                lambda: not Turn.objects.filter(
                    pk=turn.pk, state="running",
                ).exists(),
            ))
            await self.runner.drain(timeout=15)
            return await self._drain_frames(comm)

        self._assert_frames("reaper stale turn", self._run(scenario()), [
            self._echo("@kazi weather"),
            self._thinking(),
            self._reasoning(),
            self._stream(LEASE_LOST_NOTE),
            self._done(),
            self._done(),
            self._stream("", is_final=True),
            self._saved(LEASE_LOST_NOTE),
        ])
