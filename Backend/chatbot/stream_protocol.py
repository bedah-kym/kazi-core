"""One internal event sink for a turn's stream.

The coordinator and the turn runner emit typed :class:`TurnEvent` objects. Only
the adapter behind the sink touches the transport, so the v1 frames stay byte
for byte what they were (217a-1b). The v2 adapter (217a-2a) builds on the same
events, which is why they carry facts v1 drops: the text ``channel``, a call's
``call_id`` and ``duration_ms``, ``tool_dispatch`` and the approval ``call_id``
and ``expires_at``.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from asgiref.sync import sync_to_async

from orchestration.contracts import build_step_event

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

    async def emit(self, event: TurnEvent) -> None:
        kind = event.kind
        if kind in ("text", "harness"):
            text = event.text or "".join(
                segment.text for segment in event.segments
            )
            await self._send_chunk(
                self.correlation_id, text, event.is_final,
            )
            return
        if kind == "progress":
            await self._progress(event.phase, event.state, event.message)
            return
        if kind == "tool_start":
            tool = (event.name or "action").replace("_", " ")
            await self._progress("executing", "started", f"Running {tool}\u2026")
            return
        if kind == "tool_result":
            tool = (event.name or "action").replace("_", " ")
            label = f"{tool}: {event.status}"
            if event.not_run:
                await self._progress(
                    "executing", "skipped", f"{label} (not run)",
                )
            else:
                await self._progress("executing", "completed", label)
            return
        if kind == "saved":
            await self._send_saved(event)
            return
        # tool_dispatch, confirmation, done and anything newer are facts the
        # v1 stream does not render. Dropping them here is what keeps the v1
        # frames in their old order.
        return


class TurnEventSink:
    """The one emit entry point the coordinator and runner use."""

    def __init__(self, adapter: V1Adapter) -> None:
        self._adapter = adapter

    @property
    def correlation_id(self) -> str:
        return self._adapter.correlation_id

    async def emit(self, event: TurnEvent) -> None:
        await self._adapter.emit(event)


def build_stream_sink(
    send_chunk: SendChunk,
    send_step_event: SendStepEvent,
    *,
    step_id: str = "",
    correlation_id: Optional[str] = None,
    send_saved: Optional[SendSaved] = None,
) -> TurnEventSink:
    """Build the v1 sink the coordinator and runner share for one turn."""
    return TurnEventSink(V1Adapter(
        send_chunk,
        send_step_event,
        step_id=step_id,
        correlation_id=correlation_id,
        send_saved=send_saved,
    ))
