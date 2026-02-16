"""
Travel Transfers Connector
Searches for ground transfers using Amadeus Transfer Offers API
"""
import logging
from typing import Dict, Any, List
from asgiref.sync import sync_to_async
from django.conf import settings
from orchestration.connectors.base_travel_connector import BaseTravelConnector
from travel.amadeus_client import get_amadeus_client, has_amadeus_credentials

logger = logging.getLogger(__name__)


class TravelTransfersConnector(BaseTravelConnector):
    """
    Search for airport/ground transfers using Amadeus
    """

    PROVIDER_NAME = 'amadeus'
    CACHE_TTL_SECONDS = 7200  # 2 hours

    async def _fetch(self, parameters: Dict, context: Dict) -> Dict:
        origin = parameters.get('origin', '').strip()
        destination = parameters.get('destination', '').strip()
        travel_date = parameters.get('travel_date')
        travel_time = parameters.get('travel_time', '09:00')
        passengers = int(parameters.get('passengers', 1) or 1)
        luggage = int(parameters.get('luggage', 1) or 1)
        service_type = parameters.get('service_type', 'economy').lower()

        origin_norm = self._normalize_location(origin)
        destination_norm = self._normalize_location(destination)

        if not all([origin, destination, travel_date]):
            return {
                'results': [],
                'metadata': {
                    'error': 'Missing required parameters: origin, destination, travel_date'
                }
            }

        if not has_amadeus_credentials():
            if settings.TRAVEL_ALLOW_FALLBACK:
                results = self._get_fallback_transfers(origin, destination, passengers, service_type)
                return {
                    'results': results,
                    'metadata': {
                        'origin': origin,
                        'destination': destination,
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

        offers = await self._search_amadeus_transfers(
            origin_norm,
            destination_norm,
            travel_date,
            travel_time,
            passengers,
            luggage,
            service_type,
        )

        if not offers:
            if settings.TRAVEL_ALLOW_FALLBACK:
                offers = self._get_fallback_transfers(origin, destination, passengers, service_type)
            else:
                return {
                    'results': [],
                    'metadata': {
                        'error': 'No transfer offers returned from Amadeus.'
                    }
                }

        return {
            'results': offers,
            'metadata': {
                'origin': origin,
                'destination': destination,
                'travel_date': travel_date,
                'travel_time': travel_time,
                'passengers': passengers,
                'luggage': luggage,
                'service_type': service_type,
                'provider': self.PROVIDER_NAME,
                'total_found': len(offers)
            }
        }

    async def _search_amadeus_transfers(
        self,
        origin: str,
        destination: str,
        travel_date: str,
        travel_time: str,
        passengers: int,
        luggage: int,
        service_type: str,
    ) -> List[Dict]:
        client = get_amadeus_client()
        if client is None:
            return []

        start_dt = f"{travel_date}T{travel_time}:00"

        origin_code = origin.upper() if len(origin) == 3 else ''
        destination_code = destination.upper() if len(destination) == 3 else ''

        def _call_api():
            params = {
                'startDateTime': start_dt,
                'passengers': passengers,
                'transferType': 'PRIVATE',
                'travelClass': service_type.upper()
            }
            if origin_code:
                params['startLocationCode'] = origin_code
            else:
                params['startAddressLine'] = origin
            if destination_code:
                params['endLocationCode'] = destination_code
            else:
                params['endAddressLine'] = destination
            return client.shopping.transfer_offers.get(**params).data

        try:
            data = await sync_to_async(_call_api)()
        except Exception as e:
            logger.error(f"Amadeus transfer search error: {e}")
            return []

        return self._parse_transfer_offers(data)

    def _parse_transfer_offers(self, offers: List[Dict]) -> List[Dict]:
        results = []
        for i, offer in enumerate(offers[:20]):
            try:
                vehicle = offer.get('vehicle', {})
                price = offer.get('price', {})
                total = float(price.get('total', 0))
                currency = price.get('currency', 'KES')
                price_ksh = self._convert_to_ksh(total, currency)

                results.append({
                    'id': f"transfer_{i+1:03d}",
                    'provider_id': str(offer.get('id', f"offer_{i+1}")),
                    'provider': 'Amadeus',
                    'vehicle_type': vehicle.get('code', 'Sedan'),
                    'capacity': int(vehicle.get('seats', 4) or 4),
                    'price_ksh': price_ksh,
                    'estimated_duration_minutes': int(offer.get('estimatedDistance', {}).get('value', 30) or 30),
                    'rating': 4.5,
                    'driver_name': 'Professional Driver',
                    'amenities': vehicle.get('description', '').split(',') if vehicle.get('description') else ['AC'],
                    'booking_url': offer.get('self', ''),
                    'available': True,
                    'prepaid': True
                })
            except Exception as e:
                logger.warning(f"Error parsing transfer offer: {e}")
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

    def _get_fallback_transfers(self, origin: str, destination: str, passengers: int, service_type: str) -> List[Dict]:
        route_factors = {
            'airport-city': {'distance_km': 18, 'duration': 30, 'base_price': 3000},
            'airport-westlands': {'distance_km': 25, 'duration': 40, 'base_price': 4000},
            'city-city': {'distance_km': 5, 'duration': 15, 'base_price': 1500},
            'intercity': {'distance_km': 100, 'duration': 120, 'base_price': 8000}
        }

        route_type = 'city-city'
        if 'airport' in origin.lower():
            route_type = 'airport-westlands' if 'westlands' in destination.lower() else 'airport-city'

        route_info = route_factors.get(route_type, route_factors['city-city'])
        base_price = route_info['base_price']
        duration = route_info['duration']

        results = []
        results.append({
            'id': 'transfer_001',
            'provider_id': 'fallback_001',
            'provider': 'Amadeus',
            'vehicle_type': 'Standard Sedan',
            'capacity': 4,
            'price_ksh': int(base_price * 1.2),
            'estimated_duration_minutes': duration,
            'rating': 4.7,
            'driver_name': 'Driver',
            'booking_url': 'https://amadeus.com/transfers',
            'available': True
        })

        if service_type == 'premium' or passengers > 4:
            results.append({
                'id': 'transfer_002',
                'provider_id': 'fallback_002',
                'provider': 'Amadeus',
                'vehicle_type': 'SUV',
                'capacity': 6,
                'price_ksh': int(base_price * 1.5),
                'estimated_duration_minutes': duration,
                'rating': 4.9,
                'driver_name': 'Premium Driver',
                'booking_url': 'https://amadeus.com/transfers',
                'available': True
            })

        return results

    def _normalize_location(self, value: str) -> str:
        """
        Map common free-text locations to IATA codes for Amadeus searches.
        """
        if not value:
            return value
        clean = value.strip().lower()
        mapping = {
            'nairobi airport': 'NBO',
            'jomo kenyatta': 'NBO',
            'jkia': 'NBO',
            'wilson': 'WIL',
            'serena hotel': 'NBO',
            'nairobi cbd': 'NBO',
            'nairobi': 'NBO',
            'mombasa airport': 'MBA',
            'moi international': 'MBA',
            'mombasa': 'MBA',
        }
        for key, code in mapping.items():
            if key in clean:
                return code
        return value
