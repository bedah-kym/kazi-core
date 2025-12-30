"""
Travel Hotels Connector
Searches for hotels using Booking.com Affiliate API
"""
import logging
from typing import Dict, Any
from orchestration.connectors.base_travel_connector import BaseTravelConnector

logger = logging.getLogger(__name__)


class TravelHotelsConnector(BaseTravelConnector):
    """
    Search for hotels in East Africa
    Uses Booking.com Affiliate (free, commission-based)
    """
    
    PROVIDER_NAME = 'booking'
    CACHE_TTL_SECONDS = 3600  # 1 hour
    
    async def _fetch(self, parameters: Dict, context: Dict) -> Dict:
        """
        Fetch hotels from Booking.com
        
        Parameters:
            location: Nairobi, Mombasa, etc.
            check_in_date: 2025-12-25
            check_out_date: 2025-12-28
            guests: 2
            budget_ksh: 50000 (optional)
        """
        location = parameters.get('location')
        check_in = parameters.get('check_in_date')
        check_out = parameters.get('check_out_date')
        guests = parameters.get('guests', 1)
        
        if not all([location, check_in, check_out]):
            return {
                'results': [],
                'metadata': {
                    'error': 'Missing required parameters: location, check_in_date, check_out_date'
                }
            }
        
        # TODO: Implement Booking.com Affiliate API call
        # For MVP: Return mock data
        logger.info(f"Searching hotels: {location} from {check_in} to {check_out}")
        
        mock_results = [
            {
                'id': 'hotel_001',
                'provider': 'Booking.com',
                'name': 'Safari Park Hotel Nairobi',
                'location': location,
                'rating': 4.6,
                'reviews': 1250,
                'price_ksh': 8500,
                'original_price_ksh': 10000,
                'discount_percent': 15,
                'amenities': ['Pool', 'Free WiFi', 'Restaurant', 'Parking'],
                'room_type': 'Double Room',
                'nights': 3,
                'booking_url': 'https://booking.com/affiliate/mock-001',
                'image_url': 'https://via.placeholder.com/300x200'
            },
            {
                'id': 'hotel_002',
                'provider': 'Booking.com',
                'name': '5-Star Serena Hotel',
                'location': location,
                'rating': 4.8,
                'reviews': 980,
                'price_ksh': 25000,
                'original_price_ksh': 30000,
                'discount_percent': 17,
                'amenities': ['Pool', 'Spa', 'Free WiFi', 'Restaurant', 'Business Center'],
                'room_type': 'Suite',
                'nights': 3,
                'booking_url': 'https://booking.com/affiliate/mock-002',
                'image_url': 'https://via.placeholder.com/300x200'
            }
        ]
        
        return {
            'results': mock_results,
            'metadata': {
                'location': location,
                'check_in': check_in,
                'check_out': check_out,
                'guests': guests,
                'provider': self.PROVIDER_NAME,
                'affiliate_enabled': True,
                'commission_percent': 25
            }
        }
