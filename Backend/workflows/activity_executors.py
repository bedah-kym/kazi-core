"""Activity executors for workflow steps."""
from decimal import Decimal
from typing import Dict, Any
from django.conf import settings

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

    if service == 'payments' and action == 'withdraw':
        error = _enforce_withdraw_policy(params, context)
        if error:
            return {"status": "error", "error": error}

    if service in ('gmail', 'mailgun'):
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
