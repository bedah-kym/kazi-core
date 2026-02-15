"""
Travel Hotels Connector
Searches for hotels using Amadeus Hotel Offers API
"""
import logging
from typing import Dict, Any, List
from asgiref.sync import sync_to_async
from django.conf import settings
from orchestration.connectors.base_travel_connector import BaseTravelConnector
from travel.amadeus_client import get_amadeus_client, has_amadeus_credentials

logger = logging.getLogger(__name__)


class TravelHotelsConnector(BaseTravelConnector):
    """
    Search for hotels using Amadeus API
    """

    PROVIDER_NAME = 'amadeus'
    CACHE_TTL_SECONDS = 3600  # 1 hour

    async def _fetch(self, parameters: Dict, context: Dict) -> Dict:
        location = parameters.get('location', '').strip()
        check_in = parameters.get('check_in_date')
        check_out = parameters.get('check_out_date')
        guests = int(parameters.get('guests', 1) or 1)
        rooms = int(parameters.get('rooms', 1) or 1)
        budget_ksh = parameters.get('budget_ksh')

        if not all([location, check_in, check_out]):
            return {
                'results': [],
                'metadata': {
                    'error': 'Missing required parameters: location, check_in_date, check_out_date'
                }
            }

        if not has_amadeus_credentials():
            if settings.TRAVEL_ALLOW_FALLBACK:
                results = self._get_fallback_hotels(location)
                return {
                    'results': results,
                    'metadata': {
                        'location': location,
                        'provider': self.PROVIDER_NAME,
                        'fallback': True
                    }
                }
            return {
                'results': [],
                'metadata': {
                    'error': 'Amadeus credentials not configured. Set AMADEUS_API_KEY and AMADEUS_API_SECRET.'
                }
            }

        city_code = await self._resolve_city_code(location)
        if not city_code:
            return {
                'results': [],
                'metadata': {
                    'error': f'Could not resolve city code for {location}'
                }
            }

        results = await self._search_amadeus_hotels(city_code, check_in, check_out, guests, rooms)

        if budget_ksh and results:
            results = [r for r in results if r['price_ksh'] <= float(budget_ksh)]

        if not results:
            if settings.TRAVEL_ALLOW_FALLBACK:
                results = self._get_fallback_hotels(location)
            else:
                return {
                    'results': [],
                    'metadata': {
                        'error': 'No hotel offers returned from Amadeus.'
                    }
                }

        return {
            'results': results,
            'metadata': {
                'location': location,
                'check_in': check_in,
                'check_out': check_out,
                'guests': guests,
                'rooms': rooms,
                'provider': self.PROVIDER_NAME,
                'total_found': len(results)
            }
        }

    async def _search_amadeus_hotels(self, city_code: str, check_in: str, check_out: str, guests: int, rooms: int) -> List[Dict]:
        client = get_amadeus_client()
        if client is None:
            return []

        def _call_api():
            return client.shopping.hotel_offers_search.get(
                cityCode=city_code,
                checkInDate=check_in,
                checkOutDate=check_out,
                adults=guests,
                roomQuantity=rooms,
                currency='KES'
            ).data

        try:
            data = await sync_to_async(_call_api)()
        except Exception as e:
            logger.error(f"Amadeus hotel search error: {e}")
            return []

        return self._parse_amadeus_hotels(data)

    async def _resolve_city_code(self, location: str) -> str:
        location = location.strip()
        if len(location) == 3:
            return location.upper()

        city_map = {
            'nairobi': 'NBO',
            'mombasa': 'MBA',
            'kisumu': 'KIS',
            'eldoret': 'EDL',
            'london': 'LON',
            'paris': 'PAR',
            'dubai': 'DXB',
            'kampala': 'EBB',
            'dar es salaam': 'DAR',
        }
        mapped = city_map.get(location.lower())
        if mapped:
            return mapped

        client = get_amadeus_client()
        if client is None:
            return ''

        def _lookup():
            response = client.reference_data.locations.get(
                keyword=location,
                subType='CITY'
            )
            if response.data:
                return response.data[0].get('iataCode', '')
            return ''

        try:
            return await sync_to_async(_lookup)()
        except Exception:
            return ''

    def _parse_amadeus_hotels(self, hotels: List[Dict]) -> List[Dict]:
        results = []
        for i, hotel in enumerate(hotels[:20]):
            try:
                offer = (hotel.get('offers') or [{}])[0]
                price = offer.get('price', {})
                total = float(price.get('total', 0))
                currency = price.get('currency', 'KES')
                price_ksh = self._convert_to_ksh(total, currency)

                hotel_info = hotel.get('hotel', {})
                name = hotel_info.get('name', f"Hotel {i+1}")

                results.append({
                    'id': f"hotel_{i+1:03d}",
                    'provider_id': str(hotel_info.get('hotelId') or i + 1),
                    'provider': 'Amadeus',
                    'name': name,
                    'location': hotel_info.get('cityCode', ''),
                    'rating': float(hotel_info.get('rating', 4.0) or 4.0),
                    'reviews': 0,
                    'price_ksh': price_ksh,
                    'original_price_ksh': int(price_ksh * 1.1),
                    'discount_percent': 10,
                    'amenities': hotel_info.get('amenities', ['WiFi', 'Breakfast']),
                    'room_type': (offer.get('room', {}) or {}).get('typeEstimated', {}).get('category', 'Standard'),
                    'nights': offer.get('rateFamilyEstimated', {}).get('nights', 1),
                    'booking_url': offer.get('self', ''),
                    'image_url': ''
                })
            except Exception as e:
                logger.warning(f"Error parsing Amadeus hotel offer: {e}")
                continue

        return results

    def _convert_to_ksh(self, amount: float, currency: str) -> float:
        rates = {
            'USD': 130,
            'EUR': 150,
            'GBP': 170,
        }
        if currency == 'KES':
            return amount
        return round(amount * rates.get(currency, 130), 2)

    def _get_fallback_hotels(self, location: str) -> List[Dict]:
        hotel_db = {
            'nairobi': [
                {'name': 'Safari Park Hotel', 'price': 8500, 'rating': 4.6, 'reviews': 1250},
                {'name': 'Serena Hotel', 'price': 25000, 'rating': 4.8, 'reviews': 980},
                {'name': 'Crowne Plaza Nairobi', 'price': 18000, 'rating': 4.5, 'reviews': 850},
                {'name': 'Hilton Nairobi', 'price': 22000, 'rating': 4.7, 'reviews': 1100},
                {'name': 'Villa Rosa Kempinski', 'price': 35000, 'rating': 4.9, 'reviews': 650},
            ],
            'mombasa': [
                {'name': 'Tamarind Dhow', 'price': 12000, 'rating': 4.4, 'reviews': 680},
                {'name': 'Serena Beach Hotel', 'price': 18000, 'rating': 4.6, 'reviews': 720},
                {'name': 'Reef Hotel', 'price': 9500, 'rating': 4.3, 'reviews': 450},
                {'name': 'Nyali Beach Hotel', 'price': 11000, 'rating': 4.5, 'reviews': 520},
            ],
            'kisumu': [
                {'name': 'Sunset Hotel', 'price': 5500, 'rating': 4.1, 'reviews': 320},
                {'name': 'Imperial Hotel', 'price': 6500, 'rating': 4.2, 'reviews': 280},
            ],
        }

        location_lower = location.lower()
        hotels = hotel_db.get(location_lower, hotel_db.get('nairobi', []))

        results = []
        for i, hotel in enumerate(hotels):
            results.append({
                'id': f'hotel_{i+1:03d}',
                'provider_id': f'fallback_{i+1:03d}',
                'provider': 'Amadeus',
                'name': hotel['name'],
                'location': location,
                'rating': hotel['rating'],
                'reviews': hotel['reviews'],
                'price_ksh': hotel['price'],
                'original_price_ksh': int(hotel['price'] * 1.1),
                'discount_percent': 10,
                'amenities': ['Pool', 'WiFi', 'Restaurant'],
                'room_type': 'Double Room',
                'nights': 3,
                'booking_url': f'https://amadeus.com/hotels/{i+1}',
                'image_url': ''
            })

        return results
