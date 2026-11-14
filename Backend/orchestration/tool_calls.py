"""The Turn's typed tool-call record (P3-5, T-D3 final).

The coordinator writes one ``TurnToolCall`` row per call once its outcome is
known. The history builder reads them for messages linked to a Turn and falls
back to the legacy reply payload for older messages. Bodies are encrypted with
the room key, and a shortened body keeps its start and its end with a marker
that says how much was cut (A15): a cut-off middle hides the one error line,
and a cut-off end hides how a command finished.
"""
from __future__ import annotations

import base64
import json
import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.db import transaction
from django.db.models import Max

from users.encryption import TokenEncryption

from .agent_loop import _untrusted_source
from .models import TurnToolCall
from .tool_record import tool_record_input

SHOWN_LIMIT = 2000
RETRIEVAL_TOOL_NAMES = frozenset({"web_search", "search_info"})
_RAN_STATUSES = frozenset({"ok", "error", "outcome_unknown"})
_STATUS_ALIASES = {
    "success": "ok",
    "completed": "ok",
    "ok": "ok",
    "error": "error",
    "denied": "denied",
    "skipped": "skipped",
    "declined": "declined",
    "expired": "expired",
    "superseded": "superseded",
    "budget_skipped": "budget_skipped",
    "policy_stopped": "policy_stopped",
    "outcome_unknown": "outcome_unknown",
}
_DID_NOT_RUN_REASON = {
    "denied": "not executed: denied",
    "skipped": "not executed: skipped",
    "declined": "not executed: you declined",
    "expired": "not executed: the approval expired",
    "superseded": "not executed: superseded by a newer request",
    "budget_skipped": "not executed: the model asked for more budget first",
    "policy_stopped": "not executed: stopped by the safety policy",
}


def shorten_for_record(text: str, limit: int = SHOWN_LIMIT) -> Tuple[str, bool]:
    """``text`` cut to at most ``limit`` characters, keeping its start and end.

    The marker names how many characters were removed and how long the original
    was, so it can never be mistaken for the call's real content (A15).
    """
    text = str(text or "")
    if len(text) <= limit:
        return text, False
    if limit <= 0:
        return "", True
    marker_room = max(limit - 24, 1)
    head = marker_room // 2
    tail = marker_room - head
    omitted = max(0, len(text) - head - tail)
    marker = f"\n…[record shortened: {omitted:,} characters omitted of {len(text):,}]…\n"
    while head + len(marker) + tail > limit and head + tail > 2:
        if head >= tail:
            head -= 1
        else:
            tail -= 1
        omitted = max(0, len(text) - head - tail)
        marker = f"\n…[record shortened: {omitted:,} characters omitted of {len(text):,}]…\n"
    return text[:head] + marker + text[-tail:], True


def status_for_result(result: Any) -> str:
    """Map a tool result's status to one of the row's status values."""
    if not isinstance(result, dict):
        return "error"
    if result.get("outcome_unknown"):
        return "outcome_unknown"
    status = str(result.get("status") or "").strip().lower()
    if status in _STATUS_ALIASES:
        mapped = _STATUS_ALIASES[status]
        if mapped == "error" and "safety policy" in str(result.get("message") or ""):
            return "policy_stopped"
        return mapped
    return "error" if result else "ok"


def did_not_run_reason(status: str) -> str:
    return _DID_NOT_RUN_REASON.get(status, f"not executed: {status}")


def _room_cipher(room: Any) -> Optional[AESGCM]:
    key = getattr(room, "encryption_key", "") or ""
    if key.startswith("enc:"):
        key = TokenEncryption.safe_decrypt(key[4:], default=None)
    if not key:
        return None
    try:
        return AESGCM(base64.b64decode(key.encode("utf-8")))
    except Exception:
        return None


def _aad(turn_id: Any, seq: int) -> bytes:
    return f"{turn_id}:{seq}".encode("utf-8")


def encrypt_body(room: Any, value: Any, *, turn_id: Any, seq: int) -> Optional[str]:
    """Encrypt one body with the room key; None means it must not be stored."""
    if value is None:
        return None
    cipher = _room_cipher(room)
    if cipher is None:
        return None
    try:
        nonce = os.urandom(12)
        body = json.dumps(value, default=str).encode("utf-8")
        return json.dumps({
            "v": 1,
            "nonce": base64.b64encode(nonce).decode("utf-8"),
            "data": base64.b64encode(cipher.encrypt(nonce, body, _aad(turn_id, seq))).decode("utf-8"),
        })
    except Exception:
        return None


def decrypt_body(room: Any, blob: Optional[str], *, turn_id: Any, seq: int) -> Any:
    """Decrypt one body; unreadable means the caller shows nothing for it."""
    if not blob:
        return None
    cipher = _room_cipher(room)
    if cipher is None:
        return None
    try:
        envelope = json.loads(blob)
        nonce = base64.b64decode(envelope["nonce"].encode("utf-8"))
        data = base64.b64decode(envelope["data"].encode("utf-8"))
        return json.loads(cipher.decrypt(nonce, data, _aad(turn_id, seq)).decode("utf-8"))
    except Exception:
        return None


def _row_body(record: Dict[str, Any]) -> Tuple[str, bool]:
    shown = record.get("shown")
    if shown is None:
        shown = did_not_run_reason(record["status"]) if record["status"] in _DID_NOT_RUN_REASON else ""
    return shorten_for_record(str(shown))


