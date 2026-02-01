"""
Amadeus API client helpers for travel connectors.
"""
import os
from functools import lru_cache

try:
    from amadeus import Client
except Exception:  # pragma: no cover - dependency may be missing in dev
    Client = None


@lru_cache(maxsize=1)
def get_amadeus_client():
    """Return a configured Amadeus client or None if missing creds."""
    api_key = os.environ.get('AMADEUS_API_KEY')
    api_secret = os.environ.get('AMADEUS_API_SECRET')
    hostname = os.environ.get('AMADEUS_HOSTNAME', 'test')

    if not api_key or not api_secret or Client is None:
        return None

    return Client(
        client_id=api_key,
        client_secret=api_secret,
        hostname=hostname,
    )


def has_amadeus_credentials() -> bool:
    return bool(os.environ.get('AMADEUS_API_KEY') and os.environ.get('AMADEUS_API_SECRET'))
