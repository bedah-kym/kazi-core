"""Activity executors for workflow steps."""
import json
from decimal import Decimal
from typing import Dict, Any
from django.conf import settings

from orchestration.llm_client import get_llm_client
from orchestration.connectors.gmail_connector import GmailConnector
from orchestration.connectors.whatsapp_connector import WhatsAppConnector
from orchestration.connectors.payment_connector import ReadOnlyPaymentConnector
from orchestration.connectors.intersend_connector import IntersendPayConnector
from orchestration.connectors.quota_connector import QuotaConnector
from orchestration.connectors.itinerary_connector import ItineraryConnector
from orchestration.connectors.travel_buses_connector import TravelBusesConnector
from orchestration.connectors.travel_hotels_connector import TravelHotelsConnector
from orchestration.connectors.travel_flights_connector import TravelFlightsConnector
from orchestration.connectors.travel_transfers_connector import TravelTransfersConnector
from orchestration.connectors.travel_events_connector import TravelEventsConnector
from orchestration.mcp_router import SearchConnector, WeatherConnector, GiphyConnector, CurrencyConnector, ReminderConnector, UpworkConnector, CalendarConnector

from .utils import resolve_parameters

_READ_ONLY_PAYMENT_ACTIONS = {
    'check_balance',
    'list_transactions',
    'check_invoice_status',
    'check_payments'
}

_PAYMENT_ACTIONS = {
    'create_payment_link',
    'withdraw',
    'check_status'
}

_TRAVEL_ACTIONS = {
    'search_buses': TravelBusesConnector(),
    'search_hotels': TravelHotelsConnector(),
    'search_flights': TravelFlightsConnector(),
    'search_transfers': TravelTransfersConnector(),
    'search_events': TravelEventsConnector(),
    'create_itinerary': ItineraryConnector(),
    'view_itinerary': ItineraryConnector(),
    'add_to_itinerary': ItineraryConnector(),
    'book_travel_item': ItineraryConnector(),
}

_MISC_ACTIONS = {
    'find_jobs': UpworkConnector(),
    'search_info': SearchConnector(),
    'get_weather': WeatherConnector(),
    'search_gif': GiphyConnector(),
    'convert_currency': CurrencyConnector(),
    'set_reminder': ReminderConnector(),
    'check_quotas': QuotaConnector(),
    'schedule_meeting': CalendarConnector(),
    'check_availability': CalendarConnector(),
}

_AUTO_EMAIL_SUMMARY_TOKEN = "__AUTO_SUMMARY__"
_OPTION_PARAM_HINTS = ("item_id", "option", "selection")


def _has_prior_results(context: Dict[str, Any]) -> bool:
    for key, value in context.items():
        if key in ("trigger", "workflow", "user_id"):
            continue
        if not isinstance(value, dict):
            continue
        results = value.get("results")
        if isinstance(results, list) and results:
            return True
    return False


def _needs_option_context(params: Dict[str, Any]) -> bool:
    for key, value in (params or {}).items():
        if key in _OPTION_PARAM_HINTS or key.endswith("_id"):
            if isinstance(value, int):
                return True
            if isinstance(value, str) and value.strip().isdigit():
                return True
    return False


def _format_result_item(item: Dict[str, Any]) -> str:
    if "flight_number" in item:
        return (
            f"{item.get('flight_number')} {item.get('departure_time')}→{item.get('arrival_time')} "
            f"KES {item.get('price_ksh')} ({item.get('stops', 0)} stops)"
        )
    if "name" in item and "price_ksh" in item:
        return f"{item.get('name')} KES {item.get('price_ksh')} rating {item.get('rating', 'N/A')}"
    if "company" in item and "departure_time" in item:
        return (
            f"{item.get('company')} {item.get('departure_time')}→{item.get('arrival_time')} "
            f"KES {item.get('price_ksh')}"
        )
    if "vehicle_type" in item:
        return (
            f"{item.get('vehicle_type')} {item.get('capacity', '')} seats "
            f"KES {item.get('price_ksh')}"
        )
    if "title" in item:
        return str(item.get("title"))
    return str(item)


def _collect_summary_payload(context: Dict[str, Any]) -> Dict[str, Any]:
    payload = {"steps": []}
    for key, value in context.items():
        if key in ("trigger", "workflow", "user_id"):
            continue
        if not isinstance(value, dict):
            continue
        results = value.get("results")
        metadata = value.get("metadata") or {}
        if isinstance(results, list):
            compact_results = results[:5]
            payload["steps"].append({
                "step": key,
                "metadata": metadata,
                "results": compact_results,
            })
        elif value.get("status") or value.get("message"):
            payload["steps"].append({
                "step": key,
                "status": value.get("status"),
                "message": value.get("message"),
            })
    return payload


