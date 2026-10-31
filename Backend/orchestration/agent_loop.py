"""
ReAct-style Agent Loop for Kazi.

Replaces the linear parse → route → done pipeline with an autonomous
think → act → observe loop. The LLM decides which tools to call,
sees the results, and iterates until the task is complete.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from orchestration.agent_prompts import (
    NO_SEARCH_TOOL_NOTE,
    build_confirmation_prompt_many,
    build_system_prompt,
)
from orchestration.llm_client import TokenQuotaExceeded, _humanize_seconds, get_llm_client
from orchestration.memory_state import update_memory_state_batch
from orchestration.model_catalog import find_model, parse_model_id, provider_configured
from orchestration.security_policy import (
    RESTRICTED_PARAM_KEYS,
    redact_sensitive_text,
    sanitize_parameters,
)
from orchestration.telemetry import record_event
from orchestration.tool_executor import execute_tool, get_tool_risk_info, preview_tool
from orchestration.tool_record import tool_record_input
from orchestration.tool_schemas import get_tool_definitions
from orchestration.user_preferences import enforce_agent_caps

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
#  Constants                                                                  #
# --------------------------------------------------------------------------- #

# T-F3: one work-token budget per turn. "Work tokens" count what the model
# generates plus the input it newly reads (a new user message, tool results,
# anything appended since the previous call). The system prompt, the tool list
# and the history that are resent on every call are *not* work, so a long
# conversation no longer spends the budget just by being resent.
#
# These replace the old separate iteration, tool-call, shell-session and token
# caps as the thing that ends a normal turn. Remaining constants are hard
# runaway backstops, far above the bound; a healthy turn never reaches them.
DEFAULT_TURN_TOKEN_BUDGET = 100000
# The largest bound an operator may configure; a legacy install whose operator
# had the old caps switch off maps here, so it never silently shrinks.
DEFAULT_TURN_TOKEN_BUDGET_MAX = 500000
HARD_CAP_ITERATIONS = 10000
HARD_CAP_TOOL_CALLS = 10000
HARD_TURN_TIMEOUT_SECONDS = 1800  # wall-clock runaway guard (30 min)
TURN_TOKEN_WARNING_RATIO = 0.80  # tell the user once we pass this share
# A fenced owner stops before the next model call or batch, keeps what already
# ran, and says so honestly (#216b-1, E10).
LEASE_LOST_NOTE = (
    "[Harness: this turn was marked interrupted because its worker lost the "
    "lease. Nothing further was run; the steps above already happened.]\n\n"
)
# A response cut off at the model's output limit is an honest length ending,
# not an error: anything after the cut never ran.
LENGTH_LIMIT_NOTE = (
    "My response was cut short by the length limit. Anything I was still "
    "writing did not run."
)
# A delegated run gets a share of what its parent has left, never a fixed table.
DELEGATED_BUDGET_SHARE = 0.5
MIN_DELEGATED_BUDGET = 5000
# Anthropic returns stop_reason "pause_turn" while a server-side tool (its web
# search) is still running; the documented contract is to send the paused turn
# back unchanged so the model can finish. A provider that pauses forever must
# still end, so cap the run of consecutive pauses.
MAX_CONSECUTIVE_PAUSES = 3
MAX_RETRIES_PER_TOOL = 2
TOOL_TIMEOUT_SECONDS = 30
RETRY_BACKOFF_CAP_SECONDS = 300
CIRCUIT_BREAKER_FAILURE_THRESHOLD = 3
CIRCUIT_BREAKER_COOLDOWN_SECONDS = 60
CONFIRMATION_STATE_TTL = 600  # 10 minutes
AGENT_STATE_KEY = "orchestration:agent_state:{room_id}:{user_id}"
AGENT_LOOP_APPROVAL_KIND = "agent_loop"


def _pause_ttl_seconds() -> int:
    """One clock for every pause (D7)."""
    from orchestration.turns import pause_ttl_seconds

    return pause_ttl_seconds()


# Web search rate limits
DAILY_SEARCH_LIMIT = 10
MAX_SEARCHES_PER_REQUEST = 5

# The meta-tool the model calls to ask the user for more of the turn's budget.
REQUEST_MORE_BUDGET_TOOL = "request_more_budget"

# C3: a persona lookup that fails removes tool reach for the turn. The model is
# told why as harness data, never left to invent a reason (S10).
PERSONA_LOOKUP_UNAVAILABLE_NOTE = (
    "[Harness: tools are unavailable this turn because this room's settings "
    "could not be checked. Do not claim that a tool ran. Tell the user plainly "
    "that the room's settings could not be checked and that tools are "
    "unavailable until they try again.]"
)
PERSONA_LOOKUP_DENIED_REASON = (
    "Refused: tools are unavailable this turn because this room's settings "
    "could not be checked."
)


# --------------------------------------------------------------------------- #
#  Data structures                                                            #
# --------------------------------------------------------------------------- #

@dataclass
class AgentEvent:
    """A single event yielded by the agent loop to the consumer."""
    kind: str  # "text", "harness", "tool_start", "tool_result", "confirmation", "error", "done"
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LoopState:
    """Mutable state for a single agent loop invocation."""
    messages: List[Dict[str, Any]]
    iteration: int = 0
    tool_call_count: int = 0
    tokens_used: int = 0  # cumulative *work* tokens this turn (T-F3)
    start_time: float = 0.0
    tool_call_log: List[Dict[str, Any]] = field(default_factory=list)
    retry_counts: Dict[str, int] = field(default_factory=dict)  # call digest -> retry count
    paused_for_confirmation: bool = False
    pending_tool: Optional[Dict[str, Any]] = None
    pending_tier: Optional[str] = None
    # D9: every gated call in one model step is held and resolved together.
    pending_tools: List[Dict[str, Any]] = field(default_factory=list)
    pending_tiers: List[Optional[str]] = field(default_factory=list)
    # Results from the same model step that were not gated. They are kept with
    # the pause so resume/decline/expiry emit one paired user message.
    pending_step_results: List[Dict[str, Any]] = field(default_factory=list)
    # Hashes of the exact arguments shown, kept only in the encrypted state.
    pending_args_hashes: Dict[str, str] = field(default_factory=dict)
    # Calls already dispatched before a crash; they are never retried (F8).
    dispatched_calls: List[str] = field(default_factory=list)
    approval_short_id: str = ""
    budget_warning_sent: bool = False
    # T-F3: the single work-token bound for this turn, the baseline total input
    # of the previous call (so only newly-read input counts), how the turn
    # ended, and whether it is paused waiting for the user to grant more budget.
    token_budget: int = 0
    prev_input_tokens: int = 0
    end_reason: str = ""
    awaiting_budget: bool = False
    # T-F3b: the pause was the hourly token quota, not the turn's own budget, so
    # resuming it must not be read as a budget grant.
    awaiting_quota: bool = False
    budget_extensions: int = 0
    # v0.7 (#170): set once output from an untrusted source (shell, web
    # search) enters context. Tainted runs escalate external-write and
    # credential-scoped actions to a durable approval one tier up.
    tainted: bool = False
    # P3-3 time accounting: active_seconds is the sum of the segments the turn
    # actually ran; wall_started_at is the epoch when the turn began. A human
    # wait is wall time, never active time, so the hard backstop cannot fire
    # the moment a long-paused turn resumes.
    active_seconds: float = 0.0
    wall_started_at: float = 0.0
    segment_started_monotonic: float = 0.0


# --------------------------------------------------------------------------- #
#  Confirmation state persistence (Redis)                                     #
# --------------------------------------------------------------------------- #

def _state_key(room_id: int, user_id: int) -> str:
    return AGENT_STATE_KEY.format(room_id=room_id, user_id=user_id)


def loop_state_payload(state: LoopState) -> Dict[str, Any]:
    """The JSON body of a paused loop state (encrypted before it is stored)."""
    return {
        "messages": state.messages,
        "iteration": state.iteration,
        "tool_call_count": state.tool_call_count,
        "tokens_used": state.tokens_used,
        "tool_call_log": state.tool_call_log,
        "retry_counts": state.retry_counts,
        "pending_tool": state.pending_tool,
        "pending_tier": state.pending_tier,
        "pending_tools": state.pending_tools,
        "pending_tiers": state.pending_tiers,
        "pending_step_results": state.pending_step_results,
        "pending_args_hashes": state.pending_args_hashes,
        "dispatched_calls": state.dispatched_calls,
        "approval_short_id": state.approval_short_id,
        "budget_warning_sent": state.budget_warning_sent,
        "tainted": state.tainted,
        "token_budget": state.token_budget,
        "prev_input_tokens": state.prev_input_tokens,
        "end_reason": state.end_reason,
        "awaiting_budget": state.awaiting_budget,
        "awaiting_quota": state.awaiting_quota,
        "budget_extensions": state.budget_extensions,
        "active_seconds": state.active_seconds,
        "wall_started_at": state.wall_started_at,
    }


def loop_state_from_payload(data: Dict[str, Any]) -> LoopState:
    state = LoopState(
        messages=data.get("messages", []),
        iteration=data.get("iteration", 0),
        tool_call_count=data.get("tool_call_count", 0),
        tokens_used=data.get("tokens_used", 0),
        tool_call_log=data.get("tool_call_log", []),
        retry_counts=data.get("retry_counts", {}),
        pending_tool=data.get("pending_tool"),
        pending_tier=data.get("pending_tier"),
        pending_tools=list(data.get("pending_tools") or []),
        pending_tiers=list(data.get("pending_tiers") or []),
        pending_step_results=list(data.get("pending_step_results") or []),
        pending_args_hashes=dict(data.get("pending_args_hashes") or {}),
        dispatched_calls=list(data.get("dispatched_calls") or []),
        approval_short_id=str(data.get("approval_short_id") or ""),
        budget_warning_sent=data.get("budget_warning_sent", False),
        tainted=bool(data.get("tainted", False)),
        token_budget=data.get("token_budget", 0),
        prev_input_tokens=data.get("prev_input_tokens", 0),
        end_reason=data.get("end_reason", ""),
        awaiting_budget=bool(data.get("awaiting_budget", False)),
        awaiting_quota=bool(data.get("awaiting_quota", False)),
        budget_extensions=data.get("budget_extensions", 0),
        active_seconds=float(data.get("active_seconds") or 0.0),
        wall_started_at=float(data.get("wall_started_at") or 0.0),
    )
    if not state.pending_tools and state.pending_tool:
        state.pending_tools = [state.pending_tool]
        state.pending_tiers = [state.pending_tier]
    state.segment_started_monotonic = time.monotonic()
    return state


def save_loop_state(room_id: int, user_id: int, state: LoopState, ttl: Optional[int] = None) -> None:
    """Legacy cache persistence, used only when a turn has no durable record.

    A production pause is stored on the Turn; this stays for callers and tests
    that run the loop without a Turn.
    """
    cache.set(
        _state_key(room_id, user_id),
        json.dumps(loop_state_payload(state), default=str),
        ttl or CONFIRMATION_STATE_TTL,
    )


def load_loop_state(room_id: int, user_id: int) -> Optional[LoopState]:
    """Legacy cache load, used only when no paused Turn exists."""
    raw = cache.get(_state_key(room_id, user_id))
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return loop_state_from_payload(data)


def clear_loop_state(room_id: int, user_id: int) -> None:
    cache.delete(_state_key(room_id, user_id))


# delegate_task is included because a sub-agent's summary can carry text from
# anything the sub-agent read. search_info is included because a registered
# search backend returns web text, the same untrusted source as web_search.
_UNTRUSTED_TOOL_NAMES = {
    "run_command", "run_shell", "shell_command",
    "web_search", "search_info", "delegate_task",
}
_SHELL_TOOL_NAMES = {"run_command", "run_shell", "shell_command"}


def _untrusted_source(tool_name: str) -> bool:
    """Output from these tools is untrusted text that taints the run (#170)."""
    return str(tool_name or "").strip().lower() in _UNTRUSTED_TOOL_NAMES


# Room-wide, not per user: chat history is shared by every member of a room,
# so untrusted text one member's run pulled in reaches everyone's next run.
AGENT_TAINT_KEY = "agent_loop_taint:{room_id}"
_DEFAULT_TAINT_TTL = 900


def _taint_ttl() -> int:
    """Seconds a room stays tainted. 0 turns persistence off; junk means default."""
    try:
        value = int(getattr(settings, "AGENT_TAINT_TTL_SECONDS", _DEFAULT_TAINT_TTL))
    except (TypeError, ValueError):
        return _DEFAULT_TAINT_TTL
    return value if value >= 0 else _DEFAULT_TAINT_TTL


def _conversation_tainted(room_id: Optional[int]) -> bool:
    """Whether an earlier turn in this room left untrusted text behind.

    Taint outlives the run that picked it up: the text is still in the chat
    history, so a new message must not reset the gate. Fail closed.
    """
    if not room_id or not _taint_ttl():
        return False
    try:
        return bool(cache.get(AGENT_TAINT_KEY.format(room_id=room_id)))
    except Exception:
        return True


def _taint_run(state: "LoopState", room_id: Optional[int]) -> None:
    """Taint this run and the room. The window restarts on each new taint."""
    state.tainted = True
    ttl = _taint_ttl()
    if not room_id or not ttl:
        return
    try:
        cache.set(AGENT_TAINT_KEY.format(room_id=room_id), 1, ttl)
    except Exception as exc:
        # The next turn would start untainted. Make that loud, not silent.
        logger.error("Could not persist room taint for room %s: %s", room_id, exc)
        try:
            record_event("taint_persist_failed", {"room_id": room_id})
        except Exception:
            pass


def _taint_gated(action: str) -> bool:
    """External-write / credential-scoped actions that a tainted run escalates."""
    from orchestration.action_catalog import is_high_risk_action, resolve_action_alias
    from orchestration.action_receipts import requires_confirmation

    resolved = resolve_action_alias(action)
    return requires_confirmation(resolved) or is_high_risk_action(resolved)


async def _resolve_persona(room_id: Optional[int], user_id: Optional[int]):
    """The active room persona, or None when the room has none.

    A lookup failure returns ``PERSONA_LOOKUP_FAILED`` instead of None: the loop
    must be able to remove tool reach for the turn (AGENTS.md §0.3). Logged
    once per call with the exception class only — never its message.
    """
    if not room_id or not user_id:
        return None
    from orchestration.personas import (
        PERSONA_LOOKUP_FAILED,
        PersonaLookupError,
        lookup_room_persona,
    )

    try:
        return await sync_to_async(lookup_room_persona)(room_id, user_id)
    except PersonaLookupError as exc:
        cause_type = exc.cause_type
    except Exception as exc:
        cause_type = type(exc).__name__
    logger.error("Persona lookup failed for room %s: %s", room_id, cause_type)
    record_event("persona_lookup_failed", {
        "reason": "persona_lookup_failed",
        "room_id": room_id,
        "user_id": user_id,
        "error_class": cause_type,
    })
    return PERSONA_LOOKUP_FAILED


_HANDOFF_TOOL_NAME = "handoff_to_workflow"


def _is_scheduled_handoff(tc: Dict[str, Any]) -> bool:
    """A handoff that creates anything but a one-off run must be confirmed.

    A standing schedule is persistence, so no stored rule, autopilot window or
    tainted run may let it start without the user's decision.
    """
    if tc.get("name") != _HANDOFF_TOOL_NAME:
        return False
    tool_input = tc.get("input")
    if not isinstance(tool_input, dict):
        return False
    return str(tool_input.get("trigger_type") or "manual") != "manual"


def _fill_handoff_timezone(
    tc: Dict[str, Any],
    preferences: Optional[Dict[str, Any]],
) -> None:
    """Resolve the schedule's timezone before the prompt so it shows the stored value."""
    tool_input = tc.get("input")
    if not isinstance(tool_input, dict) or tool_input.get("timezone"):
        return
    timezone = preferences.get("timezone") if isinstance(preferences, dict) else None
    tool_input["timezone"] = timezone or "UTC"


def _bucket_tool_calls(
    tool_calls: List[Dict[str, Any]],
    preferences: Optional[Dict[str, Any]],
    persona=None,
    tainted: bool = False,
) -> Tuple[List[Dict[str, Any]], List[Tuple[Dict[str, Any], Optional[str]]], List[Tuple[Dict[str, Any], str]]]:
    """Split tool calls into ``(auto, pause, denied)`` for the loop.

    ``pause`` carries each call's shell tier (``None`` for non-shell tools) so
    the loop can route ``bounded`` -> inline and ``destructive`` -> durable.
    ``denied`` carries a human-readable refusal reason. A persona narrows the
    scope deterministically (#203); a tainted run escalates external-write
    and credential-scoped actions so no stored rule can bypass them (#170). A
    schedule handoff is always confirmed, whatever the rules or taint (#222).
    """
    auto: List[Dict[str, Any]] = []
    pause: List[Tuple[Dict[str, Any], Optional[str]]] = []
    denied: List[Tuple[Dict[str, Any], str]] = []
    if persona is not None:
        from orchestration.personas import PERSONA_LOOKUP_FAILED

        if persona is PERSONA_LOOKUP_FAILED:
            # The room's bounds are unknown, so nothing may run this turn.
            denied.extend((tc, PERSONA_LOOKUP_DENIED_REASON) for tc in tool_calls)
            return auto, pause, denied
    risk_preferences = _taint_preferences(preferences, tainted)
    for tc in tool_calls:
        risk = get_tool_risk_info(tc["name"], risk_preferences, tc.get("input"))
        if persona is not None:
            from orchestration.action_catalog import resolve_action_alias
            from orchestration.personas import apply_persona_bounds, persona_bounds

            risk, denial_reason = apply_persona_bounds(
                risk, persona_bounds(persona), resolve_action_alias(tc["name"]),
            )
            if denial_reason:
                denied.append((tc, denial_reason))
                continue
        tier = risk.get("shell_tier")
        if tier == "denied":
            denied.append((tc, "Refused: this command is not allowed under the active shell profile."))
        elif _is_scheduled_handoff(tc):
            _fill_handoff_timezone(tc, preferences)
            pause.append((tc, tier))
        elif tainted and tc["name"] not in _SHELL_TOOL_NAMES and _taint_gated(tc["name"]):
            # One tier up: a tainted run never lets a stored rule skip the ask.
            # Shell is excluded: its risk fn already gates a tainted egress
            # step, and a command with no way out of the sandbox cannot leak.
            pause.append((tc, tier))
        elif risk.get("requires_confirmation"):
            pause.append((tc, tier))
        else:
            auto.append(tc)
    return auto, pause, denied


def _taint_preferences(preferences: Optional[Dict[str, Any]], tainted: bool) -> Optional[Dict[str, Any]]:
    """Surface the run's taint to the pure shell risk fn. Only the loop sets it."""
    if not tainted:
        if isinstance(preferences, dict) and "_shell_tainted" in preferences:
            return {k: v for k, v in preferences.items() if k != "_shell_tainted"}
        return preferences
    marked = dict(preferences) if isinstance(preferences, dict) else {}
    marked["_shell_tainted"] = True
    return marked


def _shell_approval_basis(tc: Dict[str, Any], preferences: Optional[Dict[str, Any]], tainted: bool) -> str:
    """Why an auto-run shell command needed no prompt (recorded on its receipt)."""
    if tc.get("name") not in _SHELL_TOOL_NAMES:
        return ""
    risk = get_tool_risk_info(tc["name"], _taint_preferences(preferences, tainted), tc.get("input"))
    return str(risk.get("approval_basis") or "")


def _turn_token_budget(caps_enforced: bool) -> int:
    """The single per-turn work-token bound for this install (T-F3).

    The bound is configurable (``AGENT_TURN_TOKEN_BUDGET``) and clamped to the
    largest bound an operator may choose (``AGENT_TURN_TOKEN_BUDGET_MAX``). A
    legacy install whose operator once turned the old caps switch off maps to
    the largest bound, so it never silently gets a small budget. Nothing here
    sizes the bound from the kind of task.
    """
    try:
        normal = int(getattr(settings, "AGENT_TURN_TOKEN_BUDGET", DEFAULT_TURN_TOKEN_BUDGET))
    except (TypeError, ValueError):
        normal = DEFAULT_TURN_TOKEN_BUDGET
    try:
        ceiling = int(getattr(settings, "AGENT_TURN_TOKEN_BUDGET_MAX", DEFAULT_TURN_TOKEN_BUDGET_MAX))
    except (TypeError, ValueError):
        ceiling = DEFAULT_TURN_TOKEN_BUDGET_MAX
    if ceiling < 1:
        ceiling = DEFAULT_TURN_TOKEN_BUDGET_MAX
    if not caps_enforced:
        return ceiling
    if normal < 1:
        normal = DEFAULT_TURN_TOKEN_BUDGET
    return min(normal, ceiling)


def _delegated_budget(state: "LoopState") -> int:
    """The work-token budget a delegated run may spend (T-F3).

    A share of what the parent has left. The share never exceeds that remainder
    (a share plus a floor could outspend the parent), so a second delegation
    sees less and the delegations together cannot exceed the turn. A parent
    with less than ``MIN_DELEGATED_BUDGET`` left grants nothing: the delegation
    is refused rather than run on a share too small to do real work.
    """
    remaining = max(0, state.token_budget - state.tokens_used)
    if remaining < MIN_DELEGATED_BUDGET:
        return 0
    return min(remaining, int(remaining * DELEGATED_BUDGET_SHARE))


def _reported_tokens(result: Any) -> Optional[int]:
    """The work tokens a delegated result reports, or None when unusable."""
    if not isinstance(result, dict):
        return None
    try:
        return int(result["tokens_used"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def _apply_delegated_spend(
    state: "LoopState",
    result: Any,
    granted: Optional[int] = None,
) -> None:
    """Charge a delegated run's work tokens to the parent turn (T-F3).

    A ``delegate_task`` result carries the work tokens the sub-run spent. The
    parent's single bound only limits the turn if that spending is counted, so
    a second delegation sees what is really left. A result with no usable spend
    — a sub-run that failed before it could report, or returned a non-numeric
    value — is charged the share the parent granted, never zero: a delegation
    that consumed the turn's budget must not look unspent. Fails closed.
    """
    spent = _reported_tokens(result)
    if spent is None or spent < 0:
        spent = granted or 0
    if spent > 0:
        state.tokens_used += spent


def _granted_sub_budget(context: Dict[str, Any]) -> Optional[int]:
    """The delegated budget the harness granted, or None if it granted none.

    ``_meta_tool_context`` always sets ``sub_agent_budget`` for ``delegate_task``
    (0 when the parent has too little left). A context without the key — a
    handoff, a direct call — is not a refusal; ``_run_sub_agent`` falls back.
    """
    if "sub_agent_budget" not in context:
        return None
    try:
        return int(context.get("sub_agent_budget") or 0)
    except (TypeError, ValueError, OverflowError):
        return None


def _meta_tool_context(
    tool_name: str,
    context: Dict[str, Any],
    state: "LoopState",
    delegated_grant: Optional[int] = None,
) -> Dict[str, Any]:
    """Context for a meta-tool call, granting a delegated run its budget share.

    ``delegated_grant`` is the exact share a batch planner already set aside for
    this call; when it is omitted (a lone call, a resume) the call computes its
    own share of what is left.
    """
    if tool_name != "delegate_task":
        return context
    child = dict(context)
    if delegated_grant is None:
        child["sub_agent_budget"] = _delegated_budget(state)
    else:
        child["sub_agent_budget"] = int(delegated_grant)
    return child


def _batch_delegated_grants(
    state: "LoopState",
    safe_calls: List[Dict[str, Any]],
) -> Dict[int, int]:
    """Grants for the ``delegate_task`` calls that share one parallel batch (T-F3).

    The calls in a batch run together, so each would compute its share from the
    same untouched remainder and N calls could be granted several times what the
    parent has left. Divide one delegated share of the remainder among them
    instead, so the grants together never exceed the turn's bound. A batch too
    large to give every call a real share refuses the excess (grant 0) rather
    than handing each a sliver below the minimum. A lone call keeps its share.
    """
    positions = [
        index for index, tc in enumerate(safe_calls)
        if tc.get("name") == "delegate_task"
    ]
    if not positions:
        return {}
    total = _delegated_budget(state)
    if total <= 0:
        return {index: 0 for index in positions}
    fundable = max(1, total // MIN_DELEGATED_BUDGET)
    funded = min(len(positions), fundable)
    per_call = total // funded
    grants = {index: per_call for index in positions[:funded]}
    grants.update({index: 0 for index in positions[funded:]})
    return grants


def _is_policy_block(output: Any) -> bool:
    """True when a tool result is a safety-policy block (terminal, don't retry)."""
    return (
        isinstance(output, dict)
        and output.get("status") == "error"
        and "safety policy" in str(output.get("message") or "")
    )


HARNESS_REFUSAL_KEY = "harness_refusal"


def _is_harness_refusal(output: Any) -> bool:
    """True when the harness itself refused to run a call (T-F3).

    A budget refusal is the harness declining to spend, not the service
    failing, so it must not count toward the retry cap or the circuit breaker.
    Any result the harness marks with ``harness_refusal`` is treated the same.
    """
    return isinstance(output, dict) and bool(output.get(HARNESS_REFUSAL_KEY))


# --------------------------------------------------------------------------- #
#  Durable approval records (WorkflowApprovalRecord)                          #
# --------------------------------------------------------------------------- #

def _pending_approval_record(room_id: int, user_id: int, *, turn_id=None):
    """Return the pending, unexpired agent-loop approval row, or None.

    Sync-only helper — callers wrap it in ``sync_to_async``. Only
    ``agent_loop``-kind rows scoped to (room, user) are considered, so one
    user can never see or act on another user's pending approval.
    """
    from workflows.models import WorkflowApprovalRecord

    now = timezone.now()
    candidates = WorkflowApprovalRecord.objects.filter(
        kind=AGENT_LOOP_APPROVAL_KIND,
        room_id=room_id,
        requested_by_id=user_id,
        status="pending",
    )
    if turn_id:
        candidates = candidates.filter(turn_id=turn_id)
    for record in candidates:
        if record.expires_at is None or record.expires_at > now:
            return record
    return None


def _args_hash(tool_input: Any) -> str:
    """Stable hash of the exact arguments shown when the pause was made."""
    body = json.dumps(tool_input if isinstance(tool_input, dict) else {}, sort_keys=True, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _display_params(tool_input: Any) -> Dict[str, Any]:
    """Sanitised display values only; raw input never lands in the row."""
    if not isinstance(tool_input, dict):
        return {}
    return tool_record_input(tool_input)


_RESTRICTED_SHOWN_AS = "[secret value, not sent]"


def _approval_display_value(value: Any) -> Any:
    """The exact input shown for approval, except restricted values.

    What the user approves is what runs, so ordinary values are not redacted
    or shortened here. A value under a restricted key is dropped before
    dispatch, so it is shown as a visible placeholder that keeps the key.
    """
    if isinstance(value, dict):
        return {
            key: (
                _RESTRICTED_SHOWN_AS
                if key in RESTRICTED_PARAM_KEYS
                else _approval_display_value(nested)
            )
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [_approval_display_value(item) for item in value]
    return value


def _approval_display_params(tool_input: Any) -> Dict[str, Any]:
    if not isinstance(tool_input, dict):
        return {}
    return _approval_display_value(tool_input)


def _approval_prompt_calls(calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "name": str(call.get("name") or ""),
            "input": _approval_display_params(call.get("input")),
        }
        for call in calls
    ]


def _approval_prompt_for_calls(
    calls: List[Dict[str, Any]], short_id: str = "",
) -> str:
    if not calls:
        return ""
    return build_confirmation_prompt_many(
        _approval_prompt_calls(calls), short_id=short_id,
    )


def _approval_prompt_for_state(state: Optional[LoopState]) -> str:
    if state is None:
        return ""
    calls = list(
        state.pending_tools
        or ([state.pending_tool] if state.pending_tool else [])
    )
    return _approval_prompt_for_calls(calls, state.approval_short_id)


def _display_calls(calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Per-call display rows for the plaintext columns (no raw arguments)."""
    return [
        {
            "tool_call_id": str(call.get("id") or ""),
            "tool": str(call.get("name") or ""),
            "params": _display_params(call.get("input")),
            "args_hash": _args_hash(call.get("input")),
        }
        for call in calls
    ]


def _calls_metadata(calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Clear metadata: only call ids and tool names, never arguments or hashes."""
    return [
        {
            "tool_call_id": str(call.get("id") or ""),
            "tool": str(call.get("name") or ""),
        }
        for call in calls
    ]


def _display_effects(effects: Optional[List[str]]) -> Optional[List[str]]:
    if effects is None:
        return None
    return [redact_sensitive_text(str(effect)) for effect in effects]


async def save_pending_confirmation(
    room_id: int,
    user_id: int,
    tool: Dict[str, Any],
    confirmation_text: str,
    effects: Optional[List[str]] = None,
    *,
    turn_id=None,
    calls: Optional[List[Dict[str, Any]]] = None,
    expires_at=None,
    short_id: str = "",
) -> Optional[int]:
    """Durably persist one held batch of high-risk calls (one row per batch)."""

    def _create():
        from workflows.models import WorkflowApprovalRecord

        now = timezone.now()
        calls_list = list(calls or [tool])
        display_calls = _display_calls(calls_list)
        safe_effects = _display_effects(effects)
        expires = expires_at or (now + timedelta(seconds=CONFIRMATION_STATE_TTL))
        defaults = {
            "workflow_id": None,
            "execution_id": None,
            "turn_id": turn_id,
            "step_id": f"agent_loop:{room_id}:{user_id}",
            "service": "",
            "action": tool.get("name", ""),
            # The row is plaintext: keep only the sanitised, redacted display
            # of every call. Raw arguments live in the encrypted Turn state.
            "approval_message": redact_sensitive_text(confirmation_text),
            "sanitized_params": {
                "calls": [
                    {
                        "tool_call_id": entry["tool_call_id"],
                        "tool": entry["tool"],
                        "params": entry["params"],
                    }
                    for entry in display_calls
                ],
            },
            "expires_at": expires,
            "metadata": {
                "tool_id": tool.get("id", ""),
                "effects": safe_effects,
                "calls": _calls_metadata(calls_list),
                "short_id": short_id,
            },
        }
        record, created = WorkflowApprovalRecord.objects.get_or_create(
            kind=AGENT_LOOP_APPROVAL_KIND,
            room_id=room_id,
            requested_by_id=user_id,
            status="pending",
            defaults=defaults,
        )
        if not created and (
            record.expires_at is None
            or record.expires_at <= now
            or str(record.turn_id) != str(turn_id)
        ):
            # Refresh a stale-or-other-turn row so this pause owns the row.
            # created_at is the consent anchor, so a reused row must not keep
            # the timestamp of the prompt it used to represent.
            for attr_name, value in defaults.items():
                setattr(record, attr_name, value)
            record.created_at = now
            record.save(update_fields=[*defaults.keys(), "created_at"])
        return record.id

    return await sync_to_async(_create)()


async def _resolve_pending_approval(
    room_id: int,
    user_id: int,
    status: str,
    reviewed_by_id: Optional[int] = None,
    comment: str = "",
    *,
    turn_id=None,
) -> None:
    """Move a pending agent-loop approval to a terminal status (durable)."""

    def _update():
        record = _pending_approval_record(room_id, user_id, turn_id=turn_id)
        if record is None:
            return
        record.status = status
        record.reviewed_by_id = reviewed_by_id or record.reviewed_by_id
        record.reviewed_at = timezone.now()
        if comment:
            record.review_comment = comment
        record.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_comment"])

    await sync_to_async(_update)()


def _consume_pending_approval_sync(
    room_id: int, user_id: int, reviewer_id: int, turn_id=None,
) -> bool:
    """Atomically flip the pending agent-loop approval to ``approved``.

    Returns True only for the caller that performed the transition, so a
    double-confirm can never execute the tool twice.
    """
    from workflows.models import WorkflowApprovalRecord

    query = WorkflowApprovalRecord.objects.filter(
        kind=AGENT_LOOP_APPROVAL_KIND,
        room_id=room_id,
        requested_by_id=user_id,
        status="pending",
    )
    if turn_id:
        query = query.filter(turn_id=turn_id)
    updated = query.update(
        status="approved",
        reviewed_by_id=reviewer_id,
        reviewed_at=timezone.now(),
    )
    return updated == 1


def _pending_approval_metadata(room_id: int, user_id: int) -> Dict[str, Any]:
    record = _pending_approval_record(room_id, user_id)
    if record is None:
        return {}
    return {
        "message": record.approval_message,
        "short_id": str((record.metadata or {}).get("short_id") or ""),
        "action": record.action,
        "turn_id": str(record.turn_id) if record.turn_id else "",
    }


def _load_turn_state_sync(turn) -> Optional[LoopState]:
    from orchestration import turns as turns_mod

    payload = turns_mod.get_turn_paused_state(turn)
    if payload is None:
        return None
    return loop_state_from_payload(payload)


async def load_pending_state(
    room_id: int, user_id: int, *, turn_id=None,
) -> Optional[LoopState]:
    """The paused Turn's decrypted state, else the legacy cache state.

    ``turn_id`` reads a Turn the coordinator has already moved to ``running``.
    A Turn that exists but cannot be decrypted returns None and the caller
    denies the resume (fail closed).
    """
    from orchestration import turns as turns_mod

    if turn_id:
        turn = await turns_mod.aget_turn(turn_id)
        if turn is None:
            return None
    else:
        turn = await turns_mod.aget_paused_turn(room_id, user_id, include_expired=True)
    if turn is not None:
        if turn.state == "paused" and turn.pause_expires_at and turn.pause_expires_at <= timezone.now():
            return None
        state = await sync_to_async(_load_turn_state_sync)(turn)
        if state is not None:
            return state
        # A Turn exists but has no readable state: fail closed, no cache fallback.
        return None
    return await sync_to_async(load_loop_state)(room_id, user_id)


async def _load_resume_state(context: Dict[str, Any]):
    """Load the current Turn's state; a resume always has a Turn."""
    return await load_pending_state(
        context.get("room_id"),
        context.get("user_id"),
        turn_id=context.get("turn_id"),
    )


async def _persist_pause_state(
    context: Dict[str, Any],
    state: LoopState,
    pause_kind: str,
    *,
    ttl: Optional[int] = None,
) -> bool:
    """Persist a pause on the Turn (encrypted), or the legacy cache.

    Returns False only when a Turn exists but its state could not be stored:
    the caller must then refuse to leave a gated action pending.
    """
    from orchestration import turns as turns_mod

    room_id = context.get("room_id")
    user_id = context.get("user_id")
    turn_id = context.get("turn_id")
    state.segment_started_monotonic = time.monotonic()
    if turn_id:
        turn = await turns_mod.aget_turn(turn_id)
        if turn is None:
            return False
        ok = await turns_mod.apause_turn(
            str(turn.pk),
            turn.version,
            state_payload=loop_state_payload(state),
            pause_kind=pause_kind,
            ttl=ttl,
            tainted=state.tainted,
            budget={"token_budget": state.token_budget},
            active_seconds=state.active_seconds,
        )
        return ok
    if room_id and user_id:
        save_loop_state(room_id, user_id, state, ttl=ttl)
    return True


async def _fail_turn_now(context: Dict[str, Any], end_reason: str) -> None:
    """End a Turn that could not be recorded safely (fail closed)."""
    turn_id = context.get("turn_id")
    if not turn_id:
        return
    from orchestration import turns as turns_mod

    turn = await turns_mod.aget_turn(turn_id)
    if turn is None or turn.state not in ("queued", "running", "paused"):
        return
    await turns_mod.atransition(
        str(turn.pk), turn.version, "failed",
        from_state=turn.state, end_reason=end_reason,
    )


async def _refresh_dispatched_marker(context: Dict[str, Any], state: LoopState) -> bool:
    """Persist the dispatched-call marker before the side effect starts.

    The write is fenced by the Turn's version: a stale writer loses it and the
    caller must not run the action.
    """
    turn_id = context.get("turn_id")
    if not turn_id:
        return True
    from orchestration import turns as turns_mod

    turn = await turns_mod.aget_turn(turn_id)
    if turn is None:
        return False
    return await turns_mod.aupdate_paused_state(
        turn_id, loop_state_payload(state), expected_version=turn.version,
    )


def _supersede_older_pause_sync(
    context: Dict[str, Any], new_turn_id,
) -> List[str]:
    """End an older pause with synthetic, never-executed results (D9).

    The new turn is the only running Turn; the older paused row becomes
    ``superseded`` so there is never more than one armed action. Returns the
    names of the older held actions when it was an approval that this new pause
    dropped, so the caller can tell the user plainly.
    """
    from orchestration import turns as turns_mod

    room_id = context.get("room_id")
    user_id = context.get("user_id")
    if not room_id or not user_id:
        return []
    older = turns_mod.get_paused_turn(room_id, user_id, include_expired=False)
    if older is None or str(older.pk) == str(new_turn_id or ""):
        return []
    state = _load_turn_state_sync(older)
    dropped: List[str] = []
    if older.pause_kind == "approval" and state is not None:
        calls = list(
            state.pending_tools
            or ([state.pending_tool] if state.pending_tool else [])
        )
        dropped = [
            (str(call.get("name") or "action")).replace("_", " ")
            for call in calls
        ]
    if state is not None:
        _append_terminal_results(state, "superseded by a newer request")
        turns_mod.update_paused_state(older.pk, loop_state_payload(state))
    superseded = turns_mod.supersede_pause(older, new_turn_id)
    if superseded:
        # The synthetic results have been produced; a superseded pause can
        # never be answered, so it keeps no encrypted body.
        turns_mod.clear_paused_state(older.pk)
    from workflows.models import WorkflowApprovalRecord

    pending_rows = WorkflowApprovalRecord.objects.filter(
        kind=AGENT_LOOP_APPROVAL_KIND,
        room_id=room_id,
        requested_by_id=user_id,
        status="pending",
    )
    if older.pause_kind == "approval" and not dropped:
        # The encrypted state could not be read; the row still names the action
        # that is being dropped, so the user is never left without a name.
        fallback_action = pending_rows.values_list("action", flat=True).first()
        if fallback_action:
            dropped = [str(fallback_action).replace("_", " ")]
    pending_rows.update(
        status="cancelled",
        reviewed_at=timezone.now(),
        review_comment="Superseded by a newer request.",
    )
    return dropped


def _dropped_actions_note(names: List[str]) -> str:
    labels = ", ".join(name for name in names if name) or "the earlier action"
    return (
        f"The earlier pending action ({labels}) was not run and has been "
        "dropped because this request took its place."
    )


def _last_tool_use_ids(state: LoopState) -> List[str]:
    """Tool_use ids in model order for the newest assistant tool step."""
    for message in reversed(state.messages):
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        ids = [
            str(block.get("id") or "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "tool_use"
        ]
        if ids:
            return ids
    return []


def _ordered_step_results(
    state: LoopState,
    gated_blocks: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Build one result list in model order for the paused step.

    Non-gated results were saved when the pause was made; gated results are
    supplied now. A missing result becomes an ``is_error`` block so every
    tool_use is answered in the same user message.
    """
    gated = dict(gated_blocks or {})
    stored = {
        str(block.get("tool_use_id") or ""): block
        for block in state.pending_step_results
        if isinstance(block, dict)
    }
    ordered_ids = _last_tool_use_ids(state)
    if not ordered_ids:
        ordered_ids = [
            str(call.get("id") or "")
            for call in (state.pending_tools or ([state.pending_tool] if state.pending_tool else []))
        ]
        ordered_ids.extend(
            call_id for call_id in stored if call_id not in ordered_ids
        )
    blocks: List[Dict[str, Any]] = []
    for call_id in ordered_ids:
        block = gated.get(call_id) or stored.get(call_id)
        if block is None:
            block = {
                "type": "tool_result",
                "tool_use_id": call_id,
                "content": "not executed: no result was recorded",
                "is_error": True,
            }
        blocks.append(block)
    state.pending_step_results = []
    return blocks


def _append_terminal_results(state: LoopState, reason: str) -> None:
    """Close every held tool_use with an honest ``is_error`` result.

    Only held calls are closed. A budget/quota pause has no held call, and its
    newest tool step was already answered, so building results from the last
    assistant message again would duplicate the tool_result blocks and leave
    two user messages adjacent.
    """
    calls = list(state.pending_tools or ([state.pending_tool] if state.pending_tool else []))
    if calls:
        gated = {
            str(call.get("id") or ""): {
                "type": "tool_result",
                "tool_use_id": str(call.get("id") or ""),
                "content": f"not executed: {reason}",
                "is_error": True,
            }
            for call in calls
        }
        blocks = _ordered_step_results(state, gated)
        if blocks:
            state.messages.append({
                "role": "user",
                "content": _with_budget_note(blocks, state),
            })
    else:
        state.pending_step_results = []
    state.pending_tools = []
    state.pending_tiers = []
    state.pending_tool = None
    state.pending_tier = None
    state.pending_args_hashes = {}
    state.paused_for_confirmation = False


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


def _retry_key(name: str, tool_input: Dict[str, Any]) -> str:
    """Stable per-call key for retry accounting.

    The digest keeps raw tool input out of the paused loop state, which is
    cached now and becomes a database row in T-D4.
    """
    digest = hashlib.sha256(_dedup_key(name, tool_input).encode("utf-8")).hexdigest()
    return f"{name}:{digest}"


_MAX_CALL_LABEL_CHARS = 160


def _describe_tool_call(name: str, tool_input: Dict[str, Any]) -> str:
    """A short, redacted label for the call a retry bound stopped."""
    detail = ""
    if isinstance(tool_input, dict):
        # Drop restricted keys before rendering: redaction patterns do not
        # match JSON form, where a quote sits between key and colon.
        safe_input = sanitize_parameters(tool_input)
        for key in ("command", "cmd", "script", "query", "text"):
            value = safe_input.get(key)
            if isinstance(value, str) and value.strip():
                detail = value.strip()
                break
        if not detail:
            try:
                rendered = json.dumps(safe_input, sort_keys=True, default=str)
            except (TypeError, ValueError):
                rendered = str(safe_input)
            if rendered not in ("{}", "null", ""):
                detail = rendered
    label = f"{name} {detail}" if detail else name
    label = " ".join(redact_sensitive_text(label).split())
    if len(label) > _MAX_CALL_LABEL_CHARS:
        label = label[:_MAX_CALL_LABEL_CHARS - 3] + "..."
    return label


def _message_chars(message: Dict[str, Any]) -> int:
    """Rough character count of one history message (token proxy)."""
    content = message.get("content", "")
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return len(json.dumps(content, default=str))
    return len(str(content or ""))


def _fit_history_to_budget(
    history: List[Dict[str, Any]],
    max_chars: int,
    max_messages: int,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Trim the oldest turns so history fits a context budget, in O(n) time.

    Keeps the largest suffix that fits (most recent context is preserved),
    never drops the most recent message, and reports whether trimming occurred
    so callers can emit a ``context_compacted`` event instead of truncating
    silently.
    """
    if not history:
        return history, False
    char_counts = [_message_chars(m) for m in history]
    total_chars = sum(char_counts)
    n = len(history)
    start = 0
    while (n - start) > 1 and ((n - start) > max_messages or total_chars > max_chars):
        total_chars -= char_counts[start]
        start += 1
    return history[start:], start > 0


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
        # get-then-set with fresh TTL (cache.incr on a fresh key creates
        # it without a TTL, so it would never expire)
        current = int(cache.get(key) or 0)
        cache.set(key, current + count, 86400)
    except Exception:
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


def _total_input_tokens(response: Dict[str, Any]) -> int:
    """Every input token the provider processed for one call.

    Anthropic splits the prompt across ``input_tokens`` (uncached),
    ``cache_creation_input_tokens`` and ``cache_read_input_tokens``; the sum is
    the whole prompt regardless of cache state. OpenAI-compatible providers
    report the whole prompt in ``input_tokens``, so the extra fields are zero.
    """
    usage = response.get("usage", {}) or {}
    return (
        int(usage.get("input_tokens", 0) or 0)
        + int(usage.get("cache_read_input_tokens", 0) or 0)
        + int(usage.get("cache_creation_input_tokens", 0) or 0)
    )


def _work_tokens(response: Dict[str, Any], prev_input_tokens: int) -> Tuple[int, int]:
    """Work done by one call: generated tokens + newly-read input tokens (T-F3).

    The resent prefix — system prompt, tools and history sent again on every
    call — is not work, so only the *growth* in total input since the previous
    call counts. The first call of a turn (``prev_input_tokens == 0``)
    establishes the baseline and adds no input. Returns
    ``(work_tokens, total_input_tokens)``; the second value is the next
    baseline. Provider-agnostic: it reads only the reported input totals.
    """
    usage = response.get("usage", {}) or {}
    output = int(usage.get("output_tokens", 0) or 0)
    total_input = _total_input_tokens(response)
    new_input = max(0, total_input - prev_input_tokens) if prev_input_tokens else 0
    return output + new_input, total_input


def _harness_budget_note(used: int, total: int) -> str:
    """A short, prefix-safe note telling the model what it has left (T-F3)."""
    remaining = max(0, total - used)
    return (
        f"[Harness: turn budget {used}/{total} work tokens used; {remaining} left. "
        f"If you cannot finish within it, call {REQUEST_MORE_BUDGET_TOOL} to ask the "
        "user for more instead of stopping silently.]"
    )


def _budget_end_detail(state: "LoopState") -> str:
    """What ran this turn, for a harness note that claims no success (T-F3)."""
    ran = state.tool_call_count
    tools = list(dict.fromkeys(
        entry.get("name") for entry in state.tool_call_log if entry.get("name")
    ))
    if ran:
        detail = f"{ran} tool call{'s' if ran != 1 else ''} ran"
        detail += f" ({', '.join(tools)})" if tools else ""
    else:
        detail = "no tool calls had run yet"
    return detail


def _bound_reached_note(state: "LoopState") -> str:
    """The harness line shown when the turn's budget runs out (T-F3).

    It says plainly what ran, so "here's what I managed" is never a promise it
    does not keep, and offers the same reply a budget request does so the user
    can extend the turn (deliverable 6). It is the harness's words, not the
    model's, and claims no success.
    """
    return (
        f"I've used up this turn's budget: {_budget_end_detail(state)}. "
        "Reply **more budget** and I'll continue from where it stopped."
    )


def _maximum_budget_note(state: "LoopState") -> str:
    """The harness line shown when even a grant cannot extend the turn (T-F3).

    At the configured maximum there is no budget left to grant, so this note
    says that plainly and never offers another grant. It is the harness's
    words, separate from the model's, and claims no success.
    """
    return (
        f"I've used the maximum budget for this turn: {_budget_end_detail(state)}. "
        "I can't extend it further; send a new message to continue."
    )


def _quota_pause_ttl(retry_after_seconds: Any) -> int:
    """How long a quota-paused turn is kept so it can resume (T-F3b).

    The rolling hour can free up to an hour out, so the default 10-minute
    confirmation window is too short. Keep the state for the wait plus a margin,
    never less than the default window.
    """
    try:
        retry = int(retry_after_seconds or 0)
    except (TypeError, ValueError, OverflowError):
        retry = 0
    return max(CONFIRMATION_STATE_TTL, retry + 300)


def _quota_reached_note(state: "LoopState", exc: "TokenQuotaExceeded") -> str:
    """The harness line shown when the hourly work-token quota blocks a turn.

    It says plainly what ran, that the limit is the hour's, roughly when enough
    frees up, and how to continue from where it stopped without redoing finished
    work. It is the harness's words, not the model's, and claims no success.
    """
    detail = _budget_end_detail(state)
    if getattr(exc, "reason", "limit") == "unreadable":
        return (
            f"I couldn't check this hour's token limit, so I stopped rather than "
            f"risk overrunning it: {detail}. Reply **continue** and I'll try again."
        )
    when = _humanize_seconds(getattr(exc, "retry_after_seconds", 0))
    return (
        f"I've reached this hour's token limit: {detail}. It frees up in {when}. "
        "Reply **continue** then and I'll pick up from where it stopped."
    )


def _with_budget_note(
    content_blocks: List[Dict[str, Any]],
    state: "LoopState",
) -> List[Dict[str, Any]]:
    """Append the harness budget note to a user message of tool results.

    Appended, never prepended to the system prompt, so the cached prompt prefix
    (system + tools) is untouched and the model still sees its own results in
    order.
    """
    if state.token_budget:
        content_blocks = list(content_blocks)
        content_blocks.append({
            "type": "text",
            "text": _harness_budget_note(state.tokens_used, state.token_budget),
        })
    return content_blocks


def _append_note_to_last_user(messages: List[Dict[str, Any]], note: str) -> None:
    """Append a harness note as a text block on the newest user message.

    Keeping it on the existing user message (never a new same-role message, and
    never the system prompt) keeps the message contract valid and leaves the
    cached prompt prefix untouched.
    """
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, list):
            content.append({"type": "text", "text": note})
        else:
            message["content"] = [{"type": "text", "text": str(content or "")}, {"type": "text", "text": note}]
        return


def _rebuild_search_tool(
    tools: List[Dict[str, Any]],
    user_id: Optional[int],
    user_location: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Rebuild the native search tool from the live allowance, or drop it (T-F3).

    The allowance is read at the start of every request, not once at the start
    of the turn, so one turn can no longer exceed the daily limit. Only acts
    when a search tool was already offered; a tool list without one is returned
    unchanged.
    """
    if not any(isinstance(t, dict) and t.get("name") == "web_search" for t in tools):
        return tools
    rebuilt = [
        t for t in tools
        if not (isinstance(t, dict) and t.get("name") == "web_search")
    ]
    fresh = _build_web_search_tool(user_id, user_location)
    if fresh:
        rebuilt.append(fresh)
    return rebuilt


# Max characters for a single tool result injected into the conversation
_MAX_RESULT_CHARS = 8000

# Patterns that might indicate a tool result is trying to inject instructions
_RESULT_INJECTION_RE = None


def _get_result_injection_re():
    global _RESULT_INJECTION_RE
    if _RESULT_INJECTION_RE is None:
        import re
        patterns = [
            r"ignore\s+(all\s+|previous\s+|system\s+)*instructions",
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

    # Redact secrets/PII so they never re-enter LLM context (fail-plausible guard)
    result_json = redact_sensitive_text(result_json)

    return result_json


# --------------------------------------------------------------------------- #
#  Model routing                                                              #
# --------------------------------------------------------------------------- #

def _resolve_model_override(room_id: Optional[int]) -> Tuple[Optional[str], Optional[str]]:
    """
    Read the per-room model preference stored on the room and return a
    (provider, model) pair. Returns (None, None) for Auto — no override, an
    unset/empty value, a read failure, or a preference that is no longer in the
    catalog/configured.
    """
    if not room_id:
        return None, None
    from chatbot.model_preference import read_room_model_pref

    raw = read_room_model_pref(room_id)
    if not raw:
        return None, None
    parsed = parse_model_id(str(raw))
    if not parsed:
        return None, None
    provider, model = parsed
    if find_model(provider, model) is None or not provider_configured(provider):
        return None, None
    return provider, model


def _search_backend_available() -> bool:
    """True when a real web-search backend is registered (the search seam)."""
    from orchestration.tool_router import search_backend_available

    return search_backend_available()


def _is_search_action(tool_name: str) -> bool:
    """True for the web-search action (including its aliases)."""
    from orchestration.action_catalog import resolve_action_alias

    return resolve_action_alias(tool_name) == "search_info"


async def _execute_with_timeout(
    tool_name: str,
    tool_input: Dict[str, Any],
    context: Dict[str, Any],
    timeout: float = TOOL_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """Execute a tool with a timeout wrapper."""
    # The registered-backend search path shares the native search budget: the
    # daily limit is checked before the call and one use is recorded on success.
    is_search = _is_search_action(tool_name)
    if is_search and get_remaining_searches(context.get("user_id")) <= 0:
        return {
            "status": "error",
            "message": "You've reached today's web search limit. Try again tomorrow.",
            "results": [],
        }
    try:
        result = await asyncio.wait_for(
            execute_tool(tool_name, tool_input, context),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return {
            "status": "error",
            "message": f"Tool {tool_name} timed out after {timeout}s. "
                       f"The operation may still be processing.",
        }
    if is_search and result.get("status") == "success":
        _record_search_usage(context.get("user_id"), 1)
    return result


# --------------------------------------------------------------------------- #
#  Tool retry backoff & circuit breaker                                       #
# --------------------------------------------------------------------------- #

# Per-service degrade state: service -> {"failures": int, "opened_at": float|None}.
# Repeated failures open the circuit and block further calls for a cooldown
# window, so a flapping integration degrades instead of being hammered.
_circuit_state: Dict[str, Dict[str, Any]] = {}


def _service_for_tool(tool_name: str) -> str:
    from orchestration.action_catalog import get_action_definition

    return (get_action_definition(tool_name) or {}).get("service") or tool_name


def _retry_backoff_seconds(retry_count: int) -> float:
    """Exponential backoff for a tool retry: 2^n seconds, capped at 300."""
    return float(min(RETRY_BACKOFF_CAP_SECONDS, 2 ** max(0, retry_count)))


def _circuit_breaker_open(service: str) -> bool:
    """True when the service's circuit is tripped and still cooling down."""
    state = _circuit_state.get(service)
    if not state:
        return False
    opened_at = state.get("opened_at")
    if not opened_at:
        return False
    if time.monotonic() - opened_at >= CIRCUIT_BREAKER_COOLDOWN_SECONDS:
        _circuit_state.pop(service, None)  # half-open: allow a trial request
        return False
    return True


def _record_tool_failure(service: str) -> None:
    state = _circuit_state.setdefault(service, {"failures": 0, "opened_at": None})
    state["failures"] = state.get("failures", 0) + 1
    if state["failures"] >= CIRCUIT_BREAKER_FAILURE_THRESHOLD:
        state["opened_at"] = time.monotonic()


def _record_tool_success(service: str) -> None:
    _circuit_state.pop(service, None)


def reset_circuit_breakers() -> None:
    """Clear all circuit-breaker state (used by tests)."""
    _circuit_state.clear()


def _circuit_open_message(service: str) -> str:
    return (
        f"{service.replace('_', ' ')} is temporarily unavailable after "
        f"repeated failures. Please try again in a few seconds."
    )


def _account_tool_result(
    state: "LoopState",
    tool_name: str,
    tool_input: Dict[str, Any],
    service: str,
    result: Any,
) -> None:
    """Update retry + circuit-breaker state after a tool call (T-F3).

    The retry count is keyed by the exact call, not the tool, so different
    inputs to one tool do not share a count and an unrelated success does not
    reset it.

    A harness refusal (e.g. a budget refusal) is the harness declining to run
    the call, not the service failing: it leaves the retry count and the
    breaker untouched, so a refused batch cannot open the circuit.
    """
    if _is_harness_refusal(result):
        return
    key = _retry_key(tool_name, tool_input)
    if isinstance(result, dict) and result.get("status") == "error":
        state.retry_counts[key] = state.retry_counts.get(key, 0) + 1
        _record_tool_failure(service)
    else:
        state.retry_counts.pop(key, None)
        _record_tool_success(service)


async def _record_receipt(
    tool_name: str,
    tool_input: Dict[str, Any],
    result: Dict[str, Any],
    context: Dict[str, Any],
    basis: str = "",
) -> None:
    """Record an action receipt for audit trail (best-effort).

    ``basis`` says why a shell command ran: ``human`` (confirmed), or the
    rule that let it auto-run (``sandbox``, ``allowlist``, ``armed_window``,
    ``open_profile``).
    """
    try:
        from orchestration.action_receipts import record_action_receipt, should_record_receipt
        from orchestration.action_catalog import get_action_definition, resolve_action_alias
        # The executor runs an alias as its canonical action; audit it as that.
        tool_name = resolve_action_alias(tool_name) or tool_name
        if not should_record_receipt(tool_name):
            return
        action_def = get_action_definition(tool_name) or {}
        status = "success" if result.get("status") == "success" else "error"
        reason = result.get("message") or result.get("error") or ""
        data_payload = result.get("data") if isinstance(result.get("data"), dict) else {}
        data_payload = dict(data_payload or {"message": reason})
        if basis:
            data_payload["approval_basis"] = basis
        await record_action_receipt(
            user_id=context.get("user_id"),
            room_id=context.get("room_id"),
            action=tool_name,
            service=action_def.get("service", ""),
            params=tool_input,
            result=data_payload,
            status=status,
            reason=reason,
        )
    except Exception as exc:
        logger.warning("Action receipt not recorded for %s: %s", tool_name, exc)


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
                "timezone": {
                    "type": "string",
                    "description": (
                        "IANA timezone name for a schedule trigger (e.g. 'Africa/Nairobi'). "
                        "Defaults to your profile timezone, then UTC."
                    ),
                },
            },
            "required": ["description", "steps"],
        },
    },
    {
        "name": "list_skills",
        "description": (
            "List the available skills — reusable instruction packs for "
            "well-defined procedures (e.g. report formatting). Use this "
            "before load_skill to discover what's available."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "load_skill",
        "description": (
            "Load a skill's full instructions by name so you can follow the "
            "procedure it describes. Use list_skills first to find the name."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Skill name to load",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "save_skill",
        "description": (
            "Save a new skill — a reusable instruction pack — from this conversation. "
            "Use when the user says 'save this as a skill' or describes a procedure "
            "worth keeping. The skill is staged, never active: the user promotes it "
            "later from the Skills dashboard."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Short skill name"},
                "description": {"type": "string", "description": "One-line description"},
                "tools": {"type": "string", "description": "Comma-separated tool names it uses"},
                "when_to_use": {"type": "string", "description": "When to use the skill"},
                "inputs": {"type": "string", "description": "Required inputs and access"},
                "sequence": {"type": "string", "description": "The sequence of work"},
                "validation": {"type": "string", "description": "How to validate the result"},
                "returns": {"type": "string", "description": "What the skill returns"},
                "approval": {"type": "string", "description": "What requires approval"},
            },
            "required": ["name", "description", "sequence"],
        },
    },
    {
        "name": REQUEST_MORE_BUDGET_TOOL,
        "description": (
            "Ask the user for more budget when you cannot finish this turn within "
            "the work-token budget the harness shows you. Use it *instead of* "
            "stopping silently: also write your normal reply saying what you have "
            "done and what is left. The user can then grant more and you continue "
            "from where you stopped. Do not use it on a turn you can finish."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "What is left to do and why it needs more budget.",
                },
            },
            "required": ["reason"],
        },
    },
]

# A delegated run is bounded by the same work-token rule as its parent: the
# parent grants a share of what it has left via ``context["sub_agent_budget"]``.
# The constants below are fallbacks only — for a run spawned without a parent
# budget, and a tool-call ceiling so a degenerate provider whose results add no
# tokens cannot spin.
SUB_AGENT_TOKEN_BUDGET = 20000
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
        granted = _granted_sub_budget(context)
        if granted is not None and granted <= 0:
            # The parent turn has too little left to hand a sub-run a real
            # budget. Refuse honestly instead of running on the fallback.
            return {
                "status": "error",
                "message": (
                    "Not enough of this turn's budget is left to delegate this "
                    "task. I'll continue here or ask the user for more budget."
                ),
                HARNESS_REFUSAL_KEY: True,
            }
        return await _run_sub_agent(
            tool_input, context, preferences, parent_system, parent_tools,
        )
    elif tool_name == "handoff_to_workflow":
        return await _create_workflow_handoff(tool_input, context)
    elif tool_name == REQUEST_MORE_BUDGET_TOOL:
        # Intercepted by the main loop before execution; reaching here means it
        # was replayed outside that path.
        return {
            "status": "error",
            "message": "The budget request was already handled by the harness.",
            HARNESS_REFUSAL_KEY: True,
        }
    elif tool_name == "list_skills":
        return _list_skills()
    elif tool_name == "load_skill":
        return _load_skill(tool_input, context)
    elif tool_name == "save_skill":
        return await _save_skill(tool_input, context)
    else:
        return {"status": "error", "message": f"Unknown meta-tool: {tool_name}"}


def _list_skills() -> Dict[str, Any]:
    """List active skills (read-only meta-tool)."""
    try:
        from orchestration.skill_registry import list_skills
        skills = list_skills()
    except Exception as exc:
        logger.error("list_skills failed: %s", exc)
        return {"status": "error", "message": f"Could not list skills: {exc}"}
    return {
        "status": "success",
        "skills": skills,
        "message": (
            f"{len(skills)} skill(s) available." if skills else "No active skills."
        ),
    }


async def _save_skill(tool_input: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Save a chat-authored skill as a staged folder + reviewable draft."""
    name = (tool_input.get("name") or "").strip()
    if not name:
        return {"status": "error", "message": "Skill name is required."}

    contract = {
        section: (tool_input.get(section) or "").strip()
        for section in ("when_to_use", "inputs", "sequence", "validation", "returns", "approval")
    }
    tools = [item.strip() for item in (tool_input.get("tools") or "").split(",") if item.strip()]
    try:
        from workflows.promotion import create_skill_from_text

        path, draft = await sync_to_async(create_skill_from_text)(
            name=name,
            description=(tool_input.get("description") or "").strip(),
            tools=tools,
            contract=contract,
            user_id=context.get("user_id"),
            room_id=context.get("room_id"),
        )
    except Exception as exc:
        logger.error("save_skill failed: %s", exc)
        return {"status": "error", "message": f"Could not save the skill: {exc}"}

    return {
        "status": "success",
        "skill": draft.skill_name,
        "message": (
            f"Saved '{draft.skill_name}' as a staged skill. It is not active yet — "
            "promote it from the Skills dashboard when you're ready."
        ),
    }


def _load_skill(tool_input: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Load a skill's instructions into context (read-only meta-tool)."""
    name = tool_input.get("name", "")
    if not name:
        return {"status": "error", "message": "Skill name is required."}
    try:
        from orchestration.skill_registry import load_skill_for_agent
        result = load_skill_for_agent(name)
        if result.get("status") == "success":
            record_event("skill_loaded", {
                "skill": result.get("skill"),
                "user_id": context.get("user_id"),
                "room_id": context.get("room_id"),
                "turn_id": context.get("turn_id"),
            })
        return result
    except Exception as exc:
        logger.error("load_skill failed: %s", exc)
        return {"status": "error", "message": f"Could not load skill: {exc}"}


def _context_lease_live(context: Dict[str, Any]) -> bool:
    """Whether the caller may still act for this context's turn.

    Fail closed: a Turn-backed context with no hook is not live. A context
    without a turn id (a test or a no-turn path) keeps its old behaviour.
    """
    check = context.get("turn_live")
    if not callable(check):
        return not context.get("turn_id")
    try:
        return bool(check())
    except Exception:
        return False


async def _execute_scoped_tool_calls(
    tool_calls: List[Dict[str, Any]],
    context: Dict[str, Any],
    preferences: Optional[Dict[str, Any]],
    sub_tool_log: List[Dict[str, Any]],
    tool_call_cap: Optional[int] = None,
    persona=None,
    fence_check=None,
    lease_lost: Optional[List[bool]] = None,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Execute one response's tool_use batch under the sub-agent cap.

    The cap is enforced mid-batch — a single response can carry many
    tool_use blocks, and only checking between iterations let one batch
    blow far past the limit. Returns (result_blocks, capped).
    """
    result_blocks = []
    effective_cap = tool_call_cap or SUB_AGENT_MAX_TOOL_CALLS
    # A sub-agent inherits the parent's taint and taints itself the same way,
    # so delegation can never be a way around the tainted-egress gate.
    tainted = bool(isinstance(preferences, dict) and preferences.get("_shell_tainted")) or any(
        _untrusted_source(entry.get("name")) for entry in sub_tool_log
    )
    for tc in tool_calls:
        if len(sub_tool_log) >= effective_cap:
            return result_blocks, True
        if fence_check is not None and not fence_check():
            # The parent lost its lease mid-batch: dispatch nothing further.
            if lease_lost is not None:
                lease_lost.append(True)
            return result_blocks, False

        # Sub-agents cannot pause for confirmation; block anything that would.
        # Persona scope and receipts are the same as in the main loop.
        run_tainted = tainted
        auto, _pause, denied = _bucket_tool_calls(
            [tc], preferences, persona=persona, tainted=run_tainted,
        )
        if _untrusted_source(tc["name"]):
            tainted = True
        executed = False
        if denied:
            result = {"status": "error", "message": denied[0][1]}
        elif not auto:
            result = {
                "status": "error",
                "message": (
                    f"{tc['name']} requires explicit user confirmation. "
                    "Please ask in the main conversation before executing it."
                ),
            }
        else:
            result = await _execute_with_timeout(tc["name"], tc["input"], context)
            executed = True
        sub_tool_log.append({
            "name": tc["name"],
            "input": tc["input"],
            "status": result.get("status"),
        })
        if executed:
            await _record_receipt(
                tc["name"], tc["input"], result, context,
                basis=_shell_approval_basis(tc, preferences, run_tainted),
            )
        result_blocks.append({
            "type": "tool_result",
            "tool_use_id": tc["id"],
            "content": _sanitize_tool_result(json.dumps(result, default=str)),
        })
    return result_blocks, False


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
        return {
            "status": "error",
            "message": "No task description provided.",
            "tokens_used": 0,
        }

    # Filter tools if specified
    tools_filter = tool_input.get("tools", "")
    if tools_filter:
        allowed = {t.strip() for t in tools_filter.split(",")}
        scoped_tools = [t for t in parent_tools if t["name"] in allowed]
    else:
        scoped_tools = parent_tools
    # A sub-agent cannot ask the user for budget; only its parent can.
    scoped_tools = [t for t in scoped_tools if t.get("name") != REQUEST_MORE_BUDGET_TOOL]

    # Run a sub-loop with tighter limits
    llm = get_llm_client()
    sub_messages: List[Dict[str, Any]] = [
        {"role": "user", "content": task_description},
    ]

    collected_text: List[str] = []
    sub_tool_log: List[Dict[str, Any]] = []
    iteration = 0
    tokens_used = 0
    stopped_reason: Optional[str] = None
    completed = False
    consecutive_pauses = 0

    # The work-token budget is a share of the parent's remaining (or a handoff's
    # grant), handed to us through context. A run spawned without one falls back
    # to the default. Tool input is written by the model and never sets any of
    # this. The tool-call ceiling is a secondary guard so a degenerate provider
    # whose results add no tokens cannot spin.
    try:
        sub_budget = int(context.get("sub_agent_budget") or 0)
    except (TypeError, ValueError, OverflowError):
        sub_budget = 0
    if sub_budget <= 0:
        sub_budget = SUB_AGENT_TOKEN_BUDGET
    try:
        granted_cap = int(context.get("sub_agent_tool_call_cap") or 0)
    except (TypeError, ValueError, OverflowError):
        granted_cap = 0
    tool_call_cap = min(granted_cap, HARD_CAP_TOOL_CALLS) if granted_cap > 0 else SUB_AGENT_MAX_TOOL_CALLS
    persona = await _resolve_persona(context.get("room_id"), context.get("user_id"))
    from orchestration.personas import PERSONA_LOOKUP_FAILED

    if persona is PERSONA_LOOKUP_FAILED:
        # The room's bounds are unknown: make no tool call and end with the
        # same reason the main loop gives the model (C3).
        return {
            "status": "error",
            "message": PERSONA_LOOKUP_DENIED_REASON,
            "summary": PERSONA_LOOKUP_DENIED_REASON,
            "tools_used": [],
            "iterations": 0,
            "tokens_used": 0,
            "tool_calls": 0,
            "stopped_reason": "persona_lookup_failed",
            HARNESS_REFUSAL_KEY: True,
        }
    prev_input_tokens = 0

    while iteration < HARD_CAP_ITERATIONS:
        if not _context_lease_live(context):
            # The parent lost its lease: the child stops before its next call.
            stopped_reason = "lease_lost"
            break

        if len(sub_tool_log) >= tool_call_cap:
            stopped_reason = "max_tool_calls"
            break

        if tokens_used >= sub_budget:
            stopped_reason = "token_budget"
            break

        iteration += 1

        # Rebuild the native search tool from the live allowance on every call
        # so a delegated run cannot exceed the daily limit either (T-F3).
        tools_this_call = (
            _rebuild_search_tool(scoped_tools, context.get("user_id"))
            if scoped_tools else scoped_tools
        )
        try:
            response = await llm.create_message(
                messages=sub_messages,
                system=parent_system,
                tools=tools_this_call if tools_this_call else None,
                temperature=0.3,
                max_tokens=4096,
                user_id=context.get("user_id"),
                use_prompt_cache=True,
            )
        except Exception as exc:
            # Report what the run already spent, so the parent charges it: a
            # delegation that fails mid-task still consumed the turn's budget.
            return {
                "status": "error",
                "message": f"Sub-agent LLM error: {exc}",
                "tokens_used": tokens_used,
            }

        content_blocks = response.get("content", [])
        stop_reason = response.get("stop_reason", "end_turn")
        sub_messages.append({"role": "assistant", "content": content_blocks})
        work, prev_input_tokens = _work_tokens(response, prev_input_tokens)
        tokens_used += work
        # Charge the delegated run's work to the user's rolling-hour quota too:
        # it is the user's work, and only this run knows what it newly read
        # (T-F3b).
        llm.record_work_tokens(work, context.get("user_id"))
        searches = _count_search_uses(response)
        if searches > 0:
            # A server-side search brings in untrusted text; the main loop taints on it too.
            _record_search_usage(context.get("user_id"), searches)
            preferences = _taint_preferences(preferences, True)

        text = _extract_text(content_blocks)
        if text:
            collected_text.append(text)

        if stop_reason == "end_turn":
            completed = True
            break

        if stop_reason == "max_tokens":
            stopped_reason = "length_limit"
            # Always say the run was cut short; text collected before the cut
            # must not read as a complete summary.
            collected_text.append(LENGTH_LIMIT_NOTE)
            break

        if stop_reason == "tool_use":
            consecutive_pauses = 0
            tool_calls = _extract_tool_calls(content_blocks)
            child_lease_lost: List[bool] = []
            result_blocks, capped = await _execute_scoped_tool_calls(
                tool_calls, context, preferences, sub_tool_log,
                tool_call_cap=tool_call_cap, persona=persona,
                fence_check=lambda: _context_lease_live(context),
                lease_lost=child_lease_lost,
            )
            sub_messages.append({"role": "user", "content": result_blocks})
            if child_lease_lost:
                stopped_reason = "lease_lost"
                break
            if capped:
                stopped_reason = "max_tool_calls"
                break
            continue

        if stop_reason == "pause_turn":
            # A server-side tool is still running: send the paused turn back so
            # the model can finish, but bound the run of pauses.
            consecutive_pauses += 1
            if consecutive_pauses > MAX_CONSECUTIVE_PAUSES:
                stopped_reason = "pause_limit"
                if not collected_text:
                    collected_text.append("The model paused repeatedly and did not finish this task.")
                break
            continue

        # A stop reason we do not handle. End the run rather than re-sending the
        # same messages until the iteration cap.
        stopped_reason = f"unhandled_stop:{stop_reason}"
        if not collected_text:
            collected_text.append("The model declined to continue this task.")
        break

    if stopped_reason is None and not completed:
        stopped_reason = "max_iterations"

    if stopped_reason:
        record_event("sub_agent_budget_exhausted", {
            "reason": stopped_reason,
            "iterations": iteration,
            "tool_calls": len(sub_tool_log),
            "tokens_used": tokens_used,
            "user_id": context.get("user_id"),
            "room_id": context.get("room_id"),
            "turn_id": context.get("turn_id"),
        })

    summary = "\n".join(collected_text)
    if not summary and stopped_reason == "token_budget":
        summary = "Sub-task stopped early: token budget exhausted."

    return {
        "status": "success",
        "summary": summary or "Sub-task completed.",
        "tools_used": [e["name"] for e in sub_tool_log],
        "iterations": iteration,
        "tokens_used": tokens_used,
        "tool_calls": len(sub_tool_log),
        "stopped_reason": stopped_reason,
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
        from asgiref.sync import sync_to_async
        from orchestration.workflow_planner import execute_adhoc_workflow

        # Build the same definition shape the planner/validator consumes
        # (workflow_name / workflow_description / triggers / steps) instead of
        # the old {description, steps} shape that bypassed validation and a
        # separate raw execution path.
        def _definition(triggers):
            return {
                "workflow_name": description[:100] or "Agent handoff",
                "workflow_description": description.strip()[:300],
                "triggers": triggers,
                "steps": steps,
                "metadata": {"source": "agent_handoff"},
            }

        if trigger_type == "manual":
            definition = _definition([{"trigger_type": "manual"}])
            result = await execute_adhoc_workflow(
                definition,
                user_id=context.get("user_id"),
                room_id=context.get("room_id"),
                trigger_data={"source": "agent_handoff"},
            )
            workflow = result.get("workflow")
            workflow_id = getattr(workflow, "id", None)
            if result.get("status") == "error":
                return {
                    "status": "error",
                    "message": result.get("message") or "Workflow handoff failed.",
                }
            return {
                "status": "success",
                "message": result.get("message") or f"Workflow '{description}' started.",
                "workflow_id": workflow_id,
            }

        if trigger_type == "webhook":
            return {
                "status": "error",
                "message": (
                    "A webhook workflow needs a service and an event; "
                    "I cannot create one from chat yet."
                ),
            }

        if trigger_type != "schedule":
            return {
                "status": "error",
                "message": f"I cannot create a '{trigger_type}' workflow from chat.",
            }

        from workflows.creation import (
            WorkflowCreationError,
            create_workflow_with_triggers,
            describe_schedule,
        )

        timezone = tool_input.get("timezone")
        if not timezone:
            from orchestration.user_preferences import get_user_preferences

            preferences = await sync_to_async(get_user_preferences)(context.get("user_id"))
            timezone = (preferences or {}).get("timezone") or "UTC"

        definition = _definition([{
            "trigger_type": "schedule",
            "cron": tool_input.get("schedule"),
            "timezone": timezone,
        }])

        try:
            workflow, triggers = await create_workflow_with_triggers(
                user_id=context.get("user_id"),
                room_id=context.get("room_id"),
                definition=definition,
            )
        except WorkflowCreationError as exc:
            return {"status": "error", "message": str(exc)}

        schedule = next(
            (trigger for trigger in triggers if trigger.trigger_type == "schedule"),
            None,
        )
        schedule_text = describe_schedule(schedule) if schedule else "the requested schedule"
        return {
            "status": "success",
            "message": (
                f"Workflow '{description}' created (ID: {workflow.id}) and scheduled "
                f"to run {schedule_text}."
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
    environment_block: str = "",
    history: Optional[List[Dict[str, Any]]] = None,
    history_tainted: bool = False,
    resumed_state: Optional[LoopState] = None,
    confirmed_tool: bool = False,
    budget_extended: bool = False,
    quota_resumed: bool = False,
    expired_pending: bool = False,
    pending_note: str = "",
    nothing_pending: bool = False,
) -> AsyncGenerator[AgentEvent, None]:
    """
    Run the agentic loop. Yields AgentEvent objects for the consumer to handle.

    Args:
        user_message: The user's natural language request.
        context: Dict with user_id, room_id, username, preferences.
        preferences: Normalised user preferences.
        context_prompt: Room context string from ContextManager.
        memory_summary: Entity/action memory summary.
        environment_block: Where commands run (only sent with run_command).
        history: Previous conversation messages in Anthropic format.
        resumed_state: If resuming after a confirmation or budget pause.
        confirmed_tool: True if the user just confirmed a pending tool.
        budget_extended: True if the user just granted the turn more budget.
        quota_resumed: True if the user is resuming a turn the hourly token
            quota stopped. It continues the turn without touching its budget.
    """
    llm = get_llm_client()
    if user_message:
        context.setdefault("user_message", user_message)
        context.setdefault("raw_query", user_message)
    user_id = context.get("user_id")
    room_id = context.get("room_id")
    user_caps = preferences or {}

    def _lease_live() -> bool:
        """The coordinator's self-fence; fail closed when it cannot answer."""
        return _context_lease_live(context)

    # One work-token bound per turn (T-F3). The old all-or-nothing caps switch
    # no longer turns caps off; a legacy install where it was off maps to the
    # largest bound (_turn_token_budget). Sub-agents inherit the flag via
    # context so delegate_task never re-reads it.
    caps_enforced = context.get(
        "caps_enforced",
        await sync_to_async(enforce_agent_caps)(user_id),
    )
    context["caps_enforced"] = caps_enforced
    default_budget = _turn_token_budget(caps_enforced)

    # Claude native web search is an Anthropic server-side tool. No other
    # provider has one. `search_info` (SearchConnector) is a real search only
    # when offered: native search on Anthropic, or a registered search backend
    # elsewhere. With neither it is excluded and the prompt says so, or the
    # model invents a search.
    override_provider, _ = await sync_to_async(_resolve_model_override)(room_id)
    effective_provider = override_provider or llm.default_provider()
    native_search = effective_provider == "anthropic"
    backend_search = (not native_search) and _search_backend_available()

    # Build tool definitions (connector-configured, capability-gated, and
    # bounded by a room persona's scope — T-F2). The persona is resolved once
    # and reused for the execution bounds below. A lookup failure means the
    # room's bounds are unknown, so this turn gets no reach at all (C3).
    persona = await _resolve_persona(room_id, user_id)
    from orchestration.personas import PERSONA_LOOKUP_FAILED

    persona_unavailable = persona is PERSONA_LOOKUP_FAILED
    web_search_tool = None
    user_location_hint = None
    if persona_unavailable:
        # The history may replay a tool_use/tool_result pair, and the resume
        # path appends a tool_result. Both providers reject tool blocks when no
        # tools are defined, so keep the ordinary definitions in the request;
        # every call is refused before dispatch (_bucket_tool_calls). Only a
        # server-side tool (native search) is dropped: it would run without the
        # harness ever seeing the call.
        native_search = False
        persona_tool_scope = None
    else:
        persona_tool_scope = (
            list(persona.tool_scope)
            if persona is not None and persona.tool_scope else None
        )

    tools = get_tool_definitions(
        user_capabilities=user_caps,
        exclude_actions=[] if backend_search else ["search_info"],
        tool_scope=persona_tool_scope,
    )
    # Add meta-tools (delegate_task, handoff_to_workflow, skills).
    if persona_tool_scope:
        from orchestration.action_catalog import resolve_action_alias

        scope_set = {resolve_action_alias(name) for name in persona_tool_scope}
        tools.extend(
            tool for tool in META_TOOL_DEFINITIONS
            if resolve_action_alias(tool["name"]) in scope_set
        )
    else:
        tools.extend(META_TOOL_DEFINITIONS)

    # The budget tool is harness machinery, not a room action, so it is
    # offered even under a persona scope that would filter the other
    # meta-tools (T-F3).
    if not any(t.get("name") == REQUEST_MORE_BUDGET_TOOL for t in tools):
        tools.append(next(t for t in META_TOOL_DEFINITIONS if t["name"] == REQUEST_MORE_BUDGET_TOOL))

    if not persona_unavailable and native_search:
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
    tool_names = [tool["name"] for tool in tools]
    system = build_system_prompt(
        preferences=preferences,
        context_prompt=context_prompt,
        memory_summary=memory_summary,
        tool_names=tool_names,
        environment_block=environment_block,
    )
    if persona_unavailable:
        system += "\n\n" + PERSONA_LOOKUP_UNAVAILABLE_NOTE
    elif not web_search_tool and not backend_search:
        system += "\n\n" + NO_SEARCH_TOOL_NOTE

    # ------------------------------------------------------------------ #
    #  Initialise or resume loop state                                    #
    # ------------------------------------------------------------------ #
    lease_lost_before_loop = False
    if resumed_state and confirmed_tool and (resumed_state.pending_tools or resumed_state.pending_tool):
        state = resumed_state
        state.paused_for_confirmation = False
        # A state saved before the single bound existed has no budget yet.
        state.token_budget = state.token_budget or default_budget
        # Another member may have tainted the room while this run was paused.
        state.tainted = state.tainted or await sync_to_async(_conversation_tainted)(room_id)
        state.segment_started_monotonic = time.monotonic()

        # D9: every held call runs, in model order, on the one approval. The
        # stored tool_use input is the only thing that runs; a call already
        # dispatched before a crash is marked outcome_unknown and never retried.
        held = list(state.pending_tools or [state.pending_tool])
        gated_blocks: Dict[str, Dict[str, Any]] = {}
        for pending in held:
            if not _lease_live():
                state.end_reason = "lease_lost"
                lease_lost_before_loop = True
                yield AgentEvent("harness", {"text": LEASE_LOST_NOTE})
                break
            call_id = str(pending.get("id") or "")
            if call_id and call_id in state.dispatched_calls:
                result = {
                    "status": "error",
                    "outcome_unknown": True,
                    "message": (
                        "outcome_unknown: this call was dispatched before a "
                        "restart, so it was not run again."
                    ),
                }
            elif persona_unavailable:
                granted = None
                result = {"status": "error", "message": PERSONA_LOOKUP_DENIED_REASON}
            else:
                # Mark the call as dispatched *before* it runs. A crash after
                # this write is never retried automatically.
                state.dispatched_calls.append(call_id)
                marker_ok = await _refresh_dispatched_marker(context, state)
                if not marker_ok:
                    state.dispatched_calls.remove(call_id)
                    result = {
                        "status": "error",
                        "message": (
                            "could not record this call as dispatched safely, "
                            "so nothing was run."
                        ),
                    }
                else:
                    if pending["name"] in _META_TOOL_NAMES:
                        meta_context = _meta_tool_context(pending["name"], context, state)
                        granted = _granted_sub_budget(meta_context)
                        result = await _execute_meta_tool(
                            pending["name"], pending["input"], meta_context,
                            _taint_preferences(preferences, state.tainted), system, tools,
                        )
                    else:
                        granted = None
                        result = await _execute_with_timeout(
                            pending["name"], pending["input"], context,
                        )
                    if pending["name"] == "delegate_task":
                        _apply_delegated_spend(state, result, granted)
                    if _untrusted_source(pending["name"]):
                        _taint_run(state, room_id)

            if not persona_unavailable:
                state.tool_call_count += 1
                state.tool_call_log.append({
                    "name": pending["name"],
                    "input": pending["input"],
                    "output": result,
                    "iteration": state.iteration,
                })
                await _record_receipt(
                    pending["name"], pending["input"], result, context, basis="human",
                )

            yield AgentEvent("tool_result", {
                "name": pending["name"],
                "result": result,
                "input": pending["input"],
                "shown": _sanitize_tool_result(json.dumps(result, default=str)),
            })
            gated_blocks[call_id] = {
                "type": "tool_result",
                "tool_use_id": pending["id"],
                "content": _sanitize_tool_result(json.dumps(result, default=str)),
            }

        state.pending_tools = []
        state.pending_tiers = []
        state.pending_tool = None
        state.pending_tier = None
        state.pending_args_hashes = {}
        # Every call in the step is answered in one user message, in model
        # order: non-gated results saved with the pause, then the gated ones.
        step_blocks = _ordered_step_results(state, gated_blocks)
        state.messages.append({
            "role": "user",
            "content": _with_budget_note(step_blocks, state),
        })

    elif resumed_state and expired_pending:
        # The pause expired before the reply. Nothing ran; the model sees an
        # honest is_error result for every held call so no tool_use is unpaired.
        state = resumed_state
        state.paused_for_confirmation = False
        # The late reply starts with a full base budget of headroom. An expired
        # budget pause must not re-pause immediately after saying nothing ran.
        state.token_budget = min(
            _turn_token_budget(False),
            max(
                state.token_budget or default_budget,
                state.tokens_used + default_budget,
            ),
        )
        state.segment_started_monotonic = time.monotonic()
        # Another member may have tainted the room while this run was paused.
        state.tainted = state.tainted or await sync_to_async(_conversation_tainted)(room_id)
        _append_terminal_results(state, "approval expired")
        state.pending_tools = []
        _append_note_to_last_user(
            state.messages,
            "[Harness: that pause expired before you replied. Nothing ran. Do "
            "not claim the action happened.]",
        )

    elif resumed_state and quota_resumed:
        # The hourly quota blocked the turn and has now freed (T-F3b). Continue
        # from exactly where it stopped: the tool log is intact, so no finished
        # call repeats. This is *not* a budget grant, so the turn's budget and
        # its extension count are untouched; a plain reply resumes it.
        state = resumed_state
        state.segment_started_monotonic = time.monotonic()
        state.awaiting_budget = False
        state.awaiting_quota = False
        state.tainted = state.tainted or await sync_to_async(_conversation_tainted)(room_id)
        _append_note_to_last_user(
            state.messages,
            "[Harness: the hourly token limit has freed up. Continue from where "
            "you stopped; do not repeat work already done.]",
        )

    elif resumed_state and budget_extended:
        # The user granted more budget after the model asked for it. Continue
        # from exactly where the turn stopped: the tool log is intact, so no
        # finished call repeats. The bound grows by one base budget per grant.
        state = resumed_state
        state.segment_started_monotonic = time.monotonic()
        state.awaiting_budget = False
        state.budget_extensions += 1
        # Grow by one base budget per grant, always keeping a full base budget
        # of headroom from where the turn stopped, but never past the
        # configured maximum: above it there is nothing left to grant (T-F3).
        max_budget = _turn_token_budget(False)
        state.token_budget = min(
            max_budget,
            max(
                (state.token_budget or default_budget) + default_budget,
                state.tokens_used + default_budget,
            ),
        )
        state.tainted = state.tainted or await sync_to_async(_conversation_tainted)(room_id)
        _append_note_to_last_user(
            state.messages,
            "[Harness: the user granted more budget. Continue from where you "
            "stopped; do not repeat work already done.]",
        )

    else:
        # Fresh loop
        messages: List[Dict[str, Any]] = []
        if history:
            if getattr(settings, "HISTORY_COMPACTION_ENABLED", True):
                history, compacted = _fit_history_to_budget(
                    history,
                    max_chars=int(getattr(settings, "HISTORY_MAX_CHARS", 60000)),
                    max_messages=int(getattr(settings, "HISTORY_MAX_MESSAGES", 50)),
                )
                if compacted:
                    logger.warning(
                        "Compacted conversation history for room %s: kept %d messages",
                        room_id, len(history),
                    )
                    record_event("context_compacted", {
                        "user_id": user_id,
                        "room_id": room_id,
                        "turn_id": context.get("turn_id"),
                        "kept_messages": len(history),
                        "history_max_chars": int(getattr(settings, "HISTORY_MAX_CHARS", 60000)),
                    })
            messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        state = LoopState(
            messages=messages,
            start_time=time.monotonic(),
            segment_started_monotonic=time.monotonic(),
            wall_started_at=time.time(),
            tainted=(history_tainted or await sync_to_async(_conversation_tainted)(room_id)),
            token_budget=default_budget,
        )
        # The model sees what it has from the very first call (T-F3 follow-up),
        # not only after the first tool batch. The note rides on the new user
        # message, so the cached prompt prefix (system + tools) is untouched.
        if user_message:
            _append_note_to_last_user(
                state.messages,
                _harness_budget_note(state.tokens_used, state.token_budget),
            )
        if pending_note:
            _append_note_to_last_user(state.messages, pending_note)
        if nothing_pending:
            _append_note_to_last_user(
                state.messages,
                "[Harness: nothing was pending, so a bare yes or no approved "
                "nothing. If you are agreeing to something the model said, say "
                "what you mean.]",
            )

    # Track seen tool calls for dedup
    seen_calls: set = set()
    for entry in state.tool_call_log:
        seen_calls.add(_dedup_key(entry["name"], entry["input"]))

    # Consecutive pause_turn responses. Reset by any other stop reason.
    consecutive_pauses = 0
    turn_backed = bool(context.get("turn_id"))

    def _active_elapsed() -> float:
        return state.active_seconds + max(
            0.0, time.monotonic() - state.segment_started_monotonic,
        )

    def _accumulate_active() -> None:
        state.active_seconds = _active_elapsed()
        state.segment_started_monotonic = time.monotonic()

    # ------------------------------------------------------------------ #
    #  Main ReAct loop                                                    #
    # ------------------------------------------------------------------ #
    while state.iteration < HARD_CAP_ITERATIONS and not lease_lost_before_loop:
        # The self-fence sits before the next model call. A worker that lost its
        # lease must not dispatch anything more (#216b-1).
        if not _lease_live():
            state.end_reason = "lease_lost"
            yield AgentEvent("harness", {"text": LEASE_LOST_NOTE})
            break

        # The hard backstop runs on active time, never on a human's wait.
        if _active_elapsed() > HARD_TURN_TIMEOUT_SECONDS:
            state.end_reason = "backstop"
            yield AgentEvent("error", {
                "message": "I ran out of time on this request. "
                           "Here's what I managed so far.",
            })
            break

        # Tool-call runaway backstop. The turn's budget normally ends it first.
        if state.tool_call_count >= HARD_CAP_TOOL_CALLS:
            state.end_reason = "backstop"
            yield AgentEvent("error", {
                "message": "I've reached the safety limit on tool calls for "
                           "this request. Here's what I managed so far.",
            })
            break

        # The one bound: work done this turn, in work tokens (T-F3). Reaching
        # it without the model asking keeps the state exactly like a budget
        # request does, so the user can extend the turn in chat (T-F3 follow-up).
        if state.tokens_used >= state.token_budget:
            state.end_reason = "budget"
            # At the configured maximum there is nothing left to grant, so the
            # turn ends for good: no pause, no state kept, no offer of more.
            if state.tokens_used >= _turn_token_budget(False):
                state.awaiting_budget = False
                yield AgentEvent("error", {
                    "message": _maximum_budget_note(state),
                })
                break
            state.awaiting_budget = True
            _accumulate_active()
            if not _lease_live():
                state.end_reason = "lease_lost"
                yield AgentEvent("harness", {"text": LEASE_LOST_NOTE})
                break
            dropped = []
            if turn_backed:
                dropped = await sync_to_async(_supersede_older_pause_sync)(
                    context, context.get("turn_id"),
                )
            if dropped:
                yield AgentEvent("harness", {
                    "text": _dropped_actions_note(dropped) + "\n\n",
                })
            saved = await _persist_pause_state(context, state, "budget")
            if turn_backed and not saved:
                # Fail closed: never tell the user to reply "more budget" when
                # the paused work could not be recorded.
                state.end_reason = "pause_not_recorded"
                _append_terminal_results(state, "could not record the pause")
                await _fail_turn_now(context, "pause_not_recorded")
                yield AgentEvent("error", {
                    "message": (
                        "I couldn't save this turn's pause safely, so I stopped "
                        "here. Send a new message to continue."
                    ),
                })
                return
            if room_id and user_id and not turn_backed:
                save_loop_state(room_id, user_id, state)
            yield AgentEvent("error", {
                "message": _bound_reached_note(state),
            })
            if turn_backed:
                yield AgentEvent("paused", {
                    "pause_kind": "budget",
                    "active_seconds": state.active_seconds,
                })
                return
            break

        state.iteration += 1

        # Emit thinking event so frontend shows reasoning in timeline
        yield AgentEvent("thinking", {"text": ""})

        # ---- Call LLM ------------------------------------------------ #
        # No keyword router picks the model: the room's chosen model when one is
        # set, else the operator's default provider resolves its own default.
        # The native search tool is rebuilt from the live daily allowance on
        # every request, so one turn cannot exceed the limit (T-F3).
        call_tools = (
            _rebuild_search_tool(tools, user_id, user_location_hint)
            if native_search else tools
        )
        override_provider, override_model = await sync_to_async(_resolve_model_override)(room_id)
        try:
            response = await llm.create_message(
                messages=state.messages,
                system=system,
                tools=call_tools if call_tools else None,
                temperature=0.3,
                max_tokens=4096,
                user_id=user_id,
                model=override_model,
                provider=override_provider,
                use_prompt_cache=True,
            )
        except TokenQuotaExceeded as quota_exc:
            # The hour's work-token quota blocked the call. End the turn the
            # same way a budget stop does: keep the state so the user can
            # resume from where it stopped without redoing finished work, and
            # say plainly that the hourly limit was reached and roughly when
            # enough frees up (T-F3b).
            logger.warning("Agent loop stopped by hourly token quota: %s", quota_exc)
            state.end_reason = "quota"
            state.awaiting_budget = True
            # Mark it as a quota pause, not a budget one, so a reply resumes the
            # turn without inflating its budget (T-F3b).
            state.awaiting_quota = True
            _accumulate_active()
            quota_ttl = _quota_pause_ttl(quota_exc.retry_after_seconds)
            if not _lease_live():
                state.end_reason = "lease_lost"
                yield AgentEvent("harness", {"text": LEASE_LOST_NOTE})
                break
            dropped = []
            if turn_backed:
                dropped = await sync_to_async(_supersede_older_pause_sync)(
                    context, context.get("turn_id"),
                )
            if dropped:
                yield AgentEvent("harness", {
                    "text": _dropped_actions_note(dropped) + "\n\n",
                })
            saved = await _persist_pause_state(context, state, "quota", ttl=quota_ttl)
            if turn_backed and not saved:
                state.end_reason = "pause_not_recorded"
                _append_terminal_results(state, "could not record the pause")
                await _fail_turn_now(context, "pause_not_recorded")
                yield AgentEvent("error", {
                    "message": (
                        "I couldn't save this turn's pause safely, so I stopped "
                        "here. Send a new message to continue."
                    ),
                })
                return
            if room_id and user_id and not turn_backed:
                save_loop_state(
                    room_id, user_id, state, ttl=quota_ttl,
                )
            yield AgentEvent("error", {"message": _quota_reached_note(state, quota_exc)})
            if turn_backed:
                yield AgentEvent("paused", {
                    "pause_kind": "quota",
                    "active_seconds": state.active_seconds,
                })
                return
            break
        except Exception as exc:
            logger.error("Agent loop LLM call failed: %s", exc, exc_info=True)
            state.end_reason = "error"
            yield AgentEvent("error", {"message": f"LLM error: {exc}"})
            break

        content_blocks = response.get("content", [])
        stop_reason = response.get("stop_reason", "end_turn")

        # Count only work: generated tokens plus the input newly read since the
        # previous call. The resent prefix is not charged (T-F3).
        iter_work, state.prev_input_tokens = _work_tokens(response, state.prev_input_tokens)
        state.tokens_used += iter_work
        # Charge the same work to the rolling-hour quota, so a long conversation
        # does not spend the hour's limit just by being resent (T-F3b).
        llm.record_work_tokens(iter_work, user_id)

        if (
            not state.budget_warning_sent
            and state.tokens_used >= state.token_budget * TURN_TOKEN_WARNING_RATIO
        ):
            state.budget_warning_sent = True
            yield AgentEvent("text_delta", {
                "text": "\n\n*(Running low on this request's budget.)*\n\n",
            })

        # Track web search usage
        search_count = _count_search_uses(response)
        if search_count > 0:
            _record_search_usage(user_id, search_count)
            record_event("web_search_used", {
                "user_id": user_id,
                "room_id": room_id,
                "turn_id": context.get("turn_id"),
                "searches": search_count,
                "remaining": get_remaining_searches(user_id),
            })
            _taint_run(state, room_id)

        # Append the assistant message to the conversation
        state.messages.append({"role": "assistant", "content": content_blocks})

        # ---- Emit the model's text as one block ---------------------- #
        text = redact_sensitive_text(_extract_text(content_blocks))
        if text:
            yield AgentEvent("text_delta", {"text": text})

        # ---- If LLM is done talking, exit the loop ------------------- #
        if stop_reason == "end_turn":
            state.end_reason = "finished"
            break

        if stop_reason == "max_tokens":
            state.end_reason = "length_limit"
            yield AgentEvent("error", {
                "message": LENGTH_LIMIT_NOTE,
                "end_reason": "length_limit",
            })
            break

        # ---- Handle tool calls --------------------------------------- #
        if stop_reason == "tool_use":
            consecutive_pauses = 0
            tool_calls = _extract_tool_calls(content_blocks)

            # The model asking for more budget ends the turn honestly: answer
            # every call so the message contract stays valid, save the state,
            # and wait for the user. Nothing else in the batch runs (T-F3).
            if not persona_unavailable and any(tc["name"] == REQUEST_MORE_BUDGET_TOOL for tc in tool_calls):
                # The turn's budget is already at the configured maximum either
                # because it was granted up to the ceiling or because spending
                # reached it: a further grant could not raise it, so it is an
                # empty offer. End for good with the bound path's plain note.
                at_max = (
                    state.token_budget >= _turn_token_budget(False)
                    or state.tokens_used >= _turn_token_budget(False)
                )
                state.messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tc["id"],
                            "content": (
                                (
                                    "The maximum budget for this turn is already "
                                    "used; no further budget is available."
                                    if at_max
                                    else "Budget request received; the user has "
                                         "been asked. Waiting for their reply."
                                )
                                if tc["name"] == REQUEST_MORE_BUDGET_TOOL
                                else "Skipped: the model asked for more budget first."
                            ),
                            "is_error": tc["name"] != REQUEST_MORE_BUDGET_TOOL,
                        }
                        for tc in tool_calls
                    ],
                })
                if at_max:
                    # The grant ceiling is reached: end for good, keep no state,
                    # and never offer another grant (T-F3).
                    state.end_reason = "budget"
                    state.awaiting_budget = False
                    yield AgentEvent("error", {
                        "message": _maximum_budget_note(state),
                    })
                    break
                state.awaiting_budget = True
                state.end_reason = "asked_for_more"
                _accumulate_active()
                if not _lease_live():
                    state.end_reason = "lease_lost"
                    yield AgentEvent("harness", {"text": LEASE_LOST_NOTE})
                    break
                dropped = []
                if turn_backed:
                    dropped = await sync_to_async(_supersede_older_pause_sync)(
                        context, context.get("turn_id"),
                    )
                if dropped:
                    yield AgentEvent("harness", {
                        "text": _dropped_actions_note(dropped) + "\n\n",
                    })
                saved = await _persist_pause_state(context, state, "budget")
                if turn_backed and not saved:
                    state.end_reason = "pause_not_recorded"
                    await _fail_turn_now(context, "pause_not_recorded")
                    yield AgentEvent("error", {
                        "message": (
                            "I couldn't save this turn's pause safely, so I "
                            "stopped here. Send a new message to continue."
                        ),
                    })
                    return
                if room_id and user_id and not turn_backed:
                    save_loop_state(room_id, user_id, state)
                if not text:
                    reason = next(
                        (tc["input"].get("reason") for tc in tool_calls
                         if tc["name"] == REQUEST_MORE_BUDGET_TOOL
                         and isinstance(tc.get("input"), dict)
                         and tc["input"].get("reason")),
                        "",
                    ) or "I need more budget to finish this."
                    yield AgentEvent("text_delta", {"text": reason})
                record_event("agent_budget_requested", {
                    "user_id": user_id,
                    "room_id": room_id,
                    "turn_id": context.get("turn_id"),
                    "used": state.tokens_used,
                    "budget": state.token_budget,
                })
                yield AgentEvent("budget_request", {
                    "used": state.tokens_used,
                    "budget": state.token_budget,
                })
                if turn_backed and saved:
                    yield AgentEvent("paused", {
                        "pause_kind": "budget",
                        "active_seconds": state.active_seconds,
                    })
                    return
                break

            # Re-read room autonomy each iteration so a mid-run arm/revoke/expiry
            # is honored (the coordinator injects this refresher).
            _refresher = context.get("refresh_shell_autonomy")
            if callable(_refresher):
                preferences = await _refresher(preferences)

            # Separate into auto-execute, needs-confirmation, and refused. The
            # taint is snapshotted: this batch was decided on it.
            run_tainted = state.tainted
            safe_calls, pause_calls, denied_calls = _bucket_tool_calls(
                tool_calls, preferences,
                persona=persona,
                tainted=run_tainted,
            )

            # Checked again immediately before dispatch: a reaper may have
            # fenced this owner while the batch was being decided (#216b-1).
            if not _lease_live():
                state.end_reason = "lease_lost"
                yield AgentEvent("harness", {"text": LEASE_LOST_NOTE})
                break

            # ---- Execute safe calls (possibly in parallel) ----------- #
            tool_results: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []

            if safe_calls:
                async def _run_safe(
                    tc: Dict[str, Any],
                    delegated_grant: Optional[int] = None,
                ) -> Tuple[Dict[str, Any], Dict[str, Any], Optional[int]]:
                    dedup = _dedup_key(tc["name"], tc["input"])
                    is_retry = dedup in seen_calls
                    if is_retry:
                        # The latest identical call in this turn decides. A
                        # failure is retried under the bound below. A success
                        # is answered from the earlier result, except for a
                        # shell command, which runs again: its output can change.
                        earlier = next(
                            (e["output"] for e in reversed(state.tool_call_log)
                             if e["name"] == tc["name"]
                             and _dedup_key(e["name"], e["input"]) == dedup),
                            None,
                        )
                        if isinstance(earlier, dict) and earlier.get("status") == "success":
                            if tc["name"] not in _SHELL_TOOL_NAMES:
                                repeated = dict(earlier)
                                repeated["repeated"] = True
                                repeated["note"] = (
                                    "This exact call already ran in this turn. "
                                    "This is the earlier result; nothing ran again."
                                )
                                return tc, repeated, None
                            is_retry = False

                    service = _service_for_tool(tc["name"])
                    if _circuit_breaker_open(service):
                        return tc, {
                            "status": "error",
                            "message": _circuit_open_message(service),
                        }, None

                    if is_retry:
                        # Retrying a previously-failed identical call.
                        retries = state.retry_counts.get(
                            _retry_key(tc["name"], tc["input"]), 0,
                        )
                        if retries >= MAX_RETRIES_PER_TOOL:
                            label = _describe_tool_call(tc["name"], tc["input"])
                            return tc, {
                                "status": "error",
                                "message": (
                                    f"Stopped retrying {label} after {retries} failures in this turn. "
                                    "It can be tried again in a new message."
                                ),
                            }, None
                        await asyncio.sleep(_retry_backoff_seconds(retries))
                    seen_calls.add(dedup)

                    granted = None
                    try:
                        # Meta-tools use their own executor
                        if tc["name"] in _META_TOOL_NAMES:
                            meta_context = _meta_tool_context(
                                tc["name"], context, state, delegated_grant,
                            )
                            granted = _granted_sub_budget(meta_context)
                            result = await _execute_meta_tool(
                                tc["name"], tc["input"], meta_context,
                                _taint_preferences(preferences, run_tainted), system, tools,
                            )
                        else:
                            result = await _execute_with_timeout(tc["name"], tc["input"], context)
                    except Exception as exc:
                        logger.error("Tool %s raised unexpectedly: %s", tc["name"], exc)
                        result = {"status": "error", "message": str(exc)}

                    # Track retries + circuit breaker state. A harness refusal
                    # (e.g. a budget refusal) is not a service failure and never
                    # counts toward the retry cap or the breaker (T-F3).
                    _account_tool_result(state, tc["name"], tc["input"], service, result)
                    return tc, result, granted

                # Run in parallel. Delegations in one batch share a single
                # delegated share, so they cannot together outspend the parent.
                batch_grants = _batch_delegated_grants(state, safe_calls)
                tasks = [
                    _run_safe(tc, batch_grants.get(index))
                    for index, tc in enumerate(safe_calls)
                ]
                completed = await asyncio.gather(*tasks, return_exceptions=True)

                for item in completed:
                    if isinstance(item, Exception):
                        logger.error("Parallel tool error: %s", item)
                        continue
                    tc, result, granted = item
                    if tc["name"] == "delegate_task":
                        # Charge the sub-run's work tokens to the turn so the
                        # single bound reflects them (T-F3).
                        _apply_delegated_spend(state, result, granted)
                    tool_results.append((tc, result))
                    state.tool_call_count += 1
                    state.tool_call_log.append({
                        "name": tc["name"],
                        "input": tc["input"],
                        "output": result,
                        "iteration": state.iteration,
                    })
                    if _untrusted_source(tc["name"]):
                        _taint_run(state, room_id)
                    yield AgentEvent("tool_start", {"name": tc["name"], "input": tc["input"]})
                    yield AgentEvent("tool_result", {
                        "name": tc["name"],
                        "result": result,
                        "input": tc["input"],
                        "shown": _sanitize_tool_result(json.dumps(result, default=str)),
                    })

                    # Receipt (fire-and-forget)
                    await _record_receipt(
                        tc["name"], tc["input"], result, context,
                        basis=_shell_approval_basis(tc, preferences, run_tainted),
                    )

            # ---- Build tool_result blocks (auto + refused) ------------ #
            tool_result_blocks = []
            for tc, result in tool_results:
                tool_result_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": _sanitize_tool_result(json.dumps(result, default=str)),
                })

            # Refused calls (shell envelope or persona scope) never run.
            for tc, reason in denied_calls:
                tool_result_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": reason,
                    "is_error": True,
                })

            # ---- Handle calls that need confirmation ------------------ #
            if pause_calls:
                # D9: hold every gated call in this step. They are shown in one
                # prompt and resolved together; no call is answered with
                # "Skipped" here. Non-gated results are kept in the paused
                # state and emitted in one user message on resolution.
                held_calls = [tc for tc, _tier in pause_calls]
                held_tiers = [tier for _tc, tier in pause_calls]
                state.pending_tools = held_calls
                state.pending_tiers = held_tiers
                state.pending_tool = held_calls[0]
                state.pending_tier = held_tiers[0]
                state.pending_step_results = list(tool_result_blocks)
                state.pending_args_hashes = {
                    str(tc.get("id") or ""): _args_hash(tc.get("input"))
                    for tc in held_calls
                }
                state.paused_for_confirmation = True
                short_id = hashlib.sha256(
                    f"{room_id}:{user_id}:{time.time()}:{len(held_calls)}".encode("utf-8")
                ).hexdigest()[:8]
                state.approval_short_id = short_id

                safe_calls = [
                    {"name": tc["name"], "input": _display_params(tc["input"])}
                    for tc in held_calls
                ]
                display_text = build_confirmation_prompt_many(
                    safe_calls, short_id=short_id,
                )
                # The chat prompt is what the user approves, so it shows the
                # exact stored input (restricted values as named placeholders).
                # The plaintext row keeps the masked display.
                confirmation_text = _approval_prompt_for_calls(
                    held_calls, short_id,
                )

                # Dry-run effects for the approval card (never executes).
                effects = None
                call_effects = []
                for tc in held_calls:
                    try:
                        preview = await preview_tool(tc["name"], tc["input"], context)
                    except Exception as exc:
                        logger.warning("Preview failed for %s: %s", tc["name"], exc)
                        preview = None
                    call_effects.append(preview)
                if call_effects:
                    effects = call_effects[0]

                _accumulate_active()
                if not _lease_live():
                    state.end_reason = "lease_lost"
                    yield AgentEvent("harness", {"text": LEASE_LOST_NOTE})
                    break
                if turn_backed:
                    await sync_to_async(_supersede_older_pause_sync)(
                        context, context.get("turn_id"),
                    )

                # The durable row is written BEFORE the Turn's paused CAS, so a
                # paused Turn can never exist without the approval it needs.
                if not _lease_live():
                    state.end_reason = "lease_lost"
                    yield AgentEvent("harness", {"text": LEASE_LOST_NOTE})
                    break
                row_saved = True
                if room_id and user_id:
                    try:
                        await save_pending_confirmation(
                            room_id, user_id, held_calls[0], display_text,
                            effects=effects,
                            turn_id=context.get("turn_id"),
                            calls=held_calls,
                            expires_at=(
                                timezone.now() + timedelta(seconds=_pause_ttl_seconds())
                            ),
                            short_id=short_id,
                        )
                    except Exception as exc:
                        logger.error(
                            "Could not save the approval row: %s",
                            exc.__class__.__name__,
                        )
                        row_saved = False

                if not row_saved:
                    _append_terminal_results(state, "could not record the approval")
                    await _fail_turn_now(context, "approval_not_recorded")
                    yield AgentEvent("error", {
                        "message": (
                            "I could not record that approval safely, so I did "
                            "not run it. Please try again."
                        ),
                    })
                    return

                if turn_backed:
                    saved = await _persist_pause_state(context, state, "approval")
                else:
                    saved = True
                    if room_id and user_id:
                        save_loop_state(room_id, user_id, state)
                if not saved:
                    # Fail closed: an unrecorded pause must never leave a gated
                    # action armed for a later reply.
                    if room_id and user_id:
                        await _resolve_pending_approval(
                            room_id, user_id, "cancelled",
                            comment="The pause could not be recorded.",
                            turn_id=context.get("turn_id"),
                        )
                    _append_terminal_results(state, "could not record the pause")
                    await _fail_turn_now(context, "pause_not_recorded")
                    yield AgentEvent("error", {
                        "message": (
                            "I could not record that approval safely, so I did "
                            "not run it. Please try again."
                        ),
                    })
                    return

                yield AgentEvent("confirmation", {
                    "message": confirmation_text,
                    "tool_name": held_calls[0]["name"],
                    "tool_input": held_calls[0]["input"],
                    "calls": [
                        {
                            "tool_name": tc["name"],
                            "tool_input": tc["input"],
                            "effects": call_effects[index]
                            if index < len(call_effects) else None,
                        }
                        for index, tc in enumerate(held_calls)
                    ],
                    "effects": effects,
                    "tier": held_tiers[0],
                    "approval_id": short_id,
                })
                yield AgentEvent("paused", {
                    "pause_kind": "approval",
                    "active_seconds": state.active_seconds,
                })
                # Pause the loop — the same Turn resumes after an exact approval.
                return

            if tool_result_blocks:
                state.messages.append({
                    "role": "user",
                    "content": _with_budget_note(tool_result_blocks, state),
                })

            # If the safety policy blocked several tools in a row, stop — don't
            # let the model keep rewording attempts until its budget is gone.
            recent = state.tool_call_log[-3:]
            if len(recent) == 3 and all(_is_policy_block(entry.get("output")) for entry in recent):
                state.end_reason = "safety_stop"
                yield AgentEvent("error", {
                    "message": "I was blocked by the safety policy repeatedly, so I stopped this request.",
                })
                break

            # Continue the loop — LLM will see results and decide next step

        elif stop_reason == "pause_turn":
            # Anthropic paused while a server-side tool (its web search) is
            # still running. Send the paused turn back (the assistant message
            # was appended above) so the model finishes. A run of pauses is
            # bounded: a provider that pauses forever must still end the turn.
            consecutive_pauses += 1
            if consecutive_pauses > MAX_CONSECUTIVE_PAUSES:
                state.end_reason = "pause_limit"
                yield AgentEvent("error", {
                    "message": "The model paused repeatedly and did not finish this request.",
                })
                break
            continue

        else:
            # A stop reason we do not handle. A provider refusal is still an
            # answer; anything else is an unknown ending and must not be
            # recorded as a completed refusal.
            if stop_reason == "refusal":
                state.end_reason = "refusal"
                yield AgentEvent("text_delta", {
                    "text": "\n\n*(The model declined to answer this request.)*",
                })
            else:
                state.end_reason = "unknown_stop"
                yield AgentEvent("error", {
                    "message": f"The model stopped for an unhandled reason ({stop_reason}).",
                })
            break

    # ---- Loop finished ----------------------------------------------- #
    # This is a terminal ending: a Turn-backed pause returned above and kept
    # its state. A legacy cache budget pause stays too, so it can be resumed.
    if room_id and user_id and not (not turn_backed and state.awaiting_budget):
        clear_loop_state(room_id, user_id)
    if turn_backed:
        from orchestration import turns as turns_mod

        try:
            await turns_mod.aclear_paused_state(context.get("turn_id"))
        except Exception:
            # Cleanup must never turn a finished reply into an error.
            pass

    if not state.end_reason:
        state.end_reason = "finished"

    _accumulate_active()
    if state.wall_started_at:
        elapsed = round(max(0.0, time.time() - state.wall_started_at), 2)
    else:
        elapsed = round(state.active_seconds, 2)

    # One tracker pass for the whole turn, not one per tool call. The summary
    # the next turn reads carries the entities/actions, never a success claim.
    if state.tool_call_log:
        try:
            await update_memory_state_batch(context, state.tool_call_log)
        except Exception:
            pass

    # Full loop transcript for audit trail. Neither input values nor input key
    # names are copied into the telemetry file or the server log — only the
    # number of keys and the size, since a key name is model-written text.
    transcript = []
    for entry in state.tool_call_log:
        tool_input = entry.get("input")
        transcript.append({
            "tool": entry.get("name"),
            "status": entry.get("output", {}).get("status") if isinstance(entry.get("output"), dict) else None,
            "iteration": entry.get("iteration"),
            "input_key_count": len(tool_input) if isinstance(tool_input, dict) else 0,
            "input_chars": len(json.dumps(tool_input, default=str)),
        })

    record_event("agent_loop_done", {
        "user_id": user_id,
        "room_id": room_id,
        "turn_id": context.get("turn_id"),
        "iterations": state.iteration,
        "tool_calls": state.tool_call_count,
        "tokens_used": state.tokens_used,
        "budget": state.token_budget,
        "budget_used": state.tokens_used,
        "end_reason": state.end_reason,
        "budget_extensions": state.budget_extensions,
        "elapsed": elapsed,
        "active_seconds": state.active_seconds,
        "tools_used": list({e["name"] for e in state.tool_call_log}),
        "transcript": transcript,
    })

    yield AgentEvent("done", {
        "iterations": state.iteration,
        "tool_calls": state.tool_call_count,
        "elapsed": elapsed,
        "active_seconds": state.active_seconds,
        "budget": state.token_budget,
        "budget_used": state.tokens_used,
        "end_reason": state.end_reason,
    })


