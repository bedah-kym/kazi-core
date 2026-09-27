"""Initiative ladder (v0.6 L3, #154).

Jarvis earns initiative; it never grabs it. Each rung above "watch" is granted
by a human through the existing durable approval seam:

1. watch silently (existing),
2. daily digest (this module),
3. propose a specific action via the agent-loop confirmation path,
4. auto-execute under an approved rule, with a receipt every time.

Hard caps: a per-day proactive budget, and proactive actions never exceed the
safe tier unless an approved rule exists. Digest/proposal text is sanitized
before it reaches the model.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone as dt_timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

APPROVED_RULES_KEY = "approved_rules"
SAFE_TIER = "safe"
_BUDGET_TTL_SECONDS = 24 * 60 * 60


def _setting(name: str, default: Any = None) -> Any:
    try:
        from django.conf import settings
        return getattr(settings, name, default)
    except Exception:
        return default


def _sanitize(text: str) -> str:
    try:
        from orchestration.agent_loop import _sanitize_tool_result
        return _sanitize_tool_result(str(text))
    except Exception:
        return str(text)


def _profile(user_id: Any):
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.select_related("profile").filter(id=user_id).first()
    return getattr(user, "profile", None) if user else None


# --------------------------------------------------------------------------- #
#  Hard cap: proactive budget per day                                         #
# --------------------------------------------------------------------------- #

def proactive_budget_per_day() -> int:
    try:
        return max(0, int(_setting("PROACTIVE_BUDGET_PER_DAY", 2)))
    except Exception:
        return 2


def _budget_key(user_id: Any) -> str:
    day = datetime.now(dt_timezone.utc).strftime("%Y%m%d")
    return f"kazi:proactive:{user_id}:{day}"


def proactive_budget_remaining(user_id: Optional[int]) -> int:
    if not user_id:
        return proactive_budget_per_day()
    from django.core.cache import cache
    try:
        used = int(cache.get(_budget_key(user_id)) or 0)
    except Exception:
        used = 0
    return max(0, proactive_budget_per_day() - used)


def consume_proactive_budget(user_id: Optional[int]) -> bool:
    """Consume one proactive slot atomically. Fail closed (refuse) on any error."""
    if not user_id:
        return True
    from django.core.cache import cache
    try:
        limit = proactive_budget_per_day()
        if limit <= 0:
            return False
        key = _budget_key(user_id)
        cache.add(key, 0, _BUDGET_TTL_SECONDS)
        used = cache.incr(key)
        return used is not None and used <= limit
    except Exception:
        logger.warning("Proactive budget check failed; refusing action for user %s", user_id)
        return False


# --------------------------------------------------------------------------- #
#  Rung 2: daily digest                                                        #
# --------------------------------------------------------------------------- #

def fired_watches() -> List[Dict[str, Any]]:
    from orchestration.telemetry_rollups import WATCHES_CACHE_KEY
    from django.core.cache import cache
    try:
        return list(cache.get(WATCHES_CACHE_KEY) or [])
    except Exception:
        return []


def open_proposals(user_id: Optional[int]) -> List[Dict[str, Any]]:
    if not user_id:
        return []
    from django.utils import timezone
    from workflows.models import WorkflowApprovalRecord

    now = timezone.now()
    rows = WorkflowApprovalRecord.objects.filter(
        kind="agent_loop", requested_by_id=user_id, status="pending",
    )
    proposals: List[Dict[str, Any]] = []
    for record in rows:
        if record.expires_at and record.expires_at <= now:
            continue
        proposals.append({
            "action": record.action,
            "room_id": record.room_id,
            "message": record.approval_message,
        })
    return proposals


def watch_label(watch: Dict[str, Any]) -> str:
    """Human-readable label for a derived telemetry watch."""
    if watch.get("summary") or watch.get("label"):
        return str(watch.get("summary") or watch.get("label"))
    metric = str(watch.get("metric") or watch.get("kind") or "watch")
    labels = watch.get("labels") if isinstance(watch.get("labels"), dict) else {}
    detail = ", ".join(f"{key}={value}" for key, value in list(labels.items())[:3])
    parts = [metric]
    if detail:
        parts.append(f"({detail})")
    if watch.get("current_value") is not None:
        parts.append(f"= {watch.get('current_value')}")
    if watch.get("trend"):
        parts.append(f"[{watch.get('trend')}]")
    return " ".join(str(part) for part in parts)


def is_anomaly(watch: Dict[str, Any]) -> bool:
    """A watch is an anomaly when it is rising or over its threshold."""
    if str(watch.get("trend") or "") == "rising":
        return True
    threshold = watch.get("threshold")
    value = watch.get("current_value")
    if isinstance(threshold, (int, float)) and isinstance(value, (int, float)):
        return value > threshold
    return False


def collect_anomalies(watches: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Return the anomaly subset of the derived watches."""
    source = watches if watches is not None else fired_watches()
    return [watch for watch in source if is_anomaly(watch)]


