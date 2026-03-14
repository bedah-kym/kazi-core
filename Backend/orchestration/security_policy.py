"""Low-cost security policy helpers for prompt injection and tool abuse."""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from asgiref.sync import sync_to_async
from django.core.cache import cache

from chatbot.models import Chatroom

ROOM_ACCESS_TTL_SECONDS = 300

SENSITIVE_ACTIONS = {
    "send_email",
    "send_message",
    "send_whatsapp",
    "set_reminder",
    "create_invoice",
    "create_payment_link",
    "withdraw",
    "schedule_meeting",
    "check_availability",
    "book_travel_item",
    "add_to_itinerary",
    "create_itinerary",
    "create_workflow",
}

RESTRICTED_PARAM_KEYS = {
    "user_id",
    "room_id",
    "member_id",
    "is_admin",
    "is_superuser",
    "permissions",
    "auth",
    "token",
    "api_key",
    "secret",
    "password",
}

_INJECTION_PATTERNS = [
    r"ignore\s+(all|previous|system|developer)\s+instructions",
    r"(system|developer)\s+prompt",
    r"jailbreak",
    r"bypass\s+(safety|filters|policy|guard)",
    r"override\s+(safety|policy|guard)",
    r"reveal\s+(api\s*key|token|secret|password)",
    r"(api\s*key|access\s*token|secret|password)\b",
    r"\b(database|sql|drop\s+table|select\s+.+\s+from)\b",
    r"\b(admin\s+panel|superuser|root|sudo)\b",
    r"\b(localhost|127\.0\.0\.1|file://|/etc/passwd)\b",
]

_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)

_HIGH_RISK_VERBS = re.compile(
    r"\b(send|email|whatsapp|withdraw|transfer|pay|invoice|book|purchase|"
    r"schedule|invite|create|delete|reset)\b",
    re.IGNORECASE,
)


def is_prompt_injection(message: Optional[str]) -> bool:
    if not message:
        return False
    return bool(_INJECTION_RE.search(message))


def is_high_risk_message(message: Optional[str]) -> bool:
    if not message:
        return False
    return bool(_HIGH_RISK_VERBS.search(message))


def should_block_message(message: Optional[str]) -> bool:
    return is_prompt_injection(message) and is_high_risk_message(message)


def should_block_action(message: Optional[str], action: Optional[str]) -> bool:
    if not action:
        return False
    if not is_prompt_injection(message):
        return False
    return action in SENSITIVE_ACTIONS


def sanitize_parameters(params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(params, dict):
        return {}
    cleaned: Dict[str, Any] = {}
    for key, value in params.items():
        if key in RESTRICTED_PARAM_KEYS:
            continue
        cleaned[key] = value
    return cleaned


async def user_has_room_access(user_id: Optional[int], room_id: Optional[int]) -> bool:
    if not user_id or not room_id:
        return True
    cache_key = f"room_access:{user_id}:{room_id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return bool(cached)

    def _check():
        return Chatroom.objects.filter(id=room_id, participants__User_id=user_id).exists()

    allowed = await sync_to_async(_check)()
    cache.set(cache_key, 1 if allowed else 0, ROOM_ACCESS_TTL_SECONDS)
    return bool(allowed)


def sanitize_steps(steps: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cleaned_steps: List[Dict[str, Any]] = []
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        cleaned = dict(step)
        cleaned["params"] = sanitize_parameters(cleaned.get("params") or {})
        cleaned_steps.append(cleaned)
    return cleaned_steps


def should_refuse_sensitive_request(message: Optional[str]) -> bool:
    if not message:
        return False
    lowered = message.lower()
    # Deterministic refusal for system prompt / admin / data exfiltration requests.
    refusal_patterns = [
        r"\bsystem\s+prompt\b",
        r"\bdeveloper\s+prompt\b",
        r"\badmin\s+access\b",
        r"\bsuperuser\b",
        r"\broot\s+access\b",
        r"\bdatabase\s+dump\b",
        r"\bdump\s+the\s+database\b",
        r"\bexfiltrate\b",
        r"\bapi\s*key\b",
        r"\baccess\s*token\b",
        r"\bsecret\b",
        r"\bpassword\b",
    ]
    pattern = re.compile("|".join(refusal_patterns), re.IGNORECASE)
    return bool(pattern.search(lowered))


def sensitive_refusal_message() -> str:
    return (
        "Sorry, I can’t help with that request. "
        "If you need account or data access changes, please use the official admin tools."
    )
