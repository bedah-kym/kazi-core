"""
Plan ad-hoc multi-step workflows from a single user request and execute them.
"""
import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from asgiref.sync import sync_to_async
from django.core.cache import cache
from django.utils import timezone
from django.conf import settings

from orchestration.llm_client import get_llm_client
from workflows.capabilities import SYSTEM_CAPABILITIES, validate_workflow_definition
from workflows.temporal_integration import start_workflow_execution

logger = logging.getLogger(__name__)

MAX_ADHOC_STEPS = 7
MIN_ADHOC_STEPS = 2
MAX_WAIT_SECONDS = 20
IDEMPOTENCY_TTL_SECONDS = 90
IDEMPOTENCY_CACHE_PREFIX = "adhoc_workflow"
_CONFIRM_WORDS = {
    "yes",
    "approve",
    "approved",
    "confirm",
    "confirmed",
    "go ahead",
    "proceed",
}
_HIGH_RISK_ACTIONS = {
    ("payments", "withdraw"),
}
_SERVICE_ALIASES = {
    "email": "mailgun",
    "mail": "mailgun",
    "gmail": "mailgun",
    "mailgun": "mailgun",
    "whatsapp": "whatsapp",
    "wa": "whatsapp",
    "sms": "whatsapp",
    "text": "whatsapp",
    "payment": "payments",
    "payments": "payments",
    "wallet": "payments",
    "calendar": "calendly",
    "calendly": "calendly",
    "meeting": "calendly",
    "schedule": "calendly",
    "reminder": "reminder",
    "reminders": "reminder",
    "quota": "quota",
    "quotas": "quota",
    "usage": "quota",
    "job": "jobs",
    "jobs": "jobs",
    "upwork": "jobs",
    "search": "search",
    "web": "search",
    "google": "search",
    "weather": "weather",
    "forecast": "weather",
    "gif": "gif",
    "gifs": "gif",
    "currency": "currency",
    "fx": "currency",
    "exchange": "currency",
    "convert": "currency",
    "travel": "travel",
    "trip": "travel",
    "amadeus": "travel",
}
_ACTION_ALIASES = {
    "mailgun": {
        "email": "send_email",
        "send": "send_email",
        "send_mail": "send_email",
        "send_email": "send_email",
    },
    "whatsapp": {
        "whatsapp": "send_message",
        "send": "send_message",
        "send_whatsapp": "send_message",
        "send_message": "send_message",
        "message": "send_message",
        "send_sms": "send_message",
        "send_text": "send_message",
    },
    "payments": {
        "balance": "check_balance",
        "check_balance": "check_balance",
        "transactions": "list_transactions",
        "list_transactions": "list_transactions",
        "check_payments": "check_payments",
        "payments": "check_payments",
        "payment_status": "check_status",
        "check_status": "check_status",
        "invoice_status": "check_invoice_status",
        "check_invoice_status": "check_invoice_status",
        "create_payment_link": "create_payment_link",
        "payment_link": "create_payment_link",
        "withdraw": "withdraw",
    },
    "calendly": {
        "availability": "check_availability",
        "check_availability": "check_availability",
        "schedule": "schedule_meeting",
        "schedule_meeting": "schedule_meeting",
        "book_meeting": "schedule_meeting",
    },
    "jobs": {
        "find_jobs": "find_jobs",
        "search_jobs": "find_jobs",
        "jobs": "find_jobs",
    },
    "search": {
        "search_info": "search_info",
        "search": "search_info",
        "lookup": "search_info",
        "research": "search_info",
    },
    "weather": {
        "get_weather": "get_weather",
        "weather": "get_weather",
        "forecast": "get_weather",
    },
    "gif": {
        "search_gif": "search_gif",
        "gif": "search_gif",
        "search": "search_gif",
    },
    "currency": {
        "convert_currency": "convert_currency",
        "convert": "convert_currency",
        "exchange": "convert_currency",
    },
    "reminder": {
        "set_reminder": "set_reminder",
        "remind": "set_reminder",
    },
    "quota": {
        "check_quotas": "check_quotas",
        "quotas": "check_quotas",
        "usage": "check_quotas",
    },
}
_ACTION_SERVICE_FALLBACK = {
    "send_email": "mailgun",
    "send_whatsapp": "whatsapp",
    "send_message": "whatsapp",
    "check_balance": "payments",
    "list_transactions": "payments",
    "check_invoice_status": "payments",
    "check_payments": "payments",
    "create_payment_link": "payments",
    "withdraw": "payments",
    "check_status": "payments",
    "check_availability": "calendly",
    "schedule_meeting": "calendly",
    "find_jobs": "jobs",
    "search_info": "search",
    "get_weather": "weather",
    "search_gif": "gif",
    "convert_currency": "currency",
    "set_reminder": "reminder",
    "check_quotas": "quota",
}
_STEP_SPLIT_RE = re.compile(r"\b(?:then|and then|after that|afterwards|next)\b", re.IGNORECASE)
_TRAVEL_SERVICE_ALIASES = {
    "flight": "flight",
    "flights": "flight",
    "hotel": "hotel",
    "hotels": "hotel",
    "bus": "bus",
    "buses": "bus",
    "transfer": "transfer",
    "transfers": "transfer",
    "event": "event",
    "events": "event",
}
_TRAVEL_ACTION_PLURALS = {
    "flight": "flights",
    "hotel": "hotels",
    "bus": "buses",
    "transfer": "transfers",
    "event": "events",
}
_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"\+?\d{7,15}")