# --------------------------------------------------------------------------- #
#  Public helpers for the consumer                                            #
# --------------------------------------------------------------------------- #

_STATE_UNSET = object()


async def has_pending_agent_state(room_id: int, user_id: int) -> bool:
    """Whether an approval pause is waiting (Turn first, then the legacy row)."""
    from orchestration import turns as turns_mod

    turn = await turns_mod.aget_paused_turn(room_id, user_id)
    if turn is not None and turn.pause_kind == "approval":
        return True
    record = await sync_to_async(_pending_approval_record)(room_id, user_id)
    return record is not None


async def has_pending_budget_state(
    room_id: int, user_id: int, *, state=_STATE_UNSET,
) -> bool:
    """Whether a turn is paused waiting for the user to grant more budget."""
    if state is _STATE_UNSET:
        from orchestration import turns as turns_mod

        turn = await turns_mod.aget_paused_turn(room_id, user_id)
        if turn is not None:
            return turn.pause_kind in ("budget", "quota")
        state = await sync_to_async(load_loop_state)(room_id, user_id)
    return bool(state and state.awaiting_budget)


async def pending_resume_kind(
    room_id: int, user_id: int, *, state=_STATE_UNSET,
) -> str:
    """Which pause is waiting for a reply: ``"quota"``, ``"budget"`` or ``""``."""
    from orchestration import turns as turns_mod

    if state is _STATE_UNSET:
        turn = await turns_mod.aget_paused_turn(room_id, user_id)
        if turn is not None and turn.pause_kind in ("budget", "quota"):
            return turn.pause_kind
        state = await sync_to_async(load_loop_state)(room_id, user_id)
    if not state or not state.awaiting_budget:
        return ""
    return "quota" if (state.awaiting_quota or state.end_reason == "quota") else "budget"


