"""
Travel Flights Connector
Searches for flights using Duffel API
"""
import logging
from typing import Dict, Any
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
        Fetch flights from Duffel API
        
        Parameters:
            origin: 'NRB' (Nairobi)
            destination: 'MBA' (Mombasa) or 'LHR' (London)
            departure_date: 2025-12-25
            return_date: 2025-12-31 (optional)
            passengers: 2
        """
        origin = parameters.get('origin')
        destination = parameters.get('destination')
        departure_date = parameters.get('departure_date')
        passengers = parameters.get('passengers', 1)
        
        if not all([origin, destination, departure_date]):
            return {
                'results': [],
                'metadata': {
                    'error': 'Missing required parameters: origin, destination, departure_date'
                }
            }
        
        # TODO: Implement Duffel API call
        # For MVP: Return mock data
        logger.info(f"Searching flights: {origin} → {destination} on {departure_date}")
        
        mock_results = [
            {
                'id': 'flight_001',
                'provider': 'Duffel',
                'airline': 'Kenya Airways',
                'flight_number': 'KQ101',
                'departure_time': '09:00',
                'arrival_time': '10:30',
                'duration_minutes': 90,
                'price_ksh': 15000,
                'seats_available': 10,
                'cabin_class': 'Economy',
                'booking_url': 'https://duffel.com/mock-booking',
                'stops': 0
            },
            {
                'id': 'flight_002',
                'provider': 'Duffel',
                'airline': 'Ethiopian Airlines',
                'flight_number': 'ET500',
                'departure_time': '11:15',
                'arrival_time': '13:00',
                'duration_minutes': 105,
                'price_ksh': 12500,
                'seats_available': 5,
                'cabin_class': 'Economy',
                'booking_url': 'https://duffel.com/mock-booking-2',
                'stops': 0
            }
        ]
        
        return {
            'results': mock_results,
            'metadata': {
                'origin': origin,
                'destination': destination,
                'departure_date': departure_date,
                'passengers': passengers,
                'provider': self.PROVIDER_NAME
            }
        }
