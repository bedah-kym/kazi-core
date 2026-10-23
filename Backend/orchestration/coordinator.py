"""OrchestrationCoordinator — the chat routing facade.

`ChatConsumer.new_message` validates the WebSocket input surface (sender,
room, mute, rate-limit, encryption) and then delegates every decision
*after* `@Kazi` routing to this class. The coordinator owns the routing
pipeline (directives, pending confirmations, agent loop) and talks back to the
consumer through a small set of injected async callbacks so it has no Channels
dependency of its own.
"""
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional

from asgiref.sync import sync_to_async
from django.core.cache import cache
from django.utils import timezone

from orchestration.telemetry import record_event
from orchestration.turns import (
    TERMINAL_STATES,
    TurnRef,
    acreate_turn,
    aget_paused_turn,
    alink_message,
    atransition,
    ref_from_turn,
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
    run_agent_loop,
    has_pending_agent_state,
    has_pending_budget_state,
    pending_resume_kind,
    resume_after_confirmation,
    resume_with_more_budget,
    resume_after_quota,
    cancel_pending_action,
    clear_loop_state,
    dismiss_pending_confirmation,
    load_loop_state,
    _untrusted_source,
    _conversation_tainted,
)
from orchestration.llm_client import TokenQuotaExceeded, _humanize_seconds, get_llm_client

logger = logging.getLogger(__name__)

