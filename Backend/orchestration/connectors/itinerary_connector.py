from typing import Dict, Any
from asgiref.sync import sync_to_async
from datetime import datetime, timedelta
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)


class ItineraryConnector:
    """Simple connector to create and view itineraries using the travel app models.

    This connector is intentionally lightweight: it creates an Itinerary record
    from parameters and returns the created object's id and summary.
    """

    def _validate_date(self, date_obj: Any) -> tuple[bool, str]:
        """Validate that a date is a valid calendar date.
        
        Returns: (is_valid, error_message)
        """
        if date_obj is None:
            return True, ""
        
        # Try to parse if it's a string
        if isinstance(date_obj, str):
            try:
                datetime.fromisoformat(date_obj)
                return True, ""
            except (ValueError, TypeError):
                return False, f"Invalid date format: {date_obj}"
        
        # Check if it's already a datetime/date object
        if isinstance(date_obj, (datetime,)):
            try:
                # Validate by accessing month and day
                _ = date_obj.strftime("%Y-%m-%d")
                return True, ""
            except (AttributeError, ValueError):
                return False, f"Invalid date object: {date_obj}"
        
        return False, f"Date must be a string (ISO format) or datetime object, got {type(date_obj).__name__}"

    def _parse_date(self, date_obj: Any) -> datetime:
        """Parse date string or object to datetime."""
        if isinstance(date_obj, str):
            return datetime.fromisoformat(date_obj)
        if isinstance(date_obj, datetime):
            return date_obj
        raise ValueError(f"Cannot parse date: {date_obj}")

    async def execute(self, parameters: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        try:
            user_id = context.get("user_id")
            if not user_id:
                return {"status": "error", "message": "User context missing"}

            # Lazy import to avoid circular imports at module load
            from travel.models import Itinerary

            title = parameters.get("title") or parameters.get("destination") or "New Itinerary"
            region = parameters.get("region") or parameters.get("destination") or "unknown"
            start_date = parameters.get("start_date")
            end_date = parameters.get("end_date")
            duration_days = parameters.get("duration_days")
            budget_ksh = parameters.get("budget_ksh")

            # Auto-calculate end_date from duration if not provided
            if not end_date and start_date and duration_days:
                try:
                    start = self._parse_date(start_date)
                    end_date = start + timedelta(days=int(duration_days))
                except (ValueError, TypeError) as e:
                    return {"status": "error", "message": f"Could not calculate end date from duration: {e}"}

            # Validate dates
            start_valid, start_msg = self._validate_date(start_date)
            if not start_valid:
                return {"status": "error", "message": f"Start date error: {start_msg}"}
            
            end_valid, end_msg = self._validate_date(end_date)
            if not end_valid:
                return {"status": "error", "message": f"End date error: {end_msg}"}

            def create_itin():
                data = {"user_id": user_id, "title": title, "region": region}
                if start_date:
                    # Parse and make timezone-aware
                    start = self._parse_date(start_date)
                    start = timezone.make_aware(start) if timezone.is_naive(start) else start
                    data["start_date"] = start
                if end_date:
                    # Parse and make timezone-aware
                    end = self._parse_date(end_date)
                    end = timezone.make_aware(end) if timezone.is_naive(end) else end
                    data["end_date"] = end
                if budget_ksh:
                    data["budget_ksh"] = budget_ksh

                # Use Django ORM's create (user must exist)
                itin = Itinerary.objects.create(**data)
                return itin.id

            itin_id = await sync_to_async(create_itin)()

            return {"status": "success", "itinerary_id": itin_id, "message": "Itinerary created"}

        except Exception as e:
            logger.exception("ItineraryConnector error")
            return {"status": "error", "message": str(e)}
