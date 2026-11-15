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
from .models import Turn, TurnToolCall
from .tool_record import tool_record_input

SHOWN_LIMIT = 2000
MAX_REPLAYED_CALLS = 10
RETRIEVAL_TOOL_NAMES = frozenset({"web_search", "search_info"})
RAN_STATUSES = frozenset({"ok", "error", "outcome_unknown"})
HARNESS_NOT_RUN_STATUSES = frozenset({
    "denied", "skipped", "declined", "expired", "superseded",
    "budget_skipped",
})
_STATUS_ALIASES = {
    "success": "ok",
    "completed": "ok",
    "ok": "ok",
    "error": "error",
    "failed": "error",
    "failure": "error",
    "rejected": "error",
    "timed_out": "outcome_unknown",
    "denied": "denied",
    "skipped": "skipped",
    "declined": "declined",
    "expired": "expired",
    "superseded": "superseded",
    "budget_skipped": "budget_skipped",
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


def status_for_result(result: Any, *, not_run: bool = False) -> str:
    """Map a tool result's status to one of the row's status values.

    A row is did-not-run only when the harness says so (``not_run`` comes from
    the event, never from the tool's result). For a call that ran, a tool's
    self-reported ``skipped``/``denied``/... status is just a status string and
    maps to ran-ok; otherwise a tool could mark its own output as not run and
    escape taint. Only an explicit failure is ``error``. ``policy_stopped`` comes
    only from the executor's refusal shape (an ``error`` status with the safety
    policy message), never from a raw status a tool reports; it is kept for
    display but is still a ran call for taint.
    """
    if not isinstance(result, dict):
        return "error"
    if not_run:
        status = str(result.get("status") or "").strip().lower()
        return status if status in HARNESS_NOT_RUN_STATUSES else "skipped"
    if result.get("outcome_unknown"):
        return "outcome_unknown"
    status = str(result.get("status") or "").strip().lower()
    mapped = _STATUS_ALIASES.get(status)
    if mapped == "error" and "safety policy" in str(result.get("message") or ""):
        return "policy_stopped"
    if mapped in ("error", "outcome_unknown"):
        return mapped
    if result.get("error") and not status:
        return "error"
    return "ok"


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
        # Serialise seq allocation per Turn: two writers must not read the same
        # maximum and lose a batch to the unique (turn, seq) constraint.
        Turn.objects.select_for_update().filter(pk=turn_id).first()
        current = (
            TurnToolCall.objects.filter(turn_id=turn_id)
            .aggregate(latest=Max("seq"))["latest"]
            or 0
        )
        for record in records:
            current += 1
            seq = current
            status = status_for_result(
                record.get("result"), not_run=record.get("not_run") is True,
            )
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
                "not_run": False,
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
        # Harness intent only: never trust a not_run flag inside the result.
        "not_run": event_data.get("not_run") is True,
    })
    return record_tool_calls(turn_id, room, records)


def link_turn_calls_to_message(
    turn_id: Any, message_id: Any,
    *, first_seq: Optional[int] = None, last_seq: Optional[int] = None,
) -> int:
    """Attach only this segment's rows to the reply just saved.

    The segment knows the ``seq`` range it wrote, so a row another writer added
    while the prompt was being built is not pulled into this message. A row
    whose message was deleted keeps ``message=NULL`` and is never re-linked by
    a later segment. The call's outcome fields are never touched.
    """
    if not turn_id or not message_id:
        return 0
    if first_seq is None or last_seq is None:
        return 0
    return TurnToolCall.objects.filter(
        turn_id=turn_id, message__isnull=True,
        seq__gte=first_seq, seq__lte=last_seq,
    ).update(message_id=message_id)


