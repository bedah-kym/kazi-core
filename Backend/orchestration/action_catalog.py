"""Single source of truth for orchestration actions and policy metadata."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional, Tuple


ACTION_CATALOG: List[Dict[str, Any]] = [
    {
        "action": "send_email",
        "aliases": ["email", "send_mail", "mailgun_send_email"],
        "service": "gmail",
        "description": "Send an email",
        "params": {
            "to": {"type": "string", "required": True},
            "subject": {"type": "string", "required": True},
            "text": {"type": "string", "required": True},
            "html": {"type": "string", "required": False},
            "from": {"type": "string", "required": False},
        },
        "risk_level": "high",
        "confirmation_policy": "always",
        "capability_gate": "allow_email",
    },
    {
        "action": "send_message",
        "aliases": ["send_whatsapp", "whatsapp", "send_sms", "send_text"],
        "service": "whatsapp",
        "description": "Send a WhatsApp message",
        "params": {
            "phone_number": {"type": "string", "required": True},
            "message": {"type": "string", "required": True},
            "media_url": {"type": "string", "required": False},
        },
        "risk_level": "high",
        "confirmation_policy": "always",
        "capability_gate": "allow_whatsapp",
    },
    {
        "action": "check_balance",
        "aliases": [],
        "service": "payments",
        "description": "Check wallet balance",
        "params": {},
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": "allow_payments",
    },
    {
        "action": "list_transactions",
        "aliases": [],
        "service": "payments",
        "description": "List recent wallet transactions",
        "params": {"limit": {"type": "integer", "required": False}},
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": "allow_payments",
    },
    {
        "action": "check_invoice_status",
        "aliases": ["invoice_status"],
        "service": "payments",
        "description": "Check invoice status",
        "params": {"invoice_id": {"type": "string", "required": True}},
        "risk_level": "medium",
        "confirmation_policy": "never",
        "capability_gate": "allow_payments",
    },
    {
        "action": "check_payments",
        "aliases": [],
        "service": "payments",
        "description": "Summary of balance and recent transactions",
        "params": {},
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": "allow_payments",
    },
    {
        "action": "create_invoice",
        "aliases": ["invoice"],
        "service": "payments",
        "description": "Create an invoice and optionally notify the payer",
        "params": {
            "amount": {"type": "number", "required": True},
            "currency": {"type": "string", "required": False},
            "description": {"type": "string", "required": False},
            "payer_email": {"type": "string", "required": False},
            "phone_number": {"type": "string", "required": False},
            "send_via": {"type": "string", "required": False},
        },
        "risk_level": "high",
        "confirmation_policy": "always",
        "capability_gate": "allow_payments",
    },
    {
        "action": "create_payment_link",
        "aliases": ["payment_link"],
        "service": "payments",
        "description": "Create an IntaSend payment link",
        "params": {
            "amount": {"type": "number", "required": True},
            "currency": {"type": "string", "required": False},
            "description": {"type": "string", "required": True},
            "phone_number": {"type": "string", "required": False},
            "email": {"type": "string", "required": False},
        },
        "risk_level": "high",
        "confirmation_policy": "always",
        "capability_gate": "allow_payments",
    },
    {
        "action": "withdraw",
        "aliases": [],
        "service": "payments",
        "description": "Withdraw to M-Pesa",
        "params": {
            "amount": {"type": "number", "required": True},
            "phone_number": {"type": "string", "required": True},
        },
        "risk_level": "high",
        "confirmation_policy": "always",
        "capability_gate": "allow_payments",
    },
    {
        "action": "check_status",
        "aliases": ["payment_status"],
        "service": "payments",
        "description": "Check IntaSend payment status",
        "params": {"invoice_id": {"type": "string", "required": True}},
        "risk_level": "medium",
        "confirmation_policy": "never",
        "capability_gate": "allow_payments",
    },
    {
        "action": "check_availability",
        "aliases": [],
        "service": "calendly",
        "description": "Fetch upcoming events",
        "params": {},
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": "allow_calendar",
    },
    {
        "action": "schedule_meeting",
        "aliases": ["book_meeting"],
        "service": "calendly",
        "description": "Return booking link",
        "params": {"target_user": {"type": "string", "required": False}},
        "risk_level": "medium",
        "confirmation_policy": "never",
        "capability_gate": "allow_calendar",
    },
    {
        "action": "search_buses",
        "aliases": [],
        "service": "travel",
        "description": "Search buses",
        "params": {
            "origin": {"type": "string", "required": True},
            "destination": {"type": "string", "required": True},
            "travel_date": {"type": "string", "required": True},
        },
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": "allow_travel",
    },
    {
        "action": "search_hotels",
        "aliases": [],
        "service": "travel",
        "description": "Search hotels",
        "params": {
            "location": {"type": "string", "required": True},
            "check_in_date": {"type": "string", "required": True},
            "check_out_date": {"type": "string", "required": True},
        },
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": "allow_travel",
    },
    {
        "action": "search_flights",
        "aliases": [],
        "service": "travel",
        "description": "Search flights",
        "params": {
            "origin": {"type": "string", "required": True},
            "destination": {"type": "string", "required": True},
            "departure_date": {"type": "string", "required": True},
        },
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": "allow_travel",
    },
    {
        "action": "search_transfers",
        "aliases": [],
        "service": "travel",
        "description": "Search transfers",
        "params": {
            "origin": {"type": "string", "required": True},
            "destination": {"type": "string", "required": True},
            "travel_date": {"type": "string", "required": True},
        },
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": "allow_travel",
    },
    {
        "action": "search_events",
        "aliases": [],
        "service": "travel",
        "description": "Search events",
        "params": {"location": {"type": "string", "required": True}},
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": "allow_travel",
    },
    {
        "action": "create_itinerary",
        "aliases": [],
        "service": "travel",
        "description": "Create itinerary",
        "params": {},
        "risk_level": "medium",
        "confirmation_policy": "never",
        "capability_gate": "allow_travel",
    },
    {
        "action": "view_itinerary",
        "aliases": [],
        "service": "travel",
        "description": "View itinerary",
        "params": {"itinerary_id": {"type": "string", "required": False}},
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": "allow_travel",
    },
    {
        "action": "add_to_itinerary",
        "aliases": [],
        "service": "travel",
        "description": "Add item to itinerary",
        "params": {"item_type": {"type": "string", "required": True}},
        "risk_level": "medium",
        "confirmation_policy": "never",
        "capability_gate": "allow_travel",
    },
    {
        "action": "book_travel_item",
        "aliases": [],
        "service": "travel",
        "description": "Book itinerary item",
        "params": {"item_id": {"type": "string", "required": True}},
        "risk_level": "high",
        "confirmation_policy": "always",
        "capability_gate": "allow_travel",
    },
    {
        "action": "search_info",
        "aliases": ["search", "lookup", "research"],
        "service": "search",
        "description": "Search for information",
        "params": {"query": {"type": "string", "required": True}},
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": "allow_web_search",
    },
    {
        "action": "get_weather",
        "aliases": ["weather", "forecast"],
        "service": "weather",
        "description": "Get weather for a city",
        "params": {"city": {"type": "string", "required": True}},
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": "allow_web_search",
    },
    {
        "action": "search_gif",
        "aliases": ["gif"],
        "service": "gif",
        "description": "Search GIFs",
        "params": {"query": {"type": "string", "required": True}},
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": "allow_web_search",
    },
    {
        "action": "convert_currency",
        "aliases": ["convert", "exchange"],
        "service": "currency",
        "description": "Convert currency",
        "params": {
            "amount": {"type": "number", "required": True},
            "from_currency": {"type": "string", "required": True},
            "to_currency": {"type": "string", "required": True},
        },
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": "allow_web_search",
    },
    {
        "action": "set_reminder",
        "aliases": ["remind"],
        "service": "reminder",
        "description": "Create reminder",
        "params": {
            "content": {"type": "string", "required": True},
            "time": {"type": "string", "required": True},
            "priority": {"type": "string", "required": False},
        },
        "risk_level": "medium",
        "confirmation_policy": "never",
        "capability_gate": "allow_reminders",
    },
    {
        "action": "check_quotas",
        "aliases": ["quotas", "usage"],
        "service": "quota",
        "description": "Check user quotas",
        "params": {},
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": None,
    },
]


SERVICE_METADATA: Dict[str, Dict[str, Any]] = {
    "gmail": {"description": "Send emails from a connected Gmail account."},
    "mailgun": {"description": "Legacy alias for Gmail send-only.", "action_aliases": {"send_email": "send_email"}},
    "whatsapp": {"description": "Send WhatsApp messages using the system account."},
    "payments": {"description": "Payment actions for wallet, invoices, links, and status."},
    "calendly": {"description": "Calendly scheduling actions."},
    "travel": {"description": "Travel search and itinerary management actions."},
    "search": {"description": "Web search actions."},
    "weather": {"description": "Weather lookup actions."},
    "gif": {"description": "GIF search actions."},
    "currency": {"description": "Currency conversion actions."},
    "reminder": {"description": "Reminder scheduling actions."},
    "quota": {"description": "Usage and quota checks."},
    "schedule": {"description": "Scheduled trigger service."},
}


SERVICE_TRIGGERS: Dict[str, List[Dict[str, Any]]] = {
    "payments": [
        {
            "event": "payment.completed",
            "description": "When a payment is completed",
            "payload_fields": ["invoice_id", "amount", "email", "state"],
        },
        {
            "event": "payment.failed",
            "description": "When a payment fails",
            "payload_fields": ["invoice_id", "amount", "email", "state"],
        },
        {
            "event": "payment.updated",
            "description": "When a payment status updates",
            "payload_fields": ["invoice_id", "amount", "email", "state"],
        },
    ],
    "calendly": [
        {
            "event": "invitee.created",
            "description": "When a Calendly invitee is created",
            "payload_fields": ["uri", "email", "name"],
        },
        {
            "event": "invitee.canceled",
            "description": "When a Calendly invitee cancels",
            "payload_fields": ["uri", "email", "name"],
        },
    ],
    "schedule": [
        {
            "event": "cron",
            "description": "Cron schedule trigger",
            "payload_fields": ["cron", "timezone"],
        },
    ],
}


_ACTION_INDEX: Dict[str, Dict[str, Any]] = {item["action"]: item for item in ACTION_CATALOG}
_ALIAS_INDEX: Dict[str, str] = {}
for item in ACTION_CATALOG:
    canonical = item["action"]
    _ALIAS_INDEX[canonical] = canonical
    for alias in item.get("aliases") or []:
        _ALIAS_INDEX[str(alias).strip().lower()] = canonical


def resolve_action_alias(action: Optional[str]) -> str:
    if not action:
        return ""
    return _ALIAS_INDEX.get(str(action).strip().lower(), str(action).strip().lower())


def get_action_definition(action: Optional[str]) -> Optional[Dict[str, Any]]:
    canonical = resolve_action_alias(action)
    definition = _ACTION_INDEX.get(canonical)
    if not definition:
        return None
    return deepcopy(definition)


def iter_action_definitions() -> List[Dict[str, Any]]:
    return deepcopy(ACTION_CATALOG)


def get_supported_actions(include_aliases: bool = False) -> List[str]:
    actions = [item["action"] for item in ACTION_CATALOG]
    if not include_aliases:
        return actions
    aliases: List[str] = []
    for item in ACTION_CATALOG:
        aliases.extend(item.get("aliases") or [])
    return actions + aliases


def get_required_params(action: Optional[str]) -> List[str]:
    definition = get_action_definition(action)
    if not definition:
        return []
    required: List[str] = []
    for param_name, spec in (definition.get("params") or {}).items():
        if spec.get("required"):
            required.append(param_name)
    return required


def is_high_risk_action(action: Optional[str]) -> bool:
    definition = get_action_definition(action)
    if not definition:
        return False
    return str(definition.get("risk_level") or "").lower() == "high"


def requires_confirmation(action: Optional[str]) -> bool:
    definition = get_action_definition(action)
    if not definition:
        return False
    return str(definition.get("confirmation_policy") or "").lower() == "always"


def get_capability_gate(action: Optional[str]) -> Optional[str]:
    definition = get_action_definition(action)
    if not definition:
        return None
    return definition.get("capability_gate")


def build_capabilities_catalog() -> Dict[str, Any]:
    by_service: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in ACTION_CATALOG:
        service = str(item.get("service") or "")
        if not service:
            continue
        by_service[service].append(
            {
                "name": item["action"],
                "description": item.get("description") or item["action"].replace("_", " "),
                "params": deepcopy(item.get("params") or {}),
            }
        )

    integrations: List[Dict[str, Any]] = []
    for service in sorted(by_service.keys()):
        meta = SERVICE_METADATA.get(service, {})
        integrations.append(
            {
                "service": service,
                "description": meta.get("description") or "",
                "actions": by_service.get(service) or [],
                "triggers": deepcopy(SERVICE_TRIGGERS.get(service) or []),
            }
        )

    # Keep legacy mailgun alias service for backward compatibility in workflow editor prompts.
    if "mailgun" not in by_service:
        mailgun_meta = SERVICE_METADATA.get("mailgun")
        if mailgun_meta:
            integrations.append(
                {
                    "service": "mailgun",
                    "description": mailgun_meta.get("description") or "",
                    "actions": [
                        {
                            "name": "send_email",
                            "description": "Send an email",
                            "params": deepcopy(_ACTION_INDEX["send_email"].get("params") or {}),
                        }
                    ],
                    "triggers": [],
                }
            )

    integrations.append(
        {
            "service": "schedule",
            "description": SERVICE_METADATA.get("schedule", {}).get("description", ""),
            "actions": [],
            "triggers": deepcopy(SERVICE_TRIGGERS.get("schedule") or []),
        }
    )
    return {"integrations": integrations}


def validate_router_mappings(
    mapped_actions: Iterable[str],
    *,
    allow_unmapped: Optional[Iterable[str]] = None,
) -> Tuple[List[str], List[str]]:
    allowed_unmapped = set(resolve_action_alias(action) for action in (allow_unmapped or []))
    mapped = {resolve_action_alias(action) for action in mapped_actions if action}
    required = set(get_supported_actions()) - allowed_unmapped
    missing = sorted(required - mapped)
    extra = sorted(mapped - required)
    return missing, extra