MAX_TOOL_RECORDS = 10
_SHOWN_LIMIT = 2000


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
    # The durable turn this message belongs to; the consumer links the saved
    # reply to it. Empty only when no turn could be recorded.
    turn_id: str = ""


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
        stream_state = {"buffer": [], "last_send": 0, "first_token_sent": False, "full_response": [], "model": [], "harness": [], "tools": [], "chunks": 0, "chars": 0}  # nosec B105 — state keys, not a credential
        turn_step_id = f"turn_{message_id}"
        correlation_id = uuid.uuid4().hex
        turn_box = {"ref": None, "settled": False, "hold": False, "agent_ran": False}

        def _turn_id() -> str:
            ref = turn_box.get("ref")
            return ref.id if ref else ""

        async def _start_turn() -> Optional[TurnRef]:
            turn = await acreate_turn(
                room_id=room_id,
                user_id=user_id,
                request_message_id=message_id,
            )
            ref = ref_from_turn(turn)
            moved = await atransition(
                ref.id, ref.version, "running",
                from_state=ref.state, started_at=timezone.now(),
            )
            if not moved:
                return None
            ref = TurnRef(id=ref.id, version=ref.version + 1, state="running")
            turn_box["ref"] = ref
            return ref

        async def _resume_turn(ref: TurnRef) -> bool:
            if ref.state == "running":
                # A paused Turn was missing (an older turn or a test double);
                # the new record is already running, so only link the reply.
                await alink_message(ref.id, message_id)
                return True
            moved = await atransition(
                ref.id, ref.version, "running",
                from_state="paused", pause_kind=None, pause_expires_at=None,
            )
            if not moved:
                return False
            ref = TurnRef(id=ref.id, version=ref.version + 1, state="running")
            turn_box["ref"] = ref
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
                return False
            turn_box["ref"] = TurnRef(
                id=ref.id, version=ref.version + 1, state=to_state,
            )
            if to_state in TERMINAL_STATES or to_state == "paused":
                turn_box["settled"] = True
            return True

        async def _cancel_other_turn(turn, reason: str) -> None:
            await atransition(
                str(turn.pk), turn.version, "cancelled",
                from_state=turn.state, end_reason=reason,
            )

        async def _finish_turn(reason: str) -> None:
            """Record the loop's ending honestly (mapping table in #216a)."""
            if turn_box["settled"] or turn_box["hold"]:
                return
            ref = turn_box.get("ref")
            if ref is None:
                return
            if reason in ("budget", "asked_for_more"):
                await _advance_turn(
                    "paused", pause_kind="budget", end_reason=reason,
                    active_seconds=float(stream_state.get("elapsed") or 0),
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
                    moved = await _advance_turn("committing", end_reason=reason)
                    if not moved:
                        return
                if turn_box["ref"] and turn_box["ref"].state == "committing":
                    await _advance_turn("completed", end_reason=reason)
                return
            # Every other ending is a failure; partial says whether anything
            # the user can see had already happened.
            partial = bool(stream_state["tools"] or stream_state["model"])
            await _advance_turn(
                "failed", end_reason=reason, partial=partial,
                active_seconds=float(stream_state.get("elapsed") or 0),
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
        if is_reset_command(query):
            paused_turn = await aget_paused_turn(room_id, user_id)
            if paused_turn is not None:
                await _cancel_other_turn(paused_turn, "reset")
            await _start_turn()
            await clear_task_state(adaptive_context)
            await clear_result_sets(adaptive_context)
            await clear_memory(adaptive_context)
            # A reset is not consent: nothing pending may survive it for a later "yes".
            await dismiss_pending_confirmation(room_id, user_id)
            record_event(
                "context_reset",
                {
                    "user_id": user_id,
                    "room_id": room_id,
                    "turn_id": _turn_id(),
                },
            )
            await broadcast_chunk("Okay, starting fresh. What would you like to do?", harness=True)
            await emit_progress("done", "completed", "Context reset.")
            await _finish_turn("finished")
            return OrchestrationResult(
                full_response="".join(stream_state["full_response"]),
                persist=False,
                turn_id=_turn_id(),
            )

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
            """A durable approval, or an inline `bounded` shell pause (loop state only)."""
            if await has_pending_agent_state(room_id, user_id):
                return True
            return bool(
                loop_state
                and loop_state.pending_tool
                and loop_state.pending_tier == "bounded"
            )

        async def _pending_armable_shell() -> bool:
            """True when the pending action is a shell command the window could cover.

            The turn's pre-loop state may no longer be the pending one (a new
            turn can pause after a directive clears the old state), so read the
            current state at confirmation time.
            """
            state = await sync_to_async(load_loop_state)(room_id, user_id)
            pending = getattr(state, "pending_tool", None) if state else None
            if not isinstance(pending, dict):
                return False
            if str(pending.get("name") or "") not in {"run_command", "run_shell", "shell_command"}:
                return False
            return getattr(state, "pending_tier", None) in ("safe", "bounded")

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

        async def _relay_agent_events(events):
            """Map AgentEvents to WebSocket frames, shared by every agent run."""
            async for event in events:
                if event.kind == "text":
                    await broadcast_chunk(event.data.get("text", ""))
                elif event.kind == "text_delta":
                    await broadcast_chunk(event.data.get("text", ""))
                elif event.kind == "thinking":
                    await emit_progress("thinking", "started", "Reasoning…")
                elif event.kind == "tool_start":
                    tool = event.data.get("name", "action")
                    await emit_progress("executing", "started", f"Running {tool.replace('_', ' ')}…")
                elif event.kind == "tool_result":
                    stream_state["tools"].append(_tool_record(event.data))
                    tool = event.data.get("name", "action")
                    result = event.data.get("result", {})
                    status = result.get("status", "")
                    await emit_progress("executing", "completed", f"{tool.replace('_', ' ')}: {status}")
                elif event.kind == "paused":
                    await _advance_turn(
                        "paused",
                        pause_kind=event.data.get("pause_kind") or "approval",
                        end_reason="awaiting_approval",
                    )
                elif event.kind == "confirmation":
                    await broadcast_chunk(event.data.get("message", "Please confirm.") + await _autopilot_hint(), harness=True)
                    await emit_progress("validating", "completed", "Waiting for confirmation.")
                elif event.kind == "budget_request":
                    await broadcast_chunk(
                        "\n\nI've paused this turn because it needs more budget than it "
                        "has left. Reply **more budget** and I'll continue from where I "
                        "stopped.",
                        harness=True,
                    )
                    await emit_progress("validating", "completed", "Waiting for more budget.")
                elif event.kind == "error":
                    await broadcast_chunk(event.data.get("message", "Something went wrong."), harness=True)
                    await emit_progress("executing", "completed", "Error encountered.")
                elif event.kind == "done":
                    stream_state["elapsed"] = event.data.get("elapsed")
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
            }
            if extra:
                context.update(extra)
            return context

        async def _handle_agent_loop(query_text: str, history_msgs, ctx_prompt: str, mem_summary: str, env_block: str = ""):
            """Run the agentic loop and map AgentEvents to WebSocket frames."""
            turn_box["agent_ran"] = True
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
        loop_state = await sync_to_async(load_loop_state)(room_id, user_id)
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

        # Host grants for the sandboxed shell's egress proxy: exact replies only.
        from orchestration.shell.chat_intents import host_grant_request
        host_request = host_grant_request(query)
        if host_request:
            from orchestration.shell import host_grants as host_grants_mod
            from orchestration.shell.egress import proxy_enabled

            verb, raw_host = host_request
            try:
                if verb == "allow":
                    granted = await sync_to_async(host_grants_mod.grant)(user_id, room_id, raw_host)
                    if granted:
                        reply = (
                            f"Approved `{granted}` for this room's sandboxed shell. It expires on its "
                            f"own; reply **revoke host {granted}** to remove it sooner."
                        )
                        if not proxy_enabled():
                            reply += " The egress proxy is off on this install, so it has no effect yet."
                    else:
                        why = await sync_to_async(host_grants_mod.denial_reason)(user_id, room_id, raw_host)
                        reply = "I couldn't approve that host. " + (why or "Nothing was changed.")
                else:
                    revoked = await sync_to_async(host_grants_mod.revoke)(user_id, room_id, raw_host)
                    reply = "Host approval revoked." if revoked else "That host was not approved for this room."
            except Exception as exc:
                logger.warning("Host grant request failed: %s", exc)
                reply = "I couldn't change the host approvals just now. Nothing was changed."
            await broadcast_chunk(reply, harness=True)

        handled_directive = explicit_disarm or bool(host_request)
        if not handled_directive and is_dismiss_suggestions_command(query):
            last_reason_key = f"proactive:last_reason:{room_id}:{user_id}"
            dismissed_key = f"proactive:dismissed:{room_id}:{user_id}"
            last_reason = cache.get(last_reason_key)
            if last_reason:
                dismissed = cache.get(dismissed_key) or []
                if last_reason not in dismissed:
                    dismissed.append(last_reason)
                    cache.set(dismissed_key, dismissed, timeout=60 * 60 * 24 * 14)
            await broadcast_chunk("Got it. I will stop showing that kind of suggestion here.", harness=True)
            handled_directive = True
        if not handled_directive and is_receipts_command(query):
            receipts = await fetch_recent_receipts(
                user_id=user_id,
                room_id=room_id,
                limit=3,
            )
            await broadcast_chunk(format_receipt_list(receipts), harness=True)
            handled_directive = True
        if not handled_directive and is_undo_command(query):
            undo_result = await undo_last_action(
                user_id=user_id,
                room_id=room_id,
            )
            await broadcast_chunk(undo_result.get("message") or "Okay.", harness=True)
            handled_directive = True

        pending_handled = handled_directive

        # A directive answered this message without touching the agent's pending
        # action. That was not consent, so the action must not stay armed for a
        # later "ok". Replies to the approval machinery itself (autopilot off,
        # allow host) leave it pending.
        keeps_pending = explicit_disarm or bool(host_request)
        if handled_directive and not keeps_pending:
            if pending_confirmation:
                await dismiss_pending_confirmation(room_id, user_id)
                await broadcast_chunk("\n\nI did not run the pending action; it is cancelled.", harness=True)
            if pending_budget:
                clear_loop_state(room_id, user_id)

        # One logical turn keeps one id. A reply to a pause continues that Turn;
        # anything else supersedes the pause and starts a new record.
        paused_turn = await aget_paused_turn(room_id, user_id)
        approval_continues = bool(
            pending_confirmation and (
                is_cancel_request(query) or is_decline_reply(query)
                or is_autopilot_request(query) or is_approval_reply(query)
            )
        )
        budget_continues = bool(
            pending_budget and (
                is_budget_extend_reply(query) or is_decline_reply(query)
                or is_cancel_command(query)
            )
        )
        continues_paused = approval_continues or budget_continues
        if (
            paused_turn is not None
            and not (pending_handled and keeps_pending)
            and not continues_paused
        ):
            await _cancel_other_turn(paused_turn, "dropped")
            paused_turn = None
        if continues_paused and paused_turn is not None:
            turn_box["ref"] = ref_from_turn(paused_turn)
            await alink_message(turn_box["ref"].id, message_id)
        else:
            await _start_turn()
        if not pending_handled and turn_box["ref"] is None:
            # Another writer owns this room's active turn (the partial unique
            # constraint refused the claim). Fail closed instead of running
            # the work a second time with no record.
            await broadcast_chunk(
                "This turn could not be recorded, so nothing was run. Please try again.",
                harness=True,
            )
            await emit_progress("done", "completed", "Turn not recorded.")
            return OrchestrationResult(
                full_response="".join(stream_state["full_response"]),
                persist=False,
                turn_id="",
            )

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
                    cancel_msg = await cancel_pending_action(room_id, user_id)
                    await _advance_turn("cancelled", end_reason="cancelled")
                    await broadcast_chunk(cancel_msg or "Okay, cancelled.", harness=True)
                    pending_handled = True
                elif is_autopilot_request(query) and await _pending_armable_shell():
                    # Arming confirms the pending command only when it armed;
                    # otherwise the command stays pending for a plain yes/no.
                    if await _arm_autopilot_from_chat():
                        if await _resume_turn(turn_box["ref"]):
                            await _resume_confirmed()
                    elif turn_box["ref"] is not None and turn_box["ref"].state == "paused":
                        turn_box["hold"] = True
                    pending_handled = True
                elif is_approval_reply(query):
                    if await _resume_turn(turn_box["ref"]):
                        await _resume_confirmed()
                    else:
                        await broadcast_chunk(
                            "This turn was already resumed elsewhere. Nothing further was run.",
                            harness=True,
                        )
                    pending_handled = True
                else:
                    # Anything else is not consent: drop the pending action, say so,
                    # and handle the message as a new turn.
                    await dismiss_pending_confirmation(room_id, user_id)
                    await broadcast_chunk("I did not run the pending action; it is cancelled.\n\n", harness=True)

        # --- Agent loop budget / quota resume (T-F3, T-F3b) ---
        if not pending_handled and pending_budget:
            if is_budget_extend_reply(query):
                ctx_prompt = ""
                try:
                    ctx_prompt = await get_context_prompt() or ""
                except Exception:
                    pass
                ctx_prompt = await _with_persona_identity(room_id, user_id, ctx_prompt)
                env_block = await _environment_block()
                mem_sum = await load_memory_summary(adaptive_context) or ""
                if pending_pause == "quota":
                    # The hourly limit stopped the turn. It must not be claimed to
                    # have freed unless that was checked (T-F3b): a check that
                    # still blocks keeps the paused turn and says so, so a resume
                    # during a full hour never shows the "freed up" line.
                    still_blocked = await _quota_resume_still_blocked(user_id)
                    if still_blocked is not None:
                        if turn_box["ref"] is not None and turn_box["ref"].state == "paused":
                            turn_box["hold"] = True
                        await broadcast_chunk(
                            _quota_resume_blocked_note(still_blocked),
                            harness=True,
                        )
                    elif await _resume_turn(turn_box["ref"]):
                        # Resuming it is not a budget grant, and the wording is
                        # about the hour (T-F3b).
                        await broadcast_chunk(
                            "The hourly token limit has freed up. Continuing from where it stopped.\n\n",
                            harness=True,
                        )
                        await _handle_agent_quota_resume(ctx_prompt, mem_sum, env_block)
                    else:
                        await broadcast_chunk(
                            "This turn was already resumed elsewhere. Nothing further was run.",
                            harness=True,
                        )
                elif await _resume_turn(turn_box["ref"]):
                    await broadcast_chunk("Continuing with more budget.\n\n", harness=True)
                    await _handle_agent_budget_resume(ctx_prompt, mem_sum, env_block)
                else:
                    await broadcast_chunk(
                        "This turn was already resumed elsewhere. Nothing further was run.",
                        harness=True,
                    )
                pending_handled = True
            elif is_decline_reply(query) or is_cancel_command(query):
                # A whole-message no/stop ends the paused turn for good.
                clear_loop_state(room_id, user_id)
                await _advance_turn("cancelled", end_reason="cancelled")
                await broadcast_chunk(
                    "Okay, I've stopped there and dropped the paused work.\n\n",
                    harness=True,
                )
                pending_handled = True
            else:
                # Anything else the user writes starts a new turn, as today; the
                # paused budget turn is discarded, and the reply says so once.
                clear_loop_state(room_id, user_id)
                await broadcast_chunk(
                    "I dropped the paused work and started fresh with this message.\n\n",
                    harness=True,
                )

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
            turn_id=_turn_id(),
        )
