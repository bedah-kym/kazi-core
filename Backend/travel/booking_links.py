"""Build real provider deep links so a 'booking' hands the traveller off to an
actual checkout (airline / OTA) to pay and ticket. This is the affiliate /
handoff model — we don't take payment in-app; we send the user to a working
provider page pre-filled with their trip details.

Partner/affiliate query params can be appended later in one place (see _AFFILIATE).
"""
from urllib.parse import quote_plus
import re

# Drop affiliate ids here when we have them, e.g. {"booking.com": "aid=123456"}.
_AFFILIATE: dict = {}

_IATA_RE = re.compile(r"\(([A-Za-z]{3})\)")


def _ymd(dt):
    return dt.strftime("%Y-%m-%d") if dt else ""


def _yymmdd(dt):
    return dt.strftime("%y%m%d") if dt else ""


def _iata_codes(item, meta):
    """Pull two airport codes from metadata or '... (NBO) -> ... (MBA)' titles."""
    origin = (meta.get("origin_code") or meta.get("origin") or "").strip()
    dest = (meta.get("destination_code") or meta.get("destination") or "").strip()
    if len(origin) == 3 and len(dest) == 3 and origin.isalpha() and dest.isalpha():
        return origin.lower(), dest.lower()
    found = _IATA_RE.findall(item.title or "")
    if len(found) >= 2:
        return found[0].lower(), found[1].lower()
    return None, None


def build_booking_link(item):
    """Return (url, provider_label) — a working deep link to complete this booking.

    Honours a real provider link already attached to the item; otherwise builds
    a sensible per-type search/checkout URL pre-filled with the trip details.
    """
    meta = item.metadata or {}
    itype = item.item_type
    loc = (item.location_name or meta.get("location") or meta.get("city") or "").strip()
    start, end = item.start_datetime, item.end_datetime

    # An existing real link (e.g. from a bus provider's search result) wins.
    existing = (item.booking_url or "").strip()
    if existing and "amadeus.com" not in existing:
        return existing, (item.provider or "Provider")

    if itype == "flight":
        o, d = _iata_codes(item, meta)
        if o and d and start:
            return f"https://www.skyscanner.net/transport/flights/{o}/{d}/{_yymmdd(start)}/", "Skyscanner"
        q = (meta.get("origin") or "") + " to " + (meta.get("destination") or loc or item.title)
        q = f"Flights {q.strip()} on {_ymd(start)}".strip()
        return f"https://www.google.com/travel/flights?q={quote_plus(q)}", "Google Flights"

    if itype == "hotel":
        city = loc or item.title
        url = f"https://www.booking.com/searchresults.html?ss={quote_plus(city)}"
        if start:
            url += f"&checkin={_ymd(start)}"
        if end:
            url += f"&checkout={_ymd(end)}"
        return url, "Booking.com"

    if itype == "bus":
        return "https://buupass.com/", "BuuPass"

    if itype == "event":
        ticket = meta.get("ticket_url") or meta.get("url")
        if ticket:
            return ticket, (item.provider or "Tickets")
        return f"https://www.google.com/search?q={quote_plus((item.title or '') + ' tickets')}", "Search"

    # transfer / activity / other
    q = f"{item.title or ''} {loc}".strip()
    return f"https://www.google.com/search?q={quote_plus(q or 'travel booking')}", "Search"