def _fallback_summary_text(payload: Dict[str, Any]) -> str:
    lines = []
    for step in payload.get("steps", []):
        step_name = step.get("step", "step")
        lines.append(f"{step_name}:")
        results = step.get("results") or []
        if results:
            for item in results[:5]:
                if isinstance(item, dict):
                    lines.append(f"- {_format_result_item(item)}")
                else:
                    lines.append(f"- {item}")
        else:
            status = step.get("status") or "completed"
            message = step.get("message") or ""
            lines.append(f"- {status} {message}".strip())
    if not lines:
        return "Here are the results you requested. (No structured results were returned.)"
    return "\n".join(lines)


async def _build_email_summary(context: Dict[str, Any]) -> str:
    payload = _collect_summary_payload(context)
    if not payload.get("steps"):
        return "Here are the results you requested. (No structured results were returned.)"

    llm = get_llm_client()
    system_prompt = (
        "You are writing a short plain-text email to a user summarizing workflow results. "
        "Be concise, friendly, and keep it under 200 words. Use bullet points for results."
    )
    user_prompt = json.dumps(payload)
    try:
        summary = await llm.generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.3,
            max_tokens=300,
        )
        summary = (summary or "").strip()
        return summary or _fallback_summary_text(payload)
    except Exception:
        return _fallback_summary_text(payload)


def _enforce_withdraw_policy(params: Dict[str, Any], context: Dict[str, Any]) -> str:
    policy = (context.get('workflow') or {}).get('policy') or {}
    allowed_numbers = policy.get('allowed_phone_numbers') or []
    max_amount = policy.get('max_withdraw_amount')

    if not allowed_numbers or max_amount is None:
        return "Withdrawals require workflow policy with allowed_phone_numbers and max_withdraw_amount"

    try:
        amount = Decimal(str(params.get('amount')))
    except Exception:
        return "Invalid withdrawal amount"

    try:
        max_amount = Decimal(str(max_amount))
    except Exception:
        return "Invalid policy max_withdraw_amount"

    if amount > max_amount:
        return f"Withdrawal amount exceeds policy max ({max_amount})"

    if amount > settings.WORKFLOW_WITHDRAW_MAX:
        return f"Withdrawal amount exceeds system max ({settings.WORKFLOW_WITHDRAW_MAX})"

    phone_number = str(params.get('phone_number') or '')
    if phone_number not in allowed_numbers:
        return "Withdrawal phone number not in allowlist"

    return ""


async def execute_workflow_step(step: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    service = (step.get('service') or '').lower()
    action = step.get('action')
    params = resolve_parameters(step.get('params', {}), context)

    if _needs_option_context(params) and not _has_prior_results(context):
        return {
            "status": "error",
            "error": (
                "I need a recent list of options before I can pick an option number. "
                "Please run a search first."
            ),
        }

    if service == 'payments' and action == 'withdraw':
        error = _enforce_withdraw_policy(params, context)
        if error:
            return {"status": "error", "error": error}

    if service in ('gmail', 'mailgun'):
        if params.get('text') == _AUTO_EMAIL_SUMMARY_TOKEN:
            params['text'] = await _build_email_summary(context)
        connector = GmailConnector()
        params.setdefault('action', 'send_email')
        return await connector.execute(params, context)

    if service == 'whatsapp':
        connector = WhatsAppConnector()
        params.setdefault('action', 'send_message')
        return await connector.execute(params, context)

    if service == 'payments':
        if action in _READ_ONLY_PAYMENT_ACTIONS:
            connector = ReadOnlyPaymentConnector()
            params.setdefault('action', action)
            return await connector.execute(params, context)
        if action in _PAYMENT_ACTIONS:
            connector = IntersendPayConnector()
            params.setdefault('action', action)
            return await connector.execute(params, context)
        return {"status": "error", "error": f"Unsupported payment action: {action}"}

    if service == 'travel' and action in _TRAVEL_ACTIONS:
        connector = _TRAVEL_ACTIONS[action]
        params.setdefault('action', action)
        return await connector.execute(params, context)

    if action in _TRAVEL_ACTIONS:
        connector = _TRAVEL_ACTIONS[action]
        params.setdefault('action', action)
        return await connector.execute(params, context)

    if action in _MISC_ACTIONS:
        connector = _MISC_ACTIONS[action]
        params.setdefault('action', action)
        return await connector.execute(params, context)

    return {"status": "error", "error": f"Unsupported workflow step: {service}.{action}"}