def _load_row_bodies(room: Any, row: TurnToolCall) -> Dict[str, Any]:
    input_value = (
        decrypt_body(room, row.input_enc, turn_id=row.turn_id, seq=row.seq)
        if row.input_enc else {}
    )
    shown = (
        decrypt_body(room, row.shown_enc, turn_id=row.turn_id, seq=row.seq)
        if row.shown_enc else ""
    )
    return {
        "call_id": row.call_id,
        "seq": row.seq,
        "iteration": row.iteration,
        "name": row.name,
        "status": row.status,
        "untrusted": row.untrusted,
        "retrieval": row.retrieval,
        "input": input_value if isinstance(input_value, dict) else {},
        "shown": shown if isinstance(shown, str) else "",
        # A body missing at write time (unusable room key) is just as unreadable
        # as a body that fails to decrypt. Never replay an empty ok result.
        "unreadable": row.shown_enc is None or (
            bool(row.shown_enc) and shown is None
        ),
        "truncated": row.truncated,
    }


def load_tool_calls_for_turn(room: Any, turn_id: Any) -> List[Dict[str, Any]]:
    """The main replayable calls of one turn, oldest first (tests and audits)."""
    if not turn_id:
        return []
    rows = (
        TurnToolCall.objects.filter(turn_id=turn_id, provenance="main")
        .order_by("seq")
    )
    return [_load_row_bodies(room, row) for row in rows]


def load_tool_calls_for_messages(
    room: Any, message_ids: Iterable[Any],
) -> Dict[Any, List[Dict[str, Any]]]:
    """The last ``MAX_REPLAYED_CALLS`` main calls of each message, decrypted once.

    A row belongs to the reply message of the segment it resolved in, so a Turn
    that paused replays each row exactly once. Messages with more than the cap
    keep their latest pairs and carry a note naming how many earlier calls were
    left out; only the kept rows are decrypted.
    """
    ids = [message_id for message_id in message_ids if message_id is not None]
    if not ids:
        return {}
    # Read the order and counts first; bodies for dropped rows are never touched.
    meta: List[Tuple[Any, Any, int]] = list(
        TurnToolCall.objects.filter(message_id__in=ids, provenance="main")
        .values_list("id", "message_id", "seq")
        .order_by("message_id", "seq")
    )
    ids_by_message: Dict[Any, List[Tuple[Any, int]]] = {}
    for row_id, message_id, seq in meta:
        ids_by_message.setdefault(message_id, []).append((row_id, seq))
    keep_ids: Dict[Any, List[Any]] = {}
    omitted: Dict[Any, int] = {}
    for message_id, rows in ids_by_message.items():
        kept = rows[-MAX_REPLAYED_CALLS:]
        keep_ids[message_id] = [row_id for row_id, _seq in kept]
        omitted[message_id] = len(rows) - len(kept)
    wanted = [row_id for row_ids in keep_ids.values() for row_id in row_ids]
    if not wanted:
        return {}
    by_id = {
        row.id: row for row in TurnToolCall.objects.filter(id__in=wanted)
    }
    grouped: Dict[Any, List[Dict[str, Any]]] = {}
    for message_id, row_ids in keep_ids.items():
        calls = []
        for row_id in row_ids:
            row = by_id.get(row_id)
            if row is None:
                continue
            calls.append(_load_row_bodies(room, row))
        if omitted.get(message_id) and calls:
            calls[0]["note"] = (
                f"{omitted[message_id]} earlier tool call"
                f"{'s were' if omitted[message_id] != 1 else ' was'} not replayed."
            )
        grouped[message_id] = calls
    return grouped


def calls_that_ran(room: Any, turn_id: Any) -> List[Dict[str, Any]]:
    """Executed calls of one turn, for the memory tracker (never the declined ones)."""
    rows = (
        TurnToolCall.objects.filter(turn_id=turn_id, provenance="main")
        .order_by("seq")
    )
    out: List[Dict[str, Any]] = []
    for row in rows:
        if row.status not in RAN_STATUSES:
            continue
        out.append({
            "name": row.name,
            "input": decrypt_body(room, row.input_enc, turn_id=row.turn_id, seq=row.seq) or {},
            "output": {"status": row.status},
        })
    return out
