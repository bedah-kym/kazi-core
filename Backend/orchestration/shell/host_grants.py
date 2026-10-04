"""Per-room host grants for the shell egress proxy.

A grant lets every sandboxed command in one room reach one host through the
proxy until it expires or is revoked. It is created only by an explicit human
decision — an exact chat reply or the management command — and every grant
writes a receipt in the same transaction. Sync API; async callers wrap it.

Who may approve is an install-level switch, ``SHELL_HOST_GRANT_APPROVERS``:
``members`` (default: any member of the room) or ``staff`` (a staff member of
the room). Any member may always revoke: removing access is never the risk.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, List, Optional

from asgiref.sync import async_to_sync
from django.utils import timezone

logger = logging.getLogger(__name__)

GRANT_ACTION = "shell_host_grant"
REVOKE_ACTION = "shell_host_revoke"
APPROVERS_MEMBERS = "members"
APPROVERS_STAFF = "staff"


def _setting(name: str, default: Any) -> Any:
    from django.conf import settings

    return getattr(settings, name, default)


def _int_setting(name: str, default: int) -> int:
    try:
        value = int(_setting(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def approvers() -> str:
    """``members`` or ``staff``. Anything unrecognised means the stricter one."""
    value = str(_setting("SHELL_HOST_GRANT_APPROVERS", APPROVERS_MEMBERS) or "").strip().lower()
    return APPROVERS_MEMBERS if value == APPROVERS_MEMBERS else APPROVERS_STAFF


def normalize_host(host: Any) -> Optional[str]:
    """A plain public DNS name, or ``None``.

    No wildcards, no IP literals or numeric look-alikes, no bare labels: a
    grant names one host a human can read and recognise.
    """
    from orchestration.shell_exec.squid import valid_host_entry

    text = str(host or "").strip().lower().rstrip(".")
    if "://" in text:
        text = text.split("://", 1)[1]
    text = text.split("/", 1)[0].strip()
    if text.startswith(".") or not valid_host_entry(text):
        return None
    return text


def _is_member(user_id: Optional[int], room_id: Any) -> bool:
    if not user_id or not room_id:
        return False
    from chatbot.models import Chatroom

    return Chatroom.objects.filter(id=room_id, participants__User_id=user_id).exists()


def denial_reason(user_id: Optional[int], room_id: Any, host: Any) -> str:
    """Why this user may not approve this host here, or ``""`` if they may."""
    from django.contrib.auth import get_user_model

    if normalize_host(host) is None:
        return "It must be a plain host name like `example.com`."
    try:
        if not _is_member(user_id, room_id):
            return "You must be a member of this room."
        if approvers() == APPROVERS_STAFF:
            user = get_user_model().objects.filter(id=user_id).first()
            if not user or not (user.is_staff or user.is_superuser):
                return "On this install only an admin can approve hosts."
    except Exception as exc:
        logger.warning("Host approval check failed for room %s: %s", room_id, exc)
        return "I couldn't check your permission just now."
    return ""


def _receipt(user_id: int, room_id: Any, action: str, host: str, extra: Optional[dict] = None) -> bool:
    from orchestration.action_receipts import record_action_receipt

    receipt = async_to_sync(record_action_receipt)(
        user_id=user_id,
        room_id=room_id,
        action=action,
        service="shell",
        params={"host": host, **(extra or {})},
        result={},
        status="success",
    )
    return receipt is not None


def active_grants(room_id: Any):
    from orchestration.models import ShellHostGrant

    if not room_id:
        return ShellHostGrant.objects.none()
    return ShellHostGrant.objects.filter(
        room_id=room_id, revoked_at__isnull=True, expires_at__gt=timezone.now(),
    ).order_by("host")


def active_hosts(room_id: Any) -> List[str]:
    return list(active_grants(room_id).values_list("host", flat=True))


def grant(user_id: Optional[int], room_id: Any, host: Any, *, days: Optional[int] = None) -> Optional[str]:
    """Approve ``host`` for the room. Returns the normalized host, or ``None``.

    Refused unless the user may approve here and the receipt is written.
    """
    from django.db import transaction

    from orchestration.models import ShellHostGrant

    if denial_reason(user_id, room_id, host):
        return None
    name = normalize_host(host)
    try:
        lifetime = _int_setting("SHELL_HOST_GRANT_DAYS", 30) if days is None else max(1, int(days))
        lifetime = min(lifetime, _int_setting("SHELL_HOST_GRANT_MAX_DAYS", 365))
        expires_at = timezone.now() + timedelta(days=lifetime)
        with transaction.atomic():
            active_grants(room_id).filter(host=name).update(revoked_at=timezone.now(), revoked_by_id=user_id)
            ShellHostGrant.objects.create(
                room_id=room_id, host=name, created_by_id=user_id, expires_at=expires_at,
            )
            if not _receipt(user_id, room_id, GRANT_ACTION, name, {"expires_at": expires_at.isoformat()}):
                raise RuntimeError("receipt was not written")
    except Exception as exc:
        logger.warning("Host grant failed for room %s: %s", room_id, exc)
        return None
    return name


def revoke(user_id: Optional[int], room_id: Any, host: Any) -> bool:
    """Remove a room's approval. Any member may; the receipt is best-effort
    because a revoke must never be blocked by an audit-write failure."""
    name = normalize_host(host)
    if not name or not _is_member(user_id, room_id):
        return False
    updated = active_grants(room_id).filter(host=name).update(
        revoked_at=timezone.now(), revoked_by_id=user_id,
    )
    if updated:
        try:
            _receipt(user_id, room_id, REVOKE_ACTION, name)
        except Exception as exc:
            logger.warning("Host revoke receipt failed for room %s: %s", room_id, exc)
    return bool(updated)
