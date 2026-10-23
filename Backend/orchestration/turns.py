"""The durable Turn record and its one transition function (#216a).

A transition is a single compare-and-set write on ``(id, version)``. The
``version`` column is the fencing token: a writer whose conditional update
changes zero rows has lost ownership, writes nothing further and learns it
from the ``False`` return. Terminal states have no outgoing edges.

Everything here is sync; callers use the ``a*`` wrappers so the ORM does not
touch the event loop. Telemetry is emitted from inside the same sync function
because ``record_event`` does blocking file I/O.
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Dict, Iterable, Optional

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from orchestration.models import Turn
from orchestration.telemetry import record_event

TERMINAL_STATES = frozenset({
    "completed", "failed", "cancelled", "superseded", "expired",
})

# A full-profile worker only takes over a running/committing row once its
# wall-clock guard has certainly expired. The margin absorbs clock skew and a
# worker that is still writing its own terminal state.
STALE_TURN_MARGIN_SECONDS = 60

# to-state -> states it may be entered from. A terminal state is never a key,
# so no transition can leave one.
ALLOWED: Dict[str, frozenset] = {
    "running": frozenset({"queued", "paused"}),
    "paused": frozenset({"running"}),
    "committing": frozenset({"running"}),
    "completed": frozenset({"committing"}),
    "failed": frozenset({"running", "committing"}),
    "cancelled": frozenset({"queued", "running", "paused"}),
    "superseded": frozenset({"paused"}),
    "expired": frozenset({"queued", "paused"}),
}

_UNSET = object()


@dataclass(frozen=True)
class TurnRef:
    """A caller's in-memory view of a Turn; ``version`` is the expected fence."""

    id: str
    version: int
    state: str


def _ref(turn: Turn) -> TurnRef:
    return TurnRef(id=str(turn.pk), version=turn.version, state=turn.state)


def create_turn(
    *,
    room_id: Any,
    user_id: Any,
    request_message_id: Optional[int] = None,
    model: str = "",
    budget: Optional[Dict[str, Any]] = None,
) -> Turn:
    """Create the one row for a routed message and link the request to it."""
    turn = Turn.objects.create(
        room_id=room_id,
        user_id=user_id,
        state="queued",
        model=(model or "")[:100],
        budget=budget or {},
    )
    if request_message_id is not None:
        link_message(str(turn.pk), request_message_id)
    record_event("turn_created", {
        "turn_id": str(turn.pk),
        "room_id": room_id,
        "user_id": user_id,
        "state": turn.state,
        "schema_version": turn.schema_version,
    })
    return turn


def _active_turn(room_id: Any, user_id: Any) -> Optional[Turn]:
    return (
        Turn.objects.filter(
            room_id=room_id, user_id=user_id, state__in=("running", "committing"),
        )
        .order_by("started_at")
        .first()
    )


def _is_stale(blocker: Turn) -> bool:
    """Whether a blocking active row cannot still be held by a live worker.

    The lite profile runs exactly one web worker, and the consumer holds a
    per-process lock for the room, so any blocking row it finds is left over.
    A full profile can have another worker mid-turn; only a row past the hard
    wall-clock guard plus a margin is provably abandoned.
    """
    if getattr(settings, "KAZI_LITE_PROFILE", False):
        return True
    if blocker.started_at is None:
        return False
    from orchestration.agent_loop import HARD_TURN_TIMEOUT_SECONDS

    age = (timezone.now() - blocker.started_at).total_seconds()
    return age > HARD_TURN_TIMEOUT_SECONDS + STALE_TURN_MARGIN_SECONDS


def _insert_running(
    *,
    room_id: Any,
    user_id: Any,
    request_message_id: Optional[int],
    model: str,
    budget: Optional[Dict[str, Any]],
) -> Turn:
    with transaction.atomic():
        turn = Turn.objects.create(
            room_id=room_id,
            user_id=user_id,
            state="running",
            model=(model or "")[:100],
            budget=budget or {},
            started_at=timezone.now(),
            attempt=1,
        )
        if request_message_id is not None:
            link_message(str(turn.pk), request_message_id)
    return turn


