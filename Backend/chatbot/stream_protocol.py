"""One internal event sink for a turn's stream.

The coordinator and the turn runner emit typed :class:`TurnEvent` objects. Only
the adapter behind the sink touches the transport, so the v1 frames stay byte
for byte what they were (217a-1b). The v2 adapter (217a-2a) builds on the same
events, which is why they carry facts v1 drops: the text ``channel``, a call's
``call_id`` and ``duration_ms``, ``tool_dispatch`` and the approval ``call_id``
and ``expires_at``.

A sink renders each event once per protocol. When it is built with a
:class:`GroupStreamTransport` it puts both renders in one group message (the
v2 frames ride along under ``v2``), so a mixed room costs one broadcast per
event and each socket reads only its own protocol.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from asgiref.sync import sync_to_async

from orchestration.contracts import build_step_event
from orchestration.stream_seq import set_run_seq

logger = logging.getLogger(__name__)

SendChunk = Callable[[str, str, bool], Awaitable[None]]
SendStepEvent = Callable[[str, dict], Awaitable[None]]
SendSaved = Callable[["TurnEvent"], Awaitable[None]]

TEXT_CHANNEL_ANSWER = "answer"
TEXT_CHANNEL_NARRATION = "narration"
TEXT_CHANNEL_HARNESS = "harness"


@dataclass(frozen=True)
class TextSegment:
    """One buffered piece of text, keeping the kind and channel it arrived with."""

    kind: str
    channel: str
    text: str


@dataclass
class TurnEvent:
    """Every fact one stream event carries, whether or not v1 renders it."""

    kind: str
    text: str = ""
    channel: str = ""
    is_final: bool = False
    segments: List[TextSegment] = field(default_factory=list)
    name: str = ""
    call_id: Optional[str] = None
    duration_ms: Optional[int] = None
    status: str = ""
    not_run: bool = False
    tool_dispatch: bool = False
    phase: str = ""
    state: str = ""
    message: str = ""
    pause_kind: str = ""
    expires_at: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)


async def _broadcast_saved(event: TurnEvent) -> None:
    """The default ``ai_message_saved`` sender for a built sink.

    The runner supplies its own layer-backed sender so a saved message costs no
    extra worker hop; this default is for direct coordinator callers.
    """
    from chatbot.kazi_messages import broadcast_kazi_message

    data = event.data
    room = data.get("room")
    message = data.get("message")
    if room is None or message is None:
        return
    await sync_to_async(broadcast_kazi_message)(
        room,
        message,
        data.get("content", ""),
        turn_continues=bool(data.get("turn_continues", False)),
    )


class V1Adapter:
    """Render a :class:`TurnEvent` as today's v1 frames."""

    def __init__(
        self,
        send_chunk: SendChunk,
        send_step_event: SendStepEvent,
        *,
        step_id: str = "",
        correlation_id: Optional[str] = None,
        send_saved: Optional[SendSaved] = None,
    ) -> None:
        self._send_chunk = send_chunk
        self._send_step_event = send_step_event
        self._step_id = step_id
        self._send_saved = send_saved or _broadcast_saved
        self.correlation_id = correlation_id or uuid.uuid4().hex
        # The last channel v1 actually sent, and how many newlines its text
        # ended with, so a narration flush and the answer that follows it a
        # flush later still get exactly one blank line between them.
        self._last_text_channel = ""
        self._last_text_tail_newlines = 0

    def _render_text(self, event: TurnEvent) -> Tuple[str, str, int]:
        """The v1 chunk text: one blank line after narration, nothing stacked.

        The loop emits each step's text as one block, so a narration segment
        is a whole part: whatever follows it, another narration part included,
        starts after one blank line. Newlines already on either side of the
        boundary count toward it; only the missing ones are added.

        Returns the text plus the boundary state it leaves; the caller keeps
        that state only once the chunk is sent, so a failed send never shapes
        the next separator.
        """
        if not event.segments:
            return (
                event.text or "",
                self._last_text_channel,
                self._last_text_tail_newlines,
            )
        pieces = []
        previous = self._last_text_channel
        tail = self._last_text_tail_newlines
        for segment in event.segments:
            text = segment.text
            if not text:
                continue
            if previous == TEXT_CHANNEL_NARRATION:
                lead = len(text) - len(text.lstrip("\n"))
                text = "\n" * max(0, 2 - tail - lead) + text
            stripped = text.rstrip("\n")
            tail = tail + len(text) if not stripped else len(text) - len(stripped)
            pieces.append(text)
            previous = segment.channel
        return "".join(pieces), previous, tail

    async def _progress(self, phase: str, state: str, message: str) -> None:
        await self._send_step_event(
            self.correlation_id,
            build_step_event(
                step_id=self._step_id,
                phase=phase,
                state=state,
                message=message,
            ),
        )

    async def emit(self, event: TurnEvent) -> bool:
        kind = event.kind
        if kind in ("text", "harness"):
            text, channel, tail = self._render_text(event)
            await self._send_chunk(self.correlation_id, text, event.is_final)
            self._last_text_channel = channel
            self._last_text_tail_newlines = tail
            return True
        if kind == "progress":
            await self._progress(event.phase, event.state, event.message)
            return True
        if kind == "tool_start":
            tool = (event.name or "action").replace("_", " ")
            await self._progress("executing", "started", f"Running {tool}\u2026")
            return True
        if kind == "tool_result":
            tool = (event.name or "action").replace("_", " ")
            label = f"{tool}: {event.status}"
            if event.not_run:
                await self._progress(
                    "executing", "skipped", f"{label} (not run)",
                )
            else:
                await self._progress("executing", "completed", label)
            return True
        if kind == "saved":
            await self._send_saved(event)
            return True
        # tool_dispatch, confirmation, done and anything newer are facts the
        # v1 stream does not render. Dropping them here is what keeps the v1
        # frames in their old order.
        return False


