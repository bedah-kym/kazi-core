"""
Helpers to cache the last travel search results per user so booking can
resolve option numbers or provider IDs without re-searching.
"""
import logging
from typing import Any, Dict, List, Optional, Tuple

from django.core.cache import cache

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 60 * 60  # 1 hour


def _cache_key(user_id: Any, action: str) -> str:
    return f"travel:last_results:{user_id}:{action}"


def store_last_results(
    user_id: Any,
    action: Optional[str],
    results: Optional[List[Dict]],
    metadata: Optional[Dict] = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> None:
    """Persist latest search results for a user/action pair."""
    if not user_id or not action:
        return
    payload = {
        "results": results or [],
        "metadata": metadata or {},
    }
    try:
        cache.set(_cache_key(user_id, action), payload, ttl_seconds)
    except Exception as exc:  # pragma: no cover - defensive logging only
        logger.debug(f"Unable to cache travel results for {action}: {exc}")


def get_last_results(user_id: Any, action: Optional[str]) -> Optional[Dict]:
    if not user_id or not action:
        return None
    try:
        return cache.get(_cache_key(user_id, action))
    except Exception as exc:  # pragma: no cover - defensive logging only
        logger.debug(f"Unable to read cached travel results for {action}: {exc}")
        return None


def find_result(
    user_id: Any,
    action: Optional[str],
    identifier: Any,
) -> Tuple[Optional[Dict], Dict]:
    """
    Find a result by id, provider id, flight number, or list index (1-based)
    from the last cached results for this user/action.
    """
    session = get_last_results(user_id, action)
    if not session:
        return None, {}

    results = session.get("results") or []
    metadata = session.get("metadata") or {}

    if identifier is None:
        return None, metadata

    ident_str = str(identifier).strip()
    ident_lower = ident_str.lower()

    # Direct id match
    for item in results:
        if str(item.get("id", "")).lower() == ident_lower:
            return item, metadata

    # Provider or human-facing identifiers
    for key in ("provider_id", "flight_number", "number", "code"):
        for item in results:
            value = item.get(key)
            if value and str(value).lower() == ident_lower:
                return item, metadata

    # 1-based index from the displayed list (e.g., "1", "2")
    if ident_lower.isdigit():
        idx = int(ident_lower) - 1
        if 0 <= idx < len(results):
            return results[idx], metadata

    return None, metadata