async def pending_approval_prompt(room_id: int, user_id: int) -> Dict[str, Any]:
    """The pending prompt and its short id, read from the Turn or the row.

    A Turn-backed pause rebuilds the prompt from its encrypted state, so the
    re-shown prompt is again the exact input that will run (the plaintext row
    only holds the masked display).
    """
    from orchestration import turns as turns_mod

    turn = await turns_mod.aget_paused_turn(room_id, user_id)
    if turn is not None and turn.pause_kind == "approval":
        state = await sync_to_async(_load_turn_state_sync)(turn)
        short_id = state.approval_short_id if state else ""
        message = _approval_prompt_for_state(state)
        if not message:
            record = await sync_to_async(_pending_approval_record)(
                room_id, user_id, turn_id=turn.pk,
            )
            message = record.approval_message if record else ""
        return {"message": message, "short_id": short_id, "kind": "approval"}
    record = await sync_to_async(_pending_approval_record)(room_id, user_id)
    if record is not None:
        return {
            "message": record.approval_message,
            "short_id": str((record.metadata or {}).get("short_id") or ""),
            "kind": "approval",
        }
    return {}


async def has_expired_pause(room_id: int, user_id: int) -> bool:
    """Whether the most recent turn is a pause that just expired.

    A newer turn means the user has moved on; a bare yes then is not a late
    answer to an old prompt and must not hijack it.
    """
    from orchestration import turns as turns_mod
    from orchestration.models import Turn

    turn = await turns_mod.aget_expired_pause(room_id, user_id, within_seconds=3600)
    if turn is None:
        return False
    latest = await sync_to_async(
        lambda: Turn.objects.filter(room_id=room_id, user_id=user_id)
        .order_by("-created_at").first()
    )()
    return latest is None or str(latest.pk) == str(turn.pk)


