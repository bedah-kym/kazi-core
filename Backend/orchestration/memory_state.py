from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from asgiref.sync import sync_to_async
from django.core.cache import cache

from orchestration.telemetry import record_event

MEMORY_STATE_TTL_SECONDS = 60 * 60 * 24
MEMORY_SUMMARY_TTL_SECONDS = 60 * 60 * 24
SUMMARY_MAX_CHARS = 2000
LAST_ACTIONS_LIMIT = 5
ENTITY_KEYS = {
    "origin",
    "destination",
    "city",
    "location",
    "email",
    "phone_number",
    "amount",
    "currency",
    "departure_date",
    "return_date",
    "check_in_date",
    "check_out_date",
    "itinerary_id",
    "invoice_id",
}


def _state_key(context: Dict[str, Any]) -> str:
    user_id = context.get("user_id") or "anon"
    room_id = context.get("room_id") or "room"
    return f"memory_state:{user_id}:{room_id}"


def _summary_key(context: Dict[str, Any]) -> str:
    user_id = context.get("user_id") or "anon"
    room_id = context.get("room_id") or "room"
    return f"memory_summary:{user_id}:{room_id}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compact_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join([str(v) for v in value if v is not None])[:120]
    return str(value)[:120]


def _merge_entities(state: Dict[str, Any], params: Optional[Dict[str, Any]], result: Optional[Dict[str, Any]]) -> None:
    entities = dict(state.get("entities") or {})
    for payload in (params or {}, result or {}):
        for key, value in payload.items():
            if key in ENTITY_KEYS and value not in (None, "", [], {}):
                entities[key] = _compact_value(value)
    state["entities"] = entities


def _append_action(state: Dict[str, Any], action: Optional[str]) -> None:
    if not action:
        return
    actions = list(state.get("last_actions") or [])
    actions.append({"action": action, "ts": _now_iso()})
    if len(actions) > LAST_ACTIONS_LIMIT:
        actions = actions[-LAST_ACTIONS_LIMIT:]
    state["last_actions"] = actions


def build_memory_summary(state: Optional[Dict[str, Any]]) -> str:
    if not state:
        return ""
    entities = state.get("entities") or {}
    actions = state.get("last_actions") or []

    lines: List[str] = []
    if actions:
        action_names = [a.get("action") for a in actions if a.get("action")]
        if action_names:
            lines.append(f"Recent actions: {', '.join(action_names[-3:])}.")
    if entities:
        pairs = [f"{k}={v}" for k, v in entities.items() if v]
        if pairs:
            lines.append("Known entities: " + "; ".join(pairs[:8]) + ".")

    summary = "\n".join(lines).strip()
    if len(summary) > SUMMARY_MAX_CHARS:
        summary = summary[:SUMMARY_MAX_CHARS].rstrip() + "?"
    return summary


async def load_memory_state(context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        return await sync_to_async(cache.get)(_state_key(context))
    except Exception:
        return None


async def save_memory_state(context: Dict[str, Any], state: Dict[str, Any]) -> None:
    payload = dict(state or {})
    payload["updated_at"] = _now_iso()
    try:
        await sync_to_async(cache.set)(_state_key(context), payload, timeout=MEMORY_STATE_TTL_SECONDS)
    except Exception:
        return


async def load_memory_summary(context: Dict[str, Any]) -> Optional[str]:
    try:
        return await sync_to_async(cache.get)(_summary_key(context))
    except Exception:
        return None


async def save_memory_summary(context: Dict[str, Any], summary: str) -> None:
    if not summary:
        return
    trimmed = summary.strip()
    if len(trimmed) > SUMMARY_MAX_CHARS:
        trimmed = trimmed[:SUMMARY_MAX_CHARS].rstrip() + "?"
    try:
        await sync_to_async(cache.set)(_summary_key(context), trimmed, timeout=MEMORY_SUMMARY_TTL_SECONDS)
    except Exception:
        return


async def clear_memory(context: Dict[str, Any]) -> None:
    try:
        await sync_to_async(cache.delete)(_state_key(context))
        await sync_to_async(cache.delete)(_summary_key(context))
    except Exception:
        return


async def update_memory_state(
    context: Dict[str, Any],
    *,
    action: Optional[str],
    params: Optional[Dict[str, Any]] = None,
    result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    state = await load_memory_state(context) or {}
    _append_action(state, action)
    _merge_entities(state, params, result)
    await save_memory_state(context, state)
    summary = build_memory_summary(state)
    await save_memory_summary(context, summary)
    record_event(
        "memory_state",
        {
            "user_id": context.get("user_id"),
            "room_id": context.get("room_id"),
            "action": action,
            "summary_len": len(summary),
        },
    )

    # Bridge: persist significant entities to RoomContext.memory_facts
    entities = state.get("entities") or {}
    if entities:
        try:
            await _persist_entities_to_db(context, entities)
        except Exception:
            pass  # Non-critical; Redis state is already saved

    return state


async def _persist_entities_to_db(
    context: Dict[str, Any], entities: Dict[str, str]
) -> None:
    """Upsert key entities into RoomContext.memory_facts for long-term persistence."""
    room_id = context.get("room_id")
    if not room_id:
        return

    trivial_values = {"", "None", "null", "none"}

    def _upsert():
        from chatbot.models import RoomContext

        try:
            ctx = RoomContext.objects.get(chatroom_id=room_id)
        except RoomContext.DoesNotExist:
            return

        facts = list(ctx.memory_facts or [])
        facts_by_key = {}
        for i, fact in enumerate(facts):
            if isinstance(fact, dict) and fact.get("key"):
                facts_by_key[fact["key"]] = i

        now_iso = datetime.now(timezone.utc).isoformat()
        changed = False

        for key, value in entities.items():
            if not value or str(value).strip() in trivial_values:
                continue
            if key in facts_by_key:
                idx = facts_by_key[key]
                if facts[idx].get("value") != value:
                    facts[idx]["value"] = value
                    facts[idx]["updated_at"] = now_iso
                    changed = True
            else:
                facts.append({
                    "key": key,
                    "value": value,
                    "confidence": 0.8,
                    "updated_at": now_iso,
                })
                changed = True

        if not changed:
            return

        # Cap at 30 entries, trim lowest-confidence
        if len(facts) > 30:
            facts.sort(
                key=lambda f: float(f.get("confidence", 0)) if isinstance(f, dict) else 0,
                reverse=True,
            )
            facts = facts[:30]

        ctx.memory_facts = facts
        ctx.memory_updated_at = datetime.now(timezone.utc)
        ctx.save(update_fields=["memory_facts", "memory_updated_at"])

    await sync_to_async(_upsert)()
