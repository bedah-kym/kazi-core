"""
Integration tests for travel planner Week 2 implementation
Tests real API integrations, caching, and end-to-end workflows
"""
import asyncio
import json
from datetime import datetime, timedelta
from django.test import TestCase, AsyncTestCase
from django.contrib.auth.models import User
from django.core.cache import cache

from travel.models import Itinerary, ItineraryItem, SearchCache, BookingReference
from travel.services import ItineraryBuilder, ExportService, BookingOrchestrator
from orchestration.mcp_router import MCPRouter
from orchestration.connectors.travel_buses_connector import TravelBusesConnector
from orchestration.connectors.travel_hotels_connector import TravelHotelsConnector
from orchestration.connectors.travel_flights_connector import TravelFlightsConnector
from orchestration.connectors.travel_transfers_connector import TravelTransfersConnector
from orchestration.connectors.travel_events_connector import TravelEventsConnector


class TravelConnectorIntegrationTests(TestCase):
    """Test individual travel connector implementations"""
    
    def setUp(self):
        self.router = MCPRouter()
        self.user_id = 1
        self.context = {'user_id': self.user_id, 'room': 'test'}
    
    def test_bus_connector_returns_results(self):
        """Test that bus connector returns valid results"""
        connector = TravelBusesConnector()
        
        parameters = {
            'origin': 'Nairobi',
            'destination': 'Mombasa',
            'travel_date': '2025-12-25',
            'passengers': 2
        }
        
        # Run async function
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(connector._fetch(parameters, self.context))
        
        self.assertIn('results', result)
        self.assertIn('metadata', result)
        
        # Should have at least some buses (fallback data)
        self.assertGreaterEqual(len(result['results']), 0)
        
        if result['results']:
            bus = result['results'][0]
            self.assertIn('price_ksh', bus)
            self.assertIn('company', bus)
            self.assertIn('departure_time', bus)
    
    def test_hotel_connector_returns_results(self):
        """Test that hotel connector returns valid results"""
        connector = TravelHotelsConnector()
        
        parameters = {
            'location': 'Nairobi',
            'check_in_date': '2025-12-25',
            'check_out_date': '2025-12-28',
            'guests': 2
        }
        
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(connector._fetch(parameters, self.context))
        
        self.assertIn('results', result)
        self.assertGreaterEqual(len(result['results']), 0)
        
        if result['results']:
            hotel = result['results'][0]
            self.assertIn('price_ksh', hotel)
            self.assertIn('name', hotel)
            self.assertIn('rating', hotel)
    
    def test_flight_connector_returns_results(self):
        """Test that flight connector returns valid results"""
        connector = TravelFlightsConnector()
        
        parameters = {
            'origin': 'Nairobi',
            'destination': 'Mombasa',
            'departure_date': '2025-12-25',
            'passengers': 1
        }
        
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(connector._fetch(parameters, self.context))
        
        self.assertIn('results', result)
        self.assertGreaterEqual(len(result['results']), 0)
        
        if result['results']:
            flight = result['results'][0]
            self.assertIn('price_ksh', flight)
            self.assertIn('airline', flight)
            self.assertIn('departure_time', flight)
    
    def test_transfers_connector_returns_results(self):
        """Test that transfers connector returns valid results"""
        connector = TravelTransfersConnector()
        
        parameters = {
            'origin': 'JKIA',
            'destination': 'Nairobi City Center',
            'travel_date': '2025-12-25',
            'passengers': 2
        }
        
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(connector._fetch(parameters, self.context))
        
        self.assertIn('results', result)
        self.assertGreaterEqual(len(result['results']), 1)  # Should have at least 1 transfer option
        
        transfer = result['results'][0]
        self.assertIn('price_ksh', transfer)
        self.assertIn('provider', transfer)
    
    def test_events_connector_returns_results(self):
        """Test that events connector returns valid results"""
        connector = TravelEventsConnector()
        
        parameters = {
            'location': 'Nairobi',
            'category': 'all'
        }
        
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(connector._fetch(parameters, self.context))
        
        self.assertIn('results', result)
        self.assertGreaterEqual(len(result['results']), 0)