def _expiry_text(expires_at, *, noun: str = "pause") -> str:
    if not expires_at:
        return f"That {noun} expired"
    try:
        when = timezone.localtime(expires_at).strftime("%H:%M")
        return f"That {noun} expired at {when}"
    except Exception:
        return f"That {noun} expired"


async def _claim_turn_for_resume(context: Dict[str, Any]) -> tuple:
    """CAS the paused Turn to running; a coordinator may already have won it.

    A resume without a Turn record is refused: there is no legacy cache path.
    A ``turn_id`` that points at no Turn is also refused rather than falling
    back to whichever Turn happens to be paused.
    """
    from orchestration import turns as turns_mod

    turn_id = context.get("turn_id")
    if not turn_id:
        return None, False
    turn = await turns_mod.aget_turn(turn_id)
    if turn is None:
        return None, False
    if (
        (context.get("room_id") is not None and str(turn.room_id) != str(context.get("room_id")))
        or (context.get("user_id") is not None and str(turn.user_id) != str(context.get("user_id")))
    ):
        # A Turn id from another room/user is never a resume handle.
        return None, False
    if turn.state in ("running", "committing"):
        # The coordinator registers the lease when it wins the paused -> running
        # CAS. Registering an already-running row here could renew another
        # writer's lease, so this path only observes it.
        return turn, True
    if turn.state != "paused":
        return turn, False
    if turn.pause_expires_at and turn.pause_expires_at <= timezone.now():
        await turns_mod.aexpire_pause(turn)
        return turn, False
    moved = await turns_mod.atransition(
        str(turn.pk), turn.version, "running",
        from_state="paused", pause_kind=None, pause_expires_at=None, end_reason=None,
    )
    if moved:
        from orchestration.turn_leases import register_live_turn

        register_live_turn(str(turn.pk))
    return turn, moved


