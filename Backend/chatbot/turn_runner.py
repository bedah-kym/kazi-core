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
import threading
import weakref
from typing import Any, Dict, Optional, Set

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import transaction
from channels.layers import get_channel_layer

from orchestration.models import Turn
from orchestration.turns import (
    PauseContinuationLost,
    TurnRef,
    claim_next_queued,
    continue_pause as continue_pause_turn,
    count_queued,
    create_turn,
    expire_all_queued_for_key,
    transition,
)
from orchestration.turn_leases import (
    register_live_turn,
    shutdown_grace_seconds,
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
DISARM_FAILED_NOTICE = (
    "I couldn't confirm autopilot was disarmed. Disarm it from the operations "
    "inbox to be sure."
)
RECORD_FAILED_NOTICE = (
    "I couldn't record this message, so nothing was run. Please try again."
)
# A claim that keeps failing retries the same drainer with this backoff.
CLAIM_RETRY_BASE_SECONDS = 2
CLAIM_RETRY_MAX_SECONDS = 60

# One process-wide lock per key so two tabs in one worker cannot both pass the
# cap check together. Cross-worker serialisation is the room row's
# SELECT ... FOR UPDATE inside enqueue_turn (#216b-2 r2, item 4).
_CAP_LOCKS: "weakref.WeakValueDictionary[str, threading.Lock]" = (
    weakref.WeakValueDictionary()
)
_CAP_LOCKS_GUARD = threading.Lock()


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
    """Run a permission-reducing directive now, before any Turn is saved.

    A kill switch must never wait behind a running turn, and it must still
    work when the queue is at its cap. The matchers are the coordinator's own,
    so there is one copy of the rules. Returning a line means the message was
    fully handled and must not become a Turn; returning ``None`` means the
    side effect ran and the message continues to its normal handling.
    """
    from orchestration.adaptive_task import is_cancel_request
    from orchestration.shell import autopilot as autopilot_mod
    from orchestration.shell import host_grants as host_grants_mod
    from orchestration.shell.chat_intents import (
        host_grant_request,
        is_autopilot_disarm_request,
        is_cancel_command,
    )

    async def _disarm() -> Optional[bool]:
        """True if it was armed, False if it was not, None if the write failed."""
        try:
            return bool(await sync_to_async(autopilot_mod.disarm)(
                user_id, room_id, reason="chat",
            ))
        except Exception as exc:
            logger.warning("Autopilot disarm failed: %s", type(exc).__name__)
            return None

    if is_autopilot_disarm_request(query):
        disarmed = await _disarm()
        if disarmed is None:
            return (
                "I couldn't confirm autopilot was disarmed. Disarm it from the "
                "operations inbox to be sure."
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

    # A bare stop/cancel reduces permission too. It is not terminal: the
    # message still runs through its normal handling (which may cancel a
    # pending action). Disarm now so a live turn's mid-run re-read sees it.
    cancel = is_cancel_command(query)
    if not cancel and is_cancel_request(query):
        from orchestration.agent_loop import has_pending_agent_state

        try:
            cancel = bool(await has_pending_agent_state(room_id, user_id))
        except Exception as exc:
            logger.warning("Pending-confirmation read failed; disarming: %s", exc)
            cancel = True
    if cancel:
        disarmed = await _disarm()
        if disarmed is None:
            # The message continues to its normal handling, but the user must
            # know the window may still be armed (#303 r4, item 2). A notice
            # failure must never derail the message (#303 r5, item 5).
            try:
                await sync_to_async(_persist_immediate_notice)(
                    room_id, DISARM_FAILED_NOTICE,
                )
            except Exception as exc:
                logger.warning(
                    "Disarm-failure notice not saved: %s", type(exc).__name__,
                )
    return None


def _claim_in_thread(room_id: Any, user_id: Any):
    """Run one claim on a worker thread and close its DB connection."""
    try:
        return claim_next_queued(room_id, user_id)
    finally:
        try:
            from django.db import connection

            connection.close()
        except Exception:
            pass


def _continue_in_thread(placeholder, paused, message_id):
    """Run one pause swap on a worker thread and close its DB connection."""
    try:
        return continue_pause_turn(
            placeholder, paused, message_id=message_id,
        )
    finally:
        try:
            from django.db import connection

            connection.close()
        except Exception:
            pass


def _persist_immediate_notice(room_id: Any, text: str) -> None:
    from .models import Chatroom

    try:
        room = Chatroom.objects.filter(pk=room_id).first()
        if room is not None:
            persist_turn_notice(room, text)
    except Exception as exc:
        logger.warning("Immediate notice not saved: %s", type(exc).__name__)


class TurnRunner:
    """Claims and runs queued Turns for one key at a time, in FIFO order."""

    def __init__(self) -> None:
        self._tasks: Set[asyncio.Task] = set()
        self._active: Dict[asyncio.Task, Dict[str, Any]] = {}
        self._ending_tasks: Set[asyncio.Task] = set()
        self._keys: Dict[str, tuple] = {}
        self._key_refs: Dict[str, int] = {}
        self._retry_tasks: Dict[str, asyncio.Task] = {}
        self._shutdown_turns: Dict[str, tuple] = {}
        self._shutdown_deadline: Optional[float] = None
        self._stopping = False

    def _remaining_timeout(self, fallback: Optional[float] = None) -> float:
        """Seconds left in the one shutdown deadline, or the setting outside it."""
        deadline = self._shutdown_deadline
        if deadline is None:
            return float(
                fallback
                if fallback is not None
                else getattr(settings, "TURN_SHUTDOWN_GRACE_SECONDS", 20)
            )
        try:
            return max(0.0, deadline - asyncio.get_running_loop().time())
        except RuntimeError:
            return 0.0

    async def submit(self, room_id: Any, user_id: Any) -> asyncio.Task:
        key = f"{room_id}:{user_id}"
        self._keys[key] = (room_id, user_id)
        retrying = self._retry_tasks.get(key)
        if retrying is not None and not retrying.done():
            # The retrying drainer will see the new queued row; do not pile up
            # another one (#303 r5, item 6).
            return retrying
        self._key_refs[key] = self._key_refs.get(key, 0) + 1
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
            ending = loop.create_task(self._end_still_running(box["ref"]))
            self._ending_tasks.add(ending)
            ending.add_done_callback(self._ending_tasks.discard)

    async def drain(self, *, timeout: float = 60.0) -> None:
        """Await every submitted drain and any follow-up they schedule."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while (
            self._tasks or self._ending_tasks
        ) and loop.time() < deadline:
            pending = [
                task for task in list(self._tasks | self._ending_tasks)
                if not task.done()
            ]
            if not pending:
                break
            await asyncio.wait(pending, timeout=max(0.0, deadline - loop.time()))

    async def _drain_key(self, room_id: Any, user_id: Any) -> None:
        key = f"{room_id}:{user_id}"
        failures = 0
        task = asyncio.current_task()
        try:
            while not self._stopping:
                turn = None
                claim_failed = False
                for attempt in range(3):
                    claim = asyncio.ensure_future(
                        sync_to_async(
                            _claim_in_thread, thread_sensitive=False,
                        )(room_id, user_id)
                    )
                    try:
                        turn = await asyncio.shield(claim)
                        break
                    except asyncio.CancelledError:
                        # The claim may have committed before the cancel: keep
                        # its result so the row is ended, not abandoned. Bound
                        # the wait so a stalled DB cannot hold shutdown
                        # (#303 r4 item 6, #303 r5 item 2).
                        try:
                            turn = await asyncio.wait_for(
                                asyncio.shield(claim),
                                timeout=max(0.01, self._remaining_timeout()),
                            )
                        except Exception:
                            turn = None
                        if turn is not None:
                            await self._run_claimed(
                                turn, stop_before_execute=True,
                            )
                        raise
                    except Exception:
                        claim_failed = True
                        logger.exception(
                            "Turn claim failed for %s/%s (attempt %s)",
                            room_id, user_id, attempt + 1,
                        )
                        await asyncio.sleep(0.05)
                if turn is None:
                    if claim_failed and not self._stopping:
                        failures += 1
                        delay = min(
                            CLAIM_RETRY_MAX_SECONDS,
                            CLAIM_RETRY_BASE_SECONDS ** min(failures, 6),
                        )
                        logger.warning(
                            "Turn claim keeps failing for %s/%s; retrying in %ss",
                            room_id, user_id, delay,
                        )
                        if task is not None:
                            self._retry_tasks[key] = task
                        try:
                            await asyncio.sleep(delay)
                        finally:
                            if self._retry_tasks.get(key) is task:
                                self._retry_tasks.pop(key, None)
                        continue
                    return
                failures = 0
                if self._stopping:
                    # The claim won the race with shutdown; end it without
                    # dispatching anything (item 2).
                    await self._run_claimed(turn, stop_before_execute=True)
                    return
                await self._run_claimed(turn)
        finally:
            if self._retry_tasks.get(key) is task:
                self._retry_tasks.pop(key, None)
            if not self._stopping:
                try:
                    await asyncio.wait_for(
                        asyncio.shield(self._release_key(room_id, user_id)),
                        timeout=max(0.01, self._remaining_timeout()),
                    )
                except (asyncio.CancelledError, Exception):
                    pass

    async def _release_key(self, room_id: Any, user_id: Any) -> None:
        """Drop the key once no submitter references it and no work remains."""
        key = f"{room_id}:{user_id}"
        refs = self._key_refs.get(key, 0) - 1
        if refs > 0:
            self._key_refs[key] = refs
            return
        self._key_refs.pop(key, None)
        try:
            busy = await sync_to_async(
                lambda: Turn.objects.filter(
                    room_id=room_id, user_id=user_id,
                    state__in=("queued", "running", "committing"),
                ).exists()
            )()
        except Exception:
            return
        if not busy:
            self._keys.pop(key, None)

    async def shutdown(self, grace_seconds: Optional[int] = None) -> None:
        """Stop claiming, drain within the grace, then cancel and write endings.

        Called from ``lifespan.shutdown`` before the heartbeat stops and before
        ``shutdown.complete``. Queued rows this process leaves behind end with
        the same saved waiting notice as a reaped key (#216b-2 r2, item 2).
        """
        self._stopping = True
        grace = shutdown_grace_seconds() if grace_seconds is None else grace_seconds
        loop = asyncio.get_running_loop()
        grace = max(0, int(grace))
        # The live-turn drain gets the grace; the ending/notice writes then
        # share one extra bounded budget (capped at 5 s). Every later step uses
        # the one deadline, never a fresh full grace (#303 r5, item 2).
        ending_budget = min(5, max(1, grace))
        self._shutdown_deadline = loop.time() + grace + ending_budget
        live_deadline = loop.time() + grace
        while self._tasks and loop.time() < live_deadline:
            await asyncio.sleep(0.05)
        remaining = [task for task in list(self._tasks) if not task.done()]
        for task in remaining:
            task.cancel()
        if remaining:
            await asyncio.wait(
                remaining,
                timeout=max(0.0, self._shutdown_deadline - loop.time()),
            )
        ending = [
            task for task in list(self._ending_tasks) if not task.done()
        ]
        if ending:
            await asyncio.wait(
                ending,
                timeout=max(0.0, self._shutdown_deadline - loop.time()),
            )
        processed = set()
        for turn_id, (room_id, user_id) in list(self._shutdown_turns.items()):
            try:
                row = await self._bounded_read(
                    lambda: Turn.objects.filter(pk=turn_id)
                    .values_list("state", "end_reason").first()
                )
                if not row or row[0] != "failed" or row[1] != "shutdown":
                    # The bounded ending did not land; the reaper's notice is
                    # the backstop and must be the only one.
                    continue
                count = await self._bounded_read(
                    lambda: expire_all_queued_for_key(
                        room_id, user_id, reason="restart", notify=False,
                    )
                )
                await self._save_shutdown_notice(
                    room_id, user_id, turn_id, count,
                )
                processed.add(f"{room_id}:{user_id}")
            except Exception:
                logger.exception("Could not end the shutdown turn's queue")
        for key, (room_id, user_id) in list(self._keys.items()):
            if key in processed:
                continue
            try:
                active = await self._bounded_read(
                    lambda: Turn.objects.filter(
                        room_id=room_id, user_id=user_id,
                        state__in=("running", "committing"),
                    ).exists()
                )
                if active:
                    # Another worker still owns this key; leave its queue.
                    continue
                count = await self._bounded_read(
                    lambda: expire_all_queued_for_key(
                        room_id, user_id, reason="restart", notify=False,
                    )
                )
                if count:
                    await self._save_shutdown_notice(
                        room_id, user_id, None, count,
                    )
            except Exception:
                logger.exception(
                    "Could not expire the queue left by this shutdown",
                )

    async def _bounded_read(self, fn, default=None):
        """Run one sync read with the remaining shutdown time."""
        remaining = max(0.01, self._remaining_timeout())
        read = asyncio.ensure_future(sync_to_async(fn)())
        try:
            return await asyncio.wait_for(asyncio.shield(read), timeout=remaining)
        except asyncio.TimeoutError:
            logger.warning("Shutdown read timed out; continuing")
            return default

    async def _save_shutdown_notice(
        self, room_id: Any, user_id: Any, turn_id: Optional[str], count: int,
    ) -> None:
        """Save one notice, bounded by the same grace as the ending write."""
        from orchestration.turns import save_shutdown_notice

        timeout = max(0.01, self._remaining_timeout())
        write = asyncio.ensure_future(
            sync_to_async(save_shutdown_notice)(
                room_id, user_id, count, turn_id=turn_id,
            )
        )
        try:
            await asyncio.wait_for(asyncio.shield(write), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "Shutdown notice write timed out; the reaper is the backstop",
            )
        except asyncio.CancelledError:
            try:
                await asyncio.wait_for(write, timeout=timeout)
            except (Exception, asyncio.TimeoutError):
                logger.warning("Shutdown notice write after cancellation failed")
            raise
        except Exception:
            logger.warning("Shutdown notice write failed; the reaper is the backstop")

    async def _run_claimed(
        self, turn: Turn, *, stop_before_execute: bool = False,
    ) -> None:
        ref = TurnRef(id=str(turn.pk), version=turn.version, state="running")
        box: Dict[str, Any] = {"ref": ref, "result": None}
        task = asyncio.current_task()
        if task is not None:
            self._active[task] = box
        register_live_turn(ref.id)
        if stop_before_execute:
            self._shutdown_turns[ref.id] = (turn.room_id, turn.user_id)
            await self._end_still_running(
                ref, end_reason="shutdown", partial=False,
            )
            unregister_live_turn(ref.id)
            if task is not None:
                self._active.pop(task, None)
            return
        try:
            box["result"] = await self._execute(turn, box)
        except asyncio.CancelledError:
            self._shutdown_turns[box["ref"].id] = (turn.room_id, turn.user_id)
            await self._end_still_running(
                box["ref"], end_reason="shutdown", partial=True,
            )
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

    async def _end_still_running(
        self, ref: TurnRef, *, end_reason: str = "error",
        partial: Optional[bool] = None,
    ) -> bool:
        from orchestration.turns import _saved_reply_exists, afinalize_turn, atransition

        timeout = max(0.01, self._remaining_timeout())
        if ref.state == "committing":
            try:
                reply_saved = bool(await asyncio.wait_for(
                    asyncio.shield(
                        sync_to_async(_saved_reply_exists)(ref.id),
                    ),
                    timeout=timeout,
                ))
            except (asyncio.TimeoutError, Exception):
                reply_saved = False
            if reply_saved:
                write = asyncio.ensure_future(
                    afinalize_turn(ref.id, ref.version, reply_saved=True)
                )
            else:
                # Shutdown cut the committing turn off before its reply was
                # saved: it is a shutdown ending, not reply_not_saved, and it
                # still earns one notice (#303 r4, item 4).
                reason = (
                    "shutdown" if end_reason == "shutdown"
                    else "reply_not_saved"
                )
                write = asyncio.ensure_future(
                    atransition(
                        ref.id, ref.version, "failed",
                        from_state="committing",
                        end_reason=reason, partial=True,
                    )
                )
        else:
            updates: Dict[str, Any] = {
                "from_state": "running",
                "end_reason": end_reason,
            }
            if partial is not None:
                updates["partial"] = partial
            write = asyncio.ensure_future(
                atransition(ref.id, ref.version, "failed", **updates)
            )
        try:
            result = await asyncio.wait_for(asyncio.shield(write), timeout=timeout)
            return bool(result)
        except asyncio.TimeoutError:
            logger.warning("Turn ending write timed out; the reaper will end it")
            return False
        except asyncio.CancelledError:
            try:
                await asyncio.wait_for(write, timeout=timeout)
            except (Exception, asyncio.TimeoutError):
                logger.warning("Turn ending write after cancellation failed")
            raise
        except Exception:
            logger.warning("Turn ending write failed; the reaper will end it")
            return False

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
            swap = asyncio.ensure_future(sync_to_async(
                _continue_in_thread, thread_sensitive=False,
            )(turn, paused_turn, message_id))
            try:
                ref = await asyncio.shield(swap)
            except asyncio.CancelledError:
                # The swap may already have committed; keep its result so
                # cleanup ends the resumed Turn, not the superseded placeholder
                # (#303 r4, item 6).
                try:
                    ref = await asyncio.wait_for(
                        asyncio.shield(swap),
                        timeout=max(0.01, self._remaining_timeout()),
                    )
                except Exception:
                    ref = None
                if ref is not None:
                    unregister_live_turn(box["ref"].id)
                    register_live_turn(ref.id)
                    box["ref"] = ref
                raise
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
                try:
                    from .context_summary import schedule_context_summary

                    await sync_to_async(schedule_context_summary)(
                        room.id, ai_message.id,
                    )
                except Exception as exc:
                    logger.warning("Context summary refresh skipped: %s", exc)

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


def _cap_lock(room_id: Any, user_id: Any) -> threading.Lock:
    key = f"{room_id}:{user_id}"
    with _CAP_LOCKS_GUARD:
        lock = _CAP_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _CAP_LOCKS[key] = lock
        return lock


def enqueue_turn(
    *, room_id: Any, user_id: Any, request_message_id: int, cap: int,
    lock: Optional[threading.Lock] = None,
) -> Optional[tuple]:
    """Create the queued Turn and enforce the cap as one serialised check.

    The process lock covers tabs in one worker; ``SELECT ... FOR UPDATE`` on the
    room row covers two workers on Postgres (SQLite serialises writers and the
    lite profile is one worker). Returns ``(turn, position, active)`` or
    ``None`` when the key is already at the cap (#216b-2 r2, item 4).
    """
    from django.db import connection
    from django.db.models import Count

    if lock is None:
        lock = _cap_lock(room_id, user_id)
    with lock:
        with transaction.atomic():
            if connection.vendor != "sqlite":
                # One row lock per room serialises two workers. SQLite writes
                # are serialised already, so it never needs this read.
                from .models import Chatroom

                Chatroom.objects.select_for_update().filter(pk=room_id).first()
            counts = dict(
                Turn.objects.filter(
                    room_id=room_id,
                    user_id=user_id,
                    state__in=("queued", "running", "committing"),
                )
                .values_list("state")
                .annotate(n=Count("id"))
            )
            waiting = counts.get("queued", 0)
            active = bool(counts.get("running", 0) or counts.get("committing", 0))
            if waiting >= cap:
                return None
            turn = create_turn(
                room_id=room_id,
                user_id=user_id,
                request_message_id=request_message_id,
            )
            return turn, waiting + 1, active


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
