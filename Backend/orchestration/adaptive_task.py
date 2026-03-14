"""Adaptive task state and action registry helpers."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import logging
import re

from asgiref.sync import sync_to_async
from django.core.cache import cache

from orchestration.action_catalog import get_action_definition as get_catalog_action_definition
from orchestration.telemetry import record_event

logger = logging.getLogger(__name__)

TASK_TTL_SECONDS = 60 * 60
TASK_VERSION = 1
SUMMARY_PARAM_CANDIDATES = ("text", "message", "content")
RESULT_TTL_SECONDS = 60 * 60
_OPTION_PARAM_HINTS = ("item_id", "option", "selection")
MODE_TTL_SECONDS = 60 * 60 * 24 * 30
DEFAULT_PAUSE_SECONDS = 60 * 10
SOCIAL_PAUSE_SECONDS = 60 * 30
_SMALL_TALK_RE = re.compile(
    r"\b(hi|hello|hey|how are you|how've you|how have you|whats up|what's up|sup|good morning|good afternoon|good evening|thanks|thank you)\b",
    re.IGNORECASE,
)
_CANCEL_RE = re.compile(r"\b(cancel|nevermind|never mind|stop|forget it|drop it|not now|pause)\b", re.IGNORECASE)
_RESUME_RE = re.compile(r"\b(resume|continue|go ahead|proceed|let's finish|finish it|keep going)\b", re.IGNORECASE)
_MODE_RE = re.compile(r"\b(mode)\b", re.IGNORECASE)
_RESET_RE = re.compile(
    r"\b(new\s+conversation|start\s+over|forget\s+context|reset\s+context|clear\s+context|fresh\s+start)\b",
    re.IGNORECASE,
)

PARAM_ALIASES = {
    "search_flights": {"from": "origin", "to": "destination"},
    "search_buses": {"from": "origin", "to": "destination"},
    "search_transfers": {"from": "origin", "to": "destination"},
    "search_hotels": {"city": "location", "destination": "location"},
    "search_events": {"city": "location"},
    "get_weather": {"location": "city", "town": "city"},
    "send_message": {"phone": "phone_number", "to": "phone_number", "recipient": "phone_number"},
    "send_email": {"email": "to", "recipient": "to"},
    "create_invoice": {"email": "payer_email", "phone": "phone_number", "to": "payer_email"},
    "create_payment_link": {"phone": "phone_number", "payer_email": "email"},
}

def get_action_definition(action: Optional[str]) -> Optional[Dict[str, Any]]:
    return get_catalog_action_definition(action)


def get_required_params(action_def: Optional[Dict[str, Any]]) -> List[str]:
    required: List[str] = []
    if not action_def:
        return required
    for param_name, spec in (action_def.get("params") or {}).items():
        if spec.get("required"):
            required.append(param_name)
    return required


def normalize_params(params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(params, dict):
        return {}
    return dict(params)


def _apply_param_aliases(action: Optional[str], params: Dict[str, Any]) -> Dict[str, Any]:
    if not action:
        return params
    aliases = PARAM_ALIASES.get(action, {})
    if not aliases:
        return params
    updated = dict(params)
    for alias, target in aliases.items():
        if alias in updated and target not in updated and updated.get(alias) not in (None, "", [], {}):
            updated[target] = updated.get(alias)
    return updated


def normalize_params_for_action(action: Optional[str], params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    normalized = normalize_params(params)
    return _apply_param_aliases(action, normalized)


def merge_params(
    existing: Optional[Dict[str, Any]],
    incoming: Optional[Dict[str, Any]],
    *,
    action: Optional[str] = None,
) -> Dict[str, Any]:
    merged = normalize_params_for_action(action, existing)
    for key, value in normalize_params_for_action(action, incoming).items():
        if value in (None, "", [], {}):
            continue
        merged[key] = value
    return merged


def compute_missing_slots(action: Optional[str], params: Optional[Dict[str, Any]]) -> List[str]:
    action_def = get_action_definition(action)
    if not action_def:
        return []
    params = normalize_params_for_action(action, params)
    missing: List[str] = []
    for param_name, spec in (action_def.get("params") or {}).items():
        if spec.get("required") and not params.get(param_name):
            missing.append(param_name)
    return missing


def _format_param_label(param: str) -> str:
    return param.replace("_", " ")


def format_missing_prompt(
    action: Optional[str],
    missing: List[str],
    action_def: Optional[Dict[str, Any]] = None,
) -> str:
    if not missing:
        return ""
    action_label = action.replace("_", " ") if action else "this request"
    description = ""
    if action_def and action_def.get("description"):
        description = action_def.get("description") or ""
    parts: List[str] = []
    for param in missing:
        label = _format_param_label(param)
        suffix = ""
        if "date" in param:
            suffix = " (YYYY-MM-DD)"
        elif "time" in param:
            suffix = " (e.g., 15:00)"
        parts.append(f"{label}{suffix}")
    if description:
        return f"To {description}, I still need: {', '.join(parts)}."
    return f"To complete {action_label}, I still need: {', '.join(parts)}."


def is_small_talk(message: str) -> bool:
    if not message:
        return False
    return bool(_SMALL_TALK_RE.search(message))


def is_cancel_request(message: str) -> bool:
    if not message:
        return False
    return bool(_CANCEL_RE.search(message))


def should_use_summary(message: str) -> bool:
    if not message:
        return False
    lowered = message.lower()
    if "send" not in lowered and "email" not in lowered and "mail" not in lowered:
        return False
    return bool(re.search(r"\b(send|email|mail)\b.*\b(it|that|them|results?|summary|details)\b", lowered))


def apply_summary_defaults(
    action: Optional[str],
    params: Optional[Dict[str, Any]],
    summary_text: Optional[str],
) -> Dict[str, Any]:
    params = normalize_params(params)
    if not summary_text:
        return params
    action_def = get_action_definition(action)
    required = set(get_required_params(action_def))
    for candidate in SUMMARY_PARAM_CANDIDATES:
        if candidate in required and not params.get(candidate):
            params[candidate] = summary_text
            break
    if "subject" in required and params.get("text") and not params.get("subject"):
        params["subject"] = " ".join(str(params.get("text")).split()[:6])[:80]
    return params


def _task_cache_key(context: Dict[str, Any]) -> str:
    user_id = context.get("user_id") or "anon"
    room_id = context.get("room_id") or "room"
    return f"adaptive_task:{user_id}:{room_id}"


def _mode_cache_key(context: Dict[str, Any]) -> str:
    user_id = context.get("user_id") or "anon"
    room_id = context.get("room_id") or "room"
    return f"conversation:mode:{user_id}:{room_id}"


async def get_conversation_mode(context: Dict[str, Any]) -> str:
    key = _mode_cache_key(context)
    try:
        mode = await sync_to_async(cache.get)(key)
    except Exception as exc:
        logger.warning("Conversation mode read failed: %s", exc)
        return "auto"
    return mode or "auto"


async def set_conversation_mode(context: Dict[str, Any], mode: str) -> None:
    key = _mode_cache_key(context)
    try:
        await sync_to_async(cache.set)(key, mode, timeout=MODE_TTL_SECONDS)
    except Exception as exc:
        logger.warning("Conversation mode write failed: %s", exc)


def detect_mode_command(message: str) -> Optional[str]:
    if not message or not _MODE_RE.search(message):
        return None
    lowered = message.lower()
    if "focus" in lowered or "task" in lowered:
        return "focus"
    if "social" in lowered or "chat" in lowered or "casual" in lowered:
        return "social"
    if "auto" in lowered or "default" in lowered:
        return "auto"
    return None


def is_reset_request(message: str) -> bool:
    if not message:
        return False
    return bool(_RESET_RE.search(message))


def format_mode_ack(mode: str) -> str:
    if mode == "focus":
        return "Focus mode on. I'll prioritize tasks and keep prompts tight."
    if mode == "social":
        return "Social mode on. I'll keep things conversational and pause tasks unless you resume."
    return "Auto mode on. I'll balance conversation with task progress."


def _result_cache_key(context: Dict[str, Any], suffix: str) -> str:
    user_id = context.get("user_id") or "anon"
    room_id = context.get("room_id") or "room"
    return f"adaptive_results:{suffix}:{user_id}:{room_id}"


async def store_result_set(
    context: Dict[str, Any],
    action: Optional[str],
    results: Optional[List[Dict[str, Any]]],
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    if not action or results is None:
        return
    normalized_results: List[Dict[str, Any]] = []
    for idx, item in enumerate(results or [], start=1):
        if not isinstance(item, dict):
            continue
        base_id = (
            item.get("id")
            or item.get("provider_id")
            or item.get("offer_id")
            or item.get("flight_id")
            or item.get("item_id")
        )
        option_id = f"{idx}:{base_id}" if base_id else str(idx)
        enriched = dict(item)
        enriched.setdefault("option_id", option_id)
        enriched.setdefault("option_index", idx)
        normalized_results.append(enriched)
    payload = {
        "action": action,
        "results": normalized_results,
        "metadata": metadata or {},
        "updated_at": datetime.utcnow().isoformat(),
    }
    try:
        await sync_to_async(cache.set)(
            _result_cache_key(context, "last"),
            payload,
            timeout=RESULT_TTL_SECONDS,
        )
        if str(action).startswith("search_"):
            await sync_to_async(cache.set)(
                _result_cache_key(context, "last_search"),
                payload,
                timeout=RESULT_TTL_SECONDS,
            )
    except Exception as exc:
        logger.warning("Adaptive result set write failed: %s", exc)


async def load_last_search(context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        return await sync_to_async(cache.get)(_result_cache_key(context, "last_search"))
    except Exception as exc:
        logger.warning("Adaptive last search read failed: %s", exc)
        return None


async def clear_result_sets(context: Dict[str, Any]) -> None:
    try:
        await sync_to_async(cache.delete)(_result_cache_key(context, "last"))
        await sync_to_async(cache.delete)(_result_cache_key(context, "last_search"))
    except Exception as exc:
        logger.warning("Adaptive result set delete failed: %s", exc)


def _is_option_selection(param_name: str, value: Any) -> bool:
    if param_name in _OPTION_PARAM_HINTS or param_name.endswith("_id"):
        if isinstance(value, int):
            return True
        if isinstance(value, str) and value.strip().isdigit():
            return True
    return False


def _needs_option_context(action_def: Optional[Dict[str, Any]], params: Dict[str, Any]) -> bool:
    if not action_def:
        return False
    for param_name, spec in (action_def.get("params") or {}).items():
        if not spec.get("required"):
            continue
        value = params.get(param_name)
        if value is None:
            continue
        if _is_option_selection(param_name, value):
            return True
    return False


def _selection_index(selection: Any) -> Optional[int]:
    if selection is None:
        return None
    if isinstance(selection, int):
        return selection if selection > 0 else None
    raw = str(selection).strip().lower()
    if raw.isdigit():
        return int(raw)
    lookup = {
        "first": 1,
        "second": 2,
        "third": 3,
        "fourth": 4,
        "fifth": 5,
    }
    for key, idx in lookup.items():
        if key in raw:
            return idx
    if "option" in raw:
        digits = re.findall(r"\d+", raw)
        if digits:
            return int(digits[0])
    return None


def format_option_dependency_prompt(action_def: Optional[Dict[str, Any]], action: Optional[str]) -> str:
    description = ""
    if action_def and action_def.get("description"):
        description = str(action_def.get("description") or "").lower()
    if description:
        return (
            f"I need a recent list of {description} options before I can pick an option number. "
            "What should I search for first?"
        )
    action_label = action.replace("_", " ") if action else "this"
    return (
        f"I need a recent list of options before I can pick an option number for {action_label}. "
        "What should I search for first?"
    )


async def needs_option_context(
    context: Dict[str, Any],
    action: Optional[str],
    params: Optional[Dict[str, Any]],
    action_def: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    params = normalize_params(params)
    if not action_def:
        action_def = get_action_definition(action)
    if not _needs_option_context(action_def, params):
        return None
    last_search = await load_last_search(context)
    if last_search and (last_search.get("results") or []):
        return None
    return format_option_dependency_prompt(action_def, action)


async def resolve_option_selection(
    context: Dict[str, Any],
    action: Optional[str],
    params: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    params = normalize_params_for_action(action, params)
    action_def = get_action_definition(action)
    if not _needs_option_context(action_def, params):
        return params
    selection_value = None
    for key in ("item_id", "option", "selection"):
        if key in params:
            selection_value = params.get(key)
            break
    index = _selection_index(selection_value)
    if not index:
        return params
    last_search = await load_last_search(context)
    results = (last_search or {}).get("results") or []
    if 0 < index <= len(results):
        chosen = results[index - 1]
        item_id = (
            chosen.get("id")
            or chosen.get("provider_id")
            or chosen.get("offer_id")
            or chosen.get("flight_id")
            or chosen.get("item_id")
            or chosen.get("option_id")
        )
        if item_id:
            params["item_id"] = item_id
            params["option_index"] = index
            params["option_id"] = chosen.get("option_id") or params.get("option_id")
    return params


async def load_task_state(context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    key = _task_cache_key(context)
    try:
        return await sync_to_async(cache.get)(key)
    except Exception as exc:
        logger.warning("Adaptive task state read failed: %s", exc)
        return None


async def save_task_state(context: Dict[str, Any], state: Dict[str, Any]) -> None:
    key = _task_cache_key(context)
    payload = dict(state or {})
    payload["version"] = TASK_VERSION
    payload["updated_at"] = datetime.utcnow().isoformat()
    try:
        await sync_to_async(cache.set)(key, payload, timeout=TASK_TTL_SECONDS)
    except Exception as exc:
        logger.warning("Adaptive task state write failed: %s", exc)


async def clear_task_state(context: Dict[str, Any]) -> None:
    key = _task_cache_key(context)
    try:
        await sync_to_async(cache.delete)(key)
    except Exception as exc:
        logger.warning("Adaptive task state delete failed: %s", exc)


def is_task_paused(state: Optional[Dict[str, Any]]) -> bool:
    if not state:
        return False
    paused_until = state.get("paused_until")
    if not paused_until:
        return False
    try:
        expires = datetime.fromisoformat(paused_until)
    except Exception:
        return False
    return datetime.utcnow() < expires


def clear_task_pause(state: Dict[str, Any]) -> Dict[str, Any]:
    updated = dict(state)
    updated.pop("paused_until", None)
    updated.pop("paused_reason", None)
    return updated


def pause_task_state(state: Dict[str, Any], reason: str, seconds: int) -> Dict[str, Any]:
    updated = dict(state)
    updated["paused_until"] = (datetime.utcnow() + timedelta(seconds=seconds)).isoformat()
    updated["paused_reason"] = reason
    return updated


def init_task_state(intent: Dict[str, Any]) -> Dict[str, Any]:
    action = intent.get("action")
    params = normalize_params(intent.get("parameters"))
    missing = compute_missing_slots(action, params)
    status = "awaiting_slots" if missing else "ready"
    return {
        "mode": "intent",
        "status": status,
        "action": action,
        "parameters": params,
        "missing_slots": missing,
        "created_at": datetime.utcnow().isoformat(),
        "last_prompt": "",
    }


def is_resume_request(message: str) -> bool:
    if not message:
        return False
    return bool(_RESUME_RE.search(message))


def update_task_state(state: Dict[str, Any], new_params: Dict[str, Any]) -> Dict[str, Any]:
    action = state.get("action")
    merged = merge_params(state.get("parameters"), new_params, action=action)
    missing = compute_missing_slots(action, merged)
    status = "awaiting_slots" if missing else "ready"
    next_state = dict(state)
    next_state["parameters"] = merged
    next_state["missing_slots"] = missing
    next_state["status"] = status
    record_event(
        "slot_fill",
        {
            "action": action,
            "missing_slots": missing,
            "filled_slots": [k for k in (merged or {}).keys() if k not in missing],
            "source": "user_params",
        },
    )
    return next_state