async def resume_after_confirmation(
    *,
    context: Dict[str, Any],
    preferences: Optional[Dict[str, Any]] = None,
    context_prompt: str = "",
    memory_summary: str = "",
    environment_block: str = "",
) -> AsyncGenerator[AgentEvent, None]:
    """Resume a paused action only on an exact approval.

    The stored tool_use input is the only thing that runs, and it is re-hashed
    against the value recorded when the pause was made. A conditional update on
    the approval row and the Turn's ``paused -> running`` CAS together make the
    dispatch single-consumer, including the bounded tier.
    """
    room_id = context.get("room_id")
    user_id = context.get("user_id")

    turn, claimed = await _claim_turn_for_resume(context)
    if not claimed:
        expires = turn.pause_expires_at if turn is not None else None
        yield AgentEvent("error", {
            "message": f"{_expiry_text(expires)}; nothing ran.",
        })
        return

    # The coordinator may have done the paused -> running CAS before calling
    # us. In that case this request owns the Turn; otherwise another writer
    # already owns it and a missing row must not end their turn.
    already_running = bool(turn is not None and turn.state in ("running", "committing"))

    async def _deny(message: str, *, reason: str, reject_row: bool = False):
        if reject_row and room_id and user_id:
            await _resolve_pending_approval(
                room_id, user_id, "rejected", comment=reason,
                turn_id=turn.pk if turn is not None else None,
            )
        _append_terminal_results(state, reason)
        if turn is not None and not already_running:
            await _fail_turn_now(context, "approval_not_confirmed")
        yield AgentEvent("error", {"message": message})

    state = await _load_resume_state(context)
    if state is None:
        if turn is not None and not already_running:
            await _fail_turn_now(context, "approval_state_lost")
        await _resolve_pending_approval(
            room_id, user_id, "rejected",
            comment="Loop state lost or unreadable before confirmation.",
        )
        yield AgentEvent("error", {
            "message": "The pending action is no longer available to confirm.",
        })
        return

    held = list(state.pending_tools or ([state.pending_tool] if state.pending_tool else []))
    if not held:
        if turn is not None and not already_running:
            await _fail_turn_now(context, "approval_no_held_call")
        await _resolve_pending_approval(
            room_id, user_id, "rejected", comment="No held call before confirmation.",
        )
        yield AgentEvent("error", {
            "message": "The pending action is no longer available to confirm.",
        })
        return

    # A Turn-backed resume needs the row that belongs to this same Turn.
    record = await sync_to_async(_pending_approval_record)(
        room_id, user_id, turn_id=turn.pk if turn is not None else None,
    )
    if record is None:
        if already_running:
            yield AgentEvent("error", {
                "message": "That action was already confirmed or cancelled.",
            })
            return
        async for event in _deny(
            "No pending approval matches this turn, so nothing ran. Ask again "
            "for a fresh approval.",
            reason="no pending approval row",
        ):
            yield event
        return

    recorded = (record.metadata or {}).get("calls") or []
    stored_ids = {str(c.get("tool_call_id") or "") for c in recorded}
    held_ids = {str(call.get("id") or "") for call in held}
    stored_hashes = dict(state.pending_args_hashes or {})
    # An approval authorises exactly the call set and arguments that were shown.
    hashes_valid = (
        set(stored_hashes) == held_ids
        and all(stored_hashes.get(call_id) for call_id in held_ids)
        and all(
            stored_hashes.get(str(call.get("id") or "")) == _args_hash(call.get("input"))
            for call in held
        )
    )
    if stored_ids != held_ids or not hashes_valid:
        async for event in _deny(
            "The stored action no longer matches what was shown, so nothing "
            "ran. Ask again for a fresh approval.",
            reason="stored call set or arguments changed before confirmation",
            reject_row=True,
        ):
            yield event
        return

    consumed = await sync_to_async(_consume_pending_approval_sync)(
        room_id, user_id, user_id,
        turn.pk if turn is not None else None,
    )
    if not consumed:
        if not already_running:
            await _fail_turn_now(context, "approval_consume_failed")
        yield AgentEvent("error", {
            "message": "That action was already confirmed or cancelled.",
        })
        return

    clear_loop_state(room_id, user_id)

    async for event in run_agent_loop(
        user_message="",  # Not used on resume
        context=context,
        preferences=preferences,
        context_prompt=context_prompt,
        memory_summary=memory_summary,
        environment_block=environment_block,
        resumed_state=state,
        confirmed_tool=True,
    ):
        yield event


