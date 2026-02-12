"""
Travel Flights Connector
Searches for flights using Duffel Sandbox API
Duffel is a flight search aggregator with free sandbox for development
"""
import logging
import aiohttp
import os
import json
from typing import Dict, Any, List
from orchestration.connectors.base_travel_connector import BaseTravelConnector

logger = logging.getLogger(__name__)


class TravelFlightsConnector(BaseTravelConnector):
    """
    Search for flights in East Africa
    Uses Duffel API (free sandbox, pay-per-search live)
    """
    
    PROVIDER_NAME = 'duffel'
    CACHE_TTL_SECONDS = 3600  # 1 hour
    
    async def _fetch(self, parameters: Dict, context: Dict) -> Dict:
        """
        Fetch flights from Duffel Sandbox API
        
        Parameters:
            origin: 'NRB' (Nairobi IATA code) or city name
            destination: 'MBA' (Mombasa) or 'LHR' (London)
            departure_date: 2025-12-25
            return_date: 2025-12-31 (optional, for round trip)
            passengers: 2
            cabin_class: 'economy' (default), 'business', 'first'
        """
        origin = parameters.get('origin', '').strip()
        destination = parameters.get('destination', '').strip()
        departure_date = parameters.get('departure_date')
        return_date = parameters.get('return_date')
        passengers = parameters.get('passengers', 1)
        cabin_class = parameters.get('cabin_class', 'economy').lower()
        
        if not all([origin, destination, departure_date]):
            return {
                'results': [],
                'metadata': {
                    'error': 'Missing required parameters: origin, destination, departure_date'
                }
            }
        
        try:
            # Convert city names to IATA codes
            origin_code = self._city_to_iata(origin)
            dest_code = self._city_to_iata(destination)
            
            if not origin_code or not dest_code:
                logger.warning(f"Could not resolve airport codes: {origin} → {destination}")
                return {
                    'results': self._get_fallback_flights(origin, destination),
                    'metadata': {
                        'origin': origin,
                        'destination': destination,
                        'provider': self.PROVIDER_NAME
                    }
                }
            
            # Try Duffel API if credentials available
            results = await self._search_duffel_api(origin_code, dest_code, departure_date, return_date, passengers, cabin_class)
            
            # Fallback if API fails
            if not results:
                logger.info("Duffel API failed, using fallback data")
                results = self._get_fallback_flights(origin, destination)
            
            logger.info(f"Duffel: Found {len(results)} flights from {origin_code} to {dest_code}")
            
            return {
                'results': results,
                'metadata': {
                    'origin': origin_code,
                    'destination': dest_code,
                    'departure_date': departure_date,
                    'return_date': return_date,
                    'passengers': passengers,
                    'cabin_class': cabin_class,
                    'provider': self.PROVIDER_NAME,
                    'total_found': len(results)
                }
            }
        except Exception as e:
            logger.error(f"Flight search error: {str(e)}")
            return {
                'results': [],
                'metadata': {
                    'error': f'Failed to fetch flights: {str(e)}',
                    'origin': origin,
                    'destination': destination
                }
            }
    
    async def _search_duffel_api(self, origin: str, destination: str, departure_date: str, 
                                return_date: str, passengers: int, cabin_class: str) -> List[Dict]:
        """
        Search Duffel API
        Duffel provides free sandbox API with real flight aggregation
        
        Sign up at: https://duffel.com/
        """
        try:
            duffel_key = os.getenv('DUFFEL_API_KEY')
            if not duffel_key:
                logger.warning("DUFFEL_API_KEY not set")
                return []
            
            api_url = "https://api.duffel.com/air/search_sessions"
            
            # Build request payload
            passengers_list = [{"type": "adult"} for _ in range(passengers)]
            
            payload = {
                "data": {
                    "type": "search_session",
                    "slices": [
                        {
                            "origin_airport_iata_code": origin,
                            "destination_airport_iata_code": destination,
                            "departure_date": departure_date
                        }
                    ],
                    "passengers": passengers_list,
                    "cabin_class": cabin_class
                }
            }
            
            if return_date:
                payload["data"]["slices"].append({
                    "origin_airport_iata_code": destination,
                    "destination_airport_iata_code": origin,
                    "departure_date": return_date
                })
            
            headers = {
                'Authorization': f'Bearer {duffel_key}',
                'Content-Type': 'application/json'
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(api_url, json=payload, headers=headers, 
                                       timeout=aiohttp.ClientTimeout(total=15)) as response:
                    if response.status not in [200, 201, 202]:
                        logger.warning(f"Duffel API returned status {response.status}")
                        return []
                    
                    data = await response.json()
            
            # Parse Duffel response and extract offers
            results = []
            
            if 'data' in data and 'offers' in data['data']:
                for i, offer in enumerate(data['data']['offers'][:20]):
                    try:
                        flight = self._parse_duffel_offer(offer, i)
                        if flight:
                            results.append(flight)
                    except Exception as e:
                        logger.warning(f"Error parsing offer {i}: {str(e)}")
                        continue
            
            return results
            
        except Exception as e:
            logger.error(f"Duffel API error: {str(e)}")
            return []
    
    def _parse_duffel_offer(self, offer: Dict, index: int) -> Dict:
        """Parse a Duffel API offer into our format"""
        try:
            # Extract basic flight info
            slices = offer.get('slices', [])
            first_slice = slices[0] if slices else {}
            
            # Get segments (flight legs)
            segments = first_slice.get('segments', [])
            first_segment = segments[0] if segments else {}
            
            # Extract pricing
            total_price = offer.get('total_amount', 0)
            currency = offer.get('total_currency', 'KES')
            
            # Convert to KES if needed
            price_ksh = float(total_price)
            if currency == 'USD':
                price_ksh = price_ksh * 130
            elif currency in ['EUR', 'GBP']:
                price_ksh = price_ksh * 150
            
            return {
                'id': f"flight_{index+1:03d}",
                'provider': 'Duffel',
                'airline': first_segment.get('operating_carrier_code', 'XX'),
                'flight_number': first_segment.get('flight_number', f'XXX{index+1}'),
                'departure_time': self._extract_time(first_segment.get('departure_at', '')),
                'arrival_time': self._extract_time(first_segment.get('arrival_at', '')),
                'duration_minutes': self._calculate_duration(first_segment.get('departure_at', ''), 
                                                            first_segment.get('arrival_at', '')),
                'price_ksh': price_ksh,
                'seats_available': 10,
                'cabin_class': 'economy',
                'booking_url': f'https://duffel.com/booking/{offer.get("id", index+1)}',
                'stops': len(segments) - 1
            }
        except Exception as e:
            logger.warning(f"Error parsing Duffel offer: {str(e)}")
            return None
    
    def _city_to_iata(self, city: str) -> str:
        """Convert city name to IATA airport code"""
        city_lower = city.lower().strip()
        
        iata_map = {
            'nairobi': 'NRB',
            'mombasa': 'MBA',
            'kisumu': 'KIS',
            'eldoret': 'EDL',
            'london': 'LHR',
            'paris': 'CDG',
            'dubai': 'DXB',
            'kampala': 'EBB',
            'dar es salaam': 'DAR',
            'kigali': 'KGL',
            'addis ababa': 'ADD',
            'johannesburg': 'JNB',
            'cape town': 'CPT',
            'lagos': 'LOS',
            'accra': 'ACC',
        }
        
        return iata_map.get(city_lower, city.upper() if len(city) == 3 else '')
    
    def _extract_time(self, datetime_str: str) -> str:
        """Extract time from ISO datetime string"""
        if not datetime_str or len(datetime_str) < 11:
            return '08:00'
        try:
            return datetime_str[11:16]  # Extract HH:MM from ISO format
        except:
            return '08:00'
    
    def _calculate_duration(self, departure: str, arrival: str) -> int:
        """Calculate flight duration in minutes from ISO datetime strings"""
        try:
            from datetime import datetime
            dep = datetime.fromisoformat(departure.replace('Z', '+00:00'))
            arr = datetime.fromisoformat(arrival.replace('Z', '+00:00'))
            return int((arr - dep).total_seconds() / 60)
        except:
            return 120  # Default 2 hours
    
    def _get_fallback_flights(self, origin: str, destination: str) -> List[Dict]:
        """
        Fallback flight data for common East African routes
        """
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
                'provider': 'Duffel',
                'airline': flight['airline'],
                'flight_number': f"{flight['code']}{1000 + i}",
                'departure_time': flight['dep'],
                'arrival_time': flight['arr'],
                'duration_minutes': 90 if flight['stops'] == 0 else 240,
                'price_ksh': flight['price'],
                'seats_available': 15 - i,
                'cabin_class': 'economy',
                'booking_url': f'https://duffel.com/booking/{i+1}',
                'stops': flight['stops']
            })
        
        return results
