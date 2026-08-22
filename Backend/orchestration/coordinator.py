"""OrchestrationCoordinator — the chat routing facade.

`ChatConsumer.new_message` validates the WebSocket input surface (sender,
room, mute, rate-limit, encryption) and then delegates every decision
*after* `@mathia` routing to this class. The coordinator owns the routing
pipeline (directives, pending confirmations, agent loop, planner, intent
dispatch, general chat) and talks back to the consumer through a small set
of injected async callbacks so it has no Channels dependency of its own.
"""
import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.cache import cache

from orchestration.telemetry import record_event
from orchestration.contracts import build_step_event
from orchestration.intent_parser import parse_intent
from orchestration.user_preferences import get_user_preferences, format_style_prompt
from orchestration.action_receipts import (
    attach_receipt_to_result,
    build_confirmation_prompt,
    fetch_recent_receipts,
    is_receipt_request,
    is_undo_request,
    record_action_receipt,
    requires_confirmation,
    should_include_receipt,
    should_record_receipt,
    undo_last_action,
    format_receipt_list,
)
from orchestration.adaptive_task import (
    load_task_state,
    save_task_state,
    clear_task_state,
    init_task_state,
    update_task_state,
    get_action_definition,
    format_missing_prompt,
    apply_summary_defaults,
    should_use_summary,
    store_result_set,
    needs_option_context,
    clear_result_sets,
    is_reset_request,
    is_small_talk,
    is_cancel_request,
    is_resume_request,
    get_conversation_mode,
    set_conversation_mode,
    detect_mode_command,
    format_mode_ack,
    is_task_paused,
    pause_task_state,
    clear_task_pause,
    DEFAULT_PAUSE_SECONDS,
    SOCIAL_PAUSE_SECONDS,
)
from orchestration.memory_state import load_memory_summary, clear_memory
from orchestration.security_policy import should_refuse_sensitive_request, sensitive_refusal_message
from orchestration.agent_loop import (
    run_agent_loop,
    has_pending_agent_state,
    resume_after_confirmation,
    cancel_pending_action,
    dismiss_pending_confirmation,
)
from orchestration.mcp_router import route_intent
from orchestration.data_synthesizer import synthesize_response, synthesize_response_stream
from orchestration.workflow_planner import (
    plan_user_request,
    execute_adhoc_workflow,
    synthesize_workflow_response_stream,
    looks_like_confirmation,
    LLM_CONFIDENCE_EXECUTE,
    LLM_CONFIDENCE_CONFIRM,
)

logger = logging.getLogger(__name__)

