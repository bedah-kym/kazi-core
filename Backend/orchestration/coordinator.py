"""OrchestrationCoordinator — the chat routing facade.

`ChatConsumer.new_message` validates the WebSocket input surface (sender,
room, mute, rate-limit, encryption) and then delegates every decision
*after* `@Kazi` routing to this class. The coordinator owns the routing
pipeline (directives, pending confirmations, agent loop) and talks back to the
consumer through a small set of injected async callbacks so it has no Channels
dependency of its own.
"""
import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Awaitable, Callable, Dict, List, Optional

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.cache import cache

from orchestration.telemetry import record_event
from orchestration.turns import (
    BUSY_LIVE,
    PauseContinuationLost,
    PauseContinuationStopped,
    TERMINAL_STATES,
    USER_CANCEL,
    TurnRef,
    aclaim_turn,
    afinalize_turn,
    aget_paused_turn,
    aget_turn,
    alink_message,
    atransition,
    last_ran_turn_before,
    pause_ttl_seconds,
    ref_from_turn,
)
from orchestration.turn_leases import (
    cancel_requested,
    register_live_turn,
    request_cancel,
    turn_fenced,
    turn_live,
    unregister_live_turn,
)
from orchestration.contracts import build_step_event
from orchestration.user_preferences import conservative_default_preferences, get_user_preferences
from orchestration.action_receipts import (
    fetch_recent_receipts,
    undo_last_action,
    format_receipt_list,
)
from orchestration.adaptive_task import (
    clear_task_state,
    clear_result_sets,
    is_cancel_request,
)
from orchestration.memory_state import load_memory_summary, clear_memory
from orchestration.tool_calls import (
    did_not_run_reason,
    record_from_event,
    record_tool_calls,
)
from orchestration.tool_record import tool_record_input
from orchestration.shell.chat_intents import (
    is_approval_reply,
    is_autopilot_request,
    is_budget_extend_reply,
    is_decline_reply,
    is_cancel_command,
    is_dismiss_suggestions_command,
    is_receipts_command,
    is_reset_command,
    is_undo_command,
)
from orchestration.agent_loop import (
    AGENT_LOOP_APPROVAL_KIND,
    LEASE_LOST_NOTE,
    run_agent_loop,
    has_pending_agent_state,
    has_pending_budget_state,
    pending_resume_kind,
    resume_after_confirmation,
    resume_with_more_budget,
    resume_after_quota,
    resume_after_expiry,
    decline_after_expiry,
    has_expired_pause,
    pending_approval_prompt,
    load_pending_state,
    cancel_pending_action,
    clear_loop_state,
    dismiss_pending_confirmation,
    _resolve_pending_approval,
    _untrusted_source,
    _conversation_tainted,
)
from orchestration.llm_client import TokenQuotaExceeded, _humanize_seconds, get_llm_client

logger = logging.getLogger(__name__)

MAX_TOOL_RECORDS = 10
_SHOWN_LIMIT = 2000
BUSY_TURN_NOTICE = (
    "Another reply is still being written for you in this room; send this "
    "again when it finishes."
)


def _quota_resume_blocked_note(exc: TokenQuotaExceeded) -> str:
    """The honest line when a resume finds the hourly limit still reached.

    A resume must never claim the limit freed unless it was checked (T-F3b): say
    the limit is still reached, roughly when it frees, and that the paused turn
    is kept so a later ``continue`` picks up from where it stopped.
    """
    if getattr(exc, "reason", "limit") == "unreadable":
        return (
            "I still can't check this hour's token limit, so I've kept your paused "
            "turn rather than risk overrunning it. Please try again shortly.\n\n"
        )
    when = _humanize_seconds(getattr(exc, "retry_after_seconds", 0))
    return (
        f"The hourly token limit is still reached; it frees up in {when}. "
        "Your paused turn is kept — reply **continue** then and I'll pick up from "
        "where it stopped.\n\n"
    )


async def _quota_resume_still_blocked(user_id: int) -> Optional[TokenQuotaExceeded]:
    """The quota block still stopping this resume, or ``None`` if the hour has room.

    The hourly limit is checked with a zero-work call before the harness says it
    freed (T-F3b); the check fails closed, so a dead cache reads as blocked.
    """
    return await get_llm_client().check_hourly_quota(user_id)


async def _consent_postdates_prompt(
    room_id: Any, user_id: int, message_id: Optional[int],
) -> bool:
    """Whether the reply was sent after the pending prompt existed.

    A reply that waited on the room's turn lock, or came from a second tab,
    must never approve a prompt created after it was sent. Any missing piece
    (no request message, no approval row, an unreadable clock) denies consent.
    """
    if message_id is None:
        return False

    def _check() -> bool:
        from django.utils import timezone

        from chatbot.models import Message
        from workflows.models import WorkflowApprovalRecord

        try:
            sent_at = (
                Message.objects.filter(pk=message_id)
                .values_list("timestamp", flat=True)
                .first()
            )
            if sent_at is None:
                return False
            record = (
                WorkflowApprovalRecord.objects.filter(
                    kind=AGENT_LOOP_APPROVAL_KIND,
                    room_id=room_id,
                    requested_by_id=user_id,
                    status="pending",
                )
                .order_by("-created_at")
                .only("created_at", "expires_at")
                .first()
            )
            if record is None:
                return False
            now = timezone.now()
            if record.expires_at is not None and record.expires_at <= now:
                return False
            skew = int(getattr(settings, "CONSENT_SKEW_SECONDS", 2))
            return sent_at > record.created_at + timedelta(seconds=skew)
        except Exception as exc:
            logger.warning("Consent timestamp check failed: %s", exc)
            return False

    return await sync_to_async(_check)()


async def _consent_postdates_pause(
    paused_turn: Optional[Any], message_id: Optional[int],
) -> bool:
    """Whether a budget/quota reply was sent after the pause existed.

    The same rule as an approval reply (#216b-0): a "continue" that waited on
    the lock, or came from a second tab, must not resume work it never saw.
    A missing stamp, a missing message or an unreadable clock denies consent.
    """
    if paused_turn is None or message_id is None:
        return False
    paused_at = getattr(paused_turn, "paused_at", None)
    if paused_at is None:
        # Pauses written before migration 0005 have no stamp. Derive one from
        # the expiry and the TTL. The quota TTL can be longer than the setting,
        # so this derived time is at or after the real pause: strictly safe.
        expires_at = getattr(paused_turn, "pause_expires_at", None)
        if expires_at is not None:
            paused_at = expires_at - timedelta(seconds=pause_ttl_seconds())
    if paused_at is None:
        return False

    def _check() -> bool:
        from chatbot.models import Message

        try:
            sent_at = (
                Message.objects.filter(pk=message_id)
                .values_list("timestamp", flat=True)
                .first()
            )
            if sent_at is None:
                return False
            skew = int(getattr(settings, "CONSENT_SKEW_SECONDS", 2))
            return sent_at > paused_at + timedelta(seconds=skew)
        except Exception as exc:
            logger.warning("Pause consent timestamp check failed: %s", exc)
            return False

    return await sync_to_async(_check)()


STOPPED_TURN_NOTE = (
    "[Harness: the user stopped the previous request partway through. Do not "
    "redo or resume it unless the user asks again.]"
)
STOP_ENDED_REPLY_LINE = (
    "\n\n[Harness: you stopped this turn. The step that was already running "
    "finished, and nothing after it ran.]"
)


async def _flag_after_message(flag_at: Any, message_id: Optional[int]) -> bool:
    """Whether a stop flag is at least as new as the reply being handled.

    On an unreadable clock the stop wins (fail closed): a resume must not run
    a step when the person's stop may be the newer instruction.
    """
    if message_id is None:
        return True

    def _check() -> bool:
        from chatbot.models import Message

        try:
            sent_at = (
                Message.objects.filter(pk=message_id)
                .values_list("timestamp", flat=True)
                .first()
            )
        except Exception:
            return True
        if sent_at is None:
            return True
        return flag_at >= sent_at

    return await sync_to_async(_check)()


async def _stopped_turn_note(
    room_id: Any, user_id: int, current_turn_id: Optional[str],
) -> str:
    """One line for the turn right after a person's stop (#216b-3, item 8).

    It is built from the Turn record, never from message text, so typing the
    same words as a user message gives them no harness standing. Only the last
    Turn of this key that actually ran before the current one decides it; a
    continuation placeholder, a queued row or a later row never does. The stop
    must carry the flag (`cancel_requested_at`), which is what separates a real
    stop from the queued rows a stop bulk-cancels (round 2, item 2).
    """
    turn = await sync_to_async(last_ran_turn_before)(
        room_id, user_id, current_turn_id,
    )
    if (
        turn is not None
        and turn.state == "cancelled"
        and turn.end_reason == USER_CANCEL
        and turn.cancel_requested_at is not None
    ):
        return STOPPED_TURN_NOTE
    return ""


