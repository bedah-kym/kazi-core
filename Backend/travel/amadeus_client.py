"""
Amadeus API client helpers for travel connectors.
"""
import os
from functools import lru_cache


def _client_class():
    """Return the Amadeus client class on demand, or None if importing fails."""
    try:
        from amadeus import Client
    except Exception:  # pragma: no cover - dependency may be missing in dev
        return None
    return Client


@lru_cache(maxsize=1)
def get_amadeus_client():
    """Return a cached Amadeus client, or None without credentials or the SDK.

    Read AMADEUS_API_KEY, AMADEUS_API_SECRET, and AMADEUS_HOSTNAME (default
    'test') on the first call. The result, including None, is cached until
    cache_clear() is called. Client construction errors propagate.
    """
    api_key = os.environ.get('AMADEUS_API_KEY')
    api_secret = os.environ.get('AMADEUS_API_SECRET')
    hostname = os.environ.get('AMADEUS_HOSTNAME', 'test')
    client_class = _client_class()

    if not api_key or not api_secret or client_class is None:
        return None

    return client_class(
        client_id=api_key,
        client_secret=api_secret,
        hostname=hostname,
    )


def has_amadeus_credentials() -> bool:
    return bool(os.environ.get('AMADEUS_API_KEY') and os.environ.get('AMADEUS_API_SECRET'))