class V2Adapter:
    """Render the same :class:`TurnEvent` objects as the v2 contract's frames.

    One turn can change id during a run (the "yes" placeholder is superseded by
    the paused turn it answers), so the adapter keys its state by ``turn_id``
    and the coordinator tells it which turn is current.
    """

    _IDLE = object()

    def __init__(self) -> None:
        self._turns: Dict[str, Dict[str, Any]] = {}
        self._current: str = ""

    def _state(self, turn_id: str) -> Dict[str, Any]:
        state = self._turns.get(turn_id)
        if state is None:
            state = {
                "seq": 0,
                "started": False,
                "ended": False,
                "part": None,
                "part_count": 0,
                "expires_at": None,
                "request_message_id": None,
                "model": "",
            }
            self._turns[turn_id] = state
        return state

    def set_turn(
        self,
        turn_id: Any,
        *,
        next_seq: int = 0,
        started: bool = False,
        request_message_id: Any = None,
        model: str = "",
    ) -> None:
        if not turn_id:
            return
        tid = str(turn_id)
        state = self._state(tid)
        state["seq"] = max(int(state["seq"]), int(next_seq))
        state["started"] = bool(state["started"] or started)
        state["ended"] = False
        state["part"] = None
        if request_message_id is not None:
            state["request_message_id"] = request_message_id
        if model:
            state["model"] = model
        self._current = tid
        set_run_seq(tid, max(0, state["seq"] - 1))

    def last_seq(self, turn_id: Any = None) -> int:
        tid = str(turn_id or self._current or "")
        state = self._turns.get(tid)
        if state is None:
            return 0
        return max(0, int(state["seq"]) - 1)

    def _frame(self, state: Dict[str, Any], frame_type: str, **fields) -> Dict:
        tid = self._current
        frame = {
            "v": 2,
            "type": frame_type,
            "turn_id": tid,
            "seq": int(state["seq"]),
        }
        state["seq"] += 1
        frame.update(fields)
        set_run_seq(tid, state["seq"] - 1)
        return frame

    def _ensure_started(self, state: Dict[str, Any]) -> List[Dict]:
        if state["started"] or state["ended"]:
            return []
        state["started"] = True
        return [self._frame(
            state,
            "turn.started",
            request_message_id=state["request_message_id"],
            model=state["model"] or "",
        )]

    def _close_part(self, state: Dict[str, Any]) -> List[Dict]:
        part = state["part"]
        if part is None:
            return []
        state["part"] = None
        return [self._frame(state, "part.ended", part_id=part["id"])]

    @staticmethod
    def _segments(event: TurnEvent) -> List[TextSegment]:
        if event.segments:
            return list(event.segments)
        if event.text:
            channel = event.channel or TEXT_CHANNEL_ANSWER
            kind = "harness" if event.kind == "harness" else "text"
            return [TextSegment(kind, channel, event.text)]
        return []

    def _text_frames(self, state: Dict[str, Any], event: TurnEvent) -> List[Dict]:
        frames: List[Dict] = []
        for segment in self._segments(event):
            channel = segment.channel or TEXT_CHANNEL_ANSWER
            if segment.kind == "harness" or channel == TEXT_CHANNEL_HARNESS:
                frames.extend(self._close_part(state))
                frames.append(self._frame(
                    state, "notice", kind="info", text=segment.text,
                ))
                continue
            part = state["part"]
            if part is None or part["channel"] != channel:
                frames.extend(self._close_part(state))
                part = {
                    "id": f"p{state['part_count']}",
                    "channel": channel,
                }
                state["part_count"] += 1
                state["part"] = part
                frames.append(self._frame(
                    state, "part.started",
                    part_id=part["id"], channel=channel,
                ))
            frames.append(self._frame(
                state, "text.delta", part_id=part["id"], text=segment.text,
            ))
        if event.is_final:
            frames.extend(self._close_part(state))
        return frames

    def _terminal(self, state: Dict[str, Any], data: Dict[str, Any]) -> List[Dict]:
        frame_type = data.get("frame") or data.get("type") or ""
        fields = dict(data.get("fields") or {})
        frames = self._close_part(state)
        state["ended"] = True
        if frame_type:
            state["started"] = True
            frames.append(self._frame(state, frame_type, **fields))
        return frames

    def render(self, event: TurnEvent) -> List[Dict]:
        data = event.data or {}
        turn_id = str(data.get("turn_id") or self._current or "")
        if not turn_id:
            return []
        state = self._state(turn_id)
        self._current = turn_id
        kind = event.kind
        if kind == "started":
            state["started"] = False
            if data.get("request_message_id") is not None:
                state["request_message_id"] = data.get("request_message_id")
            if data.get("model"):
                state["model"] = data.get("model")
            return self._ensure_started(state)
        if kind == "resumed":
            state["started"] = True
            if data.get("request_message_id") is not None:
                state["request_message_id"] = data.get("request_message_id")
            return [self._frame(
                state, "turn.resumed",
                request_message_id=state["request_message_id"],
            )]
        if kind == "terminal":
            return self._terminal(state, data)
        if kind in ("text", "harness"):
            frames = self._ensure_started(state)
            frames.extend(self._text_frames(state, event))
            return frames
        if kind == "paused":
            frames = self._ensure_started(state)
            frames.extend(self._close_part(state))
            expires_at = event.expires_at or state.get("expires_at")
            frames.append(self._frame(
                state, "turn.paused",
                pause_kind=event.pause_kind or "approval",
                expires_at=expires_at,
            ))
            return frames
        if kind == "confirmation":
            if event.expires_at:
                state["expires_at"] = event.expires_at
            return []
        # progress, tool facts, done and saved have no v2-core frame. The
        # transport passes their v1 frame through to a v2 socket unchanged.
        return []


