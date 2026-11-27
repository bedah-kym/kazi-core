"""217a-1b: one event sink for the turn stream; v1 frames unchanged.

The boundary test fails if the coordinator or the runner calls the transport
directly again. The routing test drives a real Turn with a recording sink and
checks the typed events and the v1 frames the adapter derives from them.
"""
from __future__ import annotations

import ast
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone

from chatbot.models import Chatroom, Member, Message
from chatbot.stream_protocol import V1Adapter
from orchestration import turn_leases
from orchestration.agent_loop import AgentEvent
from orchestration.coordinator import OrchestrationCoordinator

FORBIDDEN = {"send_chunk", "send_step_event", "broadcast_chunk"}
ROOT = Path(__file__).resolve().parents[2]
WATCHED = (
    ROOT / "Backend" / "orchestration" / "coordinator.py",
    ROOT / "Backend" / "chatbot" / "turn_runner.py",
)


def _called_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            names.append(func.id)
        elif isinstance(func, ast.Attribute):
            names.append(func.attr)
    return names


class StreamProtocolBoundaryTests(SimpleTestCase):
    def test_the_turn_path_never_calls_the_transport_directly(self):
        offenders = {}
        for path in WATCHED:
            hits = sorted(set(_called_names(path)) & FORBIDDEN)
            if hits:
                offenders[path.name] = hits
        self.assertEqual(
            offenders, {},
            "coordinator/runner must emit through the sink, not the transport",
        )


def _text(text, channel="answer"):
    return AgentEvent("text_delta", {"text": text, "channel": channel})


def _done(reason="finished"):
    return AgentEvent("done", {
        "end_reason": reason, "elapsed": 1, "active_seconds": 1,
    })


def _loop(*events):
    async def _events(**_kwargs):
        for event in events:
            yield event

    return _events


class RecordingSink:
    """Records every typed event and renders the same events through v1."""

    def __init__(self):
        self.events = []
        self.frames = []

        async def send_chunk(correlation_id, chunk, is_final):
            self.frames.append(("chunk", chunk, is_final))

        async def send_step_event(correlation_id, payload):
            self.frames.append((
                "step", payload["phase"], payload["state"], payload["message"],
            ))

        self.adapter = V1Adapter(
            send_chunk, send_step_event, step_id="turn_1",
        )

    async def emit(self, event):
        self.events.append(event)
        await self.adapter.emit(event)

    def kinds(self):
        return [event.kind for event in self.events]

    def of_kind(self, kind):
        return [event for event in self.events if event.kind == kind]


