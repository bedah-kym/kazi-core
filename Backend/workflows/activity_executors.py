"""Activity executors for workflow steps."""
import json
from decimal import Decimal
from typing import Dict, Any, Optional
from django.conf import settings

from orchestration.llm_client import get_llm_client
from orchestration.connectors.gmail_connector import GmailConnector
from orchestration.connectors.whatsapp_connector import WhatsAppConnector
from orchestration.connectors.payment_connector import ReadOnlyPaymentConnector
from orchestration.connectors.intersend_connector import IntersendPayConnector
from orchestration.connectors.invoice_connector import InvoiceConnector
from orchestration.connectors.quota_connector import QuotaConnector
from orchestration.connectors.itinerary_connector import ItineraryConnector
from orchestration.connectors.travel_buses_connector import TravelBusesConnector
from orchestration.connectors.travel_hotels_connector import TravelHotelsConnector
from orchestration.connectors.travel_flights_connector import TravelFlightsConnector
from orchestration.connectors.travel_transfers_connector import TravelTransfersConnector
from orchestration.connectors.travel_events_connector import TravelEventsConnector
from orchestration.mcp_router import SearchConnector, WeatherConnector, GiphyConnector, CurrencyConnector, ReminderConnector, CalendarConnector
from orchestration.action_receipts import record_action_receipt, should_record_receipt
from orchestration.action_catalog import (
    get_action_definition,
    get_capability_gate,
    get_supported_actions,
    resolve_action_alias,
)
from orchestration.security_policy import should_block_action, sanitize_parameters

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

_EXECUTOR_BASE_ACTIONS = {
    "send_email",
    "send_message",
    "create_invoice",
}


def validate_executor_action_mappings() -> None:
    mapped = set(_EXECUTOR_BASE_ACTIONS)
    mapped.update(_READ_ONLY_PAYMENT_ACTIONS)
    mapped.update(_PAYMENT_ACTIONS)
    mapped.update(_TRAVEL_ACTIONS.keys())
    mapped.update(_MISC_ACTIONS.keys())
    required = set(get_supported_actions(include_aliases=False))
    missing = sorted(required - mapped)
    if missing:
        raise RuntimeError(
            "Workflow executor is missing action mappings for: " + ", ".join(missing)
        )


validate_executor_action_mappings()


def _normalize_depends_on(value: Any) -> list:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return []


def _dependency_ready(dep_id: str, context: Dict[str, Any]) -> bool:
    if dep_id not in context:
        return False
    result = context.get(dep_id)
    if isinstance(result, dict) and result.get("status") == "error":
        return False
    return True


def _has_prior_results(context: Dict[str, Any], allowed_steps: Optional[list] = None) -> bool:
    for key, value in context.items():
        if key in ("trigger", "workflow", "user_id"):
            continue
        if allowed_steps and key not in allowed_steps:
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
    action = resolve_action_alias(step.get('action'))
    params = sanitize_parameters(resolve_parameters(step.get('params', {}), context))

    async def _effective_preferences() -> Dict[str, Any]:
        prefs = context.get("preferences")
        if isinstance(prefs, dict):
            return prefs
        user_id = context.get("user_id")
        if not user_id:
            context["preferences"] = {}
            return {}
        try:
            from orchestration.user_preferences import get_user_preferences
            from asgiref.sync import sync_to_async

            prefs = await sync_to_async(get_user_preferences)(user_id)
        except Exception:
            prefs = {}
        if not isinstance(prefs, dict):
            prefs = {}
        context["preferences"] = prefs
        return prefs

    action_def = get_action_definition(action)
    if not action_def:
        return {"status": "error", "error": f"Unsupported workflow action: {action}"}

    # Guard against mismatched service/action pairs in user-provided workflow JSON.
    canonical_service = str(action_def.get("service") or "").lower()
    service_aliases = {"mailgun": "gmail"}
    normalized_service = service_aliases.get(service, service)
    if normalized_service and canonical_service and normalized_service != canonical_service:
        return {
            "status": "error",
            "error": f"Action '{action}' is not valid for service '{service}'.",
        }

    preferences = await _effective_preferences()
    capability_gate = get_capability_gate(action)
    if capability_gate and not preferences.get(capability_gate, True):
        return {
            "status": "error",
            "error": (
                f"This workflow step is blocked because '{capability_gate}' is disabled "
                "in your settings."
            ),
        }

    if should_block_action(params, action):
        return {
            "status": "error",
            "error": "This workflow step was blocked by the safety policy.",
        }

    depends_on = _normalize_depends_on(step.get("depends_on"))
    if depends_on:
        missing = [dep for dep in depends_on if not _dependency_ready(dep, context)]
        if missing:
            joined = ", ".join(missing[:3])
            return {
                "status": "error",
                "error": f"I need results from {joined} before I can continue.",
            }

    if _needs_option_context(params) and not _has_prior_results(context, allowed_steps=depends_on or None):
        return {
            "status": "error",
            "error": (
                "I need a recent list of options before I can pick an option number. "
                "Please run a search first."
            ),
        }

    async def _record_and_return(result: Dict[str, Any]) -> Dict[str, Any]:
        if should_record_receipt(action):
            status = "success"
            if isinstance(result, dict):
                if result.get("status") in ("error", "failed"):
                    status = "error"
                elif result.get("error"):
                    status = "error"
            try:
                await record_action_receipt(
                    user_id=context.get("user_id"),
                    room_id=context.get("room_id"),
                    action=action or "",
                    service=service or "",
                    params=params,
                    result=result if isinstance(result, dict) else {"result": result},
                    status=status,
                    reason=result.get("error") if isinstance(result, dict) else "",
                )
            except Exception:
                pass
        return result

    if service == 'payments' and action == 'withdraw':
        error = _enforce_withdraw_policy(params, context)
        if error:
            return await _record_and_return({"status": "error", "error": error})

    if service in ('gmail', 'mailgun'):
        if params.get('text') == _AUTO_EMAIL_SUMMARY_TOKEN:
            params['text'] = await _build_email_summary(context)
        connector = GmailConnector()
        params.setdefault('action', 'send_email')
        return await _record_and_return(await connector.execute(params, context))

    if service == 'whatsapp':
        connector = WhatsAppConnector()
        params.setdefault('action', 'send_message')
        return await _record_and_return(await connector.execute(params, context))

    if service == 'payments':
        if action in _READ_ONLY_PAYMENT_ACTIONS:
            connector = ReadOnlyPaymentConnector()
            params.setdefault('action', action)
            return await _record_and_return(await connector.execute(params, context))
        if action == 'create_invoice':
            connector = InvoiceConnector()
            params.setdefault('action', action)
            return await _record_and_return(await connector.execute(params, context))
        if action in _PAYMENT_ACTIONS:
            connector = IntersendPayConnector()
            params.setdefault('action', action)
            return await _record_and_return(await connector.execute(params, context))
        return await _record_and_return({"status": "error", "error": f"Unsupported payment action: {action}"})

    if service == 'travel' and action in _TRAVEL_ACTIONS:
        connector = _TRAVEL_ACTIONS[action]
        params.setdefault('action', action)
        return await _record_and_return(await connector.execute(params, context))

    if action in _TRAVEL_ACTIONS:
        connector = _TRAVEL_ACTIONS[action]
        params.setdefault('action', action)
        return await _record_and_return(await connector.execute(params, context))

    if action in _MISC_ACTIONS:
        connector = _MISC_ACTIONS[action]
        params.setdefault('action', action)
        return await _record_and_return(await connector.execute(params, context))

    return await _record_and_return({"status": "error", "error": f"Unsupported workflow step: {service}.{action}"})