def claim_turn(
    *,
    room_id: Any,
    user_id: Any,
    request_message_id: Optional[int] = None,
    model: str = "",
    budget: Optional[Dict[str, Any]] = None,
) -> Optional[Turn]:
    """Create the running Turn in one INSERT, taking over a provably stale row.

    A refused claim (the partial unique constraint) is only a hard refusal when
    the blocking row could still belong to a live worker. Otherwise it is ended
    ``failed(abandoned)`` and the claim is retried exactly once. Paused rows are
    never touched: they do not conflict with a running insert.
    """
    try:
        turn = _insert_running(
            room_id=room_id,
            user_id=user_id,
            request_message_id=request_message_id,
            model=model,
            budget=budget,
        )
    except IntegrityError:
        blocker = _active_turn(room_id, user_id)
        if blocker is None or not _is_stale(blocker):
            return None
        transition(
            str(blocker.pk), blocker.version, "failed",
            from_state=blocker.state, end_reason="abandoned",
        )
        try:
            turn = _insert_running(
                room_id=room_id,
                user_id=user_id,
                request_message_id=request_message_id,
                model=model,
                budget=budget,
            )
        except IntegrityError:
            return None
    record_event("turn_created", {
        "turn_id": str(turn.pk),
        "room_id": room_id,
        "user_id": user_id,
        "state": turn.state,
        "schema_version": turn.schema_version,
    })
    return turn


def link_message(turn_id: str, message_id: Optional[int]) -> bool:
    """Attach a message to a turn without touching the turn row."""
    if message_id is None:
        return False
    from chatbot.models import Message

    return bool(Message.objects.filter(pk=message_id).update(turn_id=turn_id))


def get_paused_turn(
    room_id: Any, user_id: Any, *, include_expired: bool = False,
) -> Optional[Turn]:
    """The room/user's paused Turn, expiring a stale one lazily on read."""
    turn = (
        Turn.objects.filter(room_id=room_id, user_id=user_id, state="paused")
        .order_by("-created_at")
        .first()
    )
    if turn is None:
        return None
    if _pause_expired(turn):
        expire_pause(turn)
        return turn if include_expired else None
    return turn


def get_expired_pause(
    room_id: Any, user_id: Any, *, within_seconds: int = 86400,
) -> Optional[Turn]:
    """The most recent pause this room/user let expire, for an honest reply.

    The encrypted state is kept on the row so a late reply can be answered
    with the calls that never ran. It is never executable.
    """
    cutoff = timezone.now() - timedelta(seconds=max(0, int(within_seconds)))
    return (
        Turn.objects.filter(
            room_id=room_id,
            user_id=user_id,
            state="expired",
            pause_kind__isnull=False,
            ended_at__gte=cutoff,
        )
        .order_by("-ended_at")
        .first()
    )


def _pause_expired(turn: Turn) -> bool:
    return bool(turn.pause_expires_at and turn.pause_expires_at <= timezone.now())