async def resume_after_expiry(
    *,
    context: Dict[str, Any],
    preferences: Optional[Dict[str, Any]] = None,
    context_prompt: str = "",
    memory_summary: str = "",
    environment_block: str = "",
) -> AsyncGenerator[AgentEvent, None]:
    """Answer a late reply honestly: the pause expired, nothing ran.

    The model still gets one call with an ``is_error`` result for every held
    tool_use, so history never carries an unpaired block.
    """
    from orchestration import turns as turns_mod

    room_id = context.get("room_id")
    user_id = context.get("user_id")
    turn = await turns_mod.aget_expired_pause(room_id, user_id)
    if turn is None:
        yield AgentEvent("error", {
            "message": "There is no expired pause to answer.",
        })
        return
    state = await sync_to_async(_load_turn_state_sync)(turn)
    if state is None:
        yield AgentEvent("harness", {
            "text": f"{_expiry_text(turn.pause_expires_at)}; nothing ran.",
        })
        return
    # The expired turn is answered now; it must not trigger again.
    if room_id and user_id:
        await _resolve_pending_approval(
            room_id, user_id, "timed_out",
            comment="Approval expired before the reply.",
            turn_id=turn.pk,
        )
    state.pending_tools = list(
        state.pending_tools or ([state.pending_tool] if state.pending_tool else [])
    )
    noun = "approval" if turn.pause_kind == "approval" else "pause"
    yield AgentEvent("harness", {
        "text": f"{_expiry_text(turn.pause_expires_at, noun=noun)}; nothing ran.\n\n",
    })
    try:
        async for event in run_agent_loop(
            user_message="",
            context=context,
            preferences=preferences,
            context_prompt=context_prompt,
            memory_summary=memory_summary,
            environment_block=environment_block,
            resumed_state=state,
            expired_pending=True,
        ):
            yield event
    finally:
        # The synthetic results have been produced; keep no encrypted body.
        await turns_mod.aclear_paused_state(turn.pk)