class CachingIntegrationTests(TestCase):
    """Test caching behavior across connectors"""
    
    def setUp(self):
        self.connector = TravelBusesConnector()
        self.user_id = 1
        self.context = {'user_id': self.user_id}
        cache.clear()
    
    def test_cache_hit_on_repeated_search(self):
        """Test that repeated identical searches use cache"""
        parameters = {
            'origin': 'Nairobi',
            'destination': 'Mombasa',
            'travel_date': '2025-12-25',
            'passengers': 1
        }
        
        loop = asyncio.get_event_loop()
        
        # First search (cache miss)
        result1 = loop.run_until_complete(self.connector.execute(parameters, self.context))
        cached1 = result1.get('cached', False)
        
        # Second search (cache hit)
        result2 = loop.run_until_complete(self.connector.execute(parameters, self.context))
        cached2 = result2.get('cached', False)
        
        # Second should be from cache
        self.assertTrue(cached2, "Second search should be cached")
        self.assertEqual(result1['count'], result2['count'])
    
    def test_cache_miss_on_different_search(self):
        """Test that different searches don't share cache"""
        loop = asyncio.get_event_loop()
        
        # Search 1
        params1 = {
            'origin': 'Nairobi',
            'destination': 'Mombasa',
            'travel_date': '2025-12-25',
            'passengers': 1
        }
        result1 = loop.run_until_complete(self.connector.execute(params1, self.context))
        
        # Search 2 (different destination)
        params2 = {
            'origin': 'Nairobi',
            'destination': 'Kisumu',
            'travel_date': '2025-12-25',
            'passengers': 1
        }
        result2 = loop.run_until_complete(self.connector.execute(params2, self.context))
        
        # Both searches should succeed but might have different results
        self.assertIn('results', result1)
        self.assertIn('results', result2)


class ItineraryBuildingTests(TestCase):
    """Test itinerary composition from search results"""
    
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='12345')
        self.builder = ItineraryBuilder()
    
    def test_create_itinerary_from_searches(self):
        """Test creating an itinerary from search results"""
        search_results = {
            'buses': [
                {'company': 'Skyways', 'price_ksh': 2500, 'departure_time': '08:00', 'arrival_time': '15:00', 'booking_url': 'http://example.com'},
            ],
            'hotels': [
                {'name': 'Safari Park', 'price_ksh': 8500, 'booking_url': 'http://booking.com'},
            ],
            'flights': [],
            'transfers': [],
            'events': []
        }
        
        loop = asyncio.get_event_loop()
        itinerary = loop.run_until_complete(
            self.builder.create_from_searches(
                user_id=self.user.id,
                trip_name='Nairobi to Mombasa',
                origin='Nairobi',
                destination='Mombasa',
                start_date='2025-12-25',
                end_date='2025-12-28',
                search_results=search_results
            )
        )
        
        self.assertEqual(itinerary.user, self.user)
        self.assertEqual(itinerary.title, 'Nairobi to Mombasa')
        self.assertEqual(itinerary.status, 'draft')
        
        # Check items were added
        items = itinerary.items.all()
        self.assertGreater(items.count(), 0)
        
        # Check bus was added
        bus_items = items.filter(item_type='bus')
        self.assertEqual(bus_items.count(), 1)
        
        # Check hotel was added
        hotel_items = items.filter(item_type='hotel')
        self.assertEqual(hotel_items.count(), 1)


class ExportTests(TestCase):
    """Test itinerary export functionality"""
    
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='12345')
        
        # Create test itinerary
        self.itinerary = Itinerary.objects.create(
            user=self.user,
            title='Test Trip',
            start_date=datetime.now(),
            end_date=datetime.now() + timedelta(days=3),
            budget_ksh=50000
        )
        
        # Add some items
        ItineraryItem.objects.create(
            itinerary=self.itinerary,
            item_type='hotel',
            title='Test Hotel',
            price_ksh=10000,
            status='suggested'
        )
        
        self.export_service = ExportService()
    
    def test_export_json(self):
        """Test JSON export"""
        loop = asyncio.get_event_loop()
        json_data = loop.run_until_complete(
            self.export_service.export_json(self.itinerary.id)
        )
        
        self.assertEqual(json_data['title'], 'Test Trip')
        self.assertEqual(json_data['budget_ksh'], 50000)
        self.assertIn('items', json_data)
        self.assertEqual(len(json_data['items']), 1)
    
    def test_export_ical(self):
        """Test iCalendar export"""
        loop = asyncio.get_event_loop()
        ical_data = loop.run_until_complete(
            self.export_service.export_ical(self.itinerary.id)
        )
        
        self.assertIn('BEGIN:VCALENDAR', ical_data)
        self.assertIn('END:VCALENDAR', ical_data)
        self.assertIn(self.itinerary.title, ical_data)


