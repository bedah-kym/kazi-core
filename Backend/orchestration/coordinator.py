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
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional

from asgiref.sync import sync_to_async
from django.core.cache import cache

from orchestration.telemetry import record_event
from orchestration.contracts import build_step_event
from orchestration.user_preferences import get_user_preferences
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
from orchestration.security_policy import should_refuse_sensitive_request, sensitive_refusal_message
from orchestration.shell.chat_intents import (
    is_approval_reply,
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
    resume_after_confirmation,
    cancel_pending_action,
    dismiss_pending_confirmation,
)

logger = logging.getLogger(__name__)


@dataclass
class OrchestrationResult:
    """What the consumer needs after the routing pipeline finishes."""

    full_response: str = ""
    persist: bool = True


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

        Return the accumulated response with ``persist=False`` for resets and
        sensitive-request refusals, otherwise ``True``. The caller saves it.
        Resets and refusals return without sending a final stream chunk.

        Context-prompt and progress-frame failures are ignored. A failure from
        a fresh agent run ends the turn with one error message and nothing
        further runs; errors during confirmation resume propagate.
        """
        stream_state = {"buffer": [], "last_send": 0, "first_token_sent": False, "full_response": []}  # nosec B105 — state keys, not a credential
        turn_step_id = f"turn_{message_id}"
        correlation_id = uuid.uuid4().hex

        async def broadcast_chunk(chunk_text, is_final=False):
            # Store all chunks to build full response
            if chunk_text:
                stream_state["full_response"].append(chunk_text)

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
                    try:
                        await send_chunk(correlation_id, joined_text, is_final)
                    finally:
                        record_event(
                            "progress_event",
                            {
                                "phase": "stream",
                                "state": "chunk",
                                "room_id": room_id,
                                "user_id": user_id,
                                "correlation_id": correlation_id,
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
                        "correlation_id": correlation_id,
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
        try:
            user_preferences = await sync_to_async(get_user_preferences)(user_id)
        except Exception as e:
            logger.warning(f"User preferences load failed: {e}")
            user_preferences = {}
        try:
            from orchestration.preference_mining import merge_learned_overrides
            user_preferences = await sync_to_async(merge_learned_overrides)(user_preferences, user_id, room_id)
        except Exception as e:
            logger.debug("Learned override merge skipped: %s", e)
        try:
            from orchestration.shell.profiles import resolve_profile
            resolved = await sync_to_async(resolve_profile)(room_id, user_preferences)
            user_preferences["shell_profile"] = resolved.name
        except Exception as e:
            logger.debug("Shell profile resolution skipped: %s", e)
        # Human-armed window for the unsandboxed profile. Always overwritten here
        # so a stored preference can never set it. Fail closed: no lookup -> ask.
        user_preferences.pop("_shell_tainted", None)
        try:
            from orchestration.shell.autopilot import is_armed
            user_preferences["_shell_autopilot"] = bool(
                await sync_to_async(is_armed)(user_id, room_id)
            )
        except Exception as e:
            logger.debug("Shell autopilot lookup skipped: %s", e)
            user_preferences["_shell_autopilot"] = False
        if is_reset_command(query):
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
                    "correlation_id": correlation_id,
                },
            )
            await broadcast_chunk("Okay, starting fresh. What would you like to do?")
            await emit_progress("done", "completed", "Context reset.")
            return OrchestrationResult(full_response="".join(stream_state["full_response"]), persist=False)
        if should_refuse_sensitive_request(query):
            record_event(
                "refused_sensitive_request",
                {
                    "user_id": user_id,
                    "room_id": room_id,
                    "correlation_id": correlation_id,
                },
            )
            await broadcast_chunk(sensitive_refusal_message())
            await emit_progress("done", "completed", "Request refused.")
            return OrchestrationResult(full_response="".join(stream_state["full_response"]), persist=False)

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
                    return "\n\n".join([persona_identity_prompt(persona), ctx_prompt or ""]).strip()
            except Exception as exc:
                logger.debug("Persona identity injection skipped: %s", exc)
            return ctx_prompt

        async def _with_environment(ctx_prompt: str) -> str:
            """Prepend where commands run, from the shell layer rather than a guess."""
            try:
                from orchestration.agent_prompts import build_environment_block
                from orchestration.shell.environment import get_shell_environment
                from orchestration.shell.profiles import resolve_profile

                profile = await sync_to_async(resolve_profile)(room_id, user_preferences)
                block = build_environment_block(await get_shell_environment(profile))
                return "\n\n".join([block, ctx_prompt or ""]).strip()
            except Exception as exc:
                logger.debug("Environment block skipped: %s", exc)
                return ctx_prompt

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
            from orchestration.agent_loop import load_loop_state

            state = await sync_to_async(load_loop_state)(room_id, user_id)
            return bool(state and state.pending_tool and state.pending_tier == "bounded")

        async def _pending_armable_shell() -> bool:
            """True when the pending action is a shell command the window could cover."""
            from orchestration.agent_loop import load_loop_state

            state = await sync_to_async(load_loop_state)(room_id, user_id)
            pending = getattr(state, "pending_tool", None) if state else None
            if not isinstance(pending, dict):
                return False
            if str(pending.get("name") or "") not in {"run_command", "run_shell", "shell_command"}:
                return False
            return getattr(state, "pending_tier", None) in ("safe", "bounded")

        async def _autopilot_hint() -> str:
            """Offer the window only where it applies: an unsandboxed room, a coverable command."""
            if user_preferences.get("shell_profile") != "open" or user_preferences.get("_shell_autopilot"):
                return ""
            try:
                if not await _pending_armable_shell():
                    return ""
            except Exception:
                return ""
            return (
                "\n\nThis room's shell is unsandboxed, so I ask before commands like this. "
                "Reply **autopilot** to stop asking for a limited window; destructive "
                "commands will still ask."
            )

        async def _handle_agent_loop(query_text: str, history_msgs, ctx_prompt: str, mem_summary: str):
            """Run the agentic loop and map AgentEvents to WebSocket frames."""
            await emit_progress("planning", "started", "Thinking…")
            async for event in run_agent_loop(
                user_message=query_text,
                context={
                    "user_id": user_id,
                    "room_id": room_id,
                    "username": username,
                    "preferences": user_preferences,
                    "refresh_shell_autonomy": _refresh_shell_autonomy,
                    "raw_query": query_text,
                    "user_message": query_text,
                },
                preferences=user_preferences,
                context_prompt=ctx_prompt,
                memory_summary=mem_summary,
                history=history_msgs or None,
            ):
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
                    tool = event.data.get("name", "action")
                    result = event.data.get("result", {})
                    status = result.get("status", "")
                    msg = f"{tool.replace('_', ' ')}: {status}"
                    await emit_progress("executing", "completed", msg)
                elif event.kind == "confirmation":
                    await broadcast_chunk(event.data.get("message", "Please confirm.") + await _autopilot_hint())
                    await emit_progress("validating", "completed", "Waiting for confirmation.")
                elif event.kind == "error":
                    await broadcast_chunk(event.data.get("message", "Something went wrong."))
                    await emit_progress("executing", "completed", "Error encountered.")
                elif event.kind == "done":
                    await emit_progress("done", "completed", "Request complete.")

        async def _handle_agent_resume(ctx_prompt: str, mem_summary: str):
            """Resume a paused agent loop after user confirms."""
            async for event in resume_after_confirmation(
                context={
                    "user_id": user_id,
                    "room_id": room_id,
                    "username": username,
                    "preferences": user_preferences,
                    "refresh_shell_autonomy": _refresh_shell_autonomy,
                },
                preferences=user_preferences,
                context_prompt=ctx_prompt,
                memory_summary=mem_summary,
            ):
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
                    tool = event.data.get("name", "action")
                    result = event.data.get("result", {})
                    status = result.get("status", "")
                    await emit_progress("executing", "completed", f"{tool.replace('_', ' ')}: {status}")
                elif event.kind == "confirmation":
                    await broadcast_chunk(event.data.get("message", "Please confirm.") + await _autopilot_hint())
                    await emit_progress("validating", "completed", "Waiting for confirmation.")
                elif event.kind == "error":
                    await broadcast_chunk(event.data.get("message", "Something went wrong."))
                elif event.kind == "done":
                    await emit_progress("done", "completed", "Request complete.")

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
                await broadcast_chunk(
                    "Autopilot armed for this room. Shell commands, including root and "
                    "network ones, now run on this unsandboxed host without asking until "
                    "the window expires or you say **autopilot off**. Commands on the "
                    "destructive list still ask."
                )
            else:
                await broadcast_chunk(
                    "I couldn't arm autopilot, so nothing has changed. It needs a room on "
                    "the unsandboxed shell profile. Reply yes or no to the pending command."
                )
            return armed

        # Autopilot kill switch. Evaluated before every other handler and
        # whatever the loaded flag says, so no branch below can swallow a stop.
        # While an action is pending the broad, in-sentence match still applies
        # (it fails safe); with nothing pending only a whole-message stop
        # disarms the window, so "why did it stop?" reaches the model.
        from orchestration.shell.chat_intents import is_autopilot_disarm_request
        explicit_disarm = is_autopilot_disarm_request(query)
        pending_confirmation = await _has_pending_confirmation()
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
                await broadcast_chunk("Autopilot disarmed for this room.\n\n")
            elif disarmed is None and (was_armed or explicit_disarm):
                await broadcast_chunk(
                    "I couldn't confirm autopilot was disarmed. It is off for this message; "
                    "disarm it from the operations inbox to be sure."
                )
            elif explicit_disarm:
                await broadcast_chunk("Autopilot was not armed for this room.")

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
            await broadcast_chunk(reply)

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
            await broadcast_chunk("Got it. I will stop showing that kind of suggestion here.")
            handled_directive = True
        if not handled_directive and is_receipts_command(query):
            receipts = await fetch_recent_receipts(
                user_id=user_id,
                room_id=room_id,
                limit=3,
            )
            await broadcast_chunk(format_receipt_list(receipts))
            handled_directive = True
        if not handled_directive and is_undo_command(query):
            undo_result = await undo_last_action(
                user_id=user_id,
                room_id=room_id,
            )
            await broadcast_chunk(undo_result.get("message") or "Okay.")
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
                await broadcast_chunk("\n\nI did not run the pending action; it is cancelled.")

        # --- Agent loop confirmation resume ---
        if not pending_handled:
            if pending_confirmation:
                from orchestration.shell.chat_intents import is_autopilot_request

                async def _resume_confirmed():
                    ctx_prompt = ""
                    try:
                        ctx_prompt = await get_context_prompt() or ""
                    except Exception:
                        pass
                    ctx_prompt = await _with_persona_identity(room_id, user_id, ctx_prompt)
                    ctx_prompt = await _with_environment(ctx_prompt)
                    mem_sum = await load_memory_summary(adaptive_context) or ""
                    await _handle_agent_resume(ctx_prompt, mem_sum)

                # Cancel is checked first: a refusal must never be read as consent.
                if is_cancel_request(query) or is_decline_reply(query):
                    cancel_msg = await cancel_pending_action(room_id, user_id)
                    await broadcast_chunk(cancel_msg or "Okay, cancelled.")
                    pending_handled = True
                elif is_autopilot_request(query) and await _pending_armable_shell():
                    # Arming confirms the pending command only when it armed;
                    # otherwise the command stays pending for a plain yes/no.
                    if await _arm_autopilot_from_chat():
                        await _resume_confirmed()
                    pending_handled = True
                elif is_approval_reply(query):
                    await _resume_confirmed()
                    pending_handled = True
                else:
                    # Anything else is not consent: drop the pending action, say so,
                    # and handle the message as a new turn.
                    await dismiss_pending_confirmation(room_id, user_id)
                    await broadcast_chunk("I did not run the pending action; it is cancelled.\n\n")

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
            ctx_prompt = await _with_environment(ctx_prompt)
            mem_sum = memory_summary or ""
            try:
                await _handle_agent_loop(
                    query,
                    history_messages,
                    ctx_prompt,
                    mem_sum,
                )
            except Exception as agent_exc:
                logger.warning("Agent loop failed: %s", agent_exc.__class__.__name__)
                record_event(
                    "agent_loop_failed",
                    {
                        "user_id": user_id,
                        "room_id": room_id,
                        "correlation_id": correlation_id,
                        "error_class": agent_exc.__class__.__name__,
                    },
                )
                await broadcast_chunk("Something went wrong on my side. Nothing further was run.")

        # End stream
        await emit_progress("done", "completed", "Request complete.")
        await broadcast_chunk("", is_final=True)

        full_response_text = "".join(stream_state["full_response"])

        return OrchestrationResult(full_response=full_response_text)
