"""
ReAct-style Agent Loop for Mathia.

Replaces the linear parse → route → done pipeline with an autonomous
think → act → observe loop. The LLM decides which tools to call,
sees the results, and iterates until the task is complete.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Tuple

from django.core.cache import cache

from orchestration.agent_prompts import build_confirmation_prompt, build_system_prompt
from orchestration.llm_client import get_llm_client
from orchestration.memory_state import update_memory_state, save_memory_summary
from orchestration.telemetry import record_event
from orchestration.tool_executor import execute_tool, get_tool_risk_info
from orchestration.tool_schemas import get_tool_definitions

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
#  Constants                                                                  #
# --------------------------------------------------------------------------- #

MAX_ITERATIONS = 10
MAX_TOOL_CALLS = 15
MAX_RETRIES_PER_TOOL = 2
TOOL_TIMEOUT_SECONDS = 30
LOOP_TIMEOUT_SECONDS = 120
CONFIRMATION_STATE_TTL = 600  # 10 minutes
AGENT_STATE_KEY = "orchestration:agent_state:{room_id}:{user_id}"

# Web search rate limits
DAILY_SEARCH_LIMIT = 10
MAX_SEARCHES_PER_REQUEST = 5

# Token budget per loop
LOOP_TOKEN_BUDGET = 50000  # hard cap per single agent loop invocation
LOOP_TOKEN_WARNING_RATIO = 0.80  # warn user at 80%

# Model selection
MODEL_SONNET = "claude-sonnet-4-6"
MODEL_HAIKU = "claude-haiku-4-5-20251001"


# --------------------------------------------------------------------------- #
#  Data structures                                                            #
# --------------------------------------------------------------------------- #

@dataclass
class AgentEvent:
    """A single event yielded by the agent loop to the consumer."""
    kind: str  # "text", "tool_start", "tool_result", "confirmation", "error", "done"
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LoopState:
    """Mutable state for a single agent loop invocation."""
    messages: List[Dict[str, Any]]
    iteration: int = 0
    tool_call_count: int = 0
    tokens_used: int = 0  # cumulative input + output tokens this loop
    start_time: float = 0.0
    tool_call_log: List[Dict[str, Any]] = field(default_factory=list)
    retry_counts: Dict[str, int] = field(default_factory=dict)  # tool_name -> retry count
    paused_for_confirmation: bool = False
    pending_tool: Optional[Dict[str, Any]] = None
    budget_warning_sent: bool = False


# --------------------------------------------------------------------------- #
#  Confirmation state persistence (Redis)                                     #
# --------------------------------------------------------------------------- #

def _state_key(room_id: int, user_id: int) -> str:
    return AGENT_STATE_KEY.format(room_id=room_id, user_id=user_id)


def save_loop_state(room_id: int, user_id: int, state: LoopState) -> None:
    """Persist the loop state to Redis so it survives a confirmation pause."""
    serialisable = {
        "messages": state.messages,
        "iteration": state.iteration,
        "tool_call_count": state.tool_call_count,
        "tokens_used": state.tokens_used,
        "tool_call_log": state.tool_call_log,
        "retry_counts": state.retry_counts,
        "pending_tool": state.pending_tool,
        "budget_warning_sent": state.budget_warning_sent,
    }
    cache.set(
        _state_key(room_id, user_id),
        json.dumps(serialisable, default=str),
        CONFIRMATION_STATE_TTL,
    )


def load_loop_state(room_id: int, user_id: int) -> Optional[LoopState]:
    """Load a previously paused loop state from Redis."""
    raw = cache.get(_state_key(room_id, user_id))
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    state = LoopState(
        messages=data.get("messages", []),
        iteration=data.get("iteration", 0),
        tool_call_count=data.get("tool_call_count", 0),
        tokens_used=data.get("tokens_used", 0),
        tool_call_log=data.get("tool_call_log", []),
        retry_counts=data.get("retry_counts", {}),
        pending_tool=data.get("pending_tool"),
        budget_warning_sent=data.get("budget_warning_sent", False),
    )
    return state


def clear_loop_state(room_id: int, user_id: int) -> None:
    cache.delete(_state_key(room_id, user_id))


# --------------------------------------------------------------------------- #
#  Helpers                                                                    #
# --------------------------------------------------------------------------- #

def _extract_text(content_blocks: List[Dict[str, Any]]) -> str:
    """Pull all text from a response's content blocks."""
    parts = []
    for block in content_blocks:
        if block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)


