"""
Travel Transfers Connector
Searches for ground transfers (taxis, car rentals) using Karibu Taxi API
"""
import logging
from typing import Dict, Any
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
        Fetch transfers from Karibu and fallback providers
        
        Parameters:
            origin: Nairobi Airport (JKIA)
            destination: Your Hotel
            travel_date: 2025-12-25
            passengers: 2
            luggage: 3
        """
        origin = parameters.get('origin')
        destination = parameters.get('destination')
        travel_date = parameters.get('travel_date')
        passengers = parameters.get('passengers', 1)
        luggage = parameters.get('luggage', 1)
        
        if not all([origin, destination, travel_date]):
            return {
                'results': [],
                'metadata': {
                    'error': 'Missing required parameters: origin, destination, travel_date'
                }
            }
        
        # TODO: Implement Karibu Taxi API call
        # For MVP: Return mock data
        logger.info(f"Searching transfers: {origin} → {destination} on {travel_date}")
        
        mock_results = [
            {
                'id': 'transfer_001',
                'provider': 'Karibu',
                'vehicle_type': 'Standard Taxi',
                'capacity': 4,
                'price_ksh': 3500,
                'estimated_duration_minutes': 25,
                'rating': 4.7,
                'driver_name': 'Joseph K.',
                'vehicle_registration': 'KCA 123X',
                'booking_url': 'https://karibu.com/mock-booking',
                'available': True
            },
            {
                'id': 'transfer_002',
                'provider': 'Karibu',
                'vehicle_type': 'SUV Rental',
                'capacity': 6,
                'price_ksh': 7500,
                'estimated_duration_minutes': 25,
                'rating': 4.9,
                'driver_name': 'Grace M.',
                'vehicle_registration': 'KBX 456Y',
                'booking_url': 'https://karibu.com/mock-booking-2',
                'available': True
            }
        ]
        
        return {
            'results': mock_results,
            'metadata': {
                'origin': origin,
                'destination': destination,
                'travel_date': travel_date,
                'passengers': passengers,
                'luggage': luggage,
                'provider': self.PROVIDER_NAME
            }
        }
