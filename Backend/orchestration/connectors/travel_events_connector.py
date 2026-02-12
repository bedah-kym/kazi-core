"""
Travel Events Connector
Searches for local events using Eventbrite API
"""
import logging
import os
import aiohttp
from typing import Dict, Any, List
from datetime import datetime
from django.conf import settings
from orchestration.connectors.base_travel_connector import BaseTravelConnector

logger = logging.getLogger(__name__)


class TravelEventsConnector(BaseTravelConnector):
    """
    Search for events in East Africa
    Uses Eventbrite API with fallback if enabled
    """

    PROVIDER_NAME = 'eventbrite'
    CACHE_TTL_SECONDS = 7200  # 2 hours

    async def _fetch(self, parameters: Dict, context: Dict) -> Dict:
        location = parameters.get('location', '').strip()
        event_date = parameters.get('event_date')
        start_date = parameters.get('start_date')
        end_date = parameters.get('end_date')
        category = parameters.get('category', 'all').lower()

        # Normalize dates to YYYY-MM-DD
        def _norm_date(val):
            if not val:
                return None
            try:
                return datetime.fromisoformat(str(val)[:10]).date().isoformat()
            except Exception:
                try:
                    return datetime.strptime(str(val), "%Y-%m-%d").date().isoformat()
                except Exception:
                    return None

        event_date = _norm_date(event_date)
        start_date = _norm_date(start_date or event_date)
        end_date = _norm_date(end_date or event_date)

        if not location:
            return {
                'results': [],
                'metadata': {
                    'error': 'Missing required parameter: location'
                }
            }

        results, api_error = await self._search_eventbrite_api(location, start_date, end_date, category)

        if (not results) and settings.TRAVEL_ALLOW_FALLBACK:
            results = self._get_fallback_events(location, category)
            api_error = api_error or 'No events returned from Eventbrite; using fallback.'

        if not results:
            return {
                'results': [],
                'metadata': {
                    'error': api_error or 'No events returned from Eventbrite.'
                }
            }

        return {
            'results': results,
            'metadata': {
                'location': location,
                'event_date': event_date,
                'start_date': start_date,
                'end_date': end_date,
                'category': category,
                'provider': self.PROVIDER_NAME,
                'total_found': len(results),
                'warning': api_error if api_error else None
            }
        }

    async def _search_eventbrite_api(self, location: str, start_date: str, end_date: str, category: str) -> (List[Dict], str):
        eventbrite_key = os.getenv('EVENTBRITE_API_KEY')
        if not eventbrite_key:
            msg = "EVENTBRITE_API_KEY not set"
            logger.debug(msg)
            return [], msg

        api_url = "https://www.eventbriteapi.com/v3/events/search"
        params = {
            'q': location,
            'token': eventbrite_key,
            'sort_by': 'date',
        }

        if start_date:
            params['start_date.range_start'] = f'{start_date}T00:00:00Z'
        if end_date:
            params['start_date.range_end'] = f'{end_date}T23:59:59Z'

            headers = {
            'User-Agent': 'Travel-Planner/1.0'
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status != 200:
                        text = await response.text()
                        msg = f"Eventbrite API status {response.status}: {text[:200]}"
                        logger.warning(msg)
                        return [], msg
                    data = await response.json()
        except Exception as e:
            msg = f"Eventbrite API error: {str(e)}"
            logger.error(msg)
            return [], msg

        results = []
        events = data.get('events', [])

        for i, event in enumerate(events[:20]):
            try:
                result = self._parse_eventbrite_event(event, i)
                cat_val = result.get('category', '').lower()
                if result and (category == 'all' or category in cat_val):
                    results.append(result)
            except Exception as e:
                logger.warning(f"Error parsing event {i}: {str(e)}")
                continue

        return results, None

    def _parse_eventbrite_event(self, event: Dict, index: int) -> Dict:
        # Accept either text or id for category
        category_id = event.get('category_id', 'other')
        return {
            'id': f"event_{event.get('id', index+1)[:10]}",
            'provider': 'Eventbrite',
            'title': event.get('name', {}).get('text', f'Event {index+1}'),
            'category': category_id,
            'start_datetime': event.get('start', {}).get('utc', ''),
            'end_datetime': event.get('end', {}).get('utc', ''),
            'location': event.get('venue', {}).get('name', 'Online'),
            'venue': event.get('venue', {}).get('name', 'Online'),
            'price_ksh': self._parse_eventbrite_price(event),
            'ticket_url': event.get('url', ''),
            'image_url': event.get('logo', {}).get('url', ''),
            'rating': 4.5,
            'attendees': event.get('capacity', 100)
        }

    def _parse_eventbrite_price(self, event: Dict) -> float:
        ticket_classes = event.get('ticket_classes', [])
        if ticket_classes:
            price = ticket_classes[0].get('cost', {}).get('major_value', 0)
            try:
                return float(price)
            except Exception:
                return 0
        return 0

    def _get_fallback_events(self, location: str, category: str) -> List[Dict]:
        event_db = {
            'nairobi': [
                {'title': 'Kenya Jazz Festival', 'cat': 'music', 'date': '2025-12-20', 'price': 3500, 'venue': 'Safari Park', 'attendees': 450},
                {'title': 'Tech Summit East Africa', 'cat': 'conference', 'date': '2025-12-22', 'price': 5000, 'venue': 'Convention Centre', 'attendees': 800},
            ],
            'mombasa': [
                {'title': 'Coastal Music Festival', 'cat': 'music', 'date': '2025-12-20', 'price': 2500, 'venue': 'Beach Park', 'attendees': 300},
            ],
        }

        location_lower = location.lower()
        events = event_db.get(location_lower, event_db.get('nairobi', []))

        results = []
        for i, event in enumerate(events):
            if category == 'all' or category == event['cat']:
                results.append({
                    'id': f"event_{i+1:03d}",
                    'provider': 'Eventbrite',
                    'title': event['title'],
                    'category': event['cat'],
                    'start_datetime': f"{event['date']}T18:00:00Z",
                    'end_datetime': f"{event['date']}T22:00:00Z",
                    'location': location,
                    'venue': event['venue'],
                    'price_ksh': event['price'],
                    'ticket_url': f"https://eventbrite.com/e/{i+1}",
                    'image_url': '',
                    'rating': 4.5 + (i % 3) * 0.1,
                    'attendees': event['attendees']
                })

        return results