def build_digest(user_id: Optional[int]) -> Dict[str, Any]:
    """Compose the rung-2 digest: anomalies, watches, and open proposals."""
    watches = fired_watches()
    anomalies = collect_anomalies(watches)
    anomaly_ids = {id(watch) for watch in anomalies}
    quiet = [watch for watch in watches if id(watch) not in anomaly_ids]
    proposals = open_proposals(user_id)
    lines: List[str] = []
    if anomalies:
        lines.append("Anomalies:")
        lines.extend(f"- {_sanitize(watch_label(watch))}" for watch in anomalies[:5])
    if quiet:
        lines.append("Watches:")
        lines.extend(f"- {_sanitize(watch_label(watch))}" for watch in quiet[:5])
    if proposals:
        lines.append("Waiting on your approval:")
        for proposal in proposals[:5]:
            lines.append(f"- {_sanitize(str(proposal.get('action')))} (room {proposal.get('room_id')})")
    message = "\n".join(lines)
    return {
        "message": message,
        "watches": watches,
        "anomalies": anomalies,
        "proposals": proposals,
        "empty": not message,
    }


def send_digest_for_user(user_id: Optional[int]) -> bool:
    if not user_id:
        return False
    digest = build_digest(user_id)
    if digest["empty"]:
        return False
    from django.contrib.auth import get_user_model
    from notifications.services import NotificationService

    user = get_user_model().objects.filter(id=user_id).first()
    if not user:
        return False
    NotificationService.notify(
        user=user,
        event_type="initiative.digest",
        title="Daily digest",
        body=digest["message"],
        channels={"in_app": True, "email": False, "whatsapp": False},
    )
    return True


def send_digests() -> Dict[str, Any]:
    from django.contrib.auth import get_user_model

    user_ids = list(get_user_model().objects.values_list("id", flat=True))
    sent = 0
    for user_id in user_ids:
        try:
            if send_digest_for_user(user_id):
                sent += 1
        except Exception:
            logger.warning("Digest failed for user %s", user_id, exc_info=True)
    return {"users": len(user_ids), "sent": sent}


# --------------------------------------------------------------------------- #
#  Rung 3: propose a specific action (durable confirmation seam)              #
# --------------------------------------------------------------------------- #

async def propose_action(
    *,
    user_id: int,
    room_id: int,
    action: str,
    params: Optional[Dict[str, Any]] = None,
    effects: Optional[List[str]] = None,
    message: str = "",
) -> Optional[int]:
    """Open a durable, diff-previewed proposal through the agent-loop seam."""
    from orchestration.agent_loop import save_pending_confirmation

    tool = {"id": f"proposal-{action}", "name": action, "input": params or {}}
    text = message or f"Shall I go ahead with {action.replace('_', ' ')}?"
    return await save_pending_confirmation(room_id, user_id, tool, text, effects=effects)


async def propose_rule(
    *,
    user_id: int,
    room_id: int,
    action: str,
    rule_text: str = "",
    effects: Optional[List[str]] = None,
) -> Optional[int]:
    """Open a durable approval for a rung-4 rule *before* it is stored."""
    from orchestration.agent_loop import save_pending_confirmation

    tool = {
        "id": f"rule-{action}",
        "name": "promote_rule",
        "input": {"action": action, "room_id": room_id, "rule_text": rule_text},
    }
    text = f"Approve this rule so I can run {action.replace('_', ' ')} automatically in this room?"
    return await save_pending_confirmation(room_id, user_id, tool, text, effects=effects)