PENDING_CONFIRM_TTL_SECONDS = 600
LAST_SUMMARY_TTL_SECONDS = 60 * 60
AGENT_LOOP_ENABLED = getattr(settings, "AGENT_LOOP_ENABLED", True)


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
        history_text: str,
        send_chunk: Callable[[str, str, bool], Awaitable[None]],
        send_step_event: Callable[[str, dict], Awaitable[None]],
        get_context_prompt: Callable[[], Awaitable[str]],
        bump_signals: Callable[[List[Optional[str]]], None],
    ) -> OrchestrationResult:
        stream_state = {"buffer": [], "last_send": 0, "first_token_sent": False, "full_response": []}
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
            except Exception:
                send_ok = False
                raise
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

        pending_key = f"orchestration:pending:{room_id}:{user_id}"
        last_summary_key = f"orchestration:last_summary:{room_id}:{user_id}"
        adaptive_context = {
            "user_id": user_id,
            "room_id": room_id,
            "username": username,
        }
        memory_summary = await load_memory_summary(adaptive_context)
        if memory_summary:
            history_text = "\n\n".join([memory_summary, history_text]).strip()
        user_preferences = {}
        try:
            user_preferences = await sync_to_async(get_user_preferences)(user_id)
        except Exception as e:
            logger.warning(f"User preferences load failed: {e}")
            user_preferences = {}
        style_prompt = format_style_prompt(user_preferences)
        conversation_mode = await get_conversation_mode(adaptive_context)
        mode_handled = False
        mode_cmd = detect_mode_command(query)
        if mode_cmd:
            await set_conversation_mode(adaptive_context, mode_cmd)
            conversation_mode = mode_cmd
            await broadcast_chunk(format_mode_ack(mode_cmd))
            mode_handled = True
        if is_reset_request(query):
            await clear_task_state(adaptive_context)
            await clear_result_sets(adaptive_context)
            await clear_memory(adaptive_context)
            cache.delete(pending_key)
            cache.delete(last_summary_key)
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
        summary_text_for_cache = None
        should_cache_summary = False

        def _log_telemetry(event_type, payload=None):
            try:
                record_event(event_type, payload or {})
            except Exception:
                return

        def _bump_signals(actions):
            try:
                bump_signals([a for a in actions if a])
            except Exception as exc:
                logger.warning(f"Proactive signal update skipped: {exc}")

        async def _execute_intent(intent):
            nonlocal summary_text_for_cache, should_cache_summary
            await emit_progress("validating", "started", "Checking safety and required details.")
            _log_telemetry("intent_execute", {
                "user_id": user_id,
                "room_id": room_id,
                "action": intent.get("action"),
            })
            prompt = await needs_option_context(
                adaptive_context,
                intent.get("action"),
                intent.get("parameters"),
                get_action_definition(intent.get("action")),
            )
            if prompt:
                task_state = init_task_state(intent)
                task_state["status"] = "awaiting_slots"
                task_state["missing_slots"] = task_state.get("missing_slots") or ["option_context"]
                task_state["last_prompt"] = prompt
                await save_task_state(adaptive_context, task_state)
                await broadcast_chunk(prompt)
                await emit_progress("validating", "completed", "Waiting for missing details.")
                return {"status": "needs_input", "message": prompt}
            if requires_confirmation(intent.get("action")) and not intent.get("confirmed"):
                confirm_prompt = build_confirmation_prompt(
                    intent.get("action") or "",
                    intent.get("parameters") or {},
                )
                cache.set(
                    pending_key,
                    {"kind": "intent", "intent": intent},
                    timeout=PENDING_CONFIRM_TTL_SECONDS,
                )
                await broadcast_chunk(confirm_prompt)
                await emit_progress("validating", "completed", "Waiting for confirmation.")
                return {"status": "needs_confirmation", "message": confirm_prompt}
            await emit_progress("executing", "started", f"Running {intent.get('action')}.")
            result = await route_intent(intent, {
                "user_id": user_id,
                "room_id": room_id,
                "username": username,
                "preferences": user_preferences,
            })

            logger.info(f"MCP result: {result['status']}")
            receipt = None
            terminal_statuses = {"success", "error", "failed"}
            if should_record_receipt(intent.get("action")) and result.get("status") in terminal_statuses:
                action_def = get_action_definition(intent.get("action")) or {}
                if not action_def and intent.get("action") == "send_whatsapp":
                    action_def = get_action_definition("send_message") or {}
                status = "success" if result.get("status") == "success" else "error"
                reason = result.get("message") or result.get("error") or ""
                data_payload = result.get("data") if isinstance(result.get("data"), dict) else {}
                receipt = await record_action_receipt(
                    user_id=user_id,
                    room_id=room_id,
                    action=intent.get("action") or "",
                    service=action_def.get("service") or "",
                    params=intent.get("parameters") or {},
                    result=data_payload or {"message": reason},
                    status=status,
                    reason=reason,
                )
                result = attach_receipt_to_result(result, receipt)

            if result["status"] in ("needs_clarification", "needs_confirmation"):
                clarification = (
                    result.get("clarification_prompt")
                    or result.get("message")
                    or "I need a bit more detail to proceed."
                )
                await broadcast_chunk(clarification)
                await emit_progress("executing", "completed", "Execution paused for clarification.")
            elif result["status"] == "success":
                _log_telemetry("intent_success", {
                    "user_id": user_id,
                    "room_id": room_id,
                    "action": intent.get("action"),
                })
                data_payload = result.get("data") if isinstance(result.get("data"), dict) else {}
                results = data_payload.get("results") if isinstance(data_payload, dict) else None
                if isinstance(results, list):
                    await store_result_set(
                        adaptive_context,
                        intent.get("action"),
                        results,
                        metadata=data_payload.get("metadata") if isinstance(data_payload, dict) else None,
                    )
                intent_for_synthesis = dict(intent)
                intent_for_synthesis["preferences"] = user_preferences
                summary_text_for_cache = await synthesize_response(
                    intent_for_synthesis,
                    result,
                    use_llm=False,
                )

                use_llm_stream = user_preferences.get("capability_mode") != "conserve"
                if should_include_receipt(intent.get("action")):
                    use_llm_stream = False
                async for chunk in synthesize_response_stream(
                    intent_for_synthesis,
                    result,
                    use_llm=use_llm_stream,
                ):
                    await broadcast_chunk(chunk)
                should_cache_summary = True
                _bump_signals([intent.get("action")])
                await emit_progress("executing", "completed", "Execution finished.")
            else:
                _log_telemetry("intent_error", {
                    "user_id": user_id,
                    "room_id": room_id,
                    "action": intent.get("action"),
                    "error": result.get("message"),
                })
                error_text = result.get("message") or result.get("reason") or "I could not complete that request."
                await broadcast_chunk(f"Error: {error_text}")
                await emit_progress("executing", "completed", "Execution failed.")
            return result

        def _is_dismiss_request(query_text: str) -> bool:
            lowered = query_text.lower()
            if not re.search(r"\b(dismiss|stop|no thanks)\b", lowered):
                return False
            return bool(re.search(r"\b(nudge|suggestion|proactive)\b", lowered))

        def _history_to_messages(history_text_arg: str):
            """Convert 'Member: message' text history to Anthropic messages format."""
            if not history_text_arg:
                return None
            messages = []
            for line in history_text_arg.strip().split("\n"):
                line = line.strip()
                if not line or ": " not in line:
                    continue
                speaker, _, content = line.partition(": ")
                content = content.strip()
                if not content:
                    continue
                role = "assistant" if speaker.lower() == "mathia" else "user"
                # Merge consecutive same-role messages
                if messages and messages[-1]["role"] == role:
                    messages[-1]["content"] += "\n" + content
                else:
                    messages.append({"role": role, "content": content})
            # Anthropic requires messages to start with user role
            if messages and messages[0]["role"] == "assistant":
                messages = messages[1:]
            return messages if messages else None

        async def _handle_agent_loop(query_text: str, history: str, ctx_prompt: str, mem_summary: str):
            """Run the agentic loop and map AgentEvents to WebSocket frames."""
            await emit_progress("planning", "started", "Thinking…")
            history_msgs = _history_to_messages(history)
            async for event in run_agent_loop(
                user_message=query_text,
                context={
                    "user_id": user_id,
                    "room_id": room_id,
                    "username": username,
                    "preferences": user_preferences,
                    "raw_query": query_text,
                    "user_message": query_text,
                },
                preferences=user_preferences,
                context_prompt=ctx_prompt,
                memory_summary=mem_summary,
                history=history_msgs,
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
                    await broadcast_chunk(event.data.get("message", "Please confirm."))
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
                    await broadcast_chunk(event.data.get("message", "Please confirm."))
                    await emit_progress("validating", "completed", "Waiting for confirmation.")
                elif event.kind == "error":
                    await broadcast_chunk(event.data.get("message", "Something went wrong."))
                elif event.kind == "done":
                    await emit_progress("done", "completed", "Request complete.")

        async def _stream_general_chat(query_text: str, history: str):
            from orchestration.llm_client import get_llm_client
            llm_client = get_llm_client()
            full_query = query_text
            if history:
                full_query = f"CONVERSATION HISTORY:\n{history}\n\nUSER message: {query_text}"
            system_prompt = "You are Mathia, a helpful AI assistant."
            if style_prompt:
                system_prompt = f"{system_prompt}\n{style_prompt}"
            system_prompt = (
                f"{system_prompt}\nBe concise, culturally respectful, and match the user's tone."
            )
            max_tokens = 300
            if user_preferences.get("capability_mode") == "conserve":
                max_tokens = 180
            async for chunk in llm_client.stream_text(
                system_prompt=system_prompt,
                user_prompt=full_query,
                temperature=0.7,
                max_tokens=max_tokens,
            ):
                await broadcast_chunk(chunk)

        handled_directive = mode_handled
        if not handled_directive and _is_dismiss_request(query):
            last_reason_key = f"proactive:last_reason:{room_id}:{user_id}"
            dismissed_key = f"proactive:dismissed:{room_id}:{user_id}"
            last_reason = cache.get(last_reason_key)
            if last_reason:
                dismissed = cache.get(dismissed_key) or []
                if last_reason not in dismissed:
                    dismissed.append(last_reason)
                    cache.set(dismissed_key, dismissed, timeout=60 * 60 * 24 * 14)
            cache.delete(pending_key)
            await broadcast_chunk("Got it. I will stop showing that kind of suggestion here.")
            handled_directive = True
        if not handled_directive and is_receipt_request(query):
            receipts = await fetch_recent_receipts(
                user_id=user_id,
                room_id=room_id,
                limit=3,
            )
            await broadcast_chunk(format_receipt_list(receipts))
            handled_directive = True
        if not handled_directive and is_undo_request(query):
            undo_result = await undo_last_action(
                user_id=user_id,
                room_id=room_id,
            )
            await broadcast_chunk(undo_result.get("message") or "Okay.")
            handled_directive = True
        if not handled_directive and re.search(r"\b(stop for now|pause for now|hold off)\b", query, re.IGNORECASE):
            await set_conversation_mode(adaptive_context, "social")
            await broadcast_chunk("Okay, I will pause tasks for now. Say 'resume' when you are ready.")
            handled_directive = True
        if not handled_directive and re.search(
            r"\b(what can you do|what are your (capabilities|tools|features)|"
            r"what tools do you have|help me understand|show me your tools)\b",
            query, re.IGNORECASE,
        ):
            from orchestration.action_catalog import ACTION_CATALOG
            categories = {}
            for action_def in ACTION_CATALOG:
                svc = action_def.get("service", "other")
                categories.setdefault(svc, []).append(
                    f"**{action_def['action'].replace('_', ' ')}** — {action_def.get('description', '')[:80]}"
                )
            lines = ["Here's what I can help with:\n"]
            for svc, actions in sorted(categories.items()):
                lines.append(f"\n**{svc.title()}**")
                for a in actions:
                    lines.append(f"  - {a}")
            lines.append(
                "\nI can also **search the web** for information and **delegate complex tasks** "
                "to focused sub-assistants."
            )
            await broadcast_chunk("\n".join(lines))
            handled_directive = True

        pending = cache.get(pending_key)
        pending_handled = handled_directive
        if pending and not pending_handled:
            if is_cancel_request(query):
                cache.delete(pending_key)
                await broadcast_chunk("Okay, I cancelled the pending request.")
                pending_handled = True
            elif looks_like_confirmation(query):
                pending_kind = pending.get("kind")
                cache.delete(pending_key)
                if pending_kind == "workflow":
                    pending_handled = True
                    workflow_definition = pending.get("workflow_definition") or {}
                    pending_message = pending.get("user_message") or query
                    execution = await execute_adhoc_workflow(
                        workflow_definition,
                        user_id,
                        room_id,
                        trigger_data={"message": pending_message, "room_id": room_id},
                    )
                    _log_telemetry("workflow_execution", {
                        "user_id": user_id,
                        "room_id": room_id,
                        "status": execution.get("status"),
                        "mode": execution.get("mode"),
                        "step_count": len(workflow_definition.get("steps", [])),
                    })
                    async for chunk in synthesize_workflow_response_stream(
                        pending_message,
                        workflow_definition,
                        execution.get("result") or {},
                        execution.get("status") or "running",
                        execution.get("error") or execution.get("message"),
                        preferences=user_preferences,
                    ):
                        await broadcast_chunk(chunk)
                    should_cache_summary = True
                    if execution.get("status") == "completed":
                        step_actions = [
                            step.get("action")
                            for step in workflow_definition.get("steps", [])
                            if isinstance(step, dict)
                        ]
                        _bump_signals(step_actions + ["workflow_run"])
                elif pending_kind == "intent":
                    pending_handled = True
                    intent = pending.get("intent") or {}
                    intent["confirmed"] = True
                    result = await _execute_intent(intent)
                    if result.get("status") == "success":
                        await clear_task_state(adaptive_context)
            else:
                cache.delete(pending_key)

        # --- Agent loop confirmation resume ---
        if not pending_handled and AGENT_LOOP_ENABLED:
            if await has_pending_agent_state(room_id, user_id):
                if is_cancel_request(query):
                    cancel_msg = await cancel_pending_action(room_id, user_id)
                    await broadcast_chunk(cancel_msg or "Okay, cancelled.")
                    pending_handled = True
                elif looks_like_confirmation(query):
                    ctx_prompt = ""
                    try:
                        ctx_prompt = await get_context_prompt() or ""
                    except Exception:
                        pass
                    mem_sum = await load_memory_summary(adaptive_context) or ""
                    await _handle_agent_resume(ctx_prompt, mem_sum)
                    pending_handled = True
                else:
                    # User said something other than yes/no — clear the pending state
                    await dismiss_pending_confirmation(room_id, user_id)

        if not pending_handled:
            adaptive_state = await load_task_state(adaptive_context)
            if adaptive_state and adaptive_state.get("status") == "awaiting_slots":
                if is_cancel_request(query):
                    await clear_task_state(adaptive_context)
                    cache.delete(pending_key)
                    await broadcast_chunk("Okay, I paused that task. Let me know when you want to continue.")
                    pending_handled = True
                    adaptive_state = None
                if not pending_handled and adaptive_state:
                    if is_task_paused(adaptive_state):
                        if is_resume_request(query):
                            adaptive_state = clear_task_pause(adaptive_state)
                            await save_task_state(adaptive_context, adaptive_state)
                        elif is_small_talk(query):
                            await save_task_state(adaptive_context, adaptive_state)
                            await _stream_general_chat(query, history_text)
                            pending_handled = True
                            adaptive_state = None
                    if not pending_handled and adaptive_state and is_small_talk(query) and conversation_mode != "focus":
                        pause_seconds = SOCIAL_PAUSE_SECONDS if conversation_mode == "social" else DEFAULT_PAUSE_SECONDS
                        adaptive_state = pause_task_state(adaptive_state, "small_talk", pause_seconds)
                        await save_task_state(adaptive_context, adaptive_state)
                        await _stream_general_chat(query, history_text)
                        pending_handled = True
                        adaptive_state = None
                if not pending_handled and adaptive_state:
                    expected_action = adaptive_state.get("action")
                    followup_intent = await parse_intent(query, {
                        "user_id": user_id,
                        "username": username,
                        "room_id": room_id,
                        "history": history_text,
                        "expected_action": expected_action,
                        "expected_slots": adaptive_state.get("missing_slots") or [],
                        "preferences": user_preferences,
                    })
                    if (
                        adaptive_state.get("missing_slots")
                        and not (followup_intent.get("parameters") or {})
                        and query
                        and len(query.split()) <= 4
                    ):
                        slot_name = (adaptive_state.get("missing_slots") or [None])[0]
                        if slot_name:
                            followup_intent["parameters"] = {slot_name: query.strip()}
                            record_event(
                                "slot_fill",
                                {
                                    "action": expected_action,
                                    "missing_slots": adaptive_state.get("missing_slots") or [],
                                    "filled_slots": [slot_name],
                                    "source": "direct_reply",
                                    "user_id": user_id,
                                    "room_id": room_id,
                                },
                            )
                    if (
                        followup_intent.get("action") == "general_chat"
                        and not (followup_intent.get("parameters") or {})
                        and is_small_talk(query)
                        and conversation_mode != "focus"
                    ):
                        await save_task_state(adaptive_context, adaptive_state)
                        await _stream_general_chat(query, history_text)
                        pending_handled = True
                        adaptive_state = None
                    if not pending_handled:
                        if (
                            followup_intent.get("action")
                            and followup_intent.get("action") != expected_action
                            and followup_intent.get("confidence", 0) >= LLM_CONFIDENCE_CONFIRM
                        ):
                            await clear_task_state(adaptive_context)
                        else:
                            updated_state = update_task_state(
                                adaptive_state,
                                followup_intent.get("parameters") or {},
                            )
                            action_def = get_action_definition(updated_state.get("action"))
                            if updated_state.get("status") == "awaiting_slots":
                                prompt = format_missing_prompt(
                                    updated_state.get("action"),
                                    updated_state.get("missing_slots") or [],
                                    action_def,
                                )
                                if not prompt:
                                    prompt = (
                                        followup_intent.get("clarifying_question")
                                        or "I need a bit more detail to proceed."
                                    )
                                updated_state["last_prompt"] = prompt
                                await save_task_state(adaptive_context, updated_state)
                                await broadcast_chunk(prompt)
                                pending_handled = True
                            else:
                                summary_text = None
                                if should_use_summary(query):
                                    summary_text = cache.get(last_summary_key)
                                params = apply_summary_defaults(
                                    updated_state.get("action"),
                                    updated_state.get("parameters"),
                                    summary_text,
                                )
                                updated_state["parameters"] = params
                                action_def = get_action_definition(updated_state.get("action"))
                                prompt = await needs_option_context(
                                    adaptive_context,
                                    updated_state.get("action"),
                                    params,
                                    action_def,
                                )
                                if prompt:
                                    updated_state["status"] = "awaiting_slots"
                                    updated_state["missing_slots"] = updated_state.get("missing_slots") or ["option_context"]
                                    updated_state["last_prompt"] = prompt
                                    await save_task_state(adaptive_context, updated_state)
                                    await broadcast_chunk(prompt)
                                    pending_handled = True
                                else:
                                    await save_task_state(adaptive_context, updated_state)
                                    intent = {
                                        "action": updated_state.get("action"),
                                        "parameters": params,
                                        "confidence": 1.0,
                                    }
                                    result = await _execute_intent(intent)
                                    if result.get("status") == "success":
                                        await clear_task_state(adaptive_context)
                                    pending_handled = True

        if not pending_handled and AGENT_LOOP_ENABLED and conversation_mode != "classic":
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
            mem_sum = await load_memory_summary(adaptive_context) or ""
            try:
                await _handle_agent_loop(
                    query,
                    history_text,
                    ctx_prompt,
                    mem_sum,
                )
            except Exception as agent_exc:
                logger.warning(
                    "Agent loop failed, falling back to classic pipeline: %s",
                    agent_exc,
                    exc_info=True,
                )
                # Fall through to classic path below
                pass
            else:
                pending_handled = True

        if not pending_handled:
            await emit_progress("planning", "started", "Interpreting your request.")
            plan = await plan_user_request(
                query,
                history_text,
                user_id=user_id,
                preferences=user_preferences,
            )
            await emit_progress("planning", "completed", f"Mode: {plan.get('mode')}.")
            _log_telemetry("plan_decision", {
                "user_id": user_id,
                "room_id": room_id,
                "mode": plan.get("mode"),
                "confidence": plan.get("confidence"),
            })

            if plan["mode"] == "automation_request":
                await emit_progress("executing", "started", "Preparing workflow draft.")
                from workflows.workflow_agent import handle_workflow_message
                response_text = await handle_workflow_message(user_id, room_id, query, history_text)
                await broadcast_chunk(response_text)
                await emit_progress("executing", "completed", "Workflow draft ready.")
            elif plan["mode"] == "needs_clarification":
                _log_telemetry("clarification_requested", {
                    "user_id": user_id,
                    "room_id": room_id,
                    "source": "planner",
                })
                await broadcast_chunk(plan.get("assistant_message") or "I need a bit more detail to proceed.")
                await emit_progress("validating", "completed", "Waiting for clarification.")
            elif plan["mode"] == "needs_confirmation":
                workflow_definition = plan.get("workflow_definition") or {}
                cache.set(
                    pending_key,
                    {
                        "kind": "workflow",
                        "workflow_definition": workflow_definition,
                        "user_message": query,
                    },
                    timeout=PENDING_CONFIRM_TTL_SECONDS,
                )
                await broadcast_chunk(plan.get("assistant_message") or "Please confirm to proceed.")
                await emit_progress("validating", "completed", "Waiting for confirmation.")
            elif plan["mode"] == "adhoc_workflow":
                await emit_progress("executing", "started", "Running workflow steps.")
                workflow_definition = plan.get("workflow_definition") or {}
                execution = await execute_adhoc_workflow(
                    workflow_definition,
                    user_id,
                    room_id,
                    trigger_data={"message": query, "room_id": room_id},
                )
                _log_telemetry("workflow_execution", {
                    "user_id": user_id,
                    "room_id": room_id,
                    "status": execution.get("status"),
                    "mode": execution.get("mode"),
                    "step_count": len(workflow_definition.get("steps", [])),
                })
                async for chunk in synthesize_workflow_response_stream(
                    query,
                    workflow_definition,
                    execution.get("result") or {},
                    execution.get("status") or "running",
                    execution.get("error") or execution.get("message"),
                    preferences=user_preferences,
                ):
                    await broadcast_chunk(chunk)
                should_cache_summary = True
                if execution.get("status") == "completed":
                    step_actions = [
                        step.get("action")
                        for step in workflow_definition.get("steps", [])
                        if isinstance(step, dict)
                    ]
                    _bump_signals(step_actions + ["workflow_run"])
                await emit_progress("executing", "completed", f"Workflow status: {execution.get('status')}.")
            else:
                await emit_progress("understanding", "started", "Resolving intent and parameters.")
                # Step 1: Parse intent
                intent = await parse_intent(query, {
                    "user_id": user_id,
                    "username": username,
                    "room_id": room_id,
                    "history": history_text,
                    "preferences": user_preferences,
                })
                await emit_progress("understanding", "completed", f"Intent: {intent.get('action')}.")

                logger.info(f"Intent: {intent}")
                action = intent.get("action")
                params = intent.get("parameters") if isinstance(intent.get("parameters"), dict) else {}
                if should_use_summary(query):
                    params = apply_summary_defaults(action, params, cache.get(last_summary_key))
                intent["parameters"] = params

                task_state = init_task_state(intent)

                if action == "create_workflow" and intent.get("confidence", 0) > 0.6:
                    from workflows.workflow_agent import handle_workflow_message
                    response_text = await handle_workflow_message(user_id, room_id, query, history_text)
                    await broadcast_chunk(response_text)
                elif task_state.get("status") == "awaiting_slots" and action != "general_chat":
                    action_def = get_action_definition(action)
                    question = format_missing_prompt(
                        action,
                        task_state.get("missing_slots") or [],
                        action_def,
                    )
                    if not question:
                        question = intent.get("clarifying_question") or "I need a bit more detail to proceed."
                    if conversation_mode == "social":
                        question = f"No rush — {question}"
                        task_state = pause_task_state(task_state, "social_mode", SOCIAL_PAUSE_SECONDS)
                    task_state["last_prompt"] = question
                    await save_task_state(adaptive_context, task_state)
                    _log_telemetry("clarification_requested", {
                        "user_id": user_id,
                        "room_id": room_id,
                        "source": "intent",
                        "action": action,
                        "missing_slots": task_state.get("missing_slots") or [],
                    })
                    await broadcast_chunk(question)
                elif intent.get("confidence", 0) >= LLM_CONFIDENCE_EXECUTE and action != "general_chat":
                    # Route through MCP
                    result = await _execute_intent(intent)
                    if result.get("status") == "success":
                        await clear_task_state(adaptive_context)
                elif intent.get("confidence", 0) >= LLM_CONFIDENCE_CONFIRM and action != "general_chat":
                    cache.set(
                        pending_key,
                        {"kind": "intent", "intent": intent},
                        timeout=PENDING_CONFIRM_TTL_SECONDS,
                    )
                    await clear_task_state(adaptive_context)
                    action_label = str(action or "that").replace("_", " ")
                    await broadcast_chunk(
                        f"I think you want me to {action_label}. Reply 'yes' to proceed or clarify."
                    )
                else:
                    # Fallback to LLM for general chat or low confidence (STREAMING)
                    await _stream_general_chat(query, history_text)

        # End stream
        await emit_progress("done", "completed", "Request complete.")
        await broadcast_chunk("", is_final=True)

        full_response_text = "".join(stream_state["full_response"])

        if should_cache_summary:
            summary_value = (summary_text_for_cache or full_response_text).strip()
            if summary_value:
                cache.set(last_summary_key, summary_value, timeout=LAST_SUMMARY_TTL_SECONDS)

        return OrchestrationResult(full_response=full_response_text)
