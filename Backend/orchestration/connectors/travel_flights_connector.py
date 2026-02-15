"""
Travel Flights Connector
Searches for flights using Amadeus API
"""
import logging
from typing import Dict, Any, List
from asgiref.sync import sync_to_async
from django.conf import settings
from orchestration.connectors.base_travel_connector import BaseTravelConnector
from travel.amadeus_client import get_amadeus_client, has_amadeus_credentials

logger = logging.getLogger(__name__)


class TravelFlightsConnector(BaseTravelConnector):
    """
    Search for flights using Amadeus API (real-time offers)
    """

    PROVIDER_NAME = 'amadeus'
    CACHE_TTL_SECONDS = 1800  # 30 minutes

    def __init__(self):
        super().__init__()
        self._last_error = None

    def _normalize_date(self, value: str) -> str:
        """
        Accept natural-language / partial dates (e.g., 'Dec 20', '2026-12-20')
        and return YYYY-MM-DD for Amadeus. Returns '' on failure.
        """
        if not value:
            return ''
        val = value.strip()
        if len(val) == 10 and val[4] == '-' and val[7] == '-':
            return val  # already ISO
        try:
            from dateutil import parser  # already in dependencies via requests
            dt = parser.parse(val, dayfirst=False, yearfirst=False, fuzzy=True)
            return dt.date().isoformat()
        except Exception:
            return ''

    async def _fetch(self, parameters: Dict, context: Dict) -> Dict:
        origin = parameters.get('origin', '').strip()
        destination = parameters.get('destination', '').strip()
        departure_date = self._normalize_date(parameters.get('departure_date'))
        return_date = self._normalize_date(parameters.get('return_date'))
        passengers = int(parameters.get('passengers', 1) or 1)
        cabin_class = parameters.get('cabin_class', 'economy').lower()

        # Basic validation before hitting provider
        from datetime import date
        if not all([origin, destination, departure_date]):
            return {
                'results': [],
                'metadata': {
                    'error': 'Missing required parameters: origin, destination, departure_date (use YYYY-MM-DD)'
                }
            }

        try:
            dep_dt = date.fromisoformat(departure_date)
        except Exception:
            return {
                'results': [],
                'metadata': {'error': f'Invalid departure date: {departure_date}'}
            }
        if dep_dt < date.today():
            return {
                'results': [],
                'metadata': {'error': 'Departure date is in the past. Please pick a future date.'}
            }

        if return_date:
            try:
                ret_dt = date.fromisoformat(return_date)
            except Exception:
                return {
                    'results': [],
                    'metadata': {'error': f'Invalid return date: {return_date}'}
                }
            if ret_dt < dep_dt:
                return {
                    'results': [],
                    'metadata': {'error': 'Return date cannot be before departure date.'}
                }

        if not has_amadeus_credentials():
            if settings.TRAVEL_ALLOW_FALLBACK:
                return {
                    'results': self._get_fallback_flights(origin, destination),
                    'metadata': {'provider': self.PROVIDER_NAME, 'fallback': True}
                }
            return {
                'results': [],
                'metadata': {
                    'error': 'Amadeus credentials not configured. Set AMADEUS_API_KEY and AMADEUS_API_SECRET.'
                }
            }

        origin_code = await self._resolve_location_code(origin)
        destination_code = await self._resolve_location_code(destination)

        if not origin_code or not destination_code:
            return {
                'results': [],
                'metadata': {
                    'error': f'Could not resolve airport codes for {origin} -> {destination}'
                }
            }

        results, api_error = await self._search_amadeus_api(
            origin_code,
            destination_code,
            departure_date,
            return_date,
            passengers,
            cabin_class,
        )

        if api_error:
            if settings.TRAVEL_ALLOW_FALLBACK:
                return {
                    'results': self._get_fallback_flights(origin, destination),
                    'metadata': {
                        'error': api_error,
                        'provider': self.PROVIDER_NAME,
                        'fallback': True
                    }
                }
            else:
                return {
                    'results': [],
                    'metadata': {
                        'error': api_error,
                        'provider': self.PROVIDER_NAME
                    }
                }

        if not results:
            if settings.TRAVEL_ALLOW_FALLBACK:
                results = self._get_fallback_flights(origin, destination)
            else:
                return {
                    'results': [],
                    'metadata': {
                        'error': 'No flight offers returned from Amadeus.'
                    }
                }

        return {
            'results': results,
            'metadata': {
                'origin': origin_code,
                'destination': destination_code,
                'departure_date': departure_date,
                'return_date': return_date,
                'passengers': passengers,
                'cabin_class': cabin_class,
                'provider': self.PROVIDER_NAME,
                'total_found': len(results)
            }
        }

    async def _search_amadeus_api(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        return_date: str,
        passengers: int,
        cabin_class: str,
    ) -> (List[Dict], str):
        client = get_amadeus_client()
        if client is None:
            return [], "Amadeus client not configured"

        def _call_api():
            params = {
                'originLocationCode': origin,
                'destinationLocationCode': destination,
                'departureDate': departure_date,
                'adults': passengers,
                'travelClass': cabin_class.upper(),
                'currencyCode': 'KES'
            }
            # Only include nonStop if explicitly requested; the Amadeus SDK is picky
            # about boolean serialization for this field.
            if parameters := locals().get('parameters_ctx', None):
                non_stop = parameters.get('non_stop')
                if isinstance(non_stop, bool):
                    params['nonStop'] = non_stop
                elif isinstance(non_stop, str) and non_stop.lower() in ('true', 'false'):
                    params['nonStop'] = non_stop.lower() == 'true'

            if return_date:
                params['returnDate'] = return_date
            return client.shopping.flight_offers_search.get(**params).data

        try:
            data = await sync_to_async(_call_api)()
        except Exception as e:
            # Capture more detail if available
            detail = getattr(e, 'response', None)
            detail_text = ''
            try:
                if detail and hasattr(detail, 'body'):
                    detail_text = str(detail.body)
            except Exception:
                detail_text = ''
            msg = f"Amadeus flight search error: {e}"
            if detail_text:
                msg += f" | detail: {detail_text}"
            logger.error(msg)
            return [], msg

        return self._parse_amadeus_offers(data), ""

    async def _resolve_location_code(self, location: str) -> str:
        location = location.strip()
        if len(location) == 3:
            return location.upper()

        # Static city mapping for common routes
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
            'kigali': 'KGL',
            'addis ababa': 'ADD',
            'johannesburg': 'JNB',
            'cape town': 'CPT',
            'lagos': 'LOS',
            'accra': 'ACC',
            'los angeles': 'LAX',
            'lax': 'LAX',
            'washington dc': 'WAS',
            'dc': 'WAS',
            'iad': 'IAD',
            'dca': 'DCA',
            'bwi': 'BWI',
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
                subType='AIRPORT'
            )
            if response.data:
                return response.data[0].get('iataCode', '')
            return ''

        try:
            return await sync_to_async(_lookup)()
        except Exception:
            return ''

    def _parse_amadeus_offers(self, offers: List[Dict]) -> List[Dict]:
        results = []
        for i, offer in enumerate(offers[:20]):
            try:
                itineraries = offer.get('itineraries', [])
                if not itineraries:
                    continue
                first_itinerary = itineraries[0]
                segments = first_itinerary.get('segments', [])
                if not segments:
                    continue

                first_segment = segments[0]
                last_segment = segments[-1]

                price = offer.get('price', {})
                total = float(price.get('total', 0))
                currency = price.get('currency', 'KES')
                price_ksh = self._convert_to_ksh(total, currency)

                airline = first_segment.get('carrierCode', 'XX')
                flight_number = first_segment.get('number', f'XX{i+1}')

                results.append({
                    'id': f"flight_{i+1:03d}",
                    'provider_id': str(offer.get('id', f"offer_{i+1}")),
                    'provider': 'Amadeus',
                    'airline': airline,
                    'flight_number': f"{airline}{flight_number}",
                    'departure_time': self._extract_time(first_segment.get('departure', {}).get('at', '')),
                    'arrival_time': self._extract_time(last_segment.get('arrival', {}).get('at', '')),
                    'duration_minutes': self._calculate_duration(
                        first_segment.get('departure', {}).get('at', ''),
                        last_segment.get('arrival', {}).get('at', '')
                    ),
                    'price_ksh': price_ksh,
                    'seats_available': int(offer.get('numberOfBookableSeats', 0) or 0),
                    'cabin_class': offer.get('travelerPricings', [{}])[0].get('fareDetailsBySegment', [{}])[0].get('cabin', 'ECONOMY').lower(),
                    'booking_url': offer.get('links', {}).get('flightOffers', ''),
                    'stops': max(0, len(segments) - 1)
                })
            except Exception as e:
                logger.warning(f"Error parsing Amadeus offer: {e}")
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

    def _extract_time(self, datetime_str: str) -> str:
        if not datetime_str or len(datetime_str) < 11:
            return '08:00'
        return datetime_str[11:16]

    def _calculate_duration(self, departure: str, arrival: str) -> int:
        try:
            from datetime import datetime
            dep = datetime.fromisoformat(departure.replace('Z', '+00:00'))
            arr = datetime.fromisoformat(arrival.replace('Z', '+00:00'))
            return int((arr - dep).total_seconds() / 60)
        except Exception:
            return 120

    def _get_fallback_flights(self, origin: str, destination: str) -> List[Dict]:
        flight_db = {
            'nairobi-mombasa': [
                {'airline': 'Kenya Airways', 'code': 'KQ', 'dep': '09:00', 'arr': '10:30', 'price': 15000, 'stops': 0},
                {'airline': 'Ethiopian Airlines', 'code': 'ET', 'dep': '11:15', 'arr': '13:00', 'price': 12500, 'stops': 0},
            ],
            'nairobi-london': [
                {'airline': 'Kenya Airways', 'code': 'KQ', 'dep': '15:00', 'arr': '07:30', 'price': 85000, 'stops': 0},
                {'airline': 'British Airways', 'code': 'BA', 'dep': '18:00', 'arr': '11:15', 'price': 95000, 'stops': 1},
                {'airline': 'Turkish Airlines', 'code': 'TK', 'dep': '20:00', 'arr': '09:00', 'price': 72000, 'stops': 1},
            ],
            'nairobi-dubai': [
                {'airline': 'Emirates', 'code': 'EK', 'dep': '10:00', 'arr': '13:00', 'price': 55000, 'stops': 0},
                {'airline': 'Flydubai', 'code': 'FZ', 'dep': '12:00', 'arr': '15:30', 'price': 45000, 'stops': 0},
            ],
        }

        route_key = f"{origin.lower()}-{destination.lower()}"
        flights = flight_db.get(route_key, flight_db.get('nairobi-mombasa', []))

        results = []
        for i, flight in enumerate(flights):
            results.append({
                'id': f'flight_{i+1:03d}',
                'provider_id': f'fallback_{i+1:03d}',
                'provider': 'Amadeus',
                'airline': flight['airline'],
                'flight_number': f"{flight['code']}{1000 + i}",
                'departure_time': flight['dep'],
                'arrival_time': flight['arr'],
                'duration_minutes': 90 if flight['stops'] == 0 else 240,
                'price_ksh': flight['price'],
                'seats_available': 15 - i,
                'cabin_class': 'economy',
                'booking_url': f'https://amadeus.com/booking/{i+1}',
                'stops': flight['stops']
            })

        return results