def activate_rule(user_id: int, rule: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Store a rule after its durable approval (approval comes first)."""
    return promote_rule(user_id, rule)


# --------------------------------------------------------------------------- #
#  Rung 4: approved rules (stored as JSON; no migration)                      #
# --------------------------------------------------------------------------- #

def approved_rules(user_id: Optional[int]) -> List[Dict[str, Any]]:
    profile = _profile(user_id) if user_id else None
    if not profile:
        return []
    return list((profile.notification_preferences or {}).get(APPROVED_RULES_KEY) or [])


def promote_rule(user_id: int, rule: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Record a rule the human approved once, durably (JSON)."""
    profile = _profile(user_id)
    action = str(rule.get("action") or "")
    if not profile or not action:
        return approved_rules(user_id)
    prefs = dict(profile.notification_preferences or {})
    rules = [
        existing for existing in (prefs.get(APPROVED_RULES_KEY) or [])
        if not (existing.get("action") == action and existing.get("room_id") == rule.get("room_id"))
    ]
    stored = dict(rule)
    stored.setdefault("created_at", datetime.now(dt_timezone.utc).isoformat())
    rules.append(stored)
    prefs[APPROVED_RULES_KEY] = rules
    profile.notification_preferences = prefs
    profile.save(update_fields=["notification_preferences"])
    return rules


def clear_approved_rules(user_id: int) -> bool:
    profile = _profile(user_id)
    if not profile:
        return False
    prefs = dict(profile.notification_preferences or {})
    existed = bool(prefs.get(APPROVED_RULES_KEY))
    prefs.pop(APPROVED_RULES_KEY, None)
    profile.notification_preferences = prefs
    profile.save(update_fields=["notification_preferences"])
    return existed


def is_allowed_by_rule(user_id: Optional[int], action: str, room_id: Any = None) -> bool:
    for rule in approved_rules(user_id):
        if rule.get("action") != action:
            continue
        if rule.get("room_id") in (None, room_id):
            return True
    return False


def candidate_rules(user_id: Optional[int]) -> List[Dict[str, Any]]:
    """Repeated, never-denied actions worth promoting (from #152)."""
    from orchestration.preference_mining import get_learned_overrides

    out: List[Dict[str, Any]] = []
    for room, actions in (get_learned_overrides(user_id) or {}).items():
        for action, policy in actions.items():
            if policy == "auto":
                out.append({
                    "action": action,
                    "room_id": int(room) if str(room).isdigit() else room,
                    "tier": SAFE_TIER,
                })
    return out


async def execute_approved_rule(
    *,
    user_id: int,
    room_id: int,
    action: str,
    params: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run an approved rule's action, consuming budget and writing a receipt."""
    from asgiref.sync import sync_to_async

    if not await sync_to_async(is_allowed_by_rule)(user_id, action, room_id):
        return {"status": "error", "message": "No approved rule for this action."}
    if not consume_proactive_budget(user_id):
        return {"status": "error", "message": "Daily proactive budget exhausted."}

    exec_context = dict(context or {})
    exec_context.setdefault("user_id", user_id)
    exec_context.setdefault("room_id", room_id)

    from orchestration.tool_executor import execute_tool

    result = await execute_tool(action, params or {}, exec_context)

    try:
        from orchestration.action_catalog import get_action_definition
        from orchestration.action_receipts import record_action_receipt

        service = (get_action_definition(action) or {}).get("service", "")
        receipt = await record_action_receipt(
            user_id=user_id,
            room_id=room_id,
            action=action,
            service=service,
            params=params,
            result=result if isinstance(result, dict) else {},
            status=str((result or {}).get("status", "success")),
        )
    except Exception:
        logger.error("Proactive receipt failed for %s", action, exc_info=True)
        receipt = None

    if not receipt:
        # A rung-4 run is only "successful" with its audit receipt written.
        return {
            "status": "error",
            "message": "The action ran but its audit receipt could not be written; flagged for review.",
            "receipt_failed": True,
            "data": result if isinstance(result, dict) else {},
        }
    return result


async def draft_rule(pattern: Dict[str, Any], user_id: Optional[int] = None) -> Dict[str, Any]:
    """Draft a legible rule from a repeated pattern (LLM, best-effort).

    Deterministic candidate in, legible draft out; falls back to a template if
    the LLM is unavailable.
    """
    action = str(pattern.get("action") or "")
    room_id = pattern.get("room_id")
    fallback = {
        "action": action,
        "room_id": room_id,
        "text": (
            f"Run {action.replace('_', ' ')} automatically in room {room_id} "
            "when the same pattern recurs."
        ),
    }
    if not action:
        return fallback
    try:
        from orchestration.llm_client import get_llm_client

        safe_pattern = _sanitize(str(pattern))
        client = get_llm_client()
        prose = await client.generate_text(
            "You draft one-line automation rules.",
            f"Draft one short rule for this repeated, always-approved pattern: {safe_pattern}",
        )
        if isinstance(prose, str) and prose.strip():
            fallback["text"] = prose.strip()
    except Exception:
        pass
    return fallback