@override_settings(
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
    CONSENT_SKEW_SECONDS=0,
)
class SinkRoutingTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        User = get_user_model()
        self.user = User.objects.create_user(
            username="sink-router", password="fake-token",  # nosec B106
        )
        self.member, _ = Member.objects.get_or_create(User=self.user)
        self.room = Chatroom.objects.create()
        self.room.participants.add(self.member)
        self.message = Message.objects.create(
            member=self.member,
            content="@kazi do the thing",
            timestamp=timezone.now(),
        )
        self.room.chats.add(self.message)
        turn_leases._LIVE_TURNS.clear()
        turn_leases._FENCED_TURNS.clear()
        turn_leases._LAST_RENEWAL.clear()
        self.addCleanup(turn_leases._LIVE_TURNS.clear)
        self.addCleanup(turn_leases._FENCED_TURNS.clear)
        self.addCleanup(turn_leases._LAST_RENEWAL.clear)

    def _run(self, events, sink):
        patches = {
            "orchestration.coordinator.load_memory_summary": AsyncMock(return_value=None),
            "orchestration.coordinator.get_user_preferences": lambda user_id, profile_obj=None: {},
            "orchestration.coordinator.has_pending_agent_state": AsyncMock(return_value=False),
            "orchestration.coordinator.has_pending_budget_state": AsyncMock(return_value=False),
            "orchestration.coordinator.cache": MagicMock(),
            "orchestration.coordinator.record_event": MagicMock(),
            "orchestration.coordinator.record_from_event": MagicMock(return_value=[]),
            "orchestration.coordinator.run_agent_loop": _loop(*events),
            "orchestration.turn_leases.ensure_heartbeat": MagicMock(return_value=None),
            "orchestration.turn_leases.ensure_cancel_poll": MagicMock(return_value=None),
        }

        async def run():
            try:
                return await OrchestrationCoordinator().handle_message(
                    query="do the thing",
                    user_id=self.user.id,
                    room_id=self.room.id,
                    username=self.user.username,
                    message_id=self.message.id,
                    send_chunk=AsyncMock(),
                    send_step_event=AsyncMock(),
                    get_context_prompt=AsyncMock(return_value=""),
                    bump_signals=MagicMock(),
                    sink=sink,
                )
            finally:
                await turn_leases.stop_heartbeat()

        with ExitStack() as stack:
            for target, value in patches.items():
                stack.enter_context(patch(target, new=value))
            return async_to_sync(run)()

    def test_a_plain_turn_makes_the_same_text_events_and_frames(self):
        sink = RecordingSink()
        self._run([_text("Hi there."), _done()], sink)

        text_events = sink.of_kind("text")
        self.assertEqual(
            [(event.text, event.is_final) for event in text_events],
            [("Hi there.", False), ("", True)],
        )
        self.assertEqual(text_events[0].channel, "answer")
        self.assertEqual(sink.frames, [
            ("step", "planning", "started", "Thinking\u2026"),
            ("chunk", "Hi there.", False),
            ("step", "done", "completed", "Request complete."),
            ("chunk", "", True),
        ])

    def test_a_tool_turn_keeps_dispatch_and_duration_at_the_sink(self):
        sink = RecordingSink()
        self._run([
            AgentEvent("tool_dispatch", {
                "call_id": "c1", "name": "get_weather",
                "input": {"city": "Paris"}, "iteration": 1,
            }),
            AgentEvent("tool_start", {"name": "get_weather", "input": {"city": "Paris"}}),
            AgentEvent("tool_result", {
                "id": "c1", "name": "get_weather", "input": {"city": "Paris"},
                "result": {"status": "success"},
                "shown": "sunny", "iteration": 1, "duration_ms": 7,
            }),
            _text("It is sunny."),
            _done(),
        ], sink)

        dispatch = sink.of_kind("tool_dispatch")
        self.assertEqual(len(dispatch), 1)
        self.assertEqual(dispatch[0].call_id, "c1")
        self.assertTrue(dispatch[0].tool_dispatch)
        result = sink.of_kind("tool_result")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].duration_ms, 7)
        self.assertEqual(result[0].status, "success")
        self.assertEqual(sink.of_kind("text")[0].channel, "answer")
        # tool_dispatch is a fact, not a frame: v1 does not reorder around it.
        self.assertEqual(sink.frames, [
            ("step", "planning", "started", "Thinking\u2026"),
            ("step", "executing", "started", "Running get weather\u2026"),
            ("step", "executing", "completed", "get weather: success"),
            ("chunk", "It is sunny.", False),
            ("step", "done", "completed", "Request complete."),
            ("chunk", "", True),
        ])

    def test_an_approval_pause_keeps_the_call_id_and_expiry_at_the_sink(self):
        sink = RecordingSink()
        self._run([
            AgentEvent("tool_dispatch", {
                "call_id": "c1", "name": "send_email",
                "input": {"to": "a@b.c"}, "iteration": 1,
            }),
            AgentEvent("confirmation", {
                "message": "Approve?",
                "calls": [{"call_id": "c1", "tool_name": "send_email"}],
                "expires_at": "2026-11-27T00:00:00+00:00",
            }),
            AgentEvent("paused", {"pause_kind": "approval"}),
        ], sink)

        confirmation = sink.of_kind("confirmation")
        self.assertEqual(len(confirmation), 1)
        self.assertEqual(confirmation[0].call_id, "c1")
        self.assertEqual(
            confirmation[0].expires_at, "2026-11-27T00:00:00+00:00",
        )
        self.assertEqual(
            sink.of_kind("paused")[0].pause_kind, "approval",
        )
        self.assertEqual(sink.frames, [
            ("step", "planning", "started", "Thinking\u2026"),
            ("chunk", "Approve?", False),
            ("step", "validating", "completed", "Waiting for confirmation."),
            ("step", "done", "completed", "Request complete."),
            ("chunk", "", True),
        ])
