"""The turn runner: a claimed Turn leaves the socket's dispatch loop (#216b-2).

``ChatConsumer`` validates the frame, saves the message and a ``queued`` Turn,
then submits the key here and returns. A per-key drainer claims the oldest
queued row FIFO, rebuilds the turn from the database and hands it to the
coordinator with ownership already taken. The socket is free to read frames
while that runs, and a second worker can never run the same message.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional, Set

from asgiref.sync import sync_to_async
from django.conf import settings
from channels.layers import get_channel_layer

from orchestration.models import Turn
from orchestration.turns import (
    PauseContinuationLost,
    TurnRef,
    claim_next_queued,
    continue_pause as continue_pause_turn,
    count_queued,
    create_turn,
    transition,
)
from orchestration.turn_leases import (
    register_live_turn,
    unregister_live_turn,
)

from .history import decrypt_stored, get_history_rows
from .kazi_messages import (
    KAZI_USERNAME,
    ORIGIN_TURN,
    ORIGIN_TURN_NOTICE,
    KaziMessageError,
    persist_kazi_message,
)
from .models import Chatroom, Message
from .transcript import build_history_messages, has_several_speakers, strip_wake_word

logger = logging.getLogger(__name__)

QUEUED_NOTICE = "[Harness: queued (position {position}).]"
QUEUE_FULL_NOTICE = (
    "You already have {cap} messages waiting; this one won't be answered. "
    "Send it again after the current reply."
)


def queue_cap() -> int:
    try:
        value = int(getattr(settings, "TURN_QUEUE_MAX", 3))
    except (TypeError, ValueError):
        return 3
    return value if value > 0 else 3


def _request_message(turn: Turn) -> Optional[Message]:
    return (
        Message.objects.filter(turn_id=turn.pk)
        .select_related("member__User")
        .order_by("-timestamp")
        .first()
    )


def _fail_turn(turn: Turn, reason: str) -> bool:
    return transition(
        str(turn.pk), turn.version, "failed",
        from_state=turn.state, end_reason=reason,
    )


async def run_immediate_directives(
    *, query: str, user_id: int, room_id: Any,
) -> Optional[str]:
    """Run a permission-reducing directive now and return its harness line.

    A kill switch must never wait behind a running turn, so the consumer calls
    this before it queues anything. Returning a line means the message was
    fully handled and must not become a Turn (#216b-2, item 1).
    """
    from orchestration.shell import autopilot as autopilot_mod
    from orchestration.shell import host_grants as host_grants_mod
    from orchestration.shell.chat_intents import (
        host_grant_request,
        is_autopilot_disarm_request,
    )

    if is_autopilot_disarm_request(query):
        try:
            disarmed = await sync_to_async(autopilot_mod.disarm)(
                user_id, room_id, reason="chat",
            )
        except Exception as exc:
            logger.warning("Autopilot disarm failed: %s", exc)
            return (
                "I couldn't confirm autopilot was disarmed. It is off for this "
                "message; disarm it from the operations inbox to be sure."
            )
        if disarmed:
            return "Autopilot disarmed for this room."
        return "Autopilot was not armed for this room."

    request = host_grant_request(query)
    if request:
        verb, raw_host = request
        if verb == "revoke":
            try:
                revoked = await sync_to_async(host_grants_mod.revoke)(
                    user_id, room_id, raw_host,
                )
            except Exception as exc:
                logger.warning("Host revoke request failed: %s", exc)
                return "I couldn't change the host approvals just now. Nothing was changed."
            return (
                "Host approval revoked." if revoked
                else "That host was not approved for this room."
            )
    return None


class TurnRunner:
    """Claims and runs queued Turns for one key at a time, in FIFO order."""

    def __init__(self) -> None:
        self._tasks: Set[asyncio.Task] = set()
        self._active: Dict[asyncio.Task, Dict[str, Any]] = {}

    async def submit(self, room_id: Any, user_id: Any) -> asyncio.Task:
        task = asyncio.create_task(
            self._drain_key(room_id, user_id), name="kazi-turn-drain",
        )
        self._tasks.add(task)
        task.add_done_callback(self._on_done)
        return task

    def _on_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        box = self._active.pop(task, None)
        if task.cancelled():
            logger.warning("Turn runner task was cancelled")
        else:
            exc = task.exception()
            if exc is not None:
                logger.error("Turn runner task failed: %s", exc, exc_info=exc)
        if box is not None:
            # The coroutine's own finally normally ends the row; this is the
            # last-resort path if it never got there (#216b-2, item 7).
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            loop.create_task(self._end_still_running(box["ref"]))

    async def drain(self, *, timeout: float = 60.0) -> None:
        """Await every submitted drain and any follow-up they schedule."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while self._tasks and loop.time() < deadline:
            pending = [task for task in list(self._tasks) if not task.done()]
            if not pending:
                break
            await asyncio.wait(pending, timeout=max(0.0, deadline - loop.time()))

    async def _drain_key(self, room_id: Any, user_id: Any) -> None:
        while True:
            turn = await sync_to_async(claim_next_queued)(room_id, user_id)
            if turn is None:
                return
            await self._run_claimed(turn)

    async def _run_claimed(self, turn: Turn) -> None:
        ref = TurnRef(id=str(turn.pk), version=turn.version, state="running")
        box: Dict[str, Any] = {"ref": ref, "result": None}
        task = asyncio.current_task()
        if task is not None:
            self._active[task] = box
        register_live_turn(ref.id)
        try:
            box["result"] = await self._execute(turn, box)
        except asyncio.CancelledError:
            await self._end_still_running(box["ref"])
            raise
        except PauseContinuationLost:
            # The other writer won; both of our writes rolled back. The
            # placeholder is still running and is ended below.
            logger.info("Pause continuation lost for turn %s", turn.pk)
            await self._end_still_running(box["ref"])
        except Exception:
            logger.exception("Turn runner failed for %s", turn.pk)
            await self._end_still_running(box["ref"])
        finally:
            unregister_live_turn(box["ref"].id)
            if task is not None:
                self._active.pop(task, None)

    async def _end_still_running(self, ref: TurnRef) -> None:
        from orchestration.turns import _saved_reply_exists, afinalize_turn, atransition

        timeout = float(getattr(settings, "TURN_SHUTDOWN_GRACE_SECONDS", 20))
        if ref.state == "committing":
            reply_saved = await sync_to_async(_saved_reply_exists)(ref.id)
            write = asyncio.ensure_future(
                afinalize_turn(ref.id, ref.version, reply_saved=reply_saved)
            )
        else:
            write = asyncio.ensure_future(
                atransition(
                    ref.id, ref.version, "failed",
                    from_state="running", end_reason="error",
                )
            )
        try:
            await asyncio.wait_for(asyncio.shield(write), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("Turn ending write timed out; the reaper will end it")
        except asyncio.CancelledError:
            try:
                await asyncio.wait_for(write, timeout=timeout)
            except (Exception, asyncio.TimeoutError):
                logger.warning("Turn ending write after cancellation failed")
            raise
        except Exception:
            logger.warning("Turn ending write failed; the reaper will end it")

    async def _execute(self, turn: Turn, box: Dict[str, Any]) -> Any:
        from orchestration import coordinator as coordinator_mod

        room = await sync_to_async(Chatroom.objects.filter(pk=turn.room_id).first)()
        if room is None:
            await sync_to_async(_fail_turn)(turn, "room_gone")
            return None
        request = await sync_to_async(_request_message)(turn)
        if request is None:
            await sync_to_async(_fail_turn)(turn, "request_missing")
            return None
        content, _payload = await sync_to_async(decrypt_stored)(room, request)
        if content == "Error: Could not decrypt message.":
            await sync_to_async(_fail_turn)(turn, "decrypt_failed")
            return None

        participants = await sync_to_async(
            lambda: list(room.participants.select_related("User"))
        )()
        kazi_member = next(
            (m for m in participants if m.User.username == KAZI_USERNAME), None,
        )
        human_members = [
            m for m in participants if m.User.username != KAZI_USERNAME
        ]
        is_ai_room = bool(kazi_member) and len(human_members) == 1
        addressed = strip_wake_word(content)
        ai_query = addressed if addressed is not None else (
            content.strip() if is_ai_room else None
        )
        if not ai_query:
            await sync_to_async(_fail_turn)(turn, "empty_query")
            return None

        history_rows = await sync_to_async(get_history_rows)(
            room,
            limit=8,
            request_message_id=request.id,
            exclude_message_id=request.id,
        )
        multi_user = len(human_members) > 1 or has_several_speakers(history_rows)
        history_messages = build_history_messages(
            history_rows,
            exclude_message_id=request.id,
            multi_user=multi_user,
            max_chars=int(getattr(settings, "HISTORY_MAX_CHARS", 60000)),
            viewer_user_id=turn.user_id,
        )
        speaker_note = ""
        if multi_user:
            speaker_note = (
                "Several people share this room. Earlier user turns are prefixed "
                f"with the speaker's name. The message you are answering is from "
                f"{request.member.User.username}."
            )

        layer = get_channel_layer()
        room_group = f"chat_{room.id}"

        async def send_chunk(correlation_id, chunk_text, is_final):
            if layer is None:
                return
            await layer.group_send(
                room_group,
                {
                    "type": "ai_stream_chunk",
                    "chunk": chunk_text,
                    "is_final": is_final,
                    "correlation_id": correlation_id,
                },
            )

        async def send_step_event(correlation_id, event_payload):
            if layer is None:
                return
            await layer.group_send(
                room_group,
                {
                    "type": "ai_step_event",
                    "event": event_payload,
                    "correlation_id": correlation_id,
                },
            )

        async def get_context_prompt():
            from .context_manager import ContextManager

            base = await sync_to_async(ContextManager.get_context_prompt)(
                room.id, room=room, participants=participants,
            ) or ""
            return "\n\n".join(part for part in (speaker_note, base) if part)

        def bump_signals(actions):
            from .tasks import update_proactive_signals

            for action in actions:
                update_proactive_signals(room.id, turn.user_id, action)

        async def continue_pause_cb(paused_turn, message_id):
            ref = await sync_to_async(continue_pause_turn)(
                turn, paused_turn, message_id=message_id,
            )
            if ref is None:
                raise PauseContinuationLost("paused")
            unregister_live_turn(box["ref"].id)
            register_live_turn(ref.id)
            box["ref"] = ref
            return ref

        coordinator = coordinator_mod.OrchestrationCoordinator()
        result = await coordinator.handle_message(
            query=ai_query,
            user_id=turn.user_id,
            room_id=room.id,
            username=request.member.User.username,
            message_id=request.id,
            send_chunk=send_chunk,
            send_step_event=send_step_event,
            get_context_prompt=get_context_prompt,
            bump_signals=bump_signals,
            history_messages=history_messages,
            claimed_turn=turn,
            continue_pause=continue_pause_cb,
        )
        if result.turn_id:
            # Track the turn the coordinator actually ended up holding; a
            # continuation swapped the placeholder out, and cancellation must
            # end the resumed row (#216b-2, addition A).
            box["ref"] = TurnRef(
                id=result.turn_id,
                version=result.turn_version,
                state=result.turn_state or box["ref"].state,
            )

        reply_saved = not (result.persist and result.full_response.strip())
        full_response_text = result.full_response
        if result.persist and full_response_text.strip():
            try:
                ai_message = await sync_to_async(persist_kazi_message)(
                    room,
                    content=full_response_text,
                    origin=ORIGIN_TURN,
                    turn=result.turn_id or None,
                    model_text=result.model_text,
                    harness_text=result.harness,
                    tools=result.tools,
                    by=turn.user_id,
                )
            except KaziMessageError:
                ai_message = None
                logger.warning("AI reply not saved: room key unavailable")
            if ai_message is not None:
                reply_saved = True
                await self._maybe_voice(ai_message, turn.user_id)

        if result.turn_state == "committing":
            await coordinator.finalize_turn(result, reply_saved=reply_saved)
        elif result.turn_state == "running":
            await self._end_still_running(
                TurnRef(
                    id=result.turn_id,
                    version=result.turn_version,
                    state=result.turn_state,
                )
            )
        return result

    async def _maybe_voice(self, ai_message, user_id: int) -> None:
        try:
            from orchestration.user_preferences import get_user_preferences
            from .dispatch import dispatch_task
            from .tasks import generate_voice_response

            prefs = await sync_to_async(get_user_preferences)(user_id)
            if bool(prefs.get("ai_voice_enabled", False)):
                dispatch_task(generate_voice_response, ai_message.id)
        except Exception as exc:
            logger.debug("Voice preference check skipped: %s", exc)


_DEFAULT_RUNNER: Optional[TurnRunner] = None


def get_turn_runner() -> TurnRunner:
    global _DEFAULT_RUNNER
    if _DEFAULT_RUNNER is None:
        _DEFAULT_RUNNER = TurnRunner()
    return _DEFAULT_RUNNER


async def drain(*, timeout: float = 60.0) -> None:
    """Test helper: wait for the default runner to finish its submissions."""
    await get_turn_runner().drain(timeout=timeout)


def enqueue_turn(*, room_id: Any, user_id: Any, request_message_id: int) -> Turn:
    """Create the queued Turn that a routed message hands to the runner."""
    return create_turn(
        room_id=room_id,
        user_id=user_id,
        request_message_id=request_message_id,
    )


def queued_count(room_id: Any, user_id: Any) -> int:
    return count_queued(room_id, user_id)


def persist_turn_notice(room: Chatroom, text: str) -> Optional[Message]:
    try:
        return persist_kazi_message(room, content=text, origin=ORIGIN_TURN_NOTICE)
    except KaziMessageError:
        logger.warning("Turn notice not saved: the room key is unavailable")
        return None
    except Exception as exc:
        logger.warning("Turn notice not saved: %s", type(exc).__name__)
        return None
