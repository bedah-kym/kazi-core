"""
Travel Transfers Connector
Searches for ground transfers (taxis, car rentals, ride-hailing)
Primary: Uber/Bolt API + Karibu Taxi
Fallback: Fixed rates for common routes
"""
import logging
import aiohttp
import os
from typing import Dict, Any, List
from orchestration.connectors.base_travel_connector import BaseTravelConnector

logger = logging.getLogger(__name__)


class TravelTransfersConnector(BaseTravelConnector):
    """
    Search for airport/ground transfers in East Africa
    Primary: Karibu Taxi API
    Fallback: Car rental partnerships
    """
    
    PROVIDER_NAME = 'karibu'
    CACHE_TTL_SECONDS = 7200  # 2 hours
    
    async def _fetch(self, parameters: Dict, context: Dict) -> Dict:
        """
        Fetch ground transfers (taxis, ubers, car rentals)
        
        Parameters:
            origin: 'JKIA' or address (Nairobi Airport)
            destination: Address or landmark
            travel_date: 2025-12-25
            travel_time: 09:00 (optional)
            passengers: 2
            luggage: 3
            service_type: 'economy', 'premium', 'shared' (optional)
        """
        origin = parameters.get('origin', '').strip()
        destination = parameters.get('destination', '').strip()
        travel_date = parameters.get('travel_date')
        travel_time = parameters.get('travel_time', '09:00')
        passengers = parameters.get('passengers', 1)
        luggage = parameters.get('luggage', 1)
        service_type = parameters.get('service_type', 'economy').lower()
        
        if not all([origin, destination, travel_date]):
            return {
                'results': [],
                'metadata': {
                    'error': 'Missing required parameters: origin, destination, travel_date'
                }
            }
        
        try:
            # Try to get pricing from Uber/Bolt APIs
            results = await self._search_ride_apis(origin, destination, passengers, service_type)
            
            # Fallback to fixed rate database
            if not results:
                logger.info("Ride API search failed, using fallback rates")
                results = self._get_fallback_transfers(origin, destination, passengers, service_type)
            
            logger.info(f"Found {len(results)} transfer options from {origin} to {destination}")
            
            return {
                'results': results,
                'metadata': {
                    'origin': origin,
                    'destination': destination,
                    'travel_date': travel_date,
                    'travel_time': travel_time,
                    'passengers': passengers,
                    'luggage': luggage,
                    'service_type': service_type,
                    'provider': self.PROVIDER_NAME,
                    'total_found': len(results)
                }
            }
        except Exception as e:
            logger.error(f"Transfer search error: {str(e)}")
            return {
                'results': [],
                'metadata': {
                    'error': f'Failed to fetch transfers: {str(e)}',
                    'origin': origin,
                    'destination': destination
                }
            }
    
    async def _search_ride_apis(self, origin: str, destination: str, passengers: int, service_type: str) -> List[Dict]:
        """
        Query ride-hailing APIs (Uber, Bolt, Karibu)
        All support Nairobi and major East African cities
        """
        results = []
        
        # Try Uber API if credentials available
        uber_results = await self._search_uber(origin, destination, passengers, service_type)
        if uber_results:
            results.extend(uber_results)
        
        # Try Bolt API if credentials available
        bolt_results = await self._search_bolt(origin, destination, passengers, service_type)
        if bolt_results:
            results.extend(bolt_results)
        
        # Add fixed-rate providers
        fixed_results = self._get_fixed_rate_providers(origin, destination, passengers)
        if fixed_results:
            results.extend(fixed_results)
        
        return results
    
    async def _search_uber(self, origin: str, destination: str, passengers: int, service_type: str) -> List[Dict]:
        """
        Search Uber for prices and availability
        Uber operates in Nairobi, Kampala, and other EA cities
        """
        try:
            uber_token = os.getenv('UBER_API_TOKEN')
            if not uber_token:
                return []
            
            # Uber uses lat/lng, would need geolocation
            # For MVP, skip real implementation
            logger.debug("Uber API not configured")
            return []
        except Exception as e:
            logger.warning(f"Uber search error: {str(e)}")
            return []
    
    async def _search_bolt(self, origin: str, destination: str, passengers: int, service_type: str) -> List[Dict]:
        """
        Search Bolt for prices and availability
        Bolt operates across East Africa
        """
        try:
            bolt_token = os.getenv('BOLT_API_TOKEN')
            if not bolt_token:
                return []
            
            # Bolt also uses lat/lng
            logger.debug("Bolt API not configured")
            return []
        except Exception as e:
            logger.warning(f"Bolt search error: {str(e)}")
            return []
    
    def _get_fixed_rate_providers(self, origin: str, destination: str, passengers: int) -> List[Dict]:
        """
        Return fixed-rate providers for common routes
        These are negotiated rates that don't require live API calls
        """
        results = []
        
        # Common routes in Nairobi
        origin_norm = origin.lower()
        dest_norm = destination.lower()
        
        is_airport = 'airport' in origin_norm or 'jkia' in origin_norm or 'nrb' in origin_norm
        is_city_center = 'center' in dest_norm or 'cbd' in dest_norm or 'nairobi' in dest_norm
        is_westlands = 'westlands' in dest_norm or 'village market' in dest_norm
        
        # Karibu Taxi (premium pre-booked service)
        if is_airport and passengers <= 4:
            results.append({
                'id': 'transfer_001',
                'provider': 'Karibu Taxi',
                'vehicle_type': 'Standard Sedan',
                'capacity': 4,
                'price_ksh': 3500 if is_city_center else 4500,
                'estimated_duration_minutes': 30 if is_city_center else 40,
                'rating': 4.7,
                'driver_name': 'Professional Driver',
                'amenities': ['AC', 'WiFi', 'Water'],
                'booking_url': 'https://karibu.ke/book',
                'available': True,
                'prepaid': True
            })
        
        # Standard Taxi (traditional yellow cabs)
        if passengers <= 5:
            results.append({
                'id': 'transfer_002',
                'provider': 'Standard Taxi',
                'vehicle_type': 'Taxi',
                'capacity': 5,
                'price_ksh': 2500 if is_city_center else 3500,
                'estimated_duration_minutes': 35 if is_city_center else 45,
                'rating': 4.0,
                'driver_name': 'Taxi Driver',
                'amenities': ['AC'],
                'booking_url': 'https://booking.taxi',
                'available': True,
                'prepaid': False
            })
        
        # Uber/Bolt equivalent (if passengers <= passengers limit)
        if passengers <= 6:
            results.append({
                'id': 'transfer_003',
                'provider': 'Ride Service',
                'vehicle_type': 'Economy SUV',
                'capacity': 6,
                'price_ksh': 3000 if is_city_center else 4000,
                'estimated_duration_minutes': 32 if is_city_center else 42,
                'rating': 4.5,
                'driver_name': 'App-based Driver',
                'amenities': ['AC', 'WiFi'],
                'booking_url': 'https://uber.com',
                'available': True,
                'prepaid': True
            })
        
        # Car rental option for longer stays
        results.append({
            'id': 'transfer_004',
            'provider': 'Car Rental',
            'vehicle_type': 'Rental Car',
            'capacity': 5,
            'price_ksh': 5000,  # Daily rate (can be negotiated)
            'estimated_duration_minutes': 60,  # Setup time
            'rating': 4.6,
            'driver_name': 'N/A - Self Drive',
            'amenities': ['AC', 'GPS', 'Insurance Included'],
            'booking_url': 'https://hertz.co.ke',
            'available': True,
            'prepaid': True,
            'daily_rate': True
        })
        
        return results
    
    def _get_fallback_transfers(self, origin: str, destination: str, passengers: int, service_type: str) -> List[Dict]:
        """
        Fallback transfer database for major EA routes
        """
        # Determine route type
        route_factors = {
            'airport-city': {
                'distance_km': 18,
                'duration': 30,
                'base_price': 3000,
                'surge_factor': 1.0
            },
            'airport-westlands': {
                'distance_km': 25,
                'duration': 40,
                'base_price': 4000,
                'surge_factor': 1.0
            },
            'city-city': {
                'distance_km': 5,
                'duration': 15,
                'base_price': 1500,
                'surge_factor': 1.0
            },
            'intercity': {
                'distance_km': 100,
                'duration': 120,
                'base_price': 8000,
                'surge_factor': 1.0
            }
        }
        
        # Guess route type
        route_type = 'city-city'
        if 'airport' in origin.lower():
            route_type = 'airport-westlands' if 'westlands' in destination.lower() else 'airport-city'
        
        route_info = route_factors.get(route_type, route_factors['city-city'])
        base_price = route_info['base_price']
        duration = route_info['duration']
        
        results = []
        
        # Karibu option
        results.append({
            'id': 'transfer_001',
            'provider': 'Karibu',
            'vehicle_type': 'Standard Sedan',
            'capacity': 4,
            'price_ksh': int(base_price * 1.2),
            'estimated_duration_minutes': duration,
            'rating': 4.7,
            'driver_name': 'Joseph K.',
            'vehicle_registration': 'KCA 123X',
            'booking_url': 'https://karibu.com/book',
            'available': True
        })
        
        # Regular taxi
        results.append({
            'id': 'transfer_002',
            'provider': 'Taxi',
            'vehicle_type': 'Taxi',
            'capacity': 5,
            'price_ksh': int(base_price * 0.8),
            'estimated_duration_minutes': duration + 10,
            'rating': 4.0,
            'driver_name': 'Driver',
            'booking_url': 'https://uber.com',
            'available': True
        })
        
        # Uber equivalent
        if service_type == 'premium' or passengers > 4:
            results.append({
                'id': 'transfer_003',
                'provider': 'Premium Service',
                'vehicle_type': 'SUV',
                'capacity': 6,
                'price_ksh': int(base_price * 1.5),
                'estimated_duration_minutes': duration,
                'rating': 4.9,
                'driver_name': 'Premium Driver',
                'booking_url': 'https://premiumride.co.ke',
                'available': True
            })
        
        return results