class GroupStreamTransport:
    """The one place the turn path group-sends, carrying both renders.

    The v1 frame keeps its old group ``type`` (and thus its old consumer
    handler); the v2 frames ride under ``v2``. ``v2_pass`` marks a v1-only
    frame that a v2 socket should receive in its v1 shape (the contract's
    pass-through rule).
    """

    def __init__(self, layer, room_group: str) -> None:
        self._layer = layer
        self._room_group = room_group
        self.v2: List[Dict] = []
        self.v2_pass = False

    async def _send(self, message_type: str, payload: Dict[str, Any]) -> None:
        if self._layer is None:
            return
        payload["v2"] = self.v2
        payload["v2_pass"] = self.v2_pass
        await self._layer.group_send(
            self._room_group, {"type": message_type, **payload},
        )

    async def send_chunk(self, correlation_id, chunk_text, is_final):
        await self._send("ai_stream_chunk", {
            "chunk": chunk_text,
            "is_final": is_final,
            "correlation_id": correlation_id,
        })

    async def send_step(self, correlation_id, event_payload):
        await self._send("ai_step_event", {
            "event": event_payload,
            "correlation_id": correlation_id,
        })

    async def send_saved(self, event: TurnEvent):
        from chatbot.kazi_messages import _default_frame

        data = event.data
        if "message" not in data:
            return
        await self._send("ai_message_saved", {
            "message": _default_frame(
                data["message"],
                data.get("content", ""),
                bool(data.get("turn_continues", False)),
            ),
        })

    async def send_v2(self, frames: List[Dict]) -> None:
        if self._layer is None or not frames:
            return
        await self._layer.group_send(
            self._room_group, {"type": "stream_v2", "frames": frames},
        )