async def decline_after_expiry(
    *,
    context: Dict[str, Any],
) -> AsyncGenerator[AgentEvent, None]:
    """Answer a late no/cancel honestly and run nothing.

    The expired pause is closed for good: its held calls get synthetic
    ``is_error`` results so the history pairing stays valid, the pending row is
    marked timed out, and no model call is made.
    """
    from orchestration import turns as turns_mod

    room_id = context.get("room_id")
    user_id = context.get("user_id")
    turn = await turns_mod.aget_expired_pause(room_id, user_id)
    if turn is None:
        yield AgentEvent("error", {
            "message": "There is no expired pause to answer.",
        })
        return
    state = await sync_to_async(_load_turn_state_sync)(turn)
    if room_id and user_id:
        await _resolve_pending_approval(
            room_id, user_id, "timed_out",
            comment="Approval expired before the reply.",
            turn_id=turn.pk,
        )
    if state is not None:
        state.pending_tools = list(
            state.pending_tools
            or ([state.pending_tool] if state.pending_tool else [])
        )
        _append_terminal_results(state, "approval expired")
    noun = "approval" if turn.pause_kind == "approval" else "pause"
    yield AgentEvent("harness", {
        "text": f"{_expiry_text(turn.pause_expires_at, noun=noun)}; nothing ran.\n\n",
    })
    # The synthetic results have been produced; keep no encrypted body.
    await turns_mod.aclear_paused_state(turn.pk)