def record_tool_calls(
    turn_id: Any,
    room: Any,
    records: Iterable[Dict[str, Any]],
    *,
    receipt_id: Optional[int] = None,
) -> List[TurnToolCall]:
    """Append the given calls to ``turn_id``, assigning ``seq`` in this order.

    Encryption happens before the insert; when the room key is unusable the body
    stays NULL rather than being stored in clear (fail closed).
    """
    records = list(records or [])
    if not records:
        return []
    created: List[TurnToolCall] = []
    with transaction.atomic():
        current = (
            TurnToolCall.objects.filter(turn_id=turn_id)
            .aggregate(latest=Max("seq"))["latest"]
            or 0
        )
        for record in records:
            current += 1
            seq = current
            status = status_for_result(record.get("result"))
            name = str(record.get("name") or "")
            input_value = tool_record_input(record.get("input"))
            shown, truncated = _row_body({**record, "status": status})
            call_id = str(record.get("call_id") or record.get("id") or "")[:128]
            if not call_id:
                call_id = f"turn-{turn_id}-{seq}"
            row = TurnToolCall.objects.create(
                turn_id=turn_id,
                seq=seq,
                iteration=int(record.get("iteration") or 0),
                call_id=call_id,
                name=name[:100],
                status=status,
                untrusted=bool(record.get("untrusted")) or _untrusted_source(name),
                retrieval=bool(record.get("retrieval")) or name in RETRIEVAL_TOOL_NAMES,
                provenance=record.get("provenance") or "main",
                parent_call_id=str(record.get("parent_call_id") or "")[:128],
                input_enc=encrypt_body(room, input_value, turn_id=turn_id, seq=seq),
                shown_enc=encrypt_body(room, shown, turn_id=turn_id, seq=seq),
                truncated=truncated,
                receipt_id=record.get("receipt_id") or receipt_id,
            )
            created.append(row)
    return created


def record_from_event(
    turn_id: Any,
    room: Any,
    event_data: Dict[str, Any],
    *,
    receipt_id: Optional[int] = None,
) -> List[TurnToolCall]:
    """Append the main call of one ``tool_result`` event, plus its delegated calls."""
    delegated = event_data.get("delegated_calls")
    parent_id = str(event_data.get("id") or "")
    records: List[Dict[str, Any]] = []
    if isinstance(delegated, list):
        for call in delegated:
            if not isinstance(call, dict):
                continue
            records.append({
                "call_id": call.get("call_id") or call.get("id"),
                "name": call.get("name"),
                "input": call.get("input"),
                "result": call.get("result") or {"status": call.get("status")},
                "shown": call.get("shown"),
                "iteration": event_data.get("iteration"),
                "provenance": "delegated",
                "parent_call_id": parent_id,
            })
    records.append({
        "call_id": event_data.get("id"),
        "name": event_data.get("name"),
        "input": event_data.get("input"),
        "result": event_data.get("result"),
        "shown": event_data.get("shown"),
        "iteration": event_data.get("iteration"),
        "provenance": event_data.get("provenance") or "main",
        "parent_call_id": event_data.get("parent_call_id") or "",
        "retrieval": event_data.get("retrieval"),
        "receipt_id": event_data.get("receipt_id") or receipt_id,
    })
    return record_tool_calls(turn_id, room, records)


def load_tool_calls_for_turn(room: Any, turn_id: Any) -> List[Dict[str, Any]]:
    """The main replayable calls of one turn, oldest first."""
    return load_tool_calls_for_turns(room, [turn_id]).get(turn_id, [])


def load_tool_calls_for_turns(
    room: Any, turn_ids: Iterable[Any],
) -> Dict[Any, List[Dict[str, Any]]]:
    """Decrypt each main-provenance call once, grouped by Turn id, in ``seq`` order."""
    ids = [turn_id for turn_id in turn_ids if turn_id is not None]
    if not ids:
        return {}
    grouped: Dict[Any, List[Dict[str, Any]]] = {}
    rows = (
        TurnToolCall.objects.filter(turn_id__in=ids, provenance="main")
        .order_by("turn_id", "seq")
    )
    for row in rows:
        grouped.setdefault(row.turn_id, []).append({
            "call_id": row.call_id,
            "seq": row.seq,
            "iteration": row.iteration,
            "name": row.name,
            "status": row.status,
            "untrusted": row.untrusted,
            "retrieval": row.retrieval,
            "input": decrypt_body(room, row.input_enc, turn_id=row.turn_id, seq=row.seq) or {},
            "shown": decrypt_body(room, row.shown_enc, turn_id=row.turn_id, seq=row.seq) or "",
            "truncated": row.truncated,
        })
    return grouped


def calls_that_ran(room: Any, turn_id: Any) -> List[Dict[str, Any]]:
    """Executed calls of one turn, for the memory tracker (never the declined ones)."""
    rows = (
        TurnToolCall.objects.filter(turn_id=turn_id, provenance="main")
        .order_by("seq")
    )
    out: List[Dict[str, Any]] = []
    for row in rows:
        if row.status not in _RAN_STATUSES:
            continue
        out.append({
            "name": row.name,
            "input": decrypt_body(room, row.input_enc, turn_id=row.turn_id, seq=row.seq) or {},
            "output": {"status": row.status},
        })
    return out
