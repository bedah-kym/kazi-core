"""Durable per-room model preference.

The database is the source of truth. The old cache-only key is read once for a
room that has never been stored, then written through, so a choice made before
this field existed survives the deploy.
"""
import logging
from typing import Optional

from django.core.cache import cache

from orchestration.model_catalog import (
    find_model,
    model_pref_key,
    parse_model_id,
    provider_configured,
)

logger = logging.getLogger(__name__)
_read_failure_logged = False


def _log_read_failure_once(exc: Exception) -> None:
    global _read_failure_logged
    if _read_failure_logged:
        return
    _read_failure_logged = True
    logger.warning("Could not read the room model preference; using Auto: %s", exc)


def is_selectable_model(raw: Optional[str]) -> bool:
    """True when raw names a catalog model whose provider is configured."""
    if not raw:
        return False
    parsed = parse_model_id(str(raw))
    if not parsed:
        return False
    provider, model = parsed
    return find_model(provider, model) is not None and provider_configured(provider)


def _stored_pref(room_id):
    from chatbot.models import Chatroom

    return Chatroom.objects.filter(pk=room_id).values_list(
        "model_pref", flat=True
    ).first()


def _delete_legacy_key(key: str) -> None:
    try:
        cache.delete(key)
    except Exception as exc:
        logger.warning("Could not delete the legacy room model preference: %s", exc)


def _adopt_legacy_pref(room_id: int) -> Optional[str]:
    key = model_pref_key(room_id)
    try:
        legacy = cache.get(key)
    except Exception as exc:
        _log_read_failure_once(exc)
        return None
    if not legacy:
        return None
    raw = str(legacy).strip()
    if not is_selectable_model(raw):
        _delete_legacy_key(key)
        return None
    try:
        from chatbot.models import Chatroom

        updated = Chatroom.objects.filter(
            pk=room_id, model_pref__isnull=True
        ).update(model_pref=raw)
    except Exception as exc:
        _log_read_failure_once(exc)
        return None
    if not updated:
        try:
            raw = _stored_pref(room_id)
        except Exception as exc:
            _log_read_failure_once(exc)
            return None
    _delete_legacy_key(key)
    return str(raw) if raw else None


def read_room_model_pref(room_id: Optional[int]) -> Optional[str]:
    """Return the stored provider/model id, or None for Auto.

    A missing room, an explicitly empty value, a malformed id and a read error
    all mean Auto. A read error is logged once per process.
    """
    if not room_id:
        return None
    try:
        raw = _stored_pref(room_id)
    except Exception as exc:
        _log_read_failure_once(exc)
        return None
    if raw is not None:
        return str(raw) or None
    return _adopt_legacy_pref(room_id)


def set_room_model_pref(room_id: int, raw: str) -> None:
    """Persist raw and drop the legacy cache key."""
    from chatbot.models import Chatroom

    _delete_legacy_key(model_pref_key(room_id))
    Chatroom.objects.filter(pk=room_id).update(model_pref=raw)


def clear_room_model_pref(room_id: int) -> None:
    """Store an explicit empty value, meaning Auto, and drop the legacy key."""
    set_room_model_pref(room_id, "")
