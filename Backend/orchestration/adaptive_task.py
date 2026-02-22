"""Adaptive task state and action registry helpers."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
import logging
import re

from asgiref.sync import sync_to_async
from django.core.cache import cache

from workflows.capabilities import SYSTEM_CAPABILITIES

logger = logging.getLogger(__name__)

TASK_TTL_SECONDS = 60 * 60
TASK_VERSION = 1
SUMMARY_PARAM_CANDIDATES = ("text", "message", "content")
RESULT_TTL_SECONDS = 60 * 60
_OPTION_PARAM_HINTS = ("item_id", "option", "selection")


def _build_action_registry(capabilities: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    registry: Dict[str, Dict[str, Any]] = {}
    for service in capabilities.get("integrations", []):
        service_name = service.get("service")
        for action in service.get("actions", []):
            action_name = action.get("name")
            if not action_name:
                continue
            if action_name in registry:
                continue
            registry[action_name] = {
                "service": service_name,
                "params": action.get("params") or {},
                "description": action.get("description") or "",
            }
    return registry


_ACTION_REGISTRY = _build_action_registry(SYSTEM_CAPABILITIES)


def get_action_definition(action: Optional[str]) -> Optional[Dict[str, Any]]:
    if not action:
        return None
    return _ACTION_REGISTRY.get(action)


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


def merge_params(existing: Optional[Dict[str, Any]], incoming: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged = normalize_params(existing)
    for key, value in normalize_params(incoming).items():
        if value in (None, "", [], {}):
            continue
        merged[key] = value
    return merged


def compute_missing_slots(action: Optional[str], params: Optional[Dict[str, Any]]) -> List[str]:
    action_def = get_action_definition(action)
    if not action_def:
        return []
    params = normalize_params(params)
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
    payload = {
        "action": action,
        "results": results,
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


def update_task_state(state: Dict[str, Any], new_params: Dict[str, Any]) -> Dict[str, Any]:
    action = state.get("action")
    merged = merge_params(state.get("parameters"), new_params)
    missing = compute_missing_slots(action, merged)
    status = "awaiting_slots" if missing else "ready"
    next_state = dict(state)
    next_state["parameters"] = merged
    next_state["missing_slots"] = missing
    next_state["status"] = status
    return next_state