_AUTOMATION_HINTS = (
    "workflow",
    "automate",
    "automation",
    "every ",
    "whenever",
    "schedule",
    "cron",
)


def _looks_like_automation(message: str) -> bool:
    lowered = message.lower()
    return any(hint in lowered for hint in _AUTOMATION_HINTS)


def _looks_like_confirmation(message: str) -> bool:
    lowered = message.strip().lower()
    return any(word in lowered for word in _CONFIRM_WORDS)


def _has_high_risk_step(steps: List[Dict[str, Any]]) -> bool:
    for step in steps:
        service = str(step.get("service") or "").lower()
        action = str(step.get("action") or "").lower()
        if (service, action) in _HIGH_RISK_ACTIONS:
            return True
    return False


def _normalize_service_action(step: Dict[str, Any]) -> Dict[str, Any]:
    service = str(step.get("service") or "").lower()
    action = str(step.get("action") or "").lower()

    if service in _SERVICE_ALIASES:
        service = _SERVICE_ALIASES[service]

    if not service and action in _ACTION_SERVICE_FALLBACK:
        service = _ACTION_SERVICE_FALLBACK[action]

    if service and action in _ACTION_ALIASES.get(service, {}):
        action = _ACTION_ALIASES[service][action]

    if not service and action in _ACTION_SERVICE_FALLBACK:
        service = _ACTION_SERVICE_FALLBACK[action]

    if service:
        step["service"] = service
    if action:
        step["action"] = action
    return step


def _split_step_phrases(message: str) -> List[str]:
    if not message:
        return []
    parts = _STEP_SPLIT_RE.split(message)
    return [part.strip(" ,.;") for part in parts if part.strip()]


def _coerce_year(year_str: str) -> Optional[int]:
    if not year_str:
        return None
    try:
        year = int(year_str)
    except ValueError:
        return None
    if year < 100:
        return 2000 + year
    return year


def _safe_date(year: int, month: int, day: int) -> Optional[str]:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _parse_numeric_date(day_str: str, month_str: str, year_str: str, dayfirst_default: bool = True) -> Optional[str]:
    year = _coerce_year(year_str)
    if year is None:
        return None
    try:
        day = int(day_str)
        month = int(month_str)
    except ValueError:
        return None

    if day > 12 and month <= 12:
        return _safe_date(year, month, day)
    if month > 12 and day <= 12:
        return _safe_date(year, day, month)

    if dayfirst_default:
        return _safe_date(year, month, day)
    return _safe_date(year, day, month)


