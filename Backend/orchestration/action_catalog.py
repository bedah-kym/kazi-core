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
        "description": "Send an email to a recipient via the user's connected Gmail account. Use this when the user wants to email someone.",
        "params": {
            "to": {"type": "string", "required": True, "description": "Recipient email address"},
            "subject": {"type": "string", "required": True, "description": "Email subject line"},
            "text": {"type": "string", "required": True, "description": "Plain text body of the email"},
            "html": {"type": "string", "required": False, "description": "Optional HTML body for rich formatting"},
            "from": {"type": "string", "required": False, "description": "Sender email override (defaults to user's Gmail)"},
        },
        "return_description": "Returns status and email ID on success",
        "risk_level": "high",
        "confirmation_policy": "always",
        "capability_gate": "allow_email",
    },
    {
        "action": "send_message",
        "aliases": ["send_whatsapp", "whatsapp", "send_sms", "send_text"],
        "service": "whatsapp",
        "description": "Send a WhatsApp message to a phone number. Use this when the user wants to message someone on WhatsApp.",
        "params": {
            "phone_number": {"type": "string", "required": True, "description": "Recipient phone number in international format (e.g., +254712345678)"},
            "message": {"type": "string", "required": True, "description": "Text content of the message"},
            "media_url": {"type": "string", "required": False, "description": "URL of an image or document to attach"},
        },
        "return_description": "Returns status and message SID on success",
        "risk_level": "high",
        "confirmation_policy": "always",
        "capability_gate": "allow_whatsapp",
    },
    {
        "action": "check_balance",
        "aliases": [],
        "service": "payments",
        "description": "Check the user's wallet balance. Use this when the user asks about their balance or funds.",
        "params": {},
        "return_description": "Returns current wallet balance and currency",
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": "allow_payments",
    },
    {
        "action": "list_transactions",
        "aliases": [],
        "service": "payments",
        "description": "List the user's recent wallet transactions. Use this when the user asks about their transaction history.",
        "params": {
            "limit": {"type": "integer", "required": False, "description": "Maximum number of transactions to return (default 10)"},
        },
        "return_description": "Returns a list of recent transactions with amount, type, description, and date",
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": "allow_payments",
    },
    {
        "action": "check_invoice_status",
        "aliases": ["invoice_status"],
        "service": "payments",
        "description": "Check the status of a specific invoice by its ID. Use this when the user asks about a pending invoice.",
        "params": {
            "invoice_id": {"type": "string", "required": True, "description": "The invoice ID to look up"},
        },
        "return_description": "Returns invoice status, amount, and payment details",
        "risk_level": "medium",
        "confirmation_policy": "never",
        "capability_gate": "allow_payments",
    },
    {
        "action": "check_payments",
        "aliases": [],
        "service": "payments",
        "description": "Get an overview of the user's financial status including balance and recent transactions. Use this for a general payments summary.",
        "params": {},
        "return_description": "Returns balance and a summary of recent transactions",
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": "allow_payments",
    },
    {
        "action": "create_invoice",
        "aliases": ["invoice"],
        "service": "payments",
        "description": "Create an invoice for a specified amount and optionally send it to the payer via email or WhatsApp.",
        "params": {
            "amount": {"type": "number", "required": True, "description": "Invoice amount in the specified currency"},
            "currency": {"type": "string", "required": False, "description": "Currency code (default KES). Examples: KES, USD, EUR"},
            "description": {"type": "string", "required": False, "description": "Description of what the invoice is for"},
            "payer_email": {"type": "string", "required": False, "description": "Email address of the payer to send the invoice to"},
            "phone_number": {"type": "string", "required": False, "description": "Phone number to send the invoice via WhatsApp"},
            "send_via": {"type": "string", "required": False, "description": "Notification channel: 'email', 'whatsapp', or 'both'"},
        },
        "return_description": "Returns invoice ID, payment link, and notification status",
        "risk_level": "high",
        "confirmation_policy": "always",
        "capability_gate": "allow_payments",
    },
    {
        "action": "create_payment_link",
        "aliases": ["payment_link"],
        "service": "payments",
        "description": "Create a shareable IntaSend payment link that anyone can use to pay. Supports M-Pesa and card payments.",
        "params": {
            "amount": {"type": "number", "required": True, "description": "Payment amount"},
            "currency": {"type": "string", "required": False, "description": "Currency code (default KES)"},
            "description": {"type": "string", "required": True, "description": "What the payment is for"},
            "phone_number": {"type": "string", "required": False, "description": "Payer's phone number for M-Pesa STK push"},
            "email": {"type": "string", "required": False, "description": "Payer's email address"},
        },
        "return_description": "Returns the payment link URL and reference ID",
        "risk_level": "high",
        "confirmation_policy": "always",
        "capability_gate": "allow_payments",
    },
    {
        "action": "withdraw",
        "aliases": [],
        "service": "payments",
        "description": "Withdraw funds from the user's wallet to an M-Pesa phone number. This is an irreversible financial action.",
        "params": {
            "amount": {"type": "number", "required": True, "description": "Amount to withdraw in KES"},
            "phone_number": {"type": "string", "required": True, "description": "M-Pesa phone number to send funds to (e.g., +254712345678)"},
        },
        "return_description": "Returns withdrawal status and transaction reference",
        "risk_level": "high",
        "confirmation_policy": "always",
        "capability_gate": "allow_payments",
    },
    {
        "action": "check_status",
        "aliases": ["payment_status"],
        "service": "payments",
        "description": "Check the status of an IntaSend payment by invoice ID.",
        "params": {
            "invoice_id": {"type": "string", "required": True, "description": "The IntaSend invoice/payment ID to check"},
        },
        "return_description": "Returns payment state, amount, and completion details",
        "risk_level": "medium",
        "confirmation_policy": "never",
        "capability_gate": "allow_payments",
    },
    {
        "action": "check_availability",
        "aliases": [],
        "service": "calendly",
        "description": "Fetch the user's upcoming Calendly events and schedule. Use this when the user asks about their calendar.",
        "params": {},
        "return_description": "Returns a list of upcoming events with names, times, and invitee details",
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": "allow_calendar",
    },
    {
        "action": "schedule_meeting",
        "aliases": ["book_meeting"],
        "service": "calendly",
        "description": "Get the user's Calendly booking link so others can schedule a meeting with them.",
        "params": {
            "target_user": {"type": "string", "required": False, "description": "Name or email of the person to schedule with (optional)"},
        },
        "return_description": "Returns the Calendly booking URL",
        "risk_level": "medium",
        "confirmation_policy": "never",
        "capability_gate": "allow_calendar",
    },
    {
        "action": "search_buses",
        "aliases": [],
        "service": "travel",
        "description": "Search for available bus tickets between two cities on a specific date. Returns bus options with prices and departure times.",
        "params": {
            "origin": {"type": "string", "required": True, "description": "Departure city (e.g., Nairobi)"},
            "destination": {"type": "string", "required": True, "description": "Arrival city (e.g., Mombasa)"},
            "travel_date": {"type": "string", "required": True, "description": "Travel date in YYYY-MM-DD format"},
        },
        "return_description": "Returns a list of bus options with operator, departure_time, price_ksh, and duration",
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": "allow_travel",
    },
    {
        "action": "search_hotels",
        "aliases": [],
        "service": "travel",
        "description": "Search for available hotels and accommodation in a location for specific dates. Returns hotel options with prices and ratings.",
        "params": {
            "location": {"type": "string", "required": True, "description": "City or area to search (e.g., Mombasa, Diani Beach)"},
            "check_in_date": {"type": "string", "required": True, "description": "Check-in date in YYYY-MM-DD format"},
            "check_out_date": {"type": "string", "required": True, "description": "Check-out date in YYYY-MM-DD format"},
        },
        "return_description": "Returns a list of hotels with name, rating, price_ksh, and amenities",
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": "allow_travel",
    },
    {
        "action": "search_flights",
        "aliases": [],
        "service": "travel",
        "description": "Search for available flights between two cities on a specific date via Amadeus. Returns flight options with prices, airlines, and times.",
        "params": {
            "origin": {"type": "string", "required": True, "description": "Departure city or 3-letter IATA airport code (e.g., Nairobi or NBO)"},
            "destination": {"type": "string", "required": True, "description": "Arrival city or 3-letter IATA airport code (e.g., London or LHR)"},
            "departure_date": {"type": "string", "required": True, "description": "Departure date in YYYY-MM-DD format"},
            "return_date": {"type": "string", "required": False, "description": "Return date for round trips in YYYY-MM-DD format"},
            "passengers": {"type": "integer", "required": False, "description": "Number of passengers (default 1)"},
            "cabin_class": {"type": "string", "required": False, "description": "Cabin class: economy, business, or first (default economy)"},
        },
        "return_description": "Returns a list of flights with airline, flight_number, departure_time, arrival_time, price_ksh, stops, and booking_url",
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": "allow_travel",
    },
    {
        "action": "search_transfers",
        "aliases": [],
        "service": "travel",
        "description": "Search for ground transfers and airport shuttles between two locations.",
        "params": {
            "origin": {"type": "string", "required": True, "description": "Pickup location (e.g., JKIA Airport)"},
            "destination": {"type": "string", "required": True, "description": "Drop-off location (e.g., Nairobi CBD)"},
            "travel_date": {"type": "string", "required": True, "description": "Transfer date in YYYY-MM-DD format"},
        },
        "return_description": "Returns a list of transfer options with provider, vehicle_type, price_ksh, and duration",
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": "allow_travel",
    },
    {
        "action": "search_events",
        "aliases": [],
        "service": "travel",
        "description": "Search for events and activities in a location. Returns upcoming events with ticket info.",
        "params": {
            "location": {"type": "string", "required": True, "description": "City or area to search for events (e.g., Nairobi)"},
            "start_date": {"type": "string", "required": False, "description": "Start date filter in YYYY-MM-DD format"},
            "category": {"type": "string", "required": False, "description": "Event category filter (e.g., music, tech, food)"},
        },
        "return_description": "Returns a list of events with title, date, venue, price_ksh, and ticket_url",
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": "allow_travel",
    },
    {
        "action": "create_itinerary",
        "aliases": [],
        "service": "travel",
        "description": "Create a new travel itinerary to organize trip items. Use this before adding flights, hotels, or activities.",
        "params": {
            "title": {"type": "string", "required": False, "description": "Name for the itinerary (e.g., 'Mombasa Weekend Trip')"},
        },
        "return_description": "Returns the new itinerary ID and details",
        "risk_level": "medium",
        "confirmation_policy": "never",
        "capability_gate": "allow_travel",
    },
    {
        "action": "view_itinerary",
        "aliases": [],
        "service": "travel",
        "description": "View the details of an itinerary including all added items (flights, hotels, events).",
        "params": {
            "itinerary_id": {"type": "string", "required": False, "description": "Itinerary ID to view. If not provided, shows the most recent active itinerary."},
        },
        "return_description": "Returns itinerary details with all items, dates, and total cost",
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": "allow_travel",
    },
    {
        "action": "add_to_itinerary",
        "aliases": [],
        "service": "travel",
        "description": "Add a travel item (flight, hotel, bus, transfer, or event) to an itinerary from previous search results.",
        "params": {
            "item_type": {"type": "string", "required": True, "description": "Type of item: 'flight', 'hotel', 'bus', 'transfer', or 'event'"},
            "item_id": {"type": "string", "required": False, "description": "ID of the item from search results to add"},
            "itinerary_id": {"type": "string", "required": False, "description": "Target itinerary ID. Uses active itinerary if not specified."},
        },
        "return_description": "Returns updated itinerary with the new item added",
        "risk_level": "medium",
        "confirmation_policy": "never",
        "capability_gate": "allow_travel",
    },
    {
        "action": "book_travel_item",
        "aliases": [],
        "service": "travel",
        "description": "Book a travel item (flight, hotel, etc.) from the itinerary. This initiates the actual booking with the provider.",
        "params": {
            "item_id": {"type": "string", "required": True, "description": "ID of the itinerary item to book"},
        },
        "return_description": "Returns booking confirmation with reference number and provider details",
        "risk_level": "high",
        "confirmation_policy": "always",
        "capability_gate": "allow_travel",
    },
    {
        "action": "search_info",
        "aliases": ["search", "lookup", "research"],
        "service": "search",
        "description": "Search the web for information on any topic. Use this when you need to look up facts, current events, or answer knowledge questions.",
        "params": {
            "query": {"type": "string", "required": True, "description": "The search query to look up"},
        },
        "return_description": "Returns search results with titles, snippets, and URLs",
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": "allow_web_search",
    },
    {
        "action": "get_weather",
        "aliases": ["weather", "forecast"],
        "service": "weather",
        "description": "Get current weather conditions for a city including temperature, humidity, and description.",
        "params": {
            "city": {"type": "string", "required": True, "description": "City name to get weather for (e.g., Nairobi, London, Dubai)"},
        },
        "return_description": "Returns temperature (Celsius), feels_like, humidity, wind_speed, and weather description",
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": "allow_web_search",
    },
    {
        "action": "search_gif",
        "aliases": ["gif"],
        "service": "gif",
        "description": "Search for a GIF on GIPHY matching a keyword or phrase.",
        "params": {
            "query": {"type": "string", "required": True, "description": "Search term for the GIF (e.g., 'happy dance', 'thumbs up')"},
        },
        "return_description": "Returns a GIF URL",
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": "allow_web_search",
    },
    {
        "action": "convert_currency",
        "aliases": ["convert", "exchange"],
        "service": "currency",
        "description": "Convert an amount from one currency to another using live exchange rates.",
        "params": {
            "amount": {"type": "number", "required": True, "description": "Amount to convert"},
            "from_currency": {"type": "string", "required": True, "description": "Source currency ISO code (e.g., USD, KES, EUR)"},
            "to_currency": {"type": "string", "required": True, "description": "Target currency ISO code (e.g., KES, USD, GBP)"},
        },
        "return_description": "Returns converted amount and the exchange rate used",
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": "allow_web_search",
    },
    {
        "action": "set_reminder",
        "aliases": ["remind"],
        "service": "reminder",
        "description": "Create a scheduled reminder that will notify the user at the specified time.",
        "params": {
            "content": {"type": "string", "required": True, "description": "What to remind the user about"},
            "time": {"type": "string", "required": True, "description": "When to send the reminder. Accepts ISO datetime (2026-03-15T10:00:00) or relative time (in 30 minutes, tomorrow at 9am)"},
            "priority": {"type": "string", "required": False, "description": "Priority level: 'low', 'medium', or 'high' (default medium)"},
        },
        "return_description": "Returns reminder ID and scheduled time confirmation",
        "risk_level": "medium",
        "confirmation_policy": "never",
        "capability_gate": "allow_reminders",
    },
    {
        "action": "check_quotas",
        "aliases": ["quotas", "usage"],
        "service": "quota",
        "description": "Check the user's current usage quotas for searches, LLM tokens, and API calls.",
        "params": {},
        "return_description": "Returns current usage vs limits for each quota category",
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

