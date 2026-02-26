"""Action receipt helpers for audit, confirmation, and undo."""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, Iterable, List, Optional
import re

from asgiref.sync import sync_to_async
from django.utils import timezone

from orchestration.models import ActionReceipt
from orchestration.telemetry import record_event


_ACTION_ALIASES = {
    "send_whatsapp": "send_message",
}

_ACTION_POLICIES: Dict[str, Dict[str, Any]] = {
    "send_email": {
        "requires_confirmation": True,
        "include_in_response": True,
    },
    "send_message": {
        "requires_confirmation": True,
        "include_in_response": True,
    },
    "create_payment_link": {
        "requires_confirmation": True,
        "include_in_response": True,
    },
    "withdraw": {
        "requires_confirmation": True,
        "include_in_response": True,
    },
    "book_travel_item": {
        "requires_confirmation": True,
        "include_in_response": True,
    },
    "set_reminder": {
        "requires_confirmation": False,
        "include_in_response": True,
        "reversible": True,
        "undo_action": "cancel_reminder",
    },
}

_AUDITED_ACTIONS = {
    "send_email",
    "send_message",
    "send_whatsapp",
    "create_payment_link",
    "withdraw",
    "book_travel_item",
    "set_reminder",
    "add_to_itinerary",
    "create_itinerary",
    "schedule_meeting",
}

_UNDO_RE = re.compile(r"\b(undo|revert|roll back|rollback|take that back)\b", re.IGNORECASE)
_RECEIPT_RE = re.compile(
    r"\b(receipt|action log|audit trail|what did you do|show (me )?(actions|activity))\b",
    re.IGNORECASE,
)


def normalize_action(action: Optional[str]) -> str:
    if not action:
        return ""
    action = str(action).strip().lower()
    return _ACTION_ALIASES.get(action, action)


def get_action_policy(action: Optional[str]) -> Dict[str, Any]:
    return _ACTION_POLICIES.get(normalize_action(action), {})


def requires_confirmation(action: Optional[str]) -> bool:
    return bool(get_action_policy(action).get("requires_confirmation"))


def should_record_receipt(action: Optional[str]) -> bool:
    return normalize_action(action) in _AUDITED_ACTIONS


def should_include_receipt(action: Optional[str]) -> bool:
    return bool(get_action_policy(action).get("include_in_response"))


def is_undo_request(message: str) -> bool:
    if not message:
        return False
    return bool(_UNDO_RE.search(message))


def is_receipt_request(message: str) -> bool:
    if not message:
        return False
    return bool(_RECEIPT_RE.search(message))


def _truncate(value: Any, limit: int = 160) -> Any:
    if not isinstance(value, str):
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return _truncate(str(value), limit=limit)
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "..."


def _sanitize_payload(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    sanitized: Dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, dict):
            sanitized[key] = _sanitize_payload(value)
        elif isinstance(value, list):
            sanitized[key] = [_truncate(item) for item in value[:10]]
        else:
            sanitized[key] = _truncate(value)
    return sanitized


def summarize_action(action: str, params: Dict[str, Any]) -> str:
    action = normalize_action(action)
    params = params or {}
    if action == "send_email":
        to_addr = params.get("to") or "recipient"
        subject = params.get("subject") or "(no subject)"
        return f"Email to {to_addr} (subject: {subject})"
    if action == "send_message":
        phone = params.get("phone_number") or "recipient"
        message = _truncate(params.get("message") or "", 120)
        if message:
            return f"WhatsApp to {phone}: {message}"
        return f"WhatsApp to {phone}"
    if action == "set_reminder":
        content = _truncate(params.get("content") or "reminder", 120)
        time_text = params.get("time") or params.get("scheduled_time") or "scheduled time"
        return f"Reminder set: {content} at {time_text}"
    if action == "create_payment_link":
        amount = params.get("amount") or "amount"
        currency = params.get("currency") or ""
        description = _truncate(params.get("description") or "", 120)
        label = f"Payment link for {amount} {currency}".strip()
        if description:
            return f"{label} ({description})"
        return label
    if action == "withdraw":
        amount = params.get("amount") or "amount"
        phone = params.get("phone_number") or "recipient"
        return f"Withdrawal {amount} to {phone}"
    if action == "book_travel_item":
        item_id = params.get("item_id") or "item"
        item_type = params.get("item_type") or "travel item"
        return f"Book {item_type} {item_id}"
    if action == "add_to_itinerary":
        item_type = params.get("item_type") or "item"
        return f"Added {item_type} to itinerary"
    if action == "create_itinerary":
        destination = params.get("destination") or params.get("location") or "trip"
        return f"Created itinerary for {destination}"
    if action == "schedule_meeting":
        target = params.get("target_user") or "meeting"
        return f"Scheduled meeting ({target})"
    return action.replace("_", " ")


def format_receipt_summary(receipt: ActionReceipt) -> str:
    summary = summarize_action(receipt.action, receipt.params or {})
    status = receipt.status or "success"
    if status == "error":
        return f"Failed: {summary}"
    if status == "cancelled":
        return f"Cancelled: {summary}"
    return summary