def _extract_dates_from_text(message: str) -> List[str]:
    if not message:
        return []

    lowered = message.lower()
    today = timezone.localdate()
    found: List[Tuple[int, str]] = []

    for match in re.finditer(r"\btoday\b", lowered):
        found.append((match.start(), today.isoformat()))
    for match in re.finditer(r"\btomorrow\b", lowered):
        found.append((match.start(), (today + timedelta(days=1)).isoformat()))

    for match in re.finditer(r"\b(20\d{2})-(\d{2})-(\d{2})\b", message):
        found.append((match.start(), match.group(0)))

    for match in re.finditer(r"\b(20\d{2})[/-](\d{1,2})[/-](\d{1,2})\b", message):
        iso = _safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if iso:
            found.append((match.start(), iso))

    for match in re.finditer(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b", message):
        iso = _parse_numeric_date(match.group(1), match.group(2), match.group(3), dayfirst_default=True)
        if iso:
            found.append((match.start(), iso))

    for match in re.finditer(
        r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s*(\d{1,2})(?:st|nd|rd|th)?(?:,)?\s*(\d{2,4})\b",
        lowered,
    ):
        month = _MONTHS.get(match.group(1)[:3])
        year = _coerce_year(match.group(3))
        if month and year:
            iso = _safe_date(year, month, int(match.group(2)))
            if iso:
                found.append((match.start(), iso))

    for match in re.finditer(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s*(\d{2,4})\b",
        lowered,
    ):
        month = _MONTHS.get(match.group(2)[:3])
        year = _coerce_year(match.group(3))
        if month and year:
            iso = _safe_date(year, month, int(match.group(1)))
            if iso:
                found.append((match.start(), iso))

    for match in re.finditer(
        r"\b(\d{1,2})(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)(\d{2,4})\b",
        lowered,
    ):
        month = _MONTHS.get(match.group(2))
        year = _coerce_year(match.group(3))
        if month and year:
            iso = _safe_date(year, month, int(match.group(1)))
            if iso:
                found.append((match.start(), iso))

    found.sort(key=lambda item: item[0])
    seen = set()
    ordered: List[str] = []
    for _, iso in found:
        if iso not in seen:
            seen.add(iso)
            ordered.append(iso)
    return ordered


def _normalize_date_value(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    if isinstance(value, str):
        trimmed = value.strip()
        if re.match(r"\b20\d{2}-\d{2}-\d{2}\b", trimmed):
            return trimmed
        extracted = _extract_dates_from_text(trimmed)
        if extracted:
            return extracted[0]
        return trimmed
    return value


def _extract_first_email(message: str) -> Optional[str]:
    if not message:
        return None
    match = _EMAIL_RE.search(message)
    return match.group(0) if match else None


def _extract_first_phone(message: str) -> Optional[str]:
    if not message:
        return None
    match = _PHONE_RE.search(message.replace(" ", ""))
    return match.group(0) if match else None


def _extract_guests(message: str) -> Optional[int]:
    if not message:
        return None
    lowered = message.lower()
    match = re.search(r"\b(\d{1,2})\s*(guests?|people|persons?|adults?|pax)\b", lowered)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


def _extract_rooms(message: str) -> Optional[int]:
    if not message:
        return None
    lowered = message.lower()
    match = re.search(r"\b(\d{1,2})\s*rooms?\b", lowered)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


def _extract_budget_ksh(message: str) -> Optional[float]:
    if not message:
        return None
    lowered = message.lower()
    if not any(token in lowered for token in ("budget", "ksh", "kes", "shilling", "per night", "night")):
        return None
    match = re.search(r"(?:budget|under|below|less than)\s*([0-9][0-9,\.]*)\s*(k|ksh|kes)?", lowered)
    if not match:
        match = re.search(r"\b([0-9][0-9,\.]*)\s*(ksh|kes)\b", lowered)
    if not match:
        match = re.search(r"\b([0-9]+(?:\.\d+)?)\s*k\b", lowered)
    if not match:
        return None
    raw = match.group(1).replace(",", "")
    try:
        amount = float(raw)
    except ValueError:
        return None
    suffix = match.group(2) if len(match.groups()) >= 2 else None
    if suffix == "k":
        amount *= 1000
    return amount


def _extract_invoice_id(message: str) -> Optional[str]:
    if not message:
        return None
    match = re.search(r"\binvoice\s*#?\s*([A-Za-z0-9_-]+)\b", message, flags=re.IGNORECASE)
    return match.group(1) if match else None


def _extract_currency_conversion(message: str) -> Dict[str, Optional[str]]:
    if not message:
        return {"amount": None, "from_currency": None, "to_currency": None}
    match = re.search(
        r"\b([0-9][0-9,\.]*)\s*([A-Za-z]{3})\s*(?:to|in)\s*([A-Za-z]{3})\b",
        message,
        flags=re.IGNORECASE,
    )
    if not match:
        return {"amount": None, "from_currency": None, "to_currency": None}
    amount = match.group(1).replace(",", "")
    return {
        "amount": amount,
        "from_currency": match.group(2).upper(),
        "to_currency": match.group(3).upper(),
    }


def _extract_amount_with_currency(message: str) -> Dict[str, Optional[str]]:
    if not message:
        return {"amount": None, "currency": None}
    match = re.search(r"\b([A-Za-z]{3})\s*([0-9][0-9,\.]*)\b", message, flags=re.IGNORECASE)
    if match:
        return {"amount": match.group(2).replace(",", ""), "currency": match.group(1).upper()}
    match = re.search(r"\b([0-9][0-9,\.]*)\s*([A-Za-z]{3})\b", message, flags=re.IGNORECASE)
    if match:
        return {"amount": match.group(1).replace(",", ""), "currency": match.group(2).upper()}
    match = re.search(r"\b([0-9][0-9,\.]*)\b", message)
    if match:
        return {"amount": match.group(1).replace(",", ""), "currency": None}
    return {"amount": None, "currency": None}


def _extract_message_text(message: str) -> Optional[str]:
    if not message:
        return None
    match = re.search(r'\b(?:saying|message|msg|text|body)\b[:\-]?\s*"?(.+)', message, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip().strip('"').strip()
    match = re.search(r'"([^"\n]+)"', message)
    if match:
        return match.group(1).strip()
    return None


def _extract_subject_text(message: str) -> Optional[str]:
    if not message:
        return None
    match = re.search(r'\bsubject\b[:\-]?\s*"?([^"\n]+)', message, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip().strip('"').strip()
    return None


def _default_subject_from_text(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    words = text.split()
    if not words:
        return None
    subject = " ".join(words[:6])
    return subject[:80]


def _extract_passengers(message: str) -> Optional[int]:
    if not message:
        return None
    lowered = message.lower()
    match = re.search(r"\b(\d{1,2})\s*(passenger|passengers|pax|adults?)\b", lowered)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


def _extract_item_id(message: str) -> Optional[str]:
    if not message:
        return None
    lowered = message.lower()
    match = re.search(r"\b(option|flight|hotel|bus|transfer|event)\s*#?\s*(\d+)\b", lowered)
    if match:
        return match.group(2)
    return None


def _extract_origin_destination(message: str) -> Dict[str, Optional[str]]:
    if not message:
        return {"origin": None, "destination": None}
    match = re.search(
        r"\bfrom\s+(.+?)\s+to\s+(.+?)(?:\s+on|\s+leaving|\s+departing|\s+return|\s+back|$)",
        message,
        flags=re.IGNORECASE,
    )
    if match:
        origin = match.group(1).strip(" ,.")
        destination = match.group(2).strip(" ,.")
        return {"origin": origin, "destination": destination}

    match = re.search(
        r"\b([A-Za-z .'-]{2,})\s+to\s+([A-Za-z .'-]{2,})(?:\s+on|\s+leaving|\s+departing|\s+return|\s+back|$)",
        message,
        flags=re.IGNORECASE,
    )
    if match:
        origin = match.group(1).strip(" ,.")
        destination = match.group(2).strip(" ,.")
        return {"origin": origin, "destination": destination}

    return {"origin": None, "destination": None}


def _extract_location(message: str) -> Optional[str]:
    if not message:
        return None
    match = re.search(
        r"\b(in|at)\s+([A-Za-z .'-]{2,})(?:\s+for|\s+on|\s+from|\s+to|$)",
        message,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(2).strip(" ,.")
    return None


def _extract_nights(message: str) -> Optional[int]:
    if not message:
        return None
    lowered = message.lower()
    match = re.search(r"\b(\d{1,2})\s+nights?\b", lowered)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


def _extract_time_string(message: str) -> Optional[str]:
    if not message:
        return None
    match = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", message)
    if match:
        hour = int(match.group(1))
        minute = match.group(2)
        return f"{hour:02d}:{minute}"
    match = re.search(r"\b([1-9]|1[0-2])\s*(am|pm)\b", message, flags=re.IGNORECASE)
    if match:
        hour = int(match.group(1))
        meridiem = match.group(2).lower()
        if meridiem == "pm" and hour != 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
        return f"{hour:02d}:00"
    return None


def _missing_param_message(param: str) -> str:
    prompts = {
        "departure_date": "What departure date should I use? (YYYY-MM-DD)",
        "travel_date": "What travel date should I use? (YYYY-MM-DD)",
        "check_in_date": "What is the check-in date? (YYYY-MM-DD)",
        "check_out_date": "What is the check-out date? (YYYY-MM-DD)",
        "origin": "What is the origin city or airport code?",
        "destination": "What is the destination city or airport code?",
        "location": "Which city should I search in?",
    }
    return prompts.get(param, f"I need the {param} to proceed.")


def _friendly_validation_error(error: str) -> str:
    if not error:
        return "I need a bit more detail to proceed."
    match = re.search(r"Missing param '([^']+)'", error)
    if match:
        return _missing_param_message(match.group(1))
    if "Unknown service" in error:
        return "I couldn't map one of the services. Please rephrase with an explicit action like 'search flights' or 'send email'."
    if "Invalid action" in error:
        return "I couldn't map one of the actions. Please rephrase the request with explicit steps."
    if "Params for step" in error:
        return "I need a bit more detail for one of the steps. Please share the missing details."
    return f"I need a bit more detail to run that: {error}"


def _normalize_travel_step(step: Dict[str, Any]) -> Dict[str, Any]:
    service = str(step.get("service") or "").lower()
    action = str(step.get("action") or "").lower()
    params = step.get("params") or {}
    item_type = None

    if service in _TRAVEL_SERVICE_ALIASES:
        item_type = _TRAVEL_SERVICE_ALIASES[service]
        step["service"] = "travel"
    elif service == "travel" and action in _TRAVEL_SERVICE_ALIASES:
        item_type = _TRAVEL_SERVICE_ALIASES[action]
        action = ""

    if item_type:
        plural = _TRAVEL_ACTION_PLURALS[item_type]
        if action in ("", "search", "find"):
            step["action"] = f"search_{plural}"
        elif action in ("book", "reserve", "booking", f"book_{item_type}", f"book_{plural}"):
            step["action"] = "book_travel_item"
            params.setdefault("item_type", item_type)
        elif action in ("add", "add_to_itinerary"):
            step["action"] = "add_to_itinerary"
            params.setdefault("item_type", item_type)
        elif action.startswith("search_"):
            step["action"] = action
        elif action.startswith("book_"):
            step["action"] = "book_travel_item"
            params.setdefault("item_type", item_type)

    if step.get("action") == "book_travel_item":
        params.setdefault("item_type", params.get("item_type") or item_type)

    step["params"] = params
    return step


def _infer_item_type_from_steps(previous_steps: List[Dict[str, Any]]) -> Optional[str]:
    search_map = {
        "search_flights": "flight",
        "search_hotels": "hotel",
        "search_buses": "bus",
        "search_transfers": "transfer",
        "search_events": "event",
    }
    for prev in reversed(previous_steps):
        params = prev.get("params") or {}
        item_type = params.get("item_type")
        if item_type:
            return item_type
        action = prev.get("action")
        if action in search_map:
            return search_map[action]
    return None


def _normalize_steps(steps: List[Dict[str, Any]], message: str) -> List[Dict[str, Any]]:
    extracted_dates = _extract_dates_from_text(message)
    origin_dest = _extract_origin_destination(message)
    location = _extract_location(message)
    passengers = _extract_passengers(message)
    guests = _extract_guests(message)
    rooms = _extract_rooms(message)
    budget_ksh = _extract_budget_ksh(message)
    email = _extract_first_email(message)
    phone = _extract_first_phone(message)
    item_id = _extract_item_id(message)
    invoice_id = _extract_invoice_id(message)
    nights = _extract_nights(message)
    travel_time = _extract_time_string(message)
    currency_conversion = _extract_currency_conversion(message)
    amount_currency = _extract_amount_with_currency(message)
    message_text = _extract_message_text(message)
    subject_text = _extract_subject_text(message)
    if message_text:
        message_text = _STEP_SPLIT_RE.split(message_text)[0].strip()
    lowered = message.lower()
    normalized = []
    for idx, step in enumerate(steps or []):
        if not isinstance(step, dict):
            continue
        cleaned = dict(step)
        cleaned.setdefault("id", f"step_{idx + 1}")
        if cleaned.get("service"):
            cleaned["service"] = str(cleaned["service"]).lower()
        if cleaned.get("action"):
            cleaned["action"] = str(cleaned["action"]).lower()
        if not isinstance(cleaned.get("params"), dict):
            cleaned["params"] = {}
        normalized_step = _normalize_service_action(cleaned)
        normalized_step = _normalize_travel_step(normalized_step)

        action = normalized_step.get("action")
        params = normalized_step.get("params") or {}
        if action in ("add_to_itinerary", "book_travel_item") and not params.get("item_type"):
            inferred_item_type = _infer_item_type_from_steps(normalized)
            if inferred_item_type:
                params.setdefault("item_type", inferred_item_type)
        if action in ("search_flights", "search_buses", "search_transfers"):
            if not params.get("origin") and origin_dest.get("origin"):
                params.setdefault("origin", origin_dest["origin"])
            if not params.get("destination") and origin_dest.get("destination"):
                params.setdefault("destination", origin_dest["destination"])
        if action == "search_flights":
            if params.get("departure_date"):
                normalized = _normalize_date_value(str(params.get("departure_date")))
                if normalized:
                    params["departure_date"] = normalized
            if params.get("return_date"):
                normalized = _normalize_date_value(str(params.get("return_date")))
                if normalized:
                    params["return_date"] = normalized
            if extracted_dates:
                params.setdefault("departure_date", extracted_dates[0])
            if len(extracted_dates) > 1 and ("return" in lowered or "back" in lowered):
                params.setdefault("return_date", extracted_dates[1])
            if passengers and not params.get("passengers"):
                params.setdefault("passengers", passengers)
        if action in ("search_flights", "search_buses", "search_transfers") and not params.get("departure_date") and not params.get("travel_date"):
            if extracted_dates:
                if action == "search_flights":
                    params.setdefault("departure_date", extracted_dates[0])
                else:
                    params.setdefault("travel_date", extracted_dates[0])
        if action in ("search_buses", "search_transfers") and passengers and not params.get("passengers"):
            params.setdefault("passengers", passengers)
        if action == "search_buses" and budget_ksh and not params.get("budget_ksh"):
            params.setdefault("budget_ksh", budget_ksh)
        if action == "search_transfers" and travel_time and not params.get("travel_time"):
            params.setdefault("travel_time", travel_time)
        if action == "search_hotels":
            if not params.get("location") and location:
                params.setdefault("location", location)
            if params.get("check_in_date"):
                normalized = _normalize_date_value(str(params.get("check_in_date")))
                if normalized:
                    params["check_in_date"] = normalized
            if params.get("check_out_date"):
                normalized = _normalize_date_value(str(params.get("check_out_date")))
                if normalized:
                    params["check_out_date"] = normalized
            if extracted_dates and not params.get("check_in_date"):
                params.setdefault("check_in_date", extracted_dates[0])
            if len(extracted_dates) > 1 and not params.get("check_out_date"):
                params.setdefault("check_out_date", extracted_dates[1])
            if nights and params.get("check_in_date") and not params.get("check_out_date"):
                try:
                    check_in = date.fromisoformat(params["check_in_date"])
                    params.setdefault("check_out_date", (check_in + timedelta(days=nights)).isoformat())
                except Exception:
                    pass
            if guests and not params.get("guests"):
                params.setdefault("guests", guests)
            if rooms and not params.get("rooms"):
                params.setdefault("rooms", rooms)
            if budget_ksh and not params.get("budget_ksh"):
                params.setdefault("budget_ksh", budget_ksh)
        if action in ("search_buses", "search_transfers") and params.get("travel_date"):
            normalized = _normalize_date_value(str(params.get("travel_date")))
            if normalized:
                params["travel_date"] = normalized
        if action == "search_events" and params.get("event_date"):
            normalized = _normalize_date_value(str(params.get("event_date")))
            if normalized:
                params["event_date"] = normalized
        if action == "create_itinerary":
            if params.get("start_date"):
                normalized = _normalize_date_value(str(params.get("start_date")))
                if normalized:
                    params["start_date"] = normalized
            if params.get("end_date"):
                normalized = _normalize_date_value(str(params.get("end_date")))
                if normalized:
                    params["end_date"] = normalized

        if action in ("book_travel_item", "add_to_itinerary") and not params.get("item_id") and item_id:
            params.setdefault("item_id", item_id)

        if action == "send_email":
            if "text" not in params and params.get("body"):
                params["text"] = params.get("body")
            if "text" not in params and params.get("message"):
                params["text"] = params.get("message")
            if "text" not in params and message_text:
                params.setdefault("text", message_text)
            if "subject" not in params and subject_text:
                params.setdefault("subject", subject_text)
            if "subject" not in params:
                default_subject = _default_subject_from_text(params.get("text"))
                if default_subject:
                    params.setdefault("subject", default_subject)
            if "to" not in params and email:
                params.setdefault("to", email)

        if action == "send_message":
            if "message" not in params and params.get("text"):
                params["message"] = params.get("text")
            if "message" not in params and message_text:
                params.setdefault("message", message_text)
            if "phone_number" not in params and phone:
                params.setdefault("phone_number", phone)

        if action == "convert_currency":
            if not params.get("amount") and currency_conversion.get("amount"):
                params.setdefault("amount", currency_conversion["amount"])
            if not params.get("from_currency") and currency_conversion.get("from_currency"):
                params.setdefault("from_currency", currency_conversion["from_currency"])
            if not params.get("to_currency") and currency_conversion.get("to_currency"):
                params.setdefault("to_currency", currency_conversion["to_currency"])

        if action in ("search_info", "search_gif", "find_jobs") and not params.get("query"):
            params.setdefault("query", message.strip())

        if action == "get_weather" and not params.get("city") and location:
            params.setdefault("city", location)

        if action in ("check_invoice_status", "check_status") and not params.get("invoice_id") and invoice_id:
            params.setdefault("invoice_id", invoice_id)

        if action == "create_payment_link":
            if not params.get("amount") and amount_currency.get("amount"):
                params.setdefault("amount", amount_currency["amount"])
            if not params.get("currency") and amount_currency.get("currency"):
                params.setdefault("currency", amount_currency["currency"])
            if not params.get("description"):
                params.setdefault("description", "Payment request")

        if action == "withdraw":
            if not params.get("amount") and amount_currency.get("amount"):
                params.setdefault("amount", amount_currency["amount"])
            if "phone_number" not in params and phone:
                params.setdefault("phone_number", phone)

        normalized_step["params"] = params
        normalized.append(normalized_step)
    return normalized


def _build_definition(steps: List[Dict[str, Any]], message: str) -> Dict[str, Any]:
    return {
        "workflow_name": "Ad hoc request",
        "workflow_description": message.strip()[:300],
        "triggers": [{"trigger_type": "manual"}],
        "steps": steps,
        "metadata": {"adhoc": True},
    }


async def _fallback_steps_from_intent(message: str, history_text: str) -> List[Dict[str, Any]]:
    parts = _split_step_phrases(message)
    if len(parts) < MIN_ADHOC_STEPS:
        return []
    try:
        from orchestration.intent_parser import parse_intent
    except Exception:
        return []

    steps: List[Dict[str, Any]] = []
    for idx, part in enumerate(parts):
        if not part:
            continue
        intent = await parse_intent(part, {"history": history_text} if history_text else None)
        if not intent:
            continue
        action = intent.get("action")
        if not action or action in ("general_chat", "create_workflow"):
            continue
        if intent.get("confidence") is not None:
            try:
                if float(intent.get("confidence")) < 0.35:
                    continue
            except (TypeError, ValueError):
                pass
        steps.append({
            "id": f"step_{len(steps) + 1}",
            "service": intent.get("service") or "",
            "action": action,
            "params": intent.get("parameters") or {},
        })

    return steps


async def plan_user_request(message: str, history_text: str = "") -> Dict[str, Any]:
    """
    Decide whether to run a multi-step ad-hoc workflow, ask for clarification,
    or fall back to single-action routing.
    """
    if _looks_like_automation(message):
        return {
            "mode": "automation_request",
            "assistant_message": "Got it. I can build a reusable workflow for that.",
            "workflow_definition": None,
        }

    llm = get_llm_client()
    capabilities_json = json.dumps(SYSTEM_CAPABILITIES, indent=2)

    system_prompt = "\n".join([
        "You are a planner that decides if a user request needs multiple ordered steps.",
        "If multiple steps are required now, return mode 'adhoc_workflow' and list steps.",
        "If it is a single action, return mode 'single'.",
        "If the user is asking to automate or create an ongoing workflow, return mode 'automation_request'.",
        "If required parameters are missing, return mode 'needs_clarification' with a helpful message.",
        "Use service='travel' for flights/hotels/buses/transfers/events and the specific search_* actions.",
        "Normalize dates to YYYY-MM-DD; accept DD/MM/YYYY and DD-MM-YYYY inputs from users.",
        "Map emails to service='mailgun' action='send_email', WhatsApp to service='whatsapp' action='send_message'.",
        "Only use the services/actions listed below.",
        "",
        "Available Integrations:",
        capabilities_json,
        "",
        "Return JSON only in this shape:",
        "{",
        '  "mode": "single|adhoc_workflow|automation_request|needs_clarification",',
        '  "assistant_message": "...",',
        '  "steps": [',
        '    {"id": "step_1", "service": "...", "action": "...", "params": {...}}',
        '  ] or null',
        "}",
    ])

    user_prompt = "\n".join([
        "Conversation context (most recent last):",
        history_text or "",
        "",
        f"User message: {message}",
        "",
        "Return JSON only.",
    ])

    try:
        response_text = await llm.generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.2,
            max_tokens=900,
            json_mode=True,
        )
    except Exception as exc:
        logger.error("Planner LLM failed: %s", exc)
        return {"mode": "single", "assistant_message": "", "workflow_definition": None}

    parsed = llm.extract_json(response_text) or {}
    mode = parsed.get("mode") or "single"
    assistant_message = parsed.get("assistant_message") or ""
    steps = parsed.get("steps")

    if mode == "adhoc_workflow":
        if not isinstance(steps, list):
            return {"mode": "single", "assistant_message": "", "workflow_definition": None}

        normalized_steps = _normalize_steps(steps, message)
        if len(normalized_steps) < MIN_ADHOC_STEPS:
            return {"mode": "single", "assistant_message": "", "workflow_definition": None}

        if len(normalized_steps) > MAX_ADHOC_STEPS:
            return {
                "mode": "needs_clarification",
                "assistant_message": (
                    f"That is a lot to do at once. Please break it into "
                    f"{MAX_ADHOC_STEPS} steps or fewer."
                ),
                "workflow_definition": None,
            }

        if _has_high_risk_step(normalized_steps) and not _looks_like_confirmation(message):
            return {
                "mode": "needs_clarification",
                "assistant_message": (
                    "This includes a sensitive action. Please confirm explicitly "
                    "if you want me to proceed."
                ),
                "workflow_definition": None,
            }

        definition = _build_definition(normalized_steps, message)
        valid, error = validate_workflow_definition(definition)
        if not valid:
            logger.warning("Invalid ad-hoc workflow definition: %s", error)
            fallback_steps = await _fallback_steps_from_intent(message, history_text)
            if fallback_steps:
                fallback_normalized = _normalize_steps(fallback_steps, message)
                if len(fallback_normalized) >= MIN_ADHOC_STEPS:
                    fallback_def = _build_definition(fallback_normalized, message)
                    fallback_valid, fallback_error = validate_workflow_definition(fallback_def)
                    if fallback_valid:
                        return {
                            "mode": "adhoc_workflow",
                            "assistant_message": assistant_message,
                            "workflow_definition": fallback_def,
                        }
                    error = fallback_error or error
            return {
                "mode": "needs_clarification",
                "assistant_message": _friendly_validation_error(error),
                "workflow_definition": None,
            }

        return {
            "mode": "adhoc_workflow",
            "assistant_message": assistant_message,
            "workflow_definition": definition,
        }

    if mode == "needs_clarification":
        fallback_steps = await _fallback_steps_from_intent(message, history_text)
        if fallback_steps:
            fallback_normalized = _normalize_steps(fallback_steps, message)
            if len(fallback_normalized) >= MIN_ADHOC_STEPS:
                fallback_def = _build_definition(fallback_normalized, message)
                fallback_valid, fallback_error = validate_workflow_definition(fallback_def)
                if fallback_valid:
                    return {
                        "mode": "adhoc_workflow",
                        "assistant_message": assistant_message,
                        "workflow_definition": fallback_def,
                    }
                if fallback_error:
                    assistant_message = _friendly_validation_error(fallback_error)
        return {
            "mode": "needs_clarification",
            "assistant_message": assistant_message or "I need a bit more detail to proceed.",
            "workflow_definition": None,
        }

    if mode == "automation_request":
        return {
            "mode": "automation_request",
            "assistant_message": assistant_message or "I can turn that into a workflow.",
            "workflow_definition": None,
        }

    return {"mode": "single", "assistant_message": "", "workflow_definition": None}


def _idempotency_key(user_id: int, definition: Dict[str, Any], trigger_data: Dict[str, Any]) -> str:
    payload = json.dumps(
        {"definition": definition, "trigger": trigger_data},
        sort_keys=True,
        default=str,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"{IDEMPOTENCY_CACHE_PREFIX}:{user_id}:{digest}"


async def _enqueue_deferred_execution(
    workflow_obj,
    user_id: int,
    room_id: Optional[int],
    trigger_data: Dict[str, Any],
) -> Optional[int]:
    try:
        from workflows.models import DeferredWorkflowExecution

        def _create():
            return DeferredWorkflowExecution.objects.create(
                workflow=workflow_obj,
                user_id=user_id,
                room_id=room_id,
                trigger_data=trigger_data,
                status='queued',
                attempts=0,
                next_attempt_at=timezone.now(),
            ).id

        return await sync_to_async(_create)()
    except Exception as exc:
        logger.error("Failed to enqueue deferred workflow: %s", exc)
        return None


async def _create_adhoc_workflow(user_id: int, room_id: Optional[int], definition: Dict[str, Any]):
    from workflows.models import UserWorkflow

    def _create():
        return UserWorkflow.objects.create(
            user_id=user_id,
            name=definition.get("workflow_name", "Ad hoc request"),
            description=definition.get("workflow_description", "Ad hoc execution"),
            definition=definition,
            status='active',
            created_from_room_id=room_id,
        )

    return await sync_to_async(_create)()


async def execute_adhoc_workflow(
    definition: Dict[str, Any],
    user_id: int,
    room_id: Optional[int],
    trigger_data: Optional[Dict[str, Any]] = None,
    wait_seconds: int = 12,
) -> Dict[str, Any]:
    """
    Execute an ad-hoc workflow via Temporal when possible, falling back to inline
    execution if Temporal is unavailable.
    """
    trigger_data = trigger_data or {}
    wait_seconds = min(max(wait_seconds, 1), MAX_WAIT_SECONDS)

    idempotency_key = _idempotency_key(user_id, definition, trigger_data)
    if not cache.add(idempotency_key, {"status": "running"}, IDEMPOTENCY_TTL_SECONDS):
        return {
            "status": "duplicate",
            "mode": "noop",
            "workflow": None,
            "execution": None,
            "result": {},
            "message": "I already started that request. Please wait a moment.",
        }

    workflow_obj = await _create_adhoc_workflow(user_id, room_id, definition)

    if settings.TEMPORAL_DISABLED:
        logger.info("Temporal disabled; using inline execution.")
        result = await _run_inline(definition, user_id, trigger_data)
        cache.set(idempotency_key, {"status": "completed"}, IDEMPOTENCY_TTL_SECONDS)
        return {
            "status": "completed",
            "mode": "inline",
            "workflow": workflow_obj,
            "execution": None,
            "result": result,
        }

    try:
        execution = await start_workflow_execution(workflow_obj, trigger_data, "manual")
    except Exception as exc:
        logger.error("Temporal start failed, falling back to inline execution: %s", exc)
        deferred_id = await _enqueue_deferred_execution(workflow_obj, user_id, room_id, trigger_data)
        if deferred_id:
            cache.set(idempotency_key, {"status": "queued"}, IDEMPOTENCY_TTL_SECONDS)
            return {
                "status": "queued",
                "mode": "deferred",
                "workflow": workflow_obj,
                "execution": None,
                "result": {},
                "message": "Temporal is unavailable. Your request is queued and will run when it is back up.",
            }
        result = await _run_inline(definition, user_id, trigger_data)
        cache.set(idempotency_key, {"status": "completed"}, IDEMPOTENCY_TTL_SECONDS)
        return {
            "status": "completed",
            "mode": "inline",
            "workflow": workflow_obj,
            "execution": None,
            "result": result,
        }

    completed = await _wait_for_execution(execution.id, wait_seconds)
    if completed and completed.status == "completed":
        cache.set(idempotency_key, {"status": "completed"}, IDEMPOTENCY_TTL_SECONDS)
        return {
            "status": "completed",
            "mode": "temporal",
            "workflow": workflow_obj,
            "execution": completed,
            "result": completed.result or {},
        }

    if completed and completed.status in ("failed", "cancelled"):
        cache.set(idempotency_key, {"status": completed.status}, IDEMPOTENCY_TTL_SECONDS)
        return {
            "status": completed.status,
            "mode": "temporal",
            "workflow": workflow_obj,
            "execution": completed,
            "result": completed.result or {},
            "error": completed.error_message,
        }

    cache.set(idempotency_key, {"status": "running"}, IDEMPOTENCY_TTL_SECONDS)
    return {
        "status": "running",
        "mode": "temporal",
        "workflow": workflow_obj,
        "execution": completed or execution,
        "result": completed.result if completed else {},
    }


async def _wait_for_execution(execution_id: int, wait_seconds: int):
    from workflows.models import WorkflowExecution

    deadline = time.monotonic() + max(wait_seconds, 1)
    while time.monotonic() < deadline:
        def _fetch():
            return WorkflowExecution.objects.filter(id=execution_id).first()
        execution = await sync_to_async(_fetch)()
        if execution and execution.status in ("completed", "failed", "cancelled"):
            return execution
        await asyncio.sleep(0.5)
    return await sync_to_async(lambda: WorkflowExecution.objects.filter(id=execution_id).first())()


async def _run_inline(definition: Dict[str, Any], user_id: int, trigger_data: Dict[str, Any]) -> Dict[str, Any]:
    from workflows.activity_executors import execute_workflow_step
    from workflows.utils import safe_eval_condition, compact_context

    context: Dict[str, Any] = {
        "trigger": trigger_data,
        "workflow": {"id": 0, "policy": definition.get("policy") or {}},
        "user_id": user_id,
    }

    for step in definition.get("steps", []):
        condition = step.get("condition")
        if condition and not safe_eval_condition(condition, context):
            continue
        step_id = step.get("id") or step.get("action") or f"step_{len(context)}"
        on_error = str(step.get("on_error") or "stop").lower()
        try:
            result = await execute_workflow_step(step, context)
            context[step_id] = result
        except Exception as exc:
            context[step_id] = {"status": "error", "error": str(exc)}
            if on_error == "continue":
                continue
            raise

    return compact_context(context)


async def synthesize_workflow_response_stream(
    user_message: str,
    workflow_definition: Dict[str, Any],
    execution_result: Dict[str, Any],
    status: str,
    error: Optional[str] = None,
):
    """
    Stream a natural language response summarizing a workflow run.
    """
    if status == "duplicate":
        yield "I already started that request. Please wait a moment."
        return

    if status == "queued":
        yield "Temporal is unavailable. I queued your request and will run it when it is back up."
        return

    if status in ("failed", "cancelled"):
        suffix = f" Error: {error}" if error else ""
        yield f"The workflow did not complete successfully.{suffix}"
        return

    if status != "completed":
        yield (
            "I started the workflow but it hasn't finished yet. "
            "I'll share the results once it completes."
        )
        return

    llm = get_llm_client()
    system_prompt = (
        "You are Mathia, a helpful assistant. Summarize the results of a multi-step "
        "workflow execution. Be concise and list key outputs per step. "
        "Do not invent details not present in the data."
    )
    user_prompt = json.dumps({
        "user_message": user_message,
        "workflow": workflow_definition,
        "result": execution_result,
    })

    try:
        async for chunk in llm.stream_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.4,
            max_tokens=600,
        ):
            if chunk:
                yield chunk
    except Exception as exc:
        logger.error("Workflow response synthesis failed: %s", exc)
        yield "I ran the workflow, but had trouble summarizing the results."