def _extract_tool_calls(content_blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pull all tool_use blocks from a response's content blocks."""
    calls = []
    for block in content_blocks:
        if block.get("type") == "tool_use":
            calls.append({
                "id": block.get("id", ""),
                "name": block.get("name", ""),
                "input": block.get("input", {}),
            })
    return calls


def _dedup_key(name: str, tool_input: Dict[str, Any]) -> str:
    """Hash a tool call for dedup within a single loop."""
    return f"{name}:{json.dumps(tool_input, sort_keys=True, default=str)}"


# --------------------------------------------------------------------------- #
#  Web search rate limiting                                                   #
# --------------------------------------------------------------------------- #

def _search_limit_key(user_id: int) -> str:
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    return f"search_limit:{user_id}:{today}"


def get_remaining_searches(user_id: Optional[int]) -> int:
    """Return how many web searches the user has left today."""
    if not user_id:
        return 0
    used = cache.get(_search_limit_key(user_id)) or 0
    return max(0, DAILY_SEARCH_LIMIT - int(used))


def _record_search_usage(user_id: Optional[int], count: int) -> None:
    """Increment the user's daily search counter."""
    if not user_id or count <= 0:
        return
    key = _search_limit_key(user_id)
    try:
        cache.incr(key, count)
    except ValueError:
        cache.set(key, count, 86400)


def _build_web_search_tool(user_id: Optional[int], user_location: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Build the Claude web_search server-side tool definition with rate-limited max_uses."""
    remaining = get_remaining_searches(user_id)
    if remaining <= 0:
        return None  # No budget left, don't offer the tool

    max_uses = min(remaining, MAX_SEARCHES_PER_REQUEST)

    tool: Dict[str, Any] = {
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": max_uses,
    }

    # Optionally localise results
    if user_location:
        tool["user_location"] = user_location

    return tool


def _count_search_uses(response: Dict[str, Any]) -> int:
    """Count how many web searches Claude made from the response usage block."""
    usage = response.get("usage", {})
    server_tool_use = usage.get("server_tool_use", {})
    return int(server_tool_use.get("web_search_requests", 0))


def _get_response_tokens(response: Dict[str, Any]) -> int:
    """Total tokens used in a single LLM response."""
    usage = response.get("usage", {})
    return int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))


# Max characters for a single tool result injected into the conversation
_MAX_RESULT_CHARS = 8000

# Patterns that might indicate a tool result is trying to inject instructions
_RESULT_INJECTION_RE = None


def _get_result_injection_re():
    global _RESULT_INJECTION_RE
    if _RESULT_INJECTION_RE is None:
        import re
        patterns = [
            r"ignore\s+(all|previous|system)\s+instructions",
            r"you\s+are\s+now\s+(in|a)\b",
            r"new\s+instructions?:",
            r"<\s*system\s*>",
            r"IMPORTANT:\s*override",
        ]
        _RESULT_INJECTION_RE = re.compile("|".join(patterns), re.IGNORECASE)
    return _RESULT_INJECTION_RE


def _sanitize_tool_result(result_json: str) -> str:
    """
    Sanitize a serialised tool result before injecting into the conversation.
    - Caps size to prevent token flooding
    - Strips potential prompt injection patterns from tool output
    """
    # Cap size
    if len(result_json) > _MAX_RESULT_CHARS:
        result_json = result_json[:_MAX_RESULT_CHARS] + '..."}'

    # Check for injection patterns in the result
    pattern = _get_result_injection_re()
    if pattern.search(result_json):
        logger.warning("Potential injection in tool result, stripping suspicious content")
        result_json = pattern.sub("[FILTERED]", result_json)

    return result_json


# --------------------------------------------------------------------------- #
#  Model routing                                                              #
# --------------------------------------------------------------------------- #

def _select_model(user_message: str, iteration: int) -> str:
    """
    Pick Haiku for simple single-tool requests, Sonnet for everything else.

    Heuristic: use Haiku on the FIRST iteration if the message is short and
    looks like a straightforward single action. Once we're in multi-turn
    (iteration > 1) always use Sonnet for better reasoning.
    """
    if iteration > 1:
        return MODEL_SONNET

    words = user_message.split()
    if len(words) > 25:
        return MODEL_SONNET

    # Multi-step indicators → Sonnet
    multi_step_words = {"then", "after", "next", "also", "and then", "both", "compare"}
    lowered = user_message.lower()
    if any(w in lowered for w in multi_step_words):
        return MODEL_SONNET

    return MODEL_HAIKU


async def _execute_with_timeout(
    tool_name: str,
    tool_input: Dict[str, Any],
    context: Dict[str, Any],
    timeout: float = TOOL_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """Execute a tool with a timeout wrapper."""
    try:
        return await asyncio.wait_for(
            execute_tool(tool_name, tool_input, context),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return {
            "status": "error",
            "message": f"Tool {tool_name} timed out after {timeout}s. "
                       f"The operation may still be processing.",
        }


async def _record_receipt(
    tool_name: str,
    tool_input: Dict[str, Any],
    result: Dict[str, Any],
    context: Dict[str, Any],
) -> None:
    """Record an action receipt for audit trail (best-effort)."""
    try:
        from orchestration.action_receipts import record_action_receipt, should_record_receipt
        from orchestration.action_catalog import get_action_definition
        if not should_record_receipt(tool_name):
            return
        action_def = get_action_definition(tool_name) or {}
        status = "success" if result.get("status") == "success" else "error"
        reason = result.get("message") or result.get("error") or ""
        data_payload = result.get("data") if isinstance(result.get("data"), dict) else {}
        await record_action_receipt(
            user_id=context.get("user_id"),
            room_id=context.get("room_id"),
            action=tool_name,
            service=action_def.get("service", ""),
            params=tool_input,
            result=data_payload or {"message": reason},
            status=status,
            reason=reason,
        )
    except Exception as exc:
        logger.debug("Action receipt skipped for %s: %s", tool_name, exc)


# --------------------------------------------------------------------------- #
#  Meta-tools (internal tools the agent can call)                             #
# --------------------------------------------------------------------------- #

META_TOOL_DEFINITIONS = [
    {
        "name": "delegate_task",
        "description": (
            "Delegate a sub-task to a focused assistant. Use this for complex requests "
            "that benefit from breaking into parts (e.g., comparing flights AND hotels "
            "separately, then combining results). The sub-assistant has access to the "
            "same tools but focuses on one specific goal."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Clear description of the sub-task to accomplish",
                },
                "tools": {
                    "type": "string",
                    "description": (
                        "Comma-separated list of tool names the sub-assistant should use. "
                        "If omitted, all tools are available."
                    ),
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "handoff_to_workflow",
        "description": (
            "Hand off a long-running or scheduled task to a durable workflow. "
            "Use this when a task needs monitoring over time (e.g., 'notify me when "
            "payment completes') or needs to run on a schedule. The workflow runs "
            "independently and will notify the user when done."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "What the workflow should accomplish",
                },
                "steps": {
                    "type": "string",
                    "description": (
                        "JSON array of workflow steps, each with 'service', 'action', "
                        "and 'parameters' fields"
                    ),
                },
                "trigger_type": {
                    "type": "string",
                    "description": "When to run: 'manual' (now), 'schedule' (cron), or 'webhook'",
                    "enum": ["manual", "schedule", "webhook"],
                },
                "schedule": {
                    "type": "string",
                    "description": "Cron expression if trigger_type is 'schedule' (e.g., '0 9 * * *' for daily 9am)",
                },
            },
            "required": ["description", "steps"],
        },
    },
]

# Sub-agent limits (tighter than parent)
SUB_AGENT_MAX_ITERATIONS = 5
SUB_AGENT_MAX_TOOL_CALLS = 8


async def _execute_meta_tool(
    tool_name: str,
    tool_input: Dict[str, Any],
    context: Dict[str, Any],
    preferences: Optional[Dict[str, Any]],
    parent_system: str,
    parent_tools: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Execute an internal meta-tool (delegate_task or handoff_to_workflow)."""

    if tool_name == "delegate_task":
        return await _run_sub_agent(
            tool_input, context, preferences, parent_system, parent_tools,
        )
    elif tool_name == "handoff_to_workflow":
        return await _create_workflow_handoff(tool_input, context)
    else:
        return {"status": "error", "message": f"Unknown meta-tool: {tool_name}"}


async def _run_sub_agent(
    tool_input: Dict[str, Any],
    context: Dict[str, Any],
    preferences: Optional[Dict[str, Any]],
    parent_system: str,
    parent_tools: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Spawn a focused sub-agent loop for a specific task."""
    task_description = tool_input.get("task", "")
    if not task_description:
        return {"status": "error", "message": "No task description provided."}

    # Filter tools if specified
    tools_filter = tool_input.get("tools", "")
    if tools_filter:
        allowed = {t.strip() for t in tools_filter.split(",")}
        scoped_tools = [t for t in parent_tools if t["name"] in allowed]
    else:
        scoped_tools = parent_tools

    # Run a sub-loop with tighter limits
    llm = get_llm_client()
    sub_messages: List[Dict[str, Any]] = [
        {"role": "user", "content": task_description},
    ]

    collected_text: List[str] = []
    sub_tool_log: List[Dict[str, Any]] = []
    iteration = 0

    while iteration < SUB_AGENT_MAX_ITERATIONS:
        iteration += 1
        if len(sub_tool_log) >= SUB_AGENT_MAX_TOOL_CALLS:
            break

        try:
            response = await llm.create_message(
                messages=sub_messages,
                system=parent_system,
                tools=scoped_tools if scoped_tools else None,
                temperature=0.3,
                max_tokens=4096,
                user_id=context.get("user_id"),
            )
        except Exception as exc:
            return {"status": "error", "message": f"Sub-agent LLM error: {exc}"}

        content_blocks = response.get("content", [])
        stop_reason = response.get("stop_reason", "end_turn")
        sub_messages.append({"role": "assistant", "content": content_blocks})

        text = _extract_text(content_blocks)
        if text:
            collected_text.append(text)

        if stop_reason == "end_turn":
            break

        if stop_reason == "tool_use":
            tool_calls = _extract_tool_calls(content_blocks)
            result_blocks = []
            for tc in tool_calls:
                result = await _execute_with_timeout(tc["name"], tc["input"], context)
                sub_tool_log.append({
                    "name": tc["name"],
                    "input": tc["input"],
                    "status": result.get("status"),
                })
                # Memory update
                try:
                    await update_memory_state(
                        context, action=tc["name"],
                        params=tc["input"], result=result,
                    )
                except Exception:
                    pass
                result_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": _sanitize_tool_result(json.dumps(result, default=str)),
                })
            sub_messages.append({"role": "user", "content": result_blocks})

    return {
        "status": "success",
        "summary": "\n".join(collected_text) or "Sub-task completed.",
        "tools_used": [e["name"] for e in sub_tool_log],
        "iterations": iteration,
    }


async def _create_workflow_handoff(
    tool_input: Dict[str, Any],
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """Create a Temporal or deferred workflow from agent instructions."""
    description = tool_input.get("description", "")
    steps_raw = tool_input.get("steps", "[]")
    trigger_type = tool_input.get("trigger_type", "manual")

    try:
        steps = json.loads(steps_raw) if isinstance(steps_raw, str) else steps_raw
    except (json.JSONDecodeError, TypeError):
        return {"status": "error", "message": "Invalid steps JSON."}

    if not isinstance(steps, list) or not steps:
        return {"status": "error", "message": "Steps must be a non-empty list."}

    try:
        from workflows.models import UserWorkflow
        from asgiref.sync import sync_to_async

        workflow_def = {
            "description": description,
            "steps": steps,
        }
        workflow = await sync_to_async(UserWorkflow.objects.create)(
            user_id=context.get("user_id"),
            name=description[:100],
            definition=workflow_def,
            status="active",
        )

        if trigger_type == "manual":
            from workflows.temporal_integration import start_workflow_execution
            execution = await start_workflow_execution(
                workflow,
                trigger_data={"source": "agent_handoff"},
                trigger_type="manual",
            )
            return {
                "status": "success",
                "message": f"Workflow '{description}' started (ID: {workflow.id}).",
                "workflow_id": workflow.id,
            }
        else:
            return {
                "status": "success",
                "message": (
                    f"Workflow '{description}' created (ID: {workflow.id}). "
                    f"Trigger type: {trigger_type}."
                ),
                "workflow_id": workflow.id,
            }

    except Exception as exc:
        logger.error("Workflow handoff failed: %s", exc, exc_info=True)
        return {"status": "error", "message": f"Failed to create workflow: {exc}"}


_META_TOOL_NAMES = {t["name"] for t in META_TOOL_DEFINITIONS}


# --------------------------------------------------------------------------- #
#  Main agent loop                                                            #
# --------------------------------------------------------------------------- #

async def run_agent_loop(
    *,
    user_message: str,
    context: Dict[str, Any],
    preferences: Optional[Dict[str, Any]] = None,
    context_prompt: str = "",
    memory_summary: str = "",
    history: Optional[List[Dict[str, Any]]] = None,
    resumed_state: Optional[LoopState] = None,
    confirmed_tool: bool = False,
) -> AsyncGenerator[AgentEvent, None]:
    """
    Run the agentic loop. Yields AgentEvent objects for the consumer to handle.

    Args:
        user_message: The user's natural language request.
        context: Dict with user_id, room_id, username, preferences.
        preferences: Normalised user preferences.
        context_prompt: Room context string from ContextManager.
        memory_summary: Entity/action memory summary.
        history: Previous conversation messages in Anthropic format.
        resumed_state: If resuming after a confirmation pause.
        confirmed_tool: True if the user just confirmed a pending tool.
    """
    llm = get_llm_client()
    user_id = context.get("user_id")
    room_id = context.get("room_id")
    user_caps = preferences or {}

    # Build tool definitions (filtered by user capabilities)
    tools = get_tool_definitions(
        user_capabilities=user_caps,
        exclude_actions=["search_info"],  # Replaced by Claude native web search
    )
    # Add meta-tools (delegate_task, handoff_to_workflow)
    tools.extend(META_TOOL_DEFINITIONS)

    # Add Claude native web search (server-side tool) with rate-limited budget
    user_location_hint = None
    if preferences:
        loc = preferences.get("location")
        tz = preferences.get("timezone")
        if loc or tz:
            user_location_hint = {"type": "approximate"}
            if loc:
                user_location_hint["city"] = loc
            if tz:
                user_location_hint["timezone"] = tz
    web_search_tool = _build_web_search_tool(user_id, user_location_hint)
    if web_search_tool:
        tools.append(web_search_tool)

    # Build system prompt
    system = build_system_prompt(
        preferences=preferences,
        context_prompt=context_prompt,
        memory_summary=memory_summary,
    )

    # ------------------------------------------------------------------ #
    #  Initialise or resume loop state                                    #
    # ------------------------------------------------------------------ #
    if resumed_state and confirmed_tool and resumed_state.pending_tool:
        state = resumed_state
        state.start_time = time.monotonic()
        state.paused_for_confirmation = False

        # Execute the previously paused tool
        pending = state.pending_tool
        state.pending_tool = None
        result = await _execute_with_timeout(
            pending["name"], pending["input"], context,
        )
        state.tool_call_count += 1
        state.tool_call_log.append({
            "name": pending["name"],
            "input": pending["input"],
            "output": result,
            "iteration": state.iteration,
        })

        yield AgentEvent("tool_result", {
            "name": pending["name"],
            "result": result,
        })

        # Memory update & receipt for confirmed tool
        try:
            await update_memory_state(
                context, action=pending["name"],
                params=pending["input"], result=result,
            )
        except Exception:
            pass
        await _record_receipt(pending["name"], pending["input"], result, context)

        # Append tool result to messages
        state.messages.append({
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": pending["id"],
                "content": _sanitize_tool_result(json.dumps(result, default=str)),
            }],
        })

    else:
        # Fresh loop
        messages: List[Dict[str, Any]] = []
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        state = LoopState(
            messages=messages,
            start_time=time.monotonic(),
        )

    # Track seen tool calls for dedup
    seen_calls: set = set()
    for entry in state.tool_call_log:
        seen_calls.add(_dedup_key(entry["name"], entry["input"]))

    # ------------------------------------------------------------------ #
    #  Main ReAct loop                                                    #
    # ------------------------------------------------------------------ #
    while state.iteration < MAX_ITERATIONS:
        # Timeout check
        elapsed = time.monotonic() - state.start_time
        if elapsed > LOOP_TIMEOUT_SECONDS:
            yield AgentEvent("error", {
                "message": "I ran out of time on this request. "
                           "Here's what I managed so far.",
            })
            break

        # Tool call budget check
        if state.tool_call_count >= MAX_TOOL_CALLS:
            yield AgentEvent("error", {
                "message": "I've reached the maximum number of tool calls "
                           "for this request.",
            })
            break

        state.iteration += 1

        # Emit thinking event so frontend shows reasoning in timeline
        yield AgentEvent("thinking", {"text": ""})

        # Token budget check
        if state.tokens_used >= LOOP_TOKEN_BUDGET:
            yield AgentEvent("error", {
                "message": "I've used up my thinking budget for this request. "
                           "Here's what I managed so far.",
            })
            break

        # ---- Call LLM (streaming) ------------------------------------ #
        selected_model = _select_model(user_message, state.iteration)

        # Accumulators for the streamed response
        content_blocks: List[Dict[str, Any]] = []
        collected_text: List[str] = []
        active_tool_blocks: Dict[int, Dict[str, Any]] = {}
        tool_json_accum: Dict[int, str] = {}
        stop_reason = "end_turn"
        response_usage: Dict[str, Any] = {}
        block_index = -1
        is_streaming_text = False

        try:
            async for event in llm.stream_message(
                messages=state.messages,
                system=system,
                tools=tools if tools else None,
                temperature=0.3,
                max_tokens=4096,
                user_id=user_id,
                model=selected_model,
                use_prompt_cache=True,
            ):
                etype = event.get("type", "")

                if etype == "text":
                    # Stream text deltas to the user in real-time
                    delta = event.get("text", "")
                    if delta:
                        collected_text.append(delta)
                        if not is_streaming_text:
                            is_streaming_text = True
                        yield AgentEvent("text_delta", {"text": delta})

                elif etype == "tool_use_start":
                    # A tool call is starting
                    is_streaming_text = False
                    block_index += 1

                elif etype == "tool_use_end":
                    # Tool call fully received
                    tc_block = {
                        "type": "tool_use",
                        "id": event.get("id", ""),
                        "name": event.get("name", ""),
                        "input": event.get("input", {}),
                    }
                    content_blocks.append(tc_block)

                elif etype == "message_done":
                    stop_reason = event.get("stop_reason", "end_turn")
                    response_usage = event.get("usage", {})

        except Exception as exc:
            logger.error("Agent loop LLM stream failed: %s", exc, exc_info=True)
            yield AgentEvent("error", {"message": f"LLM error: {exc}"})
            break

        # Build the full text block for conversation history
        full_text = "".join(collected_text)
        if full_text:
            content_blocks.insert(0, {"type": "text", "text": full_text})

        # Track token usage
        iter_tokens = int(response_usage.get("input_tokens", 0)) + int(response_usage.get("output_tokens", 0))
        state.tokens_used += iter_tokens

        if (
            not state.budget_warning_sent
            and state.tokens_used >= LOOP_TOKEN_BUDGET * LOOP_TOKEN_WARNING_RATIO
        ):
            state.budget_warning_sent = True
            yield AgentEvent("text_delta", {
                "text": "\n\n*(Running low on my thinking budget for this request.)*\n\n",
            })

        # Track web search usage
        server_tool_use = response_usage.get("server_tool_use", {})
        search_count = int(server_tool_use.get("web_search_requests", 0))
        if search_count > 0:
            _record_search_usage(user_id, search_count)
            record_event("web_search_used", {
                "user_id": user_id,
                "room_id": room_id,
                "searches": search_count,
                "remaining": get_remaining_searches(user_id),
            })

        # Append the assistant message to the conversation
        state.messages.append({"role": "assistant", "content": content_blocks})

        # ---- If LLM is done talking, exit the loop ------------------- #
        if stop_reason == "end_turn":
            break

        if stop_reason == "max_tokens":
            yield AgentEvent("error", {
                "message": "My response was cut short due to length limits.",
            })
            break

        # ---- Handle tool calls --------------------------------------- #
        if stop_reason == "tool_use":
            tool_calls = _extract_tool_calls(content_blocks)

            # Separate into safe (auto-execute) and needs-confirmation
            safe_calls: List[Dict[str, Any]] = []
            confirm_calls: List[Dict[str, Any]] = []

            for tc in tool_calls:
                risk = get_tool_risk_info(tc["name"], preferences)
                if risk["requires_confirmation"]:
                    confirm_calls.append(tc)
                else:
                    safe_calls.append(tc)

            # ---- Execute safe calls (possibly in parallel) ----------- #
            tool_results: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []

            if safe_calls:
                async def _run_safe(tc: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
                    dedup = _dedup_key(tc["name"], tc["input"])
                    if dedup in seen_calls:
                        # Return cached result from a previous identical call
                        cached = next(
                            (e["output"] for e in state.tool_call_log
                             if e["name"] == tc["name"]
                             and _dedup_key(e["name"], e["input"]) == dedup),
                            None,
                        )
                        if cached is not None:
                            return tc, cached
                        # If no cached result found, check retry budget
                        retries = state.retry_counts.get(tc["name"], 0)
                        if retries >= MAX_RETRIES_PER_TOOL:
                            return tc, {
                                "status": "error",
                                "message": f"Max retries ({MAX_RETRIES_PER_TOOL}) reached for {tc['name']}.",
                            }
                        state.retry_counts[tc["name"]] = retries + 1
                    seen_calls.add(dedup)
                    # Meta-tools use their own executor
                    if tc["name"] in _META_TOOL_NAMES:
                        result = await _execute_meta_tool(
                            tc["name"], tc["input"], context,
                            preferences, system, tools,
                        )
                    else:
                        result = await _execute_with_timeout(tc["name"], tc["input"], context)
                    # Track retries on error
                    if result.get("status") == "error":
                        state.retry_counts[tc["name"]] = state.retry_counts.get(tc["name"], 0) + 1
                    return tc, result

                # Run in parallel
                tasks = [_run_safe(tc) for tc in safe_calls]
                completed = await asyncio.gather(*tasks, return_exceptions=True)

                for item in completed:
                    if isinstance(item, Exception):
                        logger.error("Parallel tool error: %s", item)
                        continue
                    tc, result = item
                    tool_results.append((tc, result))
                    state.tool_call_count += 1
                    state.tool_call_log.append({
                        "name": tc["name"],
                        "input": tc["input"],
                        "output": result,
                        "iteration": state.iteration,
                    })
                    yield AgentEvent("tool_start", {"name": tc["name"], "input": tc["input"]})
                    yield AgentEvent("tool_result", {"name": tc["name"], "result": result})

                    # Memory update & receipt (fire-and-forget)
                    try:
                        await update_memory_state(
                            context, action=tc["name"],
                            params=tc["input"], result=result,
                        )
                    except Exception:
                        pass
                    await _record_receipt(tc["name"], tc["input"], result, context)

            # ---- Handle confirmation-required calls ------------------ #
            if confirm_calls:
                # Only handle the first one; the LLM will call the rest after
                tc = confirm_calls[0]
                state.pending_tool = tc
                state.paused_for_confirmation = True

                # Save state to Redis for resume
                if room_id and user_id:
                    save_loop_state(room_id, user_id, state)

                confirmation_text = build_confirmation_prompt(
                    tc["name"], tc["input"],
                )
                yield AgentEvent("confirmation", {
                    "message": confirmation_text,
                    "tool_name": tc["name"],
                    "tool_input": tc["input"],
                })
                # Pause the loop — consumer will resume after user confirms
                return

            # ---- Append all tool results to messages for next iteration #
            tool_result_blocks = []
            for tc, result in tool_results:
                tool_result_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": _sanitize_tool_result(json.dumps(result, default=str)),
                })

            if tool_result_blocks:
                state.messages.append({
                    "role": "user",
                    "content": tool_result_blocks,
                })

            # Continue the loop — LLM will see results and decide next step

    # ---- Loop finished ----------------------------------------------- #
    if room_id and user_id:
        clear_loop_state(room_id, user_id)

    elapsed = round(time.monotonic() - state.start_time, 2)

    # Save a completion summary to memory
    if state.tool_call_log:
        actions_done = [e["name"] for e in state.tool_call_log]
        summary = f"Completed: {', '.join(actions_done)} ({elapsed}s)"
        try:
            await save_memory_summary(context, summary)
        except Exception:
            pass

    # Full loop transcript for audit trail
    transcript = []
    for entry in state.tool_call_log:
        transcript.append({
            "tool": entry.get("name"),
            "input": entry.get("input"),
            "status": entry.get("output", {}).get("status") if isinstance(entry.get("output"), dict) else None,
            "iteration": entry.get("iteration"),
        })

    record_event("agent_loop_done", {
        "user_id": user_id,
        "room_id": room_id,
        "iterations": state.iteration,
        "tool_calls": state.tool_call_count,
        "tokens_used": state.tokens_used,
        "elapsed": elapsed,
        "tools_used": list({e["name"] for e in state.tool_call_log}),
        "transcript": transcript,
    })

    yield AgentEvent("done", {
        "iterations": state.iteration,
        "tool_calls": state.tool_call_count,
        "elapsed": elapsed,
    })


# --------------------------------------------------------------------------- #
#  Public helpers for the consumer                                            #
# --------------------------------------------------------------------------- #

def has_pending_agent_state(room_id: int, user_id: int) -> bool:
    """Check if there's a paused agent loop waiting for confirmation."""
    return load_loop_state(room_id, user_id) is not None


async def resume_after_confirmation(
    *,
    context: Dict[str, Any],
    preferences: Optional[Dict[str, Any]] = None,
    context_prompt: str = "",
    memory_summary: str = "",
) -> AsyncGenerator[AgentEvent, None]:
    """
    Resume a paused agent loop after the user confirms a high-risk action.
    """
    room_id = context.get("room_id")
    user_id = context.get("user_id")

    state = load_loop_state(room_id, user_id)
    if not state or not state.pending_tool:
        yield AgentEvent("error", {
            "message": "No pending action found to confirm.",
        })
        return

    clear_loop_state(room_id, user_id)

    async for event in run_agent_loop(
        user_message="",  # Not used on resume
        context=context,
        preferences=preferences,
        context_prompt=context_prompt,
        memory_summary=memory_summary,
        resumed_state=state,
        confirmed_tool=True,
    ):
        yield event


async def cancel_pending_action(
    room_id: int, user_id: int,
) -> Optional[str]:
    """Cancel a pending confirmation and clean up state."""
    state = load_loop_state(room_id, user_id)
    if not state or not state.pending_tool:
        return None
    tool_name = state.pending_tool.get("name", "action")
    clear_loop_state(room_id, user_id)
    return f"Cancelled the pending {tool_name.replace('_', ' ')}."