def format_receipt_list(receipts: Iterable[ActionReceipt]) -> str:
    receipt_list = list(receipts)
    if not receipt_list:
        return "No recent actions recorded yet."
    lines = ["Recent actions:"]
    for idx, receipt in enumerate(receipt_list, start=1):
        timestamp = timezone.localtime(receipt.created_at).strftime("%b %d %H:%M")
        summary = format_receipt_summary(receipt)
        lines.append(f"{idx}. {timestamp} - {summary}")
    return "\n".join(lines)


def build_confirmation_prompt(action: str, params: Dict[str, Any]) -> str:
    summary = summarize_action(action, params or {})
    return (
        f"Please confirm I should proceed: {summary}. "
        "Reply 'yes' to continue or 'cancel' to stop."
    )


async def record_action_receipt(
    *,
    user_id: int,
    room_id: Optional[int],
    action: str,
    service: str,
    params: Optional[Dict[str, Any]],
    result: Optional[Dict[str, Any]],
    status: str,
    reason: str = "",
) -> Optional[ActionReceipt]:
    if not user_id or not action:
        return None

    policy = get_action_policy(action)
    reversible = bool(policy.get("reversible"))
    undo_action = policy.get("undo_action") if reversible else ""
    undo_params: Dict[str, Any] = {}
    result = result or {}

    if reversible and action == "set_reminder":
        reminder_id = result.get("reminder_id") or (params or {}).get("reminder_id")
        if reminder_id:
            undo_params["reminder_id"] = reminder_id

    sanitized_params = _sanitize_payload(params or {})
    sanitized_result = _sanitize_payload(result or {})

    def _create():
        return ActionReceipt.objects.create(
            user_id=user_id,
            room_id=room_id,
            action=normalize_action(action),
            service=service or "",
            params=sanitized_params,
            result=sanitized_result,
            status=status,
            reversible=reversible,
            undo_action=undo_action or "",
            undo_params=undo_params,
            reason=reason or "",
        )

    receipt = await sync_to_async(_create)()
    try:
        record_event("action_receipt", {
            "user_id": user_id,
            "room_id": room_id,
            "action": action,
            "status": status,
        })
    except Exception:
        pass
    return receipt


def attach_receipt_to_result(result: Dict[str, Any], receipt: Optional[ActionReceipt]) -> Dict[str, Any]:
    if not isinstance(result, dict) or not receipt:
        return result
    if not should_include_receipt(receipt.action):
        return result
    undo_hint = "Say 'undo' to cancel it." if receipt.reversible else ""
    result["receipt"] = {
        "id": receipt.id,
        "summary": format_receipt_summary(receipt),
        "reversible": receipt.reversible,
        "undo_hint": undo_hint,
    }
    return result


async def fetch_recent_receipts(
    *,
    user_id: int,
    room_id: Optional[int] = None,
    limit: int = 3,
    reversible_only: bool = False,
    since_minutes: int = 1440,
) -> List[ActionReceipt]:
    if not user_id:
        return []
    since = timezone.now() - timedelta(minutes=since_minutes)

    def _query():
        qs = ActionReceipt.objects.filter(user_id=user_id, created_at__gte=since)
        if room_id:
            qs = qs.filter(room_id=room_id)
        if reversible_only:
            qs = qs.filter(reversible=True, status="success")
        return list(qs.order_by("-created_at")[:limit])

    return await sync_to_async(_query)()


async def undo_last_action(
    *,
    user_id: int,
    room_id: Optional[int] = None,
) -> Dict[str, Any]:
    receipts = await fetch_recent_receipts(
        user_id=user_id,
        room_id=room_id,
        limit=1,
        reversible_only=True,
        since_minutes=1440,
    )
    if not receipts:
        return {"status": "noop", "message": "I could not find a reversible action to undo."}

    receipt = receipts[0]
    if receipt.undo_action == "cancel_reminder":
        return await _undo_reminder(receipt)

    return {"status": "noop", "message": "I cannot undo that action, but I can help adjust it."}


async def _undo_reminder(receipt: ActionReceipt) -> Dict[str, Any]:
    from chatbot.models import Reminder

    reminder_id = (receipt.undo_params or {}).get("reminder_id")
    if not reminder_id:
        return {"status": "error", "message": "I could not find the reminder to cancel."}

    def _cancel():
        reminder = Reminder.objects.filter(id=reminder_id, user_id=receipt.user_id).first()
        if not reminder:
            return "not_found", None
        if reminder.status != "pending":
            return reminder.status, reminder
        reminder.status = "cancelled"
        reminder.save(update_fields=["status"])
        return "cancelled", reminder

    status, reminder = await sync_to_async(_cancel)()
    if status == "not_found":
        return {"status": "error", "message": "I could not find that reminder."}
    if status != "cancelled":
        return {"status": "noop", "message": f"That reminder is already {status}."}

    def _update_receipt():
        receipt.status = "cancelled"
        receipt.save(update_fields=["status"])

    await sync_to_async(_update_receipt)()

    await record_action_receipt(
        user_id=receipt.user_id,
        room_id=receipt.room_id,
        action="cancel_reminder",
        service="reminder",
        params={"reminder_id": reminder_id},
        result={"status": "cancelled"},
        status="success",
    )

    when_text = ""
    if reminder:
        when_text = timezone.localtime(reminder.scheduled_time).strftime("%b %d %H:%M")
    suffix = f" (was scheduled for {when_text})" if when_text else ""
    return {"status": "success", "message": f"Cancelled the reminder{suffix}."}