async def resume_after_quota(
    *,
    context: Dict[str, Any],
    preferences: Optional[Dict[str, Any]] = None,
    context_prompt: str = "",
    memory_summary: str = "",
    environment_block: str = "",
) -> AsyncGenerator[AgentEvent, None]:
    """Continue a turn the hourly token quota stopped, from where it stopped."""
    room_id = context.get("room_id")
    user_id = context.get("user_id")
    turn, claimed = await _claim_turn_for_resume(context)
    if not claimed:
        expires = turn.pause_expires_at if turn is not None else None
        yield AgentEvent("error", {
            "message": f"{_expiry_text(expires)}; nothing ran.",
        })
        return
    state = await _load_resume_state(context)
    if not state or not (state.awaiting_quota or state.end_reason == "quota"):
        yield AgentEvent("error", {
            "message": "There is no paused turn waiting for the hourly limit to free up.",
        })
        return
    clear_loop_state(room_id, user_id)
    async for event in run_agent_loop(
        user_message="",  # Not used on resume
        context=context,
        preferences=preferences,
        context_prompt=context_prompt,
        memory_summary=memory_summary,
        environment_block=environment_block,
        resumed_state=state,
        quota_resumed=True,
    ):
        yield event


async def resume_with_more_budget(
    *,
    context: Dict[str, Any],
    preferences: Optional[Dict[str, Any]] = None,
    context_prompt: str = "",
    memory_summary: str = "",
    environment_block: str = "",
) -> AsyncGenerator[AgentEvent, None]:
    """Continue a turn whose model asked for more budget, from where it stopped."""
    room_id = context.get("room_id")
    user_id = context.get("user_id")
    turn, claimed = await _claim_turn_for_resume(context)
    if not claimed:
        expires = turn.pause_expires_at if turn is not None else None
        yield AgentEvent("error", {
            "message": f"{_expiry_text(expires)}; nothing ran.",
        })
        return
    state = await _load_resume_state(context)
    if not state or not state.awaiting_budget:
        yield AgentEvent("error", {
            "message": "There is no paused turn waiting for more budget.",
        })
        return
    clear_loop_state(room_id, user_id)
    async for event in run_agent_loop(
        user_message="",  # Not used on resume
        context=context,
        preferences=preferences,
        context_prompt=context_prompt,
        memory_summary=memory_summary,
        environment_block=environment_block,
        resumed_state=state,
        budget_extended=True,
    ):
        yield event


async def cancel_pending_action(
    room_id: int, user_id: int,
) -> Optional[str]:
    """Cancel a pending confirmation and clean up state (durable)."""
    from orchestration import turns as turns_mod

    turn = await turns_mod.aget_paused_turn(room_id, user_id)
    state = await load_pending_state(room_id, user_id)
    held = list(state.pending_tools or ([state.pending_tool] if state and state.pending_tool else [])) if state else []
    if turn is not None:
        await turns_mod.atransition(
            str(turn.pk), turn.version, "cancelled",
            from_state="paused", end_reason="cancelled",
        )
    record = await sync_to_async(_pending_approval_record)(room_id, user_id)
    if record is None and not held:
        clear_loop_state(room_id, user_id)
        return None
    await _resolve_pending_approval(
        room_id, user_id, "cancelled", reviewed_by_id=user_id,
        turn_id=turn.pk if turn is not None else None,
    )
    clear_loop_state(room_id, user_id)
    if held:
        names = ", ".join(
            (call.get("name") or "action").replace("_", " ") for call in held
        )
        return f"Cancelled the pending {names}."
    tool_name = (record.action if record is not None else "") or "action"
    return f"Cancelled the pending {tool_name.replace('_', ' ')}."


async def dismiss_pending_confirmation(room_id: int, user_id: int) -> None:
    """Silently clear a pending confirmation the user did not act on."""
    from orchestration import turns as turns_mod

    turn = await turns_mod.aget_paused_turn(room_id, user_id)
    if turn is not None:
        await turns_mod.atransition(
            str(turn.pk), turn.version, "cancelled",
            from_state="paused", end_reason="dismissed",
        )
    await _resolve_pending_approval(
        room_id, user_id, "cancelled",
        turn_id=turn.pk if turn is not None else None,
    )
    clear_loop_state(room_id, user_id)