async def _user_cancel_ending(ref: TurnRef) -> bool:
    """Whether a loop that stopped should end as a person's cancel, not a fence.

    A fence and a stop that both hold resolve as the fence: the row is no
    longer this owner's to write, so ``failed(lease_lost)`` wins. The DB flag
    is authoritative; the in-memory check keeps normal endings query-free.
    """
    if not cancel_requested(ref.id):
        return False
    if turn_fenced(ref.id):
        return False
    row = await aget_turn(ref.id)
    return bool(row is not None and row.cancel_requested_at is not None)


async def _end_unexpected_turn(turn_box: Dict, stream_state: Dict, outcome: str) -> None:
    """Write the honest terminal state for an exception after the claim.

    ``CancelledError`` now means only the runner's shutdown cut this turn off:
    a closed tab does not cancel a turn, and a person's cancel arrives as
    ``cancel_requested_at`` (216b-3), not as task cancellation. A shutdown
    ending is ``failed("shutdown")`` with ``partial=True``: a tool may already
    have been dispatched without a visible ``tool_start`` event (E10), so
    "nothing ran" is never a safe guess. The write is shielded so it still
    lands; the caller re-raises the original exception either way.
    """
    ref = turn_box.get("ref")
    if ref is None or turn_box.get("settled"):
        return
    unregister_live_turn(ref.id)
    if outcome == "cancelled":
        # The server cut the turn off. It is not the user's cancel, and the
        # dispatch state is unknown until P3-5's per-call rows exist.
        if ref.state == "committing":
            # A reply already saved still completes; otherwise this is a
            # shutdown ending, not reply_not_saved (#303 r4, item 4).
            from orchestration.turns import _saved_reply_exists

            try:
                reply_saved = bool(await asyncio.wait_for(
                    asyncio.shield(
                        sync_to_async(_saved_reply_exists)(ref.id),
                    ),
                    timeout=float(getattr(
                        settings, "TURN_SHUTDOWN_GRACE_SECONDS", 20,
                    )),
                ))
            except (asyncio.TimeoutError, Exception):
                reply_saved = False
            if reply_saved:
                to_state = "completed"
                updates: Dict = {"from_state": ref.state}
            else:
                to_state = "failed"
                updates = {
                    "from_state": ref.state,
                    "end_reason": "shutdown",
                    "partial": True,
                }
        else:
            to_state = "failed"
            updates = {
                "from_state": ref.state,
                "end_reason": "shutdown",
                "partial": True,
            }
    else:
        to_state = "failed"
        updates = {
            "from_state": ref.state,
            "end_reason": "error",
            "partial": bool(stream_state.get("tools") or stream_state.get("model")),
            "active_seconds": float(stream_state.get("elapsed") or 0),
        }
    task = asyncio.ensure_future(
        atransition(ref.id, ref.version, to_state, **updates),
    )
    if outcome != "cancelled":
        try:
            await task
        except Exception:
            logger.warning("Could not record the turn's unexpected ending.")
        return
    # A shutdown ending is bounded like every other shutdown write: if the
    # database is stalled, the reaper (or the lite boot sweep) ends the row.
    grace = float(getattr(settings, "TURN_SHUTDOWN_GRACE_SECONDS", 20))
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=grace)
    except asyncio.TimeoutError:
        logger.warning("Shutdown ending write timed out; the reaper will end the turn.")
    except asyncio.CancelledError:
        # The inner task keeps running after the shield is cancelled; wait for
        # it, within the grace, so the row is terminal before this
        # cancellation finishes.
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=grace)
        except (Exception, asyncio.TimeoutError):
            pass
        raise


def _tool_record(data: Dict) -> Dict:
    """One tool call of the turn: the call's input (sanitised) and the text the model saw."""
    result = data.get("result")
    result = result if isinstance(result, dict) else {}
    raw_input = data.get("input")
    raw_input = raw_input if isinstance(raw_input, dict) else {}
    return {
        "name": data.get("name") or "",
        "input": tool_record_input(raw_input),
        "status": result.get("status") or "",
        "shown": str(data.get("shown") or "")[:_SHOWN_LIMIT],
    }


def _cap_tool_records(records: List[Dict]) -> List[Dict]:
    if len(records) <= MAX_TOOL_RECORDS:
        return records
    dropped = len(records) - MAX_TOOL_RECORDS
    capped = [dict(record) for record in records[:MAX_TOOL_RECORDS]]
    capped[-1]["note"] = f"{dropped} more tool calls were not recorded."
    return capped


_AUTOPILOT_ARMED_REPLY = (
    "Autopilot armed for this room. Shell commands, including root and "
    "network ones, now run on this unsandboxed host without asking until "
    "the window expires or you say **autopilot off**. Commands on the "
    "destructive list still ask."
)
_AUTOPILOT_ARMED_TAINTED_REPLY = (
    "Autopilot armed for this room. Shell commands, including root ones, "
    "now run on this unsandboxed host without asking until the window "
    "expires or you say **autopilot off**. Commands on the destructive "
    "list still ask. Because this room recently read untrusted content, "
    "commands that reach the network will still ask while the room holds "
    "it. That lifts on its own, though I can't tell you exactly when."
)
_AUTOPILOT_HINT = (
    "\n\nThis room's shell is unsandboxed, so I ask before commands like this. "
    "Reply **autopilot** to stop asking for a limited window; destructive "
    "commands will still ask."
)
_AUTOPILOT_HINT_TAINTED = (
    _AUTOPILOT_HINT
    + " While this room holds untrusted content, commands that reach the "
    "network will still ask."
)


def _room_exists(room_id) -> bool:
    from chatbot.models import Chatroom

    return Chatroom.objects.filter(pk=room_id).exists()


def _persist_turn_notice(room_id, text: str) -> bool:
    """Save and broadcast one harness note outside a Turn."""
    from chatbot.kazi_messages import (
        KaziMessageError,
        ORIGIN_TURN_NOTICE,
        persist_kazi_message,
    )
    from chatbot.models import Chatroom

    room = Chatroom.objects.filter(pk=room_id).first()
    if room is None:
        return False
    try:
        persist_kazi_message(room, content=text, origin=ORIGIN_TURN_NOTICE)
    except KaziMessageError:
        logger.warning("Turn notice not saved: the room key is unavailable")
        return False
    except Exception as exc:
        logger.warning("Turn notice not saved: %s", type(exc).__name__)
        return False
    return True


def _history_replays_untrusted(history_msgs: Optional[List[dict]]) -> bool:
    """Whether the built history carries a replayed result from an untrusted tool."""
    for message in history_msgs or []:
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and not str(block.get("id") or "").startswith("noexec-")
                and _untrusted_source(block.get("name"))
            ):
                return True
    return False


@dataclass
class OrchestrationResult:
    """What the consumer needs after the routing pipeline finishes."""

    full_response: str = ""
    persist: bool = True
    tools: List[Dict] = field(default_factory=list)
    # The reply in two parts: what the model wrote and what the harness wrote.
    model_text: str = ""
    harness: str = ""
    # True when the Turn's typed rows hold this turn's calls, so the reply
    # payload must not also carry the legacy ``tools`` list (D11).
    tool_calls_recorded: bool = False
    # The durable turn this message belongs to; the consumer links the saved
    # reply to it. Empty only when no turn could be recorded.
    turn_id: str = ""
    # The fence and state of the turn when this result was produced. A turn in
    # ``committing`` is completed by ``finalize_turn`` only after the consumer
    # has saved the reply.
    turn_version: int = 0
    turn_state: str = ""