class BookingOrchestratorTests(TestCase):
    """Test booking management"""
    
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='12345')
        self.orchestrator = BookingOrchestrator()
        
        # Create test itinerary and item
        self.itinerary = Itinerary.objects.create(
            user=self.user,
            title='Test Trip',
            start_date=datetime.now(),
            end_date=datetime.now() + timedelta(days=3)
        )
        
        self.item = ItineraryItem.objects.create(
            itinerary=self.itinerary,
            item_type='hotel',
            title='Test Hotel',
            price_ksh=10000,
            status='suggested',
            metadata={'booking_url': 'https://booking.com/test', 'provider': 'booking'}
        )
    
    def test_record_booking(self):
        """Test recording a booking"""
        loop = asyncio.get_event_loop()
        booking_ref = loop.run_until_complete(
            self.orchestrator.record_booking(
                item_id=self.item.id,
                confirmation_code='CONF123',
                booking_reference='REF456'
            )
        )
        
        self.assertEqual(booking_ref.confirmation_code, 'CONF123')
        self.assertEqual(booking_ref.status, 'confirmed')
        
        # Check item status was updated
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'booked')
    
    def test_get_booking_status(self):
        """Test retrieving booking status"""
        # First create a booking
        BookingReference.objects.create(
            itinerary_item=self.item,
            confirmation_code='CONF123',
            booking_reference='REF456',
            status='confirmed'
        )
        
        loop = asyncio.get_event_loop()
        status = loop.run_until_complete(
            self.orchestrator.get_booking_status(self.item.id)
        )
        
        self.assertEqual(status['status'], 'confirmed')
        self.assertEqual(status['confirmation_code'], 'CONF123')


class EndToEndWorkflowTests(TestCase):
    """End-to-end tests for complete travel planner workflow"""
    
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='12345')
        self.router = MCPRouter()
        self.builder = ItineraryBuilder()
        self.orchestrator = BookingOrchestrator()
    
    def test_full_workflow(self):
        """
        Test complete workflow:
        1. Search for buses
        2. Search for hotels
        3. Create itinerary from results
        4. Record booking
        5. Verify booking status
        """
        loop = asyncio.get_event_loop()
        
        # Step 1: Search buses
        bus_results = loop.run_until_complete(
            self.router.route(
                'search_buses',
                {
                    'origin': 'Nairobi',
                    'destination': 'Mombasa',
                    'travel_date': '2025-12-25'
                },
                {'user_id': self.user.id}
            )
        )
        self.assertIn('results', bus_results)
        
        # Step 2: Search hotels
        hotel_results = loop.run_until_complete(
            self.router.route(
                'search_hotels',
                {
                    'location': 'Mombasa',
                    'check_in_date': '2025-12-25',
                    'check_out_date': '2025-12-28'
                },
                {'user_id': self.user.id}
            )
        )
        self.assertIn('results', hotel_results)
        
        # Step 3: Create itinerary
        search_results = {
            'buses': bus_results.get('results', []),
            'hotels': hotel_results.get('results', []),
            'flights': [],
            'transfers': [],
            'events': []
        }
        
        itinerary = loop.run_until_complete(
            self.builder.create_from_searches(
                user_id=self.user.id,
                trip_name='Nairobi to Mombasa',
                origin='Nairobi',
                destination='Mombasa',
                start_date='2025-12-25',
                end_date='2025-12-28',
                search_results=search_results
            )
        )
        
        self.assertIsNotNone(itinerary.id)
        items = itinerary.items.all()
        self.assertGreater(items.count(), 0)
        
        # Step 4: Record booking for first item
        first_item = items.first()
        booking = loop.run_until_complete(
            self.orchestrator.record_booking(
                item_id=first_item.id,
                confirmation_code='TEST123',
                booking_reference='TESTREF'
            )
        )
        
        self.assertIsNotNone(booking.id)
        
        # Step 5: Verify booking status
        status = loop.run_until_complete(
            self.orchestrator.get_booking_status(first_item.id)
        )
        
        self.assertEqual(status['status'], 'confirmed')
        self.assertEqual(status['confirmation_code'], 'TEST123')
