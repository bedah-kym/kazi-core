from typing import Dict, Any, Optional
from asgiref.sync import sync_to_async
from datetime import datetime, timedelta
from django.utils import timezone
from django.utils.dateparse import parse_datetime
import logging

logger = logging.getLogger(__name__)


class ItineraryConnector:
    """Connector to create, view, and update itineraries in the travel app."""

    def _parse_datetime(self, value: Any, fallback_days: int = 1) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            parsed = parse_datetime(value)
            if parsed:
                return parsed
            try:
                return datetime.fromisoformat(value)
            except Exception:
                pass
        return timezone.now() + timedelta(days=fallback_days)

    async def execute(self, parameters: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        action = parameters.get("action")

        if action == "create_itinerary":
            return await self.create_itinerary(parameters, context)
        if action == "view_itinerary":
            return await self.view_itinerary(parameters, context)
        if action == "add_to_itinerary":
            return await self.add_to_itinerary(parameters, context)
        if action == "book_travel_item":
            return await self.book_travel_item(parameters, context)

        return {"status": "error", "message": f"Unknown itinerary action: {action}"}

    async def create_itinerary(self, parameters: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        user_id = context.get("user_id")
        if not user_id:
            return {"status": "error", "message": "User context missing"}

        from travel.models import Itinerary

        title = parameters.get("title") or parameters.get("destination") or "New Itinerary"
        region = parameters.get("region") or parameters.get("destination") or "kenya"
        start_date = parameters.get("start_date")
        end_date = parameters.get("end_date")
        duration_days = parameters.get("duration_days")
        budget_ksh = parameters.get("budget_ksh")

        if not end_date and start_date and duration_days:
            try:
                start_dt = self._parse_datetime(start_date)
                end_date = start_dt + timedelta(days=int(duration_days))
            except Exception as e:
                return {"status": "error", "message": f"Could not calculate end date: {e}"}

        def _create_itin():
            active_itin = Itinerary.objects.filter(user_id=user_id, status='active').first()
            if active_itin:
                if title and title != "New Itinerary":
                    active_itin.title = title
                if budget_ksh:
                    active_itin.budget_ksh = budget_ksh
                active_itin.save()
                return active_itin

            now = timezone.now()
            default_start = now + timedelta(days=1)
            default_end = default_start + timedelta(days=7)

            data = {
                "user_id": user_id,
                "title": title,
                "region": region,
                "status": 'active',
                "start_date": default_start,
                "end_date": default_end
            }

            if start_date:
                start_dt = self._parse_datetime(start_date)
                data["start_date"] = timezone.make_aware(start_dt) if timezone.is_naive(start_dt) else start_dt
            if end_date:
                end_dt = self._parse_datetime(end_date)
                data["end_date"] = timezone.make_aware(end_dt) if timezone.is_naive(end_dt) else end_dt

            if budget_ksh:
                data["budget_ksh"] = budget_ksh

            return Itinerary.objects.create(**data)

        itin = await sync_to_async(_create_itin)()

        return {
            "status": "success",
            "itinerary_id": itin.id,
            "message": "Itinerary created",
            "title": itin.title
        }

    async def view_itinerary(self, parameters: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        user_id = context.get("user_id")
        if not user_id:
            return {"status": "error", "message": "User context missing"}

        from travel.models import Itinerary

        itinerary_id = parameters.get("itinerary_id")

        def _get_itin():
            qs = Itinerary.objects.filter(user_id=user_id)
            if itinerary_id:
                return qs.filter(id=itinerary_id).first()
            return qs.filter(status='active').first() or qs.order_by('-created_at').first()

        itin = await sync_to_async(_get_itin)()
        if not itin:
            return {"status": "error", "message": "No itinerary found"}

        items = await sync_to_async(lambda: list(itin.items.order_by('start_datetime')[:20]))()

        return {
            "status": "success",
            "itinerary": {
                "id": itin.id,
                "title": itin.title,
                "status": itin.status,
                "start_date": itin.start_date.isoformat() if itin.start_date else None,
                "end_date": itin.end_date.isoformat() if itin.end_date else None,
                "budget_ksh": float(itin.budget_ksh) if itin.budget_ksh else None,
                "items": [
                    {
                        "id": item.id,
                        "type": item.item_type,
                        "title": item.title,
                        "start_datetime": item.start_datetime.isoformat() if item.start_datetime else None,
                        "location": item.location_name,
                        "price_ksh": float(item.price_ksh) if item.price_ksh else None,
                        "status": item.status,
                        "booking_url": item.booking_url
                    }
                    for item in items
                ]
            }
        }

    async def add_to_itinerary(self, parameters: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        user_id = context.get("user_id")
        if not user_id:
            return {"status": "error", "message": "User context missing"}

        from travel.models import Itinerary, ItineraryItem

        itinerary_id = parameters.get("itinerary_id")
        item_type = parameters.get("item_type")
        item_data = parameters.get("item", {}) or {}

        if not item_type:
            return {"status": "error", "message": "item_type is required"}

        def _get_itin():
            qs = Itinerary.objects.filter(user_id=user_id)
            if itinerary_id:
                return qs.filter(id=itinerary_id).first()
            return qs.filter(status='active').first() or qs.order_by('-created_at').first()

        itin = await sync_to_async(_get_itin)()
        if not itin:
            return {"status": "error", "message": "No itinerary found"}

        title = parameters.get("title") or item_data.get("title") or item_data.get("name") or f"{item_type.title()} Item"
        start_dt = self._parse_datetime(parameters.get("start_datetime") or item_data.get("start_datetime"))
        end_dt_value = parameters.get("end_datetime") or item_data.get("end_datetime")
        end_dt = self._parse_datetime(end_dt_value) if end_dt_value else None

        def _create_item():
            return ItineraryItem.objects.create(
                itinerary=itin,
                item_type=item_type,
                title=title,
                description=parameters.get("description") or item_data.get("description"),
                start_datetime=timezone.make_aware(start_dt) if timezone.is_naive(start_dt) else start_dt,
                end_datetime=timezone.make_aware(end_dt) if end_dt and timezone.is_naive(end_dt) else end_dt,
                location_name=parameters.get("location") or item_data.get("location"),
                provider=parameters.get("provider") or item_data.get("provider"),
                provider_id=parameters.get("item_id") or item_data.get("id"),
                price_ksh=parameters.get("price_ksh") or item_data.get("price_ksh"),
                booking_url=parameters.get("booking_url") or item_data.get("booking_url"),
                metadata=parameters.get("metadata") or item_data,
                status='planned'
            )

        item = await sync_to_async(_create_item)()

        return {
            "status": "success",
            "message": "Item added to itinerary",
            "item_id": item.id,
            "itinerary_id": itin.id
        }

    async def book_travel_item(self, parameters: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        user_id = context.get("user_id")
        if not user_id:
            return {"status": "error", "message": "User context missing"}

        from travel.models import ItineraryItem, BookingReference
        from travel.search_state import find_result
        import re

        raw_item_id = (
            parameters.get("item_id")
            or parameters.get("flight_id")
            or parameters.get("id")
        )
        item_id = None
        if isinstance(raw_item_id, int):
            item_id = raw_item_id
        elif isinstance(raw_item_id, str):
            match = re.search(r"\d+", raw_item_id)
            if match:
                try:
                    item_id = int(match.group())
                except Exception:
                    item_id = None
        item_type = parameters.get("item_type")

        existing_item = None
        if item_id:
            existing_item = await sync_to_async(
                lambda: ItineraryItem.objects.filter(id=item_id, itinerary__user_id=user_id).first()
            )()

        # If we don't have a saved itinerary item, try to resolve from last search results
        search_action = self._resolve_search_action(item_type, raw_item_id) or 'search_flights'
        search_metadata: Dict[str, Any] = {}
        if not existing_item and search_action:
            candidate, search_metadata = find_result(user_id, search_action, raw_item_id)
            if (not candidate) and parameters.get("flight_number"):
                candidate, search_metadata = find_result(
                    user_id,
                    search_action,
                    parameters.get("flight_number")
                )
            if (not candidate) and item_id:
                candidate, search_metadata = find_result(user_id, search_action, item_id)

            if candidate:
                inferred_type = item_type or self._infer_item_type_from_action(search_action)
                existing_item = await self._create_item_from_search(
                    user_id=user_id,
                    item_type=inferred_type,
                    item_data=candidate,
                    search_metadata=search_metadata,
                )

        if not existing_item:
            return {
                "status": "error",
                "message": (
                    "I couldn't match that option to your latest search. "
                    "Please pick an option number from the last list or run a new search."
                )
            }

        def _create_booking():
            item = existing_item

            # Avoid duplicate booking references for the same item
            existing_booking = getattr(item, "booking_reference", None)
            if existing_booking:
                return existing_booking, None

            booking_url = (
                parameters.get("booking_url")
                or item.booking_url
                or "https://amadeus.com"
            )
            booking = BookingReference.objects.create(
                itinerary_item=item,
                provider=item.provider or parameters.get("provider") or "Unknown",
                provider_booking_id=str(
                    parameters.get("provider_booking_id")
                    or parameters.get("booking_reference")
                    or item.provider_id
                    or raw_item_id
                    or item.id
                ),
                booking_reference=str(
                    parameters.get("booking_reference")
                    or parameters.get("provider_booking_id")
                    or item.provider_id
                    or raw_item_id
                    or item.id
                ),
                confirmation_code=parameters.get("confirmation_code"),
                status='pending',
                booking_url=booking_url,
                confirmation_email=parameters.get("confirmation_email")
            )

            item.status = 'booked'
            item.booking_url = item.booking_url or booking_url
            item.save(update_fields=['status', 'booking_url'])
            return booking, None

        booking, error = await sync_to_async(_create_booking)()
        if error:
            return {"status": "error", "message": error}

        return {
            "status": "success",
            "message": "Booking initiated",
            "booking_id": booking.id,
            "booking_url": booking.booking_url
        }

    def _resolve_search_action(self, item_type: Optional[str], raw_item_id: Any) -> Optional[str]:
        mapping = {
            'flight': 'search_flights',
            'hotel': 'search_hotels',
            'bus': 'search_buses',
            'transfer': 'search_transfers',
            'event': 'search_events',
        }
        if item_type and item_type.lower() in mapping:
            return mapping[item_type.lower()]

        if isinstance(raw_item_id, str):
            lower = raw_item_id.lower()
            for prefix, action in mapping.items():
                if lower.startswith(prefix):
                    return action
        return None

    def _infer_item_type_from_action(self, action: Optional[str]) -> str:
        reverse_map = {
            'search_flights': 'flight',
            'search_hotels': 'hotel',
            'search_buses': 'bus',
            'search_transfers': 'transfer',
            'search_events': 'event',
        }
        return reverse_map.get(action or '', 'other')

    async def _get_or_create_itinerary(
        self,
        user_id: int,
        title_hint: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
    ):
        from travel.models import Itinerary

        def _resolve_itinerary():
            qs = Itinerary.objects.filter(user_id=user_id)
            itin = qs.filter(status='active').first() or qs.order_by('-created_at').first()
            if itin:
                return itin

            start_dt = self._parse_datetime(start_date, fallback_days=1)
            end_dt = self._parse_datetime(end_date, fallback_days=3)
            start_dt = timezone.make_aware(start_dt) if timezone.is_naive(start_dt) else start_dt
            end_dt = timezone.make_aware(end_dt) if timezone.is_naive(end_dt) else end_dt

            return Itinerary.objects.create(
                user_id=user_id,
                title=title_hint or "New Itinerary",
                region='worldwide',
                start_date=start_dt,
                end_date=end_dt,
                status='active'
            )

        return await sync_to_async(_resolve_itinerary)()

    def _combine_date_time(self, date_str: Optional[str], time_str: Optional[str]) -> datetime:
        if date_str:
            try:
                dt_str = f"{date_str} {time_str or '09:00'}"
                dt = datetime.fromisoformat(dt_str)
                return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
            except Exception:
                pass
        fallback = timezone.now() + timedelta(days=1)
        return fallback

    def _location_hint(self, search_metadata: Dict[str, Any]) -> Optional[str]:
        origin = search_metadata.get('origin')
        destination = search_metadata.get('destination')
        if origin or destination:
            return f"{origin or ''}->{destination or ''}".strip('->')
        return search_metadata.get('location') or search_metadata.get('city')

    async def _create_item_from_search(
        self,
        user_id: int,
        item_type: str,
        item_data: Dict[str, Any],
        search_metadata: Dict[str, Any],
    ):
        from travel.models import ItineraryItem

        itinerary = await self._get_or_create_itinerary(
            user_id=user_id,
            title_hint=search_metadata.get('title') or search_metadata.get('destination'),
            start_date=search_metadata.get('departure_date') or search_metadata.get('check_in') or search_metadata.get('travel_date'),
            end_date=search_metadata.get('return_date') or search_metadata.get('check_out') or search_metadata.get('travel_date'),
        )

        start_dt = self._combine_date_time(
            search_metadata.get('departure_date')
            or search_metadata.get('check_in')
            or search_metadata.get('travel_date'),
            item_data.get('departure_time') or search_metadata.get('travel_time')
        )

        title = (
            item_data.get('title')
            or item_data.get('name')
            or item_data.get('flight_number')
            or f"{item_type.title()} Option"
        )

        provider = item_data.get('provider') or search_metadata.get('provider') or "Unknown"
        provider_id = item_data.get('provider_id') or item_data.get('id')
        booking_url = item_data.get('booking_url') or search_metadata.get('booking_url') or "https://amadeus.com"

        def _create():
            return ItineraryItem.objects.create(
                itinerary=itinerary,
                item_type=item_type or 'other',
                title=title,
                description=item_data.get('description'),
                start_datetime=start_dt,
                end_datetime=None,
                location_name=item_data.get('location') or self._location_hint(search_metadata),
                provider=provider,
                provider_id=provider_id,
                price_ksh=item_data.get('price_ksh') or 0,
                booking_url=booking_url,
                metadata={
                    'provider': provider,
                    'provider_id': provider_id,
                    'search_action': search_metadata.get('search_action'),
                    'raw_result': item_data,
                },
                status='planned'
            )

        return await sync_to_async(_create)()