def pause_ttl_seconds() -> int:
    """One TTL for every pause kind (D7); junk falls back to ten minutes."""
    default = 600
    try:
        value = int(getattr(settings, "AGENT_PAUSE_TTL_SECONDS", default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def pause_expiry(ttl: Optional[int] = None):
    return timezone.now() + timedelta(seconds=ttl or pause_ttl_seconds())


def _room_key(room_id: Any) -> Optional[str]:
    from chatbot.models import Chatroom
    from users.encryption import TokenEncryption

    if not room_id:
        return None
    try:
        key = (
            Chatroom.objects.filter(pk=room_id)
            .values_list("encryption_key", flat=True)
            .first()
        )
    except Exception:
        return None
    if key and key.startswith("enc:"):
        key = TokenEncryption.safe_decrypt(key[4:], default=None)
    return key or None


def _room_cipher(room_id: Any):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _room_key(room_id)
    if not key:
        return None
    try:
        return AESGCM(base64.b64decode(key.encode("utf-8")))
    except Exception:
        return None


def _encode_b64(data: bytes) -> str:
    return base64.b64encode(data).decode("utf-8")


def encrypt_paused_state(room_id: Any, payload: Dict[str, Any]) -> Optional[str]:
    """Encrypt the loop state with the room key, as chat messages are (D5)."""
    cipher = _room_cipher(room_id)
    if cipher is None:
        return None
    try:
        nonce = os.urandom(12)
        body = json.dumps(payload, default=str).encode("utf-8")
        return json.dumps({
            "v": 1,
            "nonce": _encode_b64(nonce),
            "data": _encode_b64(cipher.encrypt(nonce, body, None)),
        })
    except Exception:
        return None


def decrypt_paused_state(room_id: Any, blob: Optional[str]) -> Optional[Dict[str, Any]]:
    """Decrypt a paused state; unreadable means the caller must fail closed."""
    cipher = _room_cipher(room_id)
    if cipher is None or not blob:
        return None
    try:
        envelope = json.loads(blob)
        nonce = base64.b64decode(envelope["nonce"].encode("utf-8"))
        data = base64.b64decode(envelope["data"].encode("utf-8"))
        return json.loads(cipher.decrypt(nonce, data, None).decode("utf-8"))
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            "paused state decrypt failed: %s", type(exc).__name__,
        )
        return None


def get_turn(turn_id: Any) -> Optional[Turn]:
    if not turn_id:
        return None
    try:
        return Turn.objects.filter(pk=turn_id).first()
    except (ValueError, TypeError):
        return None


def get_turn_paused_state(turn: Optional[Turn]) -> Optional[Dict[str, Any]]:
    if turn is None:
        return None
    return decrypt_paused_state(turn.room_id, turn.paused_state_enc)


def pause_turn(
    turn_id: Any,
    expected_version: int,
    *,
    state_payload: Dict[str, Any],
    pause_kind: str,
    ttl: Optional[int] = None,
    tainted: bool = False,
    budget: Optional[Dict[str, Any]] = None,
    active_seconds: Optional[float] = None,
) -> bool:
    """Persist a pause as the Turn's encrypted resting state.

    The state is encrypted before the compare-and-set, so a failed encryption
    refuses the pause instead of writing plaintext (fail closed).
    """
    turn = get_turn(turn_id)
    if turn is None:
        return False
    encrypted = encrypt_paused_state(turn.room_id, state_payload)
    if encrypted is None:
        return False
    expires_at = pause_expiry(ttl)
    return transition(
        str(turn.pk),
        expected_version,
        "paused",
        from_state="running",
        pause_kind=pause_kind,
        pause_expires_at=expires_at,
        paused_state_enc=encrypted,
        tainted=bool(tainted),
        budget=budget if budget is not None else {},
        active_seconds=active_seconds if active_seconds is not None else 0.0,
    )


def update_paused_state(turn_id: Any, state_payload: Dict[str, Any]) -> bool:
    """Refresh the resting state without moving the Turn (crash markers)."""
    turn = get_turn(turn_id)
    if turn is None:
        return False
    encrypted = encrypt_paused_state(turn.room_id, state_payload)
    if encrypted is None:
        return False
    return bool(
        Turn.objects.filter(pk=turn.pk, state__in=("paused", "running")).update(
            paused_state_enc=encrypted,
        )
    )


def clear_paused_state(turn_id: Any) -> bool:
    if not turn_id:
        return False
    try:
        return bool(
            Turn.objects.filter(pk=turn_id).update(paused_state_enc=None)
        )
    except (ValueError, TypeError):
        return False


def expire_pause(turn: Turn) -> bool:
    """Move a stale pause to ``expired`` once, keeping its state for a reply."""
    return transition(
        str(turn.pk),
        turn.version,
        "expired",
        from_state="paused",
        end_reason="pause_expired",
    )


def supersede_pause(turn: Turn, by_turn_id: Any) -> bool:
    return transition(
        str(turn.pk),
        turn.version,
        "superseded",
        from_state="paused",
        end_reason="superseded",
        superseded_by_id=by_turn_id,
    )


def transition(
    turn_id: str,
    expected_version: int,
    to_state: str,
    *,
    from_state: Optional[str] = None,
    end_reason: Any = _UNSET,
    pause_kind: Any = _UNSET,
    partial: Any = _UNSET,
    started_at: Any = _UNSET,
    ended_at: Any = _UNSET,
    active_seconds: Any = _UNSET,
    budget: Any = _UNSET,
    tainted: Any = _UNSET,
    model: Any = _UNSET,
    pause_expires_at: Any = _UNSET,
    paused_state_enc: Any = _UNSET,
    superseded_by_id: Any = _UNSET,
) -> bool:
    """Move a turn with one compare-and-set write.

    Returns ``True`` only when this caller won the race. A lost race changes
    nothing in the database and emits no telemetry.
    """
    allowed_from = ALLOWED.get(to_state)
    if not allowed_from:
        return False

    updates: Dict[str, Any] = {
        "state": to_state,
        "version": F("version") + 1,
    }
    if to_state == "running":
        updates["attempt"] = F("attempt") + 1

    optional = {
        "end_reason": end_reason,
        "pause_kind": pause_kind,
        "partial": partial,
        "started_at": started_at,
        "ended_at": ended_at,
        "active_seconds": active_seconds,
        "budget": budget,
        "tainted": tainted,
        "model": model,
        "pause_expires_at": pause_expires_at,
        "paused_state_enc": paused_state_enc,
        "superseded_by_id": superseded_by_id,
    }
    for field, value in optional.items():
        if value is not _UNSET:
            updates[field] = value

    if to_state in TERMINAL_STATES and ended_at is _UNSET:
        updates["ended_at"] = timezone.now()
    # A finished turn keeps no resting state. ``expired`` and ``superseded``
    # keep theirs briefly so a late reply can be answered honestly.
    if (
        to_state in ("completed", "failed", "cancelled")
        and "paused_state_enc" not in updates
    ):
        updates["paused_state_enc"] = None

    try:
        with transaction.atomic():
            updated = (
                Turn.objects.filter(
                    pk=turn_id,
                    version=expected_version,
                    state__in=allowed_from,
                ).update(**updates)
            )
    except (ValueError, TypeError, IntegrityError):
        # A malformed id, an unusable value or a losing insert against the
        # partial unique constraint is a lost write, not a crash.
        return False

    if updated != 1:
        return False

    event = "turn_paused" if to_state == "paused" else "turn_transition"
    payload = {
        "turn_id": str(turn_id),
        "from": from_state,
        "to": to_state,
        "pause_kind": updates.get("pause_kind"),
        "end_reason": updates.get("end_reason"),
        "partial": bool(updates.get("partial")) if "partial" in updates else None,
        "version": expected_version + 1,
    }
    record_event(event, payload)
    return True


def advance(ref: TurnRef, to_state: str, **kwargs) -> Optional[TurnRef]:
    """Transition a ``TurnRef`` and return the new ref, or ``None`` on loss."""
    if not transition(ref.id, ref.version, to_state, from_state=ref.state, **kwargs):
        return None
    return TurnRef(id=ref.id, version=ref.version + 1, state=to_state)


def finalize_turn(turn_id: str, expected_version: int, *, reply_saved: bool) -> bool:
    """Record the committing turn's true ending after the reply is persisted."""
    if reply_saved:
        return transition(
            turn_id, expected_version, "completed", from_state="committing",
        )
    return transition(
        turn_id, expected_version, "failed", from_state="committing",
        end_reason="reply_not_saved", partial=True,
    )


async def acreate_turn(**kwargs) -> Turn:
    return await sync_to_async(create_turn)(**kwargs)


async def aclaim_turn(**kwargs) -> Optional[Turn]:
    return await sync_to_async(claim_turn)(**kwargs)


async def afinalize_turn(turn_id: str, expected_version: int, *, reply_saved: bool) -> bool:
    return await sync_to_async(finalize_turn)(
        turn_id, expected_version, reply_saved=reply_saved,
    )


async def alink_message(turn_id: str, message_id: Optional[int]) -> bool:
    return await sync_to_async(link_message)(turn_id, message_id)


async def aget_paused_turn(
    room_id: Any, user_id: Any, *, include_expired: bool = False,
) -> Optional[Turn]:
    return await sync_to_async(get_paused_turn)(
        room_id, user_id, include_expired=include_expired,
    )


async def atransition(turn_id: str, expected_version: int, to_state: str, **kwargs) -> bool:
    return await sync_to_async(transition)(turn_id, expected_version, to_state, **kwargs)


async def aget_turn(turn_id: Any) -> Optional[Turn]:
    return await sync_to_async(get_turn)(turn_id)


async def aget_turn_paused_state(turn: Optional[Turn]) -> Optional[Dict[str, Any]]:
    return await sync_to_async(get_turn_paused_state)(turn)


async def apause_turn(*args, **kwargs) -> bool:
    return await sync_to_async(pause_turn)(*args, **kwargs)


async def aupdate_paused_state(turn_id: Any, state_payload: Dict[str, Any]) -> bool:
    return await sync_to_async(update_paused_state)(turn_id, state_payload)


async def aclear_paused_state(turn_id: Any) -> bool:
    return await sync_to_async(clear_paused_state)(turn_id)


async def aexpire_pause(turn: Turn) -> bool:
    return await sync_to_async(expire_pause)(turn)


async def asupersede_pause(turn: Turn, by_turn_id: Any) -> bool:
    return await sync_to_async(supersede_pause)(turn, by_turn_id)


async def aget_expired_pause(
    room_id: Any, user_id: Any, *, within_seconds: int = 86400,
) -> Optional[Turn]:
    return await sync_to_async(get_expired_pause)(
        room_id, user_id, within_seconds=within_seconds,
    )


def ref_from_turn(turn: Turn) -> TurnRef:
    return _ref(turn)


def legal_from(to_state: str) -> Iterable[str]:
    return ALLOWED.get(to_state, frozenset())