class OrchestrationCoordinator:
    """Routes a single AI message through the full orchestration pipeline."""

    async def handle_message(
        self,
        *,
        query: str,
        user_id: int,
        room_id: str,
        username: str,
        message_id: Optional[int],
        send_chunk: Callable[[str, str, bool], Awaitable[None]],
        send_step_event: Callable[[str, dict], Awaitable[None]],
        get_context_prompt: Callable[[], Awaitable[str]],
        bump_signals: Callable[[List[Optional[str]]], None],
        history_messages: Optional[List[dict]] = None,
        claimed_turn: Optional[Any] = None,
        continue_pause: Optional[Callable[..., Awaitable[Any]]] = None,
    ) -> OrchestrationResult:
        """Route a chat turn, stream its response, and update room/user task state.

        Pending confirmations are handled before new requests and may execute
        actions; plain approval replies must match the whole message.
        ``send_chunk`` receives (correlation_id, text, is_final), while
        ``send_step_event`` receives (correlation_id, event_payload).
        ``get_context_prompt`` supplies extra agent context; ``bump_signals``
        receives action names after successful intent or workflow execution.

        Return the accumulated response with ``persist=False`` for a reset,
        otherwise ``True``. The caller saves it. A reset returns without
        sending a final stream chunk.

        Context-prompt and progress-frame failures are ignored. A failure from
        a fresh agent run ends the turn with one error message and nothing
        further runs; errors during confirmation resume propagate.
        """
        stream_state = {"buffer": [], "last_send": 0, "first_token_sent": False, "full_response": [], "model": [], "harness": [], "tools": [], "chunks": 0, "chars": 0, "tool_calls_recorded": False}  # nosec B105 — state keys, not a credential
        turn_box = {
            "ref": None, "settled": False, "hold": False, "agent_ran": False,
            "claim_status": "",
        }
        try:
            return await self._handle_message_body(
                stream_state=stream_state,
                turn_box=turn_box,
                query=query,
                user_id=user_id,
                room_id=room_id,
                username=username,
                message_id=message_id,
                send_chunk=send_chunk,
                send_step_event=send_step_event,
                get_context_prompt=get_context_prompt,
                bump_signals=bump_signals,
                history_messages=history_messages,
                claimed_turn=claimed_turn,
                continue_pause=continue_pause,
            )
        except asyncio.CancelledError:
            await _end_unexpected_turn(turn_box, stream_state, "cancelled")
            raise
        except Exception:
            await _end_unexpected_turn(turn_box, stream_state, "error")
            raise

    async def _handle_message_body(
        self,
        *,
        stream_state: Dict,
        turn_box: Dict,
        query: str,
        user_id: int,
        room_id: str,
        username: str,
        message_id: Optional[int],
        send_chunk: Callable[[str, str, bool], Awaitable[None]],
        send_step_event: Callable[[str, dict], Awaitable[None]],
        get_context_prompt: Callable[[], Awaitable[str]],
        bump_signals: Callable[[List[Optional[str]]], None],
        history_messages: Optional[List[dict]] = None,
        claimed_turn: Optional[Any] = None,
        continue_pause: Optional[Callable[..., Awaitable[Any]]] = None,
    ) -> OrchestrationResult:
        turn_step_id = f"turn_{message_id}"
        correlation_id = uuid.uuid4().hex

        def _turn_id() -> str:
            ref = turn_box.get("ref")
            return ref.id if ref else ""

        def _register_turn(turn_id: str) -> None:
            """Register without losing a stop that landed just before this."""
            stopped = cancel_requested(turn_id)
            register_live_turn(turn_id)
            if stopped:
                request_cancel(turn_id)

        async def _start_turn() -> Optional[TurnRef]:
            claim = await aclaim_turn(
                room_id=room_id,
                user_id=user_id,
                request_message_id=message_id,
            )
            turn_box["claim_status"] = claim.status
            turn = claim.turn
            if turn is None:
                return None
            ref = ref_from_turn(turn)
            turn_box["ref"] = ref
            _register_turn(ref.id)
            return ref

        async def _adopt_paused_turn() -> bool:
            """Swap the runner's placeholder for the paused Turn (an ending path).

            The runner keeps the placeholder as its claimed Turn until the pause
            really resumes or really ends. On a hold the paused Turn is left
            untouched with its state and approval row.
            """
            paused = turn_box.get("paused_turn")
            if paused is None:
                return True
            try:
                new_ref = await continue_pause(paused, message_id)
            except PauseContinuationLost:
                # The other writer won; the caller shows the honest line.
                return False
            if new_ref is None:
                return False
            turn_box["ref"] = new_ref
            turn_box["paused_turn"] = None
            return True

        async def _resume_turn(ref: TurnRef) -> bool:
            paused = turn_box.get("paused_turn")
            if paused is not None and continue_pause is not None:
                # Deferred handoff: the swap happens only now, when the pause
                # really resumes, not when the message is first read.
                try:
                    new_ref = await continue_pause(paused, message_id)
                except PauseContinuationStopped:
                    # The person's stop is newer than this reply: nothing
                    # resumed runs a step (#216b-3 round 2, item 1).
                    await _end_resume_stopped()
                    return False
                except PauseContinuationLost:
                    # The other writer won; the caller shows the honest line.
                    return False
                if new_ref is None:
                    return False
                turn_box["ref"] = new_ref
                turn_box["paused_turn"] = None
                return True
            if ref.state == "running":
                # A paused Turn was missing (an older turn or a test double);
                # the new record is already running, so only link the reply.
                await alink_message(ref.id, message_id)
                _register_turn(ref.id)
                return True
            row = await aget_turn(ref.id)
            if (
                row is not None
                and row.cancel_requested_at is not None
                and await _flag_after_message(row.cancel_requested_at, message_id)
            ):
                await _end_resume_stopped()
                return False
            moved = await atransition(
                ref.id, ref.version, "running",
                from_state="paused", pause_kind=None, pause_expires_at=None,
                end_reason=None,
            )
            if not moved:
                return False
            ref = TurnRef(id=ref.id, version=ref.version + 1, state="running")
            turn_box["ref"] = ref
            _register_turn(ref.id)
            await alink_message(ref.id, message_id)
            return True

        async def _advance_turn(to_state: str, **kwargs) -> bool:
            if turn_box["settled"]:
                return False
            ref = turn_box.get("ref")
            if ref is None:
                return False
            moved = await atransition(
                ref.id, ref.version, to_state, from_state=ref.state, **kwargs,
            )
            if not moved:
                # A pause may already have been written by the loop itself
                # (the Turn is the source of truth). Adopt it rather than
                # writing a second transition.
                if to_state in ("paused", "cancelled", "expired"):
                    current = await aget_turn(ref.id)
                    if current is not None and current.state == to_state:
                        turn_box["ref"] = ref_from_turn(current)
                        turn_box["settled"] = True
                        unregister_live_turn(ref.id)
                        return True
                # The write was lost: another writer owns or ended this row, so
                # this process must stop renewing it.
                unregister_live_turn(ref.id)
                return False
            turn_box["ref"] = TurnRef(
                id=ref.id, version=ref.version + 1, state=to_state,
            )
            if to_state in TERMINAL_STATES or to_state == "paused":
                turn_box["settled"] = True
                unregister_live_turn(ref.id)
            return True

        async def _add_stopped_reply_line() -> None:
            """End the saved reply with the one honest stop line, once."""
            if turn_box.get("stopped_reply_added"):
                return
            turn_box["stopped_reply_added"] = True
            await broadcast_chunk(STOP_ENDED_REPLY_LINE, harness=True)

        async def _stop_pause_edge() -> bool:
            """End a flagged Turn at a pause edge instead of showing a prompt."""
            turn_id = _turn_id()
            if not turn_id or turn_box.get("settled"):
                return False
            if turn_fenced(turn_id):
                # A fence wins over a stop: the row is no longer this owner's
                # to end as the person's cancel (same rule as the loop ending).
                return False
            if not cancel_requested(turn_id):
                row = await aget_turn(turn_id)
                if row is None or row.cancel_requested_at is None:
                    return False
                request_cancel(turn_id)
            else:
                row = await aget_turn(turn_id)
            if (
                row is None
                or row.cancel_requested_at is None
                or row.state not in ("running", "paused")
            ):
                return False
            # Read the held calls while the Turn still carries its pause state;
            # cancelling clears it.
            held_state = await load_pending_state(
                room_id, user_id, turn_id=str(row.pk),
            )
            held = list(
                getattr(held_state, "pending_tools", None)
                or ([held_state.pending_tool] if held_state and held_state.pending_tool else [])
            )
            partial = bool(stream_state["tools"] or stream_state["model"])
            moved = await atransition(
                str(row.pk), row.version, "cancelled",
                from_state=row.state, end_reason=USER_CANCEL, partial=partial,
            )
            if moved:
                turn_box["ref"] = TurnRef(
                    id=str(row.pk), version=row.version + 1, state="cancelled",
                )
            turn_box["settled"] = True
            unregister_live_turn(str(row.pk))
            try:
                await _resolve_pending_approval(
                    room_id, user_id, "cancelled",
                    comment="The user stopped this turn.",
                    turn_id=str(row.pk),
                )
            except Exception:
                logger.warning(
                    "Stopped pause approval row not resolved", exc_info=True,
                )
            if not turn_box.get("stop_rows_written"):
                turn_box["stop_rows_written"] = True
                await _record_not_run(
                    held, "skipped", "you stopped the turn",
                    int(getattr(held_state, "iteration", 0) or 0),
                    turn_id=str(row.pk),
                )
                await _refresh_turn_memory(str(row.pk))
            clear_loop_state(room_id, user_id)
            if moved:
                # Only a cancel this owner really wrote earns the stop line; a
                # lost write means another writer (the reaper) ended the row.
                await _add_stopped_reply_line()
            return True

        async def _end_resume_stopped() -> None:
            """A stop beat the resume swap: end the pause and the placeholder."""
            turn_box["stopped"] = True
            ref = turn_box.get("ref")
            if ref is not None and ref.state in ("running", "paused"):
                await _add_stopped_reply_line()
                await _advance_turn(
                    "cancelled", end_reason=USER_CANCEL,
                    partial=bool(stream_state["tools"] or stream_state["model"]),
                )
            try:
                # The stop path normally did this. It is idempotent and keeps
                # the invariant when the swap lost before the stop handler ran.
                await cancel_pending_action(room_id, user_id)
            except Exception:
                logger.warning("Stopped resume pause not cancelled", exc_info=True)

        async def _resume_lost() -> None:
            """The 'elsewhere' line, unless the person's stop was the reason."""
            if turn_box.get("stopped"):
                return
            await broadcast_chunk(
                "This turn was already resumed elsewhere. Nothing further was run.",
                harness=True,
            )

        async def _cancel_other_turn(turn, reason: str) -> None:
            await atransition(
                str(turn.pk), turn.version, "cancelled",
                from_state=turn.state, end_reason=reason,
            )

        async def _finish_turn(reason: str) -> None:
            """Record the loop's ending honestly (mapping table in #216a)."""
            if turn_box["settled"]:
                return
            # On the runner path the ref is the placeholder while the paused
            # Turn is separate; a hold keeps the pause, not the placeholder.
            if turn_box["hold"] and turn_box.get("paused_turn") is None:
                return
            ref = turn_box.get("ref")
            if ref is None:
                return
            if reason == "lease_lost" and await _user_cancel_ending(ref):
                # The loop stops before its next step on either a fence or a
                # person's stop; the flag tells them apart (#216b-3, G5). Keep
                # results and receipts; end honestly as the person's cancel.
                partial = bool(stream_state["tools"] or stream_state["model"])
                await _add_stopped_reply_line()
                await _advance_turn(
                    "cancelled", end_reason=USER_CANCEL, partial=partial,
                )
                return
            if reason in ("budget", "asked_for_more"):
                # At the configured ceiling the loop keeps no state to resume,
                # so a pause here would strand the turn. End it for good.
                if reason == "budget" and not await has_pending_budget_state(room_id, user_id):
                    partial = bool(stream_state["tools"] or stream_state["model"])
                    await _advance_turn(
                        "failed", end_reason="budget", partial=partial,
                        active_seconds=float(stream_state.get("active_seconds") or 0),
                    )
                    return
                await _advance_turn(
                    "paused", pause_kind="budget", end_reason=reason,
                    active_seconds=float(stream_state.get("active_seconds") or 0),
                )
                return
            if reason == "quota":
                await _advance_turn(
                    "paused", pause_kind="quota", end_reason="quota",
                )
                return
            if not await sync_to_async(_room_exists)(room_id):
                reason = "room_gone"
            if reason in ("finished", "refusal"):
                if ref.state == "running":
                    # ``completed`` waits for the consumer to save the reply;
                    # finalize_turn() is the only writer of that edge.
                    moved = await _advance_turn("committing", end_reason=reason)
                    if not moved:
                        return
                return
            # Every other ending is a failure; partial says whether anything
            # the user can see had already happened.
            partial = bool(stream_state["tools"] or stream_state["model"])
            await _advance_turn(
                "failed", end_reason=reason, partial=partial,
                active_seconds=float(stream_state.get("active_seconds") or 0),
            )

        async def broadcast_chunk(chunk_text, is_final=False, harness=False):
            # Store all chunks to build full response
            if chunk_text:
                stream_state["full_response"].append(chunk_text)
                stream_state["harness" if harness else "model"].append(chunk_text)

            # Filter leading whitespace if first token hasn't been sent
            if not stream_state["first_token_sent"] and not is_final:
                if not chunk_text.strip():
                    return  # Ignore pure whitespace at start
                chunk_text = chunk_text.lstrip()  # Trim leading space of first word
                stream_state["first_token_sent"] = True

            stream_state["buffer"].append(chunk_text)

            current_time = time.time()
            joined_text = "".join(stream_state["buffer"])

            # Send if buffer > 6 chars OR > 0.08s passed OR is_final
            if len(joined_text) > 6 or (current_time - stream_state["last_send"]) > 0.08 or is_final:
                if joined_text or is_final:
                    if joined_text:
                        stream_state["chunks"] += 1
                        stream_state["chars"] += len(joined_text)
                    try:
                        await send_chunk(correlation_id, joined_text, is_final)
                    finally:
                        if is_final:
                            record_event(
                                "progress_event",
                                {
                                    "phase": "stream",
                                    "state": "completed",
                                    "chunks": stream_state["chunks"],
                                    "chars": stream_state["chars"],
                                    "room_id": room_id,
                                    "user_id": user_id,
                                    "turn_id": _turn_id(),
                                },
                            )
                    stream_state["buffer"] = []
                    stream_state["last_send"] = current_time

        async def emit_progress(phase, state, message_text=""):
            event_payload = build_step_event(
                step_id=turn_step_id,
                phase=phase,
                state=state,
                message=message_text,
            )
            send_ok = True
            try:
                await send_step_event(correlation_id, event_payload)
            except Exception as exc:
                send_ok = False
                logger.warning("Progress frame send failed: %s", exc.__class__.__name__)
            finally:
                record_event(
                    "progress_event",
                    {
                        "phase": phase,
                        "state": state,
                        "room_id": room_id,
                        "user_id": user_id,
                        "turn_id": _turn_id(),
                        "sent": send_ok,
                    },
                )

        adaptive_context = {
            "user_id": user_id,
            "room_id": room_id,
            "username": username,
        }
        memory_summary = await load_memory_summary(adaptive_context)
        user_preferences = {}
        preferences_loaded = True
        profile_obj = None
        profile_loaded = False
        try:
            from orchestration.shell.profile_store import profile as load_profile
            profile_obj = await sync_to_async(load_profile)(user_id)
            profile_loaded = True
        except Exception as e:
            logger.debug("Profile load skipped: %s", e)
        if not profile_loaded:
            # The profile could not be read at all. Try the preferences path once
            # more (it fails closed on its own) before denying the sensitive gates.
            try:
                user_preferences = await sync_to_async(get_user_preferences)(user_id)
            except Exception as e:
                logger.warning(f"User preferences load failed: {e}")
                user_preferences = conservative_default_preferences()
                preferences_loaded = False
        else:
            try:
                user_preferences = await sync_to_async(get_user_preferences)(
                    user_id, profile_obj=profile_obj,
                )
            except Exception as e:
                logger.warning(f"User preferences load failed: {e}")
                user_preferences = conservative_default_preferences()
                preferences_loaded = False
        # Learned overrides only relieve friction beneath the user's own choices.
        # When those could not be read, none is applied.
        if preferences_loaded:
            try:
                from orchestration.preference_mining import merge_learned_overrides
                user_preferences = await sync_to_async(merge_learned_overrides)(user_preferences, user_id, room_id)
            except Exception as e:
                logger.debug("Learned override merge skipped: %s", e)
        resolved_profile = None
        try:
            from orchestration.shell.profiles import resolve_profile
            resolved_profile = await sync_to_async(resolve_profile)(room_id, user_preferences)
            user_preferences["shell_profile"] = resolved_profile.name
        except Exception as e:
            logger.debug("Shell profile resolution skipped: %s", e)
        # Human-armed window for the unsandboxed profile. Always overwritten here
        # so a stored preference can never set it. Fail closed: no lookup -> ask.
        user_preferences.pop("_shell_tainted", None)
        try:
            from orchestration.shell.autopilot import is_armed
            user_preferences["_shell_autopilot"] = bool(
                await sync_to_async(is_armed)(user_id, room_id, profile_obj=profile_obj)
            )
        except Exception as e:
            logger.debug("Shell autopilot lookup skipped: %s", e)
            user_preferences["_shell_autopilot"] = False
        # One read per turn: the reset and pause checks below share this state.
        loop_state = None

        def _log_telemetry(event_type, payload=None):
            try:
                record_event(event_type, payload or {})
            except Exception:
                return

        async def _with_persona_identity(room_id, user_id, ctx_prompt: str) -> str:
            """Prepend the room persona's identity + skills to the loop context."""
            try:
                from orchestration.personas import persona_identity_prompt, resolve_room_persona

                persona = await sync_to_async(resolve_room_persona)(room_id, user_id)
                if persona:
                    identity = persona_identity_prompt(persona, user_capabilities=user_preferences)
                    return "\n\n".join([identity, ctx_prompt or ""]).strip()
            except Exception as exc:
                logger.debug("Persona identity injection skipped: %s", exc)
            return ctx_prompt

        async def _environment_block() -> str:
            """Where commands run, from the shell layer rather than a guess.

            Returned separately so it can be its own prompt section and ride
            only with the shell tool (T-F2)."""
            if resolved_profile is None:
                return ""
            try:
                from orchestration.agent_prompts import build_environment_block
                from orchestration.shell.environment import get_shell_environment

                return build_environment_block(await get_shell_environment(resolved_profile))
            except Exception as exc:
                logger.debug("Environment block skipped: %s", exc)
                return ""

        # Set when this turn's message is a stop word: the window stays off for
        # the whole turn even if the store write failed.
        autopilot_turn = {"forced_off": False}

        async def _refresh_shell_autonomy(prefs):
            """Re-read the armed window so a mid-run disarm/expiry takes effect."""
            from orchestration.shell import autopilot as autopilot_mod

            refreshed = dict(prefs) if isinstance(prefs, dict) else {}
            if autopilot_turn["forced_off"]:
                refreshed["_shell_autopilot"] = False
                return refreshed
            try:
                refreshed["_shell_autopilot"] = bool(
                    await sync_to_async(autopilot_mod.is_armed)(user_id, room_id)
                )
            except Exception as exc:
                logger.debug("Autopilot refresh skipped: %s", exc)
                refreshed["_shell_autopilot"] = False
            return refreshed

        async def _has_pending_confirmation() -> bool:
            """An approval pause on the Turn (or a legacy durable row)."""
            return await has_pending_agent_state(room_id, user_id)

        async def _pending_armable_shell() -> bool:
            """True only when EVERY held call is a shell command the window covers.

            The turn's pre-loop state may no longer be the pending one (a new
            turn can pause after a directive clears the old state), so read the
            current state at confirmation time. A batch with one non-shell call
            is never confirmable by "autopilot".
            """
            state = await load_pending_state(room_id, user_id)
            if not state:
                return False
            held = list(
                getattr(state, "pending_tools", None)
                or ([getattr(state, "pending_tool", None)] if getattr(state, "pending_tool", None) else [])
            )
            tiers = list(getattr(state, "pending_tiers", None) or [])
            if not held:
                return False
            for index, pending in enumerate(held):
                if not isinstance(pending, dict):
                    return False
                if str(pending.get("name") or "") not in {"run_command", "run_shell", "shell_command"}:
                    return False
                tier = tiers[index] if index < len(tiers) else getattr(state, "pending_tier", None)
                if tier not in ("safe", "bounded"):
                    return False
            return True

        async def _room_is_tainted() -> bool:
            """Whether the turn starts tainted. Unreadable means yes.

            The loop starts tainted for the room window or a replayed untrusted
            history, so the wording must use the same decision (C4).
            """
            if _history_replays_untrusted(history_messages):
                return True
            try:
                return bool(await sync_to_async(_conversation_tainted)(room_id))
            except Exception as exc:
                logger.debug("Room taint read skipped: %s", exc)
                return True

        async def _autopilot_hint() -> str:
            """Offer the window only where it applies: an unsandboxed room, a coverable command."""
            if user_preferences.get("shell_profile") != "open" or user_preferences.get("_shell_autopilot"):
                return ""
            try:
                if not await _pending_armable_shell():
                    return ""
            except Exception:
                return ""
            return _AUTOPILOT_HINT_TAINTED if await _room_is_tainted() else _AUTOPILOT_HINT

        async def _record_room():
            """The room once, for encrypting the Turn's tool-call bodies."""
            room = turn_box.get("record_room")
            if room is not None:
                return room
            from chatbot.models import Chatroom

            try:
                room = await sync_to_async(Chatroom.objects.filter(pk=room_id).first)()
            except Exception:
                room = None
            turn_box["record_room"] = room
            return room

        async def _persist_tool_event(data: Dict) -> bool:
            """Append one tool_result event to the Turn's typed record."""
            turn_id = _turn_id()
            if not turn_id:
                return False
            room = await _record_room()
            if room is None:
                return False
            try:
                await sync_to_async(record_from_event)(turn_id, room, data)
            except Exception as exc:
                logger.warning(
                    "Tool call record not saved: %s", exc.__class__.__name__,
                )
                return False
            stream_state["tool_calls_recorded"] = True
            return True

        async def _record_not_run(
            held: List[Dict], status: str, reason: str, iteration: int,
            *, turn_id: Optional[str] = None,
        ) -> None:
            """Write the rows of held calls that resolved without running."""
            turn_id = turn_id or _turn_id()
            if not turn_id or not held:
                return
            room = await _record_room()
            if room is None:
                return
            records = [
                {
                    "call_id": call.get("id"),
                    "name": call.get("name"),
                    "input": call.get("input") or {},
                    "result": {"status": status, "message": reason},
                    "shown": did_not_run_reason(status),
                    "iteration": iteration,
                    "provenance": "main",
                }
                for call in held if isinstance(call, dict)
            ]
            try:
                await sync_to_async(record_tool_calls)(turn_id, room, records)
            except Exception as exc:
                logger.warning(
                    "Tool call record not saved: %s", exc.__class__.__name__,
                )
                return
            stream_state["tool_calls_recorded"] = True

        async def _refresh_turn_memory(turn_id: str) -> None:
            """Let last_actions follow the Turn's rows after a pause resolves."""
            if not turn_id:
                return
            from orchestration.memory_state import update_memory_state_batch

            try:
                await update_memory_state_batch(
                    {"user_id": user_id, "room_id": room_id, "turn_id": turn_id},
                    [],
                )
            except Exception as exc:
                logger.debug("Turn memory refresh skipped: %s", exc)

        async def _bump_server_tool_uses(turn_id: str, count: int) -> None:
            from django.db.models import F

            from orchestration.models import Turn

            try:
                await sync_to_async(Turn.objects.filter(pk=turn_id).update)(
                    server_tool_uses=F("server_tool_uses") + count,
                )
            except Exception as exc:
                logger.debug("server_tool_uses not recorded: %s", exc)

        async def _record_provider_search(
            count: int, *, name: str = "web_search", iteration: Any = None,
            parent_call_id: str = "",
        ) -> None:
            """Record a provider-side search for Phase 4; it is never replayed."""
            turn_id = _turn_id()
            if count <= 0 or not turn_id:
                return
            await _bump_server_tool_uses(turn_id, count)
            room = await _record_room()
            if room is None:
                return
            try:
                await sync_to_async(record_tool_calls)(turn_id, room, [{
                    "call_id": f"server-{uuid.uuid4().hex[:12]}",
                    "name": name,
                    "input": {},
                    "result": {"status": "ok"},
                    "shown": (
                        f"{name} ran on the provider ({count} request(s)); "
                        "its results are part of the assistant's reply."
                    ),
                    "iteration": iteration,
                    "provenance": "provider",
                    "retrieval": True,
                    "parent_call_id": parent_call_id,
                }])
            except Exception as exc:
                logger.warning(
                    "Provider search not recorded: %s", exc.__class__.__name__,
                )
                return
            stream_state["tool_calls_recorded"] = True

        async def _relay_agent_events(events):
            """Map AgentEvents to WebSocket frames, shared by every agent run."""
            async for event in events:
                if event.kind == "text":
                    await broadcast_chunk(event.data.get("text", ""))
                elif event.kind == "text_delta":
                    await broadcast_chunk(event.data.get("text", ""))
                elif event.kind == "harness":
                    text = event.data.get("text", "")
                    turn_id = _turn_id()
                    if (
                        text == LEASE_LOST_NOTE
                        and cancel_requested(turn_id)
                        and not turn_fenced(turn_id)
                    ):
                        # A person's stop is not a lost lease. The saved stop
                        # notice already says what happened; never print the
                        # fence line for it (#216b-3, item 5).
                        continue
                    await broadcast_chunk(text, harness=True)
                elif event.kind == "thinking":
                    await emit_progress("thinking", "started", "Reasoning…")
                elif event.kind == "tool_start":
                    tool = event.data.get("name", "action")
                    await emit_progress("executing", "started", f"Running {tool.replace('_', ' ')}…")
                elif event.kind == "tool_result":
                    not_run = bool(event.data.get("not_run"))
                    if not not_run:
                        # A call that did not run is not work done: never make
                        # the turn partial or the payload's legacy record.
                        stream_state["tools"].append(_tool_record(event.data))
                    await _persist_tool_event(event.data)
                    await _record_provider_search(
                        int(event.data.get("server_tool_uses") or 0),
                        iteration=event.data.get("iteration"),
                        parent_call_id=str(event.data.get("id") or ""),
                    )
                    tool = event.data.get("name", "action")
                    result = event.data.get("result", {})
                    status = result.get("status", "")
                    label = f"{tool.replace('_', ' ')}: {status}"
                    if not_run:
                        await emit_progress("executing", "skipped", f"{label} (not run)")
                    else:
                        await emit_progress("executing", "completed", label)
                elif event.kind == "server_tool_use":
                    await _record_provider_search(
                        int(event.data.get("count") or 0),
                        name=event.data.get("name") or "web_search",
                        iteration=event.data.get("iteration"),
                    )
                elif event.kind == "paused":
                    if turn_box.get("settled"):
                        continue
                    if await _stop_pause_edge():
                        continue
                    pause_kind = event.data.get("pause_kind") or "approval"
                    await _advance_turn(
                        "paused",
                        pause_kind=pause_kind,
                        end_reason=(
                            "awaiting_approval" if pause_kind == "approval"
                            else pause_kind
                        ),
                        active_seconds=float(
                            event.data.get("active_seconds") or 0,
                        ),
                    )
                elif event.kind == "confirmation":
                    if await _stop_pause_edge():
                        continue
                    turn_box["new_pause_shown"] = True
                    await broadcast_chunk(event.data.get("message", "Please confirm.") + await _autopilot_hint(), harness=True)
                    await emit_progress("validating", "completed", "Waiting for confirmation.")
                elif event.kind == "budget_request":
                    if await _stop_pause_edge():
                        continue
                    await broadcast_chunk(
                        "\n\nI've paused this turn because it needs more budget than it "
                        "has left. Reply **more budget** and I'll continue from where I "
                        "stopped.",
                        harness=True,
                    )
                    await emit_progress("validating", "completed", "Waiting for more budget.")
                elif event.kind == "error":
                    message = event.data.get("message", "Something went wrong.")
                    if event.data.get("end_reason") == "length_limit":
                        # A cut-off response can stop mid-word, so start the
                        # note on its own paragraph. With no visible text the
                        # reply must not begin with blank lines.
                        if "".join(stream_state["model"]).strip():
                            message = "\n\n" + message
                        label = "Response cut short by the length limit."
                    else:
                        label = "Error encountered."
                    await broadcast_chunk(message, harness=True)
                    await emit_progress("executing", "completed", label)
                elif event.kind == "done":
                    stream_state["elapsed"] = event.data.get("elapsed")
                    stream_state["active_seconds"] = event.data.get("active_seconds")
                    turn_box["end_reason"] = event.data.get("end_reason") or "finished"
                    await _finish_turn(turn_box["end_reason"])
                    await emit_progress("done", "completed", "Request complete.")

        def _agent_context(extra=None):
            context = {
                "user_id": user_id,
                "room_id": room_id,
                "username": username,
                "preferences": user_preferences,
                "refresh_shell_autonomy": _refresh_shell_autonomy,
                "turn_id": _turn_id(),
                "turn_live": lambda: turn_live(_turn_id()) if _turn_id() else True,
            }
            if extra:
                context.update(extra)
            return context

        async def _handle_agent_loop(query_text: str, history_msgs, ctx_prompt: str, mem_summary: str, env_block: str = ""):
            """Run the agentic loop and map AgentEvents to WebSocket frames."""
            turn_box["agent_ran"] = True
            pending_note = "\n".join(
                note for note in (
                    turn_box.get("pending_note", ""),
                    turn_box.get("stop_note", ""),
                ) if note
            )
            await emit_progress("planning", "started", "Thinking…")
            await _relay_agent_events(run_agent_loop(
                user_message=query_text,
                context=_agent_context({"raw_query": query_text, "user_message": query_text}),
                preferences=user_preferences,
                context_prompt=ctx_prompt,
                memory_summary=mem_summary,
                environment_block=env_block,
                history=history_msgs or None,
                history_tainted=_history_replays_untrusted(history_msgs),
                pending_note=pending_note,
                nothing_pending=bool(turn_box.get("nothing_pending")),
            ))

        async def _handle_agent_resume(ctx_prompt: str, mem_summary: str, env_block: str = ""):
            """Resume a paused agent loop after user confirms."""
            turn_box["agent_ran"] = True
            await _relay_agent_events(resume_after_confirmation(
                context=_agent_context(),
                preferences=user_preferences,
                context_prompt=ctx_prompt,
                memory_summary=mem_summary,
                environment_block=env_block,
            ))

        async def _handle_agent_budget_resume(ctx_prompt: str, mem_summary: str, env_block: str = ""):
            """Continue a turn after the user granted it more budget (T-F3)."""
            turn_box["agent_ran"] = True
            await _relay_agent_events(resume_with_more_budget(
                context=_agent_context(),
                preferences=user_preferences,
                context_prompt=ctx_prompt,
                memory_summary=mem_summary,
                environment_block=env_block,
            ))

        async def _handle_agent_quota_resume(ctx_prompt: str, mem_summary: str, env_block: str = ""):
            """Continue a turn the hourly limit stopped, without more budget (T-F3b)."""
            turn_box["agent_ran"] = True
            await _relay_agent_events(resume_after_quota(
                context=_agent_context(),
                preferences=user_preferences,
                context_prompt=ctx_prompt,
                memory_summary=mem_summary,
                environment_block=env_block,
            ))

        async def _arm_autopilot_from_chat() -> bool:
            """Arm the window from an exact chat reply. Returns whether it armed."""
            from orchestration.shell import autopilot as autopilot_mod

            try:
                armed = await sync_to_async(autopilot_mod.arm)(user_id, room_id, armed_by=user_id)
            except Exception as exc:
                logger.warning("Autopilot arm failed: %s", exc)
                armed = False
            if armed:
                user_preferences["_shell_autopilot"] = True
                reply = (
                    _AUTOPILOT_ARMED_TAINTED_REPLY
                    if await _room_is_tainted()
                    else _AUTOPILOT_ARMED_REPLY
                )
                await broadcast_chunk(reply, harness=True)
            else:
                await broadcast_chunk(
                    "I couldn't arm autopilot, so nothing has changed. It needs a room on "
                    "the unsandboxed shell profile. Reply yes or no to the pending command.",
                    harness=True,
                )
            return armed

        # Autopilot kill switch. Evaluated before every other handler and
        # whatever the loaded flag says, so no branch below can swallow a stop.
        # While an action is pending the broad, in-sentence match still applies
        # (it fails safe); with nothing pending only a whole-message stop
        # disarms the window, so "why did it stop?" reaches the model.
        from orchestration.shell.chat_intents import is_autopilot_disarm_request
        explicit_disarm = is_autopilot_disarm_request(query)
        loop_state = await load_pending_state(room_id, user_id)
        pending_confirmation = await _has_pending_confirmation()
        pending_budget = await has_pending_budget_state(room_id, user_id, state=loop_state)
        # A quota pause also keeps the turn with awaiting_budget set, but it must
        # resume without inflating the budget (T-F3b).
        pending_pause = await pending_resume_kind(room_id, user_id, state=loop_state) if pending_budget else ""
        cancel_request = is_cancel_request(query) if pending_confirmation else is_cancel_command(query)
        if explicit_disarm or cancel_request:
            was_armed = bool(user_preferences.get("_shell_autopilot"))
            autopilot_turn["forced_off"] = True
            user_preferences["_shell_autopilot"] = False
            disarmed = None
            try:
                from orchestration.shell import autopilot as autopilot_mod
                disarmed = await sync_to_async(autopilot_mod.disarm)(user_id, room_id, reason="chat")
            except Exception as exc:
                logger.warning("Autopilot disarm failed: %s", exc)
            if disarmed:
                await broadcast_chunk("Autopilot disarmed for this room.\n\n", harness=True)
            elif disarmed is None and (was_armed or explicit_disarm):
                await broadcast_chunk(
                    "I couldn't confirm autopilot was disarmed. It is off for this message; "
                    "disarm it from the operations inbox to be sure.",
                    harness=True,
                )
            elif explicit_disarm:
                await broadcast_chunk("Autopilot was not armed for this room.", harness=True)

        deferred_directive = None
        immediate_directive = bool(explicit_disarm)

        # Host grants for the sandboxed shell's egress proxy: exact replies only.
        # Revoking a host only reduces permission, so it runs now. Allowing one
        # acts, so it waits for a successful claim (#216b-1, R1).
        from orchestration.shell.chat_intents import host_grant_request
        host_request = host_grant_request(query)
        if host_request:
            verb, raw_host = host_request
            if verb == "allow":
                deferred_directive = ("allow_host", raw_host)
            else:
                from orchestration.shell import host_grants as host_grants_mod

                try:
                    revoked = await sync_to_async(host_grants_mod.revoke)(
                        user_id, room_id, raw_host,
                    )
                    reply = (
                        "Host approval revoked."
                        if revoked else "That host was not approved for this room."
                    )
                except Exception as exc:
                    logger.warning("Host revoke request failed: %s", exc)
                    reply = (
                        "I couldn't change the host approvals just now. "
                        "Nothing was changed."
                    )
                await broadcast_chunk(reply, harness=True)
                immediate_directive = True

        handled_directive = immediate_directive or bool(host_request)
        if not handled_directive and is_reset_command(query):
            # Reset clears memory, so it is an acting directive: it waits for
            # ownership and never runs on BUSY_LIVE (#216b-1, item 8).
            deferred_directive = ("reset", None)
            handled_directive = True
        if not handled_directive and is_dismiss_suggestions_command(query):
            deferred_directive = ("dismiss", None)
            handled_directive = True
        if not handled_directive and is_receipts_command(query):
            deferred_directive = ("receipts", None)
            handled_directive = True
        if not handled_directive and is_undo_command(query):
            deferred_directive = ("undo", None)
            handled_directive = True

        async def _run_deferred_directive() -> bool:
            nonlocal deferred_directive
            action = deferred_directive
            deferred_directive = None
            if action is None:
                return False
            kind, value = action
            if kind == "reset":
                paused = await aget_paused_turn(room_id, user_id)
                if paused is not None:
                    await _cancel_other_turn(paused, "reset")
                await clear_task_state(adaptive_context)
                await clear_result_sets(adaptive_context)
                await clear_memory(adaptive_context)
                # A reset is not consent: nothing pending may survive it for a
                # later "yes".
                await dismiss_pending_confirmation(room_id, user_id)
                record_event(
                    "context_reset",
                    {
                        "user_id": user_id,
                        "room_id": room_id,
                        "turn_id": _turn_id(),
                    },
                )
                await broadcast_chunk(
                    "Okay, starting fresh. What would you like to do?",
                    harness=True,
                )
                return True
            if kind == "allow_host":
                from orchestration.shell import host_grants as host_grants_mod
                from orchestration.shell.egress import proxy_enabled

                try:
                    granted = await sync_to_async(host_grants_mod.grant)(
                        user_id, room_id, value,
                    )
                    if granted:
                        reply = (
                            f"Approved `{granted}` for this room's sandboxed shell. "
                            f"It expires on its own; reply **revoke host {granted}** "
                            "to remove it sooner."
                        )
                        if not proxy_enabled():
                            reply += (
                                " The egress proxy is off on this install, so it "
                                "has no effect yet."
                            )
                    else:
                        why = await sync_to_async(host_grants_mod.denial_reason)(
                            user_id, room_id, value,
                        )
                        reply = "I couldn't approve that host. " + (
                            why or "Nothing was changed."
                        )
                except Exception as exc:
                    logger.warning("Host grant request failed: %s", exc)
                    reply = (
                        "I couldn't change the host approvals just now. "
                        "Nothing was changed."
                    )
                await broadcast_chunk(reply, harness=True)
            elif kind == "dismiss":
                last_reason_key = f"proactive:last_reason:{room_id}:{user_id}"
                dismissed_key = f"proactive:dismissed:{room_id}:{user_id}"
                last_reason = cache.get(last_reason_key)
                if last_reason:
                    dismissed = cache.get(dismissed_key) or []
                    if last_reason not in dismissed:
                        dismissed.append(last_reason)
                        cache.set(dismissed_key, dismissed, timeout=60 * 60 * 24 * 14)
                await broadcast_chunk(
                    "Got it. I will stop showing that kind of suggestion here.",
                    harness=True,
                )
            elif kind == "receipts":
                receipts = await fetch_recent_receipts(
                    user_id=user_id,
                    room_id=room_id,
                    limit=3,
                )
                await broadcast_chunk(format_receipt_list(receipts), harness=True)
            elif kind == "undo":
                undo_result = await undo_last_action(
                    user_id=user_id,
                    room_id=room_id,
                )
                await broadcast_chunk(
                    undo_result.get("message") or "Okay.", harness=True,
                )
            return False

        pending_handled = immediate_directive
        directive_handled = immediate_directive

        # Check an expired pause BEFORE claiming a new Turn: has_expired_pause
        # requires the expired Turn to still be the newest for this room.
        late_reply = bool(
            not pending_handled and not pending_confirmation and not pending_budget
            and (
                is_approval_reply(query) or is_decline_reply(query)
                or is_budget_extend_reply(query) or is_cancel_command(query)
            )
        )
        expired_pause = await has_expired_pause(room_id, user_id) if late_reply else False

        # One logical turn keeps one id. A reply that resolves a pause continues
        # that Turn; anything else (a question, another request) keeps the pause
        # armed and starts a new record (D8).
        paused_turn = await aget_paused_turn(room_id, user_id)
        consent_reply = bool(
            pending_confirmation
            and (is_autopilot_request(query) or is_approval_reply(query))
        )
        consent_postdates_prompt = bool(
            consent_reply
            and await _consent_postdates_prompt(room_id, user_id, message_id)
        )
        autopilot_request = bool(
            consent_postdates_prompt and is_autopilot_request(query)
        )
        armable_shell = bool(autopilot_request and await _pending_armable_shell())
        approval_continues = bool(
            pending_confirmation and (
                is_cancel_request(query) or is_decline_reply(query)
                or autopilot_request
                or (is_approval_reply(query) and consent_postdates_prompt)
            )
        )
        budget_yes = bool(
            is_budget_extend_reply(query) or is_approval_reply(query)
        )
        # A budget/quota "continue" is consent only if it postdates the pause
        # (#216b-3, item 6). A late no or cancel still cancels whichever stamp
        # it carries, because that fails safe.
        budget_consent = bool(
            pending_budget and budget_yes
            and await _consent_postdates_pause(paused_turn, message_id)
        )
        budget_continues = bool(
            pending_budget and (
                budget_consent or is_decline_reply(query)
                or is_cancel_command(query)
            )
        )
        continues_paused = approval_continues or budget_continues
        if continues_paused and paused_turn is not None:
            if claimed_turn is not None and continue_pause is not None:
                # Keep the placeholder. The swap happens only in _resume_turn
                # (really resumes) or _adopt_paused_turn (really ends), so a
                # hold path leaves the paused Turn paused (#303 r4, item 1).
                turn_box["ref"] = ref_from_turn(claimed_turn)
                turn_box["paused_turn"] = paused_turn
            else:
                turn_box["ref"] = ref_from_turn(paused_turn)
            await alink_message(turn_box["ref"].id, message_id)
        elif claimed_turn is not None:
            # The drainer claimed this Turn before handing it over, so the
            # coordinator must not claim a second one (#216b-2, item 3).
            turn_box["ref"] = ref_from_turn(claimed_turn)
        else:
            await _start_turn()
        if turn_box["ref"] is None and (
            deferred_directive is not None or not pending_handled
        ):
            # A refused claim really ran nothing. A live owner gets a saved
            # notice; any other refusal keeps the fail-closed line. Acting
            # directives never run without ownership (#216b-1, items 8-9).
            if turn_box.get("claim_status") == BUSY_LIVE:
                await sync_to_async(_persist_turn_notice)(
                    room_id, BUSY_TURN_NOTICE,
                )
            else:
                await broadcast_chunk(
                    "This turn could not be recorded, so nothing was run. "
                    "Please try again.",
                    harness=True,
                )
            await emit_progress("done", "completed", "Turn not recorded.")
            await broadcast_chunk("", is_final=True)
            return OrchestrationResult(
                full_response="".join(stream_state["full_response"]),
                persist=False,
                turn_id="",
            )

        if deferred_directive is not None:
            reset_ran = await _run_deferred_directive()
            pending_handled = True
            directive_handled = True
            if reset_ran:
                await emit_progress("done", "completed", "Context reset.")
                await _finish_turn("finished")
                return OrchestrationResult(
                    full_response="".join(stream_state["full_response"]),
                    persist=False,
                    turn_id=_turn_id(),
                    turn_version=(
                        turn_box["ref"].version if turn_box.get("ref") else 0
                    ),
                    turn_state=(
                        turn_box["ref"].state if turn_box.get("ref") else ""
                    ),
                )

        def _paused_still_held() -> bool:
            return (
                turn_box.get("paused_turn") is not None
                or (
                    turn_box.get("ref") is not None
                    and turn_box["ref"].state == "paused"
                )
            )

        pending_left = False

        # A late reply to a pause that already expired is answered honestly,
        # never treated as consent. A late yes/continue resumes with is_error
        # results for the calls that never ran; a late no/cancel closes the
        # pause and runs nothing.
        if late_reply and expired_pause:
            turn_box["agent_ran"] = True
            if is_decline_reply(query) or is_cancel_command(query):
                # A late no/cancel closes the expired pause; it is never consent
                # and must not restart the old task.
                await _relay_agent_events(decline_after_expiry(
                    context=_agent_context(),
                ))
                turn_box["end_reason"] = "finished"
            else:
                ctx_prompt = ""
                try:
                    ctx_prompt = await get_context_prompt() or ""
                except Exception:
                    pass
                ctx_prompt = await _with_persona_identity(room_id, user_id, ctx_prompt)
                env_block = await _environment_block()
                mem_sum = await load_memory_summary(adaptive_context) or ""
                await _relay_agent_events(resume_after_expiry(
                    context=_agent_context(),
                    preferences=user_preferences,
                    context_prompt=ctx_prompt,
                    memory_summary=mem_sum,
                    environment_block=env_block,
                ))
            pending_handled = True

        # --- Agent loop confirmation resume ---
        if not pending_handled:
            if pending_confirmation:
                async def _resume_confirmed():
                    ctx_prompt = ""
                    try:
                        ctx_prompt = await get_context_prompt() or ""
                    except Exception:
                        pass
                    ctx_prompt = await _with_persona_identity(room_id, user_id, ctx_prompt)
                    env_block = await _environment_block()
                    mem_sum = await load_memory_summary(adaptive_context) or ""
                    await _handle_agent_resume(ctx_prompt, mem_sum, env_block)

                # Cancel is checked first: a refusal must never be read as consent.
                if is_cancel_request(query) or is_decline_reply(query):
                    if not await _adopt_paused_turn():
                        await broadcast_chunk(
                            "This turn was already resumed elsewhere. "
                            "Nothing further was run.",
                            harness=True,
                        )
                    else:
                        held_state = await load_pending_state(room_id, user_id)
                        cancel_msg = await cancel_pending_action(room_id, user_id)
                        await _advance_turn("cancelled", end_reason="cancelled")
                        held = list(
                            getattr(held_state, "pending_tools", None)
                            or ([held_state.pending_tool] if held_state and held_state.pending_tool else [])
                        )
                        await _record_not_run(
                            held, "declined", "you declined",
                            int(getattr(held_state, "iteration", 0) or 0),
                        )
                        await _refresh_turn_memory(_turn_id())
                        await broadcast_chunk(
                            cancel_msg or "Okay, cancelled.", harness=True,
                        )
                    pending_handled = True
                elif autopilot_request:
                    if armable_shell:
                        # Arming confirms only when every held call is a shell
                        # command the window covers, and only if it armed.
                        if await _arm_autopilot_from_chat():
                            if await _resume_turn(turn_box["ref"]):
                                await _resume_confirmed()
                            else:
                                await _resume_lost()
                            pending_handled = True
                        else:
                            if _paused_still_held():
                                turn_box["hold"] = True
                            pending_left = True
                            pending_handled = True
                    else:
                        # A mixed batch is never confirmed by "autopilot". Arm
                        # the window if eligible and keep the batch pending.
                        await _arm_autopilot_from_chat()
                        if _paused_still_held():
                            turn_box["hold"] = True
                        pending_left = True
                        pending_handled = True
                elif is_approval_reply(query) and consent_postdates_prompt:
                    if await _resume_turn(turn_box["ref"]):
                        await _resume_confirmed()
                    else:
                        await _resume_lost()
                    pending_handled = True
                else:
                    # D8: a question or another request is not consent. The
                    # action stays pending; this message runs as a new Turn that
                    # sees a harness note, and the prompt is shown again last.
                    info = await pending_approval_prompt(room_id, user_id)
                    prompt = info.get("message") or ""
                    if prompt:
                        short = info.get("short_id") or ""
                        turn_box["pending_note"] = (
                            "[Harness: an action is waiting for your approval: "
                            f"{prompt}" + (f" (approval id {short})" if short else "") + "]"
                        )
                        pending_left = True
                    else:
                        # No approval row backs this paused Turn, so it cannot
                        # be confirmed. Do not tell the user an action waits.
                        held_state = await load_pending_state(room_id, user_id)
                        paused_id = (
                            str(paused_turn.pk) if paused_turn is not None
                            else _turn_id()
                        )
                        await cancel_pending_action(room_id, user_id)
                        held = list(
                            getattr(held_state, "pending_tools", None)
                            or ([held_state.pending_tool] if held_state and held_state.pending_tool else [])
                        )
                        await _record_not_run(
                            held, "skipped", "no approval record could be loaded",
                            int(getattr(held_state, "iteration", 0) or 0),
                            turn_id=paused_id,
                        )
                        await _refresh_turn_memory(paused_id)
                        turn_box["pending_note"] = (
                            "[Harness: the earlier approval record could not be "
                            "loaded, so nothing is waiting and nothing ran.]"
                        )
                        pending_left = False

        # --- Agent loop budget / quota resume (T-F3, T-F3b) ---
        if not pending_handled and pending_budget:
            if budget_yes and budget_consent:
                ctx_prompt = ""
                try:
                    ctx_prompt = await get_context_prompt() or ""
                except Exception:
                    pass
                ctx_prompt = await _with_persona_identity(room_id, user_id, ctx_prompt)
                env_block = await _environment_block()
                mem_sum = await load_memory_summary(adaptive_context) or ""
                if pending_pause == "quota":
                    still_blocked = await _quota_resume_still_blocked(user_id)
                    if still_blocked is not None:
                        if _paused_still_held():
                            turn_box["hold"] = True
                        await broadcast_chunk(
                            _quota_resume_blocked_note(still_blocked),
                            harness=True,
                        )
                    elif await _resume_turn(turn_box["ref"]):
                        await broadcast_chunk(
                            "The hourly token limit has freed up. Continuing from where it stopped.\n\n",
                            harness=True,
                        )
                        await _handle_agent_quota_resume(ctx_prompt, mem_sum, env_block)
                    else:
                        await _resume_lost()
                elif await _resume_turn(turn_box["ref"]):
                    await broadcast_chunk("Continuing with more budget.\n\n", harness=True)
                    await _handle_agent_budget_resume(ctx_prompt, mem_sum, env_block)
                else:
                    await _resume_lost()
                pending_handled = True
            elif is_decline_reply(query) or is_cancel_command(query):
                # A whole-message no/stop ends the paused turn for good.
                if not await _adopt_paused_turn():
                    await broadcast_chunk(
                        "This turn was already resumed elsewhere. "
                        "Nothing further was run.",
                        harness=True,
                    )
                else:
                    clear_loop_state(room_id, user_id)
                    await _advance_turn("cancelled", end_reason="cancelled")
                    await _refresh_turn_memory(_turn_id())
                    await broadcast_chunk(
                        "Okay, I've stopped there and dropped the paused work.\n\n",
                        harness=True,
                    )
                pending_handled = True
            else:
                # D8 also applies to a budget/quota pause: the work stays paused
                # and the offer is shown again after this new turn.
                turn_box["pending_note"] = (
                    "[Harness: a turn is paused waiting for more budget. It was "
                    "not dropped. If the user wants it to continue, they can "
                    "reply continue or grant more budget.]"
                )
                pending_left = True

        if directive_handled and (pending_confirmation or pending_budget):
            pending_left = True

        if not pending_handled and not pending_confirmation and not pending_budget:
            if is_approval_reply(query) or is_decline_reply(query):
                turn_box["nothing_pending"] = True

        if not pending_handled:
            # --- Agentic loop path ---
            _log_telemetry("agent_loop_start", {
                "user_id": user_id,
                "room_id": room_id,
            })
            ctx_prompt = ""
            try:
                ctx_prompt = await get_context_prompt() or ""
            except Exception:
                pass
            ctx_prompt = await _with_persona_identity(room_id, user_id, ctx_prompt)
            env_block = await _environment_block()
            mem_sum = memory_summary or ""
            ref = turn_box.get("ref")
            if ref is not None:
                turn_box["stop_note"] = await _stopped_turn_note(
                    room_id, user_id, ref.id,
                )
            try:
                await _handle_agent_loop(
                    query,
                    history_messages,
                    ctx_prompt,
                    mem_sum,
                    env_block,
                )
            except Exception as agent_exc:
                logger.warning("Agent loop failed: %s", agent_exc.__class__.__name__)
                record_event(
                    "agent_loop_failed",
                    {
                        "user_id": user_id,
                        "room_id": room_id,
                        "turn_id": _turn_id(),
                        "error_class": agent_exc.__class__.__name__,
                    },
                )
                await broadcast_chunk("Something went wrong on my side. Nothing further was run.", harness=True)
                await _finish_turn("error")

        # The harness shows a still-pending prompt last, so a bare yes answers
        # the shown action rather than the model's own question (D8).
        if pending_left and not turn_box.get("new_pause_shown"):
            info = await pending_approval_prompt(room_id, user_id)
            if info.get("message"):
                message = str(info["message"])
                short = str(info.get("short_id") or "")
                if short and short not in message:
                    message += f" (approval id {short})"
                await broadcast_chunk("\n\n" + message, harness=True)
            elif pending_budget:
                await broadcast_chunk(
                    "\n\nThis turn is still paused waiting for more budget. "
                    "Reply **continue** (or grant more budget) to pick up where it "
                    "stopped, or **no** to drop it.",
                    harness=True,
                )

        # End stream
        await emit_progress("done", "completed", "Request complete.")
        await broadcast_chunk("", is_final=True)
        await _finish_turn(turn_box.get("end_reason") or ("error" if turn_box["agent_ran"] else "finished"))

        full_response_text = "".join(stream_state["full_response"])

        return OrchestrationResult(
            full_response=full_response_text,
            tools=_cap_tool_records(stream_state["tools"]),
            model_text="".join(stream_state["model"]),
            harness="".join(stream_state["harness"]),
            tool_calls_recorded=bool(stream_state.get("tool_calls_recorded")),
            turn_id=_turn_id(),
            turn_version=turn_box["ref"].version if turn_box.get("ref") else 0,
            turn_state=turn_box["ref"].state if turn_box.get("ref") else "",
        )

    async def finalize_turn(
        self, result: OrchestrationResult, *, reply_saved: bool,
    ) -> bool:
        """Record ``committing -> completed`` (or ``failed(reply_not_saved)``).

        The consumer calls this only after it has tried to persist the reply,
        so ``completed`` can no longer be written before the reply exists.
        """
        if not result.turn_id or result.turn_state != "committing":
            return False
        try:
            return await afinalize_turn(
                result.turn_id, result.turn_version, reply_saved=reply_saved,
            )
        finally:
            unregister_live_turn(result.turn_id)