class TurnEventSink:
    """The one emit entry point the coordinator and runner use.

    Fail-soft by design: a frame the transport cannot send is counted and
    dropped, never raised into the turn. ``CancelledError`` still propagates so
    a shutdown can stop the stream.
    """

    def __init__(
        self,
        adapter: Optional[V1Adapter],
        *,
        v2: Optional[V2Adapter] = None,
        transport: Optional[GroupStreamTransport] = None,
    ) -> None:
        self._adapter = adapter
        self._v2 = v2
        self._transport = transport

    @property
    def correlation_id(self) -> str:
        return self._adapter.correlation_id if self._adapter else ""

    def set_turn(self, turn_id: Any, **kwargs: Any) -> None:
        if self._v2 is not None:
            self._v2.set_turn(turn_id, **kwargs)

    def last_seq(self, turn_id: Any = None) -> int:
        return self._v2.last_seq(turn_id) if self._v2 is not None else 0

    async def emit(self, event: TurnEvent) -> bool:
        v2_frames: List[Dict] = []
        try:
            if self._v2 is not None:
                v2_frames = self._v2.render(event)
            if self._transport is not None:
                self._transport.v2 = v2_frames
                self._transport.v2_pass = not v2_frames
            sent_v1 = False
            if self._adapter is not None:
                sent_v1 = bool(await self._adapter.emit(event))
            if self._transport is not None and v2_frames and not sent_v1:
                await self._transport.send_v2(v2_frames)
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a lost frame must not end a turn
            logger.warning(
                "Stream frame send failed: %s", exc.__class__.__name__,
            )
            try:
                from orchestration.telemetry import record_event

                record_event("stream_send_failed", {
                    "kind": event.kind,
                    "turn_id": str((event.data or {}).get("turn_id") or ""),
                    "error_class": exc.__class__.__name__,
                    "is_final": bool(event.is_final),
                })
            except Exception:  # pragma: no cover - telemetry is best effort
                pass
            return False


def build_stream_sink(
    send_chunk: SendChunk,
    send_step_event: SendStepEvent,
    *,
    step_id: str = "",
    correlation_id: Optional[str] = None,
    send_saved: Optional[SendSaved] = None,
    v2_transport: Optional[GroupStreamTransport] = None,
) -> TurnEventSink:
    """Build the sink the coordinator and runner share for one turn.

    With ``v2_transport`` the sink renders v2 too and sends both protocols in
    that transport's one group message. Without it the sink is v1 only, which
    keeps direct coordinator callers and the existing tests unchanged.
    """
    adapter = V1Adapter(
        send_chunk,
        send_step_event,
        step_id=step_id,
        correlation_id=correlation_id,
        send_saved=send_saved,
    )
    v2 = V2Adapter() if v2_transport is not None else None
    return TurnEventSink(adapter, v2=v2, transport=v2_transport)
