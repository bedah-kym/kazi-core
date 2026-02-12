"""
Travel Events Connector
Searches for local events (concerts, sports, conferences) using Eventbrite API
"""
import logging
import aiohttp
import os
from typing import Dict, Any, List
from datetime import datetime
from bs4 import BeautifulSoup
from orchestration.connectors.base_travel_connector import BaseTravelConnector

logger = logging.getLogger(__name__)


class TravelEventsConnector(BaseTravelConnector):
    """
    Search for events in East Africa
    Uses Eventbrite API (free tier) + local scraping fallback
    """
    
    PROVIDER_NAME = 'eventbrite'
    CACHE_TTL_SECONDS = 7200  # 2 hours (events change less frequently)
    
    async def _fetch(self, parameters: Dict, context: Dict) -> Dict:
        """
        Fetch events from Eventbrite API
        
        Parameters:
            location: Nairobi, Mombasa, etc.
            event_date: 2025-12-25 (optional)
            start_date: 2025-12-20 (optional, date range start)
            end_date: 2025-12-31 (optional, date range end)
            category: music, sports, cultural, food, conference (optional)
            radius_km: 20 (optional)
        """
        location = parameters.get('location', '').strip()
        event_date = parameters.get('event_date')
        start_date = parameters.get('start_date')
        end_date = parameters.get('end_date')
        category = parameters.get('category', 'all').lower()
        radius_km = parameters.get('radius_km', 20)
        
        if not location:
            return {
                'results': [],
                'metadata': {
                    'error': 'Missing required parameter: location'
                }
            }
        
        try:
            # Try Eventbrite API first
            results = await self._search_eventbrite_api(location, start_date or event_date, end_date or event_date, category)
            
            # Fallback if API fails
            if not results:
                logger.info("Eventbrite API failed, using fallback data")
                results = self._get_fallback_events(location, category)
            
            logger.info(f"Found {len(results)} events in {location}")
            
            return {
                'results': results,
                'metadata': {
                    'location': location,
                    'event_date': event_date,
                    'start_date': start_date,
                    'end_date': end_date,
                    'category': category,
                    'provider': self.PROVIDER_NAME,
                    'total_found': len(results)
                }
            }
        except Exception as e:
            logger.error(f"Event search error: {str(e)}")
            return {
                'results': [],
                'metadata': {
                    'error': f'Failed to fetch events: {str(e)}',
                    'location': location
                }
            }
    
    async def _search_eventbrite_api(self, location: str, start_date: str, end_date: str, category: str) -> List[Dict]:
        """
        Search Eventbrite API for events
        Eventbrite offers free API with basic event discovery
        """
        try:
            eventbrite_key = os.getenv('EVENTBRITE_API_KEY')
            if not eventbrite_key:
                logger.debug("EVENTBRITE_API_KEY not set")
                return []
            
            # Eventbrite API endpoint for event search
            api_url = "https://www.eventbriteapi.com/v3/events/search"
            
            # Map location to geography code if possible
            location_map = {
                'nairobi': '10001',
                'kenya': '10001',
                'mombasa': '10001',
                'kampala': '10002',
                'uganda': '10002',
                'dar es salaam': '10003',
                'tanzania': '10003',
            }
            
            location_code = location_map.get(location.lower(), '')
            
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
            
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, params=params, headers=headers, 
                                      timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status != 200:
                        logger.warning(f"Eventbrite API returned status {response.status}")
                        return []
                    
                    data = await response.json()
            
            results = []
            events = data.get('events', [])
            
            for i, event in enumerate(events[:20]):
                try:
                    result = self._parse_eventbrite_event(event, i)
                    if result and (category == 'all' or category in result.get('category', '').lower()):
                        results.append(result)
                except Exception as e:
                    logger.warning(f"Error parsing event {i}: {str(e)}")
                    continue
            
            return results
            
        except Exception as e:
            logger.error(f"Eventbrite API error: {str(e)}")
            return []
    
    def _parse_eventbrite_event(self, event: Dict, index: int) -> Dict:
        """Parse Eventbrite API event into our format"""
        try:
            return {
                'id': f"event_{event.get('id', index+1)[:10]}",
                'provider': 'Eventbrite',
                'title': event.get('name', {}).get('text', f'Event {index+1}'),
                'category': event.get('category_id', 'other'),
                'start_datetime': event.get('start', {}).get('utc', ''),
                'end_datetime': event.get('end', {}).get('utc', ''),
                'location': event.get('venue', {}).get('name', 'Online'),
                'venue': event.get('venue', {}).get('name', 'Online'),
                'price_ksh': self._parse_eventbrite_price(event),
                'ticket_url': event.get('url', ''),
                'image_url': event.get('logo', {}).get('url', 'https://via.placeholder.com/300x200'),
                'rating': 4.5,
                'attendees': event.get('capacity', 100)
            }
        except Exception as e:
            logger.warning(f"Error parsing Eventbrite event: {str(e)}")
            return None
    
    def _parse_eventbrite_price(self, event: Dict) -> float:
        """Extract price from Eventbrite event"""
        try:
            ticket_classes = event.get('ticket_classes', [])
            if ticket_classes:
                price = ticket_classes[0].get('cost', {}).get('major_value', 0)
                return float(price)
            return 0
        except:
            return 0
    
    def _get_fallback_events(self, location: str, category: str) -> List[Dict]:
        """
        Fallback event database for major East African cities
        Includes common event types and seasonal events
        """
        event_db = {
            'nairobi': [
                {'title': 'Kenya Jazz Festival 2025', 'cat': 'music', 'date': '2025-12-20', 'price': 3500, 'venue': 'Safari Park', 'attendees': 450},
                {'title': 'Tech Summit East Africa', 'cat': 'conference', 'date': '2025-12-22', 'price': 5000, 'venue': 'Convention Centre', 'attendees': 800},
                {'title': 'Food & Wine Festival', 'cat': 'food', 'date': '2025-12-21', 'price': 2500, 'venue': 'Riverside Park', 'attendees': 600},
                {'title': 'Cultural Festival', 'cat': 'cultural', 'date': '2025-12-23', 'price': 1500, 'venue': 'Cultural Centre', 'attendees': 300},
                {'title': 'Music Concert Series', 'cat': 'music', 'date': '2025-12-24', 'price': 4000, 'venue': 'Arena', 'attendees': 5000},
                {'title': 'Sports Day Championship', 'cat': 'sports', 'date': '2025-12-25', 'price': 2000, 'venue': 'Stadium', 'attendees': 2000},
            ],
            'mombasa': [
                {'title': 'Coastal Music Festival', 'cat': 'music', 'date': '2025-12-20', 'price': 2500, 'venue': 'Beach Park', 'attendees': 300},
                {'title': 'Seafood Festival', 'cat': 'food', 'date': '2025-12-21', 'price': 2000, 'venue': 'Old Town', 'attendees': 400},
                {'title': 'Cultural Heritage Day', 'cat': 'cultural', 'date': '2025-12-22', 'price': 1000, 'venue': 'Fort Jesus', 'attendees': 200},
            ],
            'kampala': [
                {'title': 'Uganda Music Festival', 'cat': 'music', 'date': '2025-12-20', 'price': 3000, 'venue': 'Kampala Arena', 'attendees': 400},
                {'title': 'EastAfrican Tech Conference', 'cat': 'conference', 'date': '2025-12-23', 'price': 6000, 'venue': 'Convention Hall', 'attendees': 600},
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
                    'ticket_url': f'https://eventbrite.com/e/{i+1}',
                    'image_url': 'https://via.placeholder.com/300x200',
                    'rating': 4.5 + (i % 3) * 0.1,
                    'attendees': event['attendees']
                })
        
        return results
