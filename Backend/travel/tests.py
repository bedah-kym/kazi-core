"""Travel app unit tests"""
import csv
import os
import tempfile
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from travel.amadeus_client import get_amadeus_client, has_amadeus_credentials
from travel.llm_composer import LLMComposer
from travel.practical_service import VisaService, WeatherService
from travel.recommendation_service import RecommendationService
from travel.search_state import find_result, get_last_results, store_last_results
from travel.services import BookingOrchestrator, ExportService, ItineraryBuilder
from .models import (
    BookingReference,
    Event,
    Itinerary,
    ItineraryItem,
    SearchCache,
    TripFeedback,
)

User = get_user_model()


class ItineraryModelTests(TestCase):
    """Test Itinerary model"""
    
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', email='test@example.com', password='pass123')
    
    def test_create_itinerary(self):
        """Test creating an itinerary"""
        itinerary = Itinerary.objects.create(
            user=self.user,
            title='Kenya Safari',
            region='kenya',
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=7),
            budget_ksh=150000.00
        )
        self.assertEqual(itinerary.title, 'Kenya Safari')
        self.assertEqual(itinerary.user, self.user)
        self.assertEqual(itinerary.status, 'draft')
    
    def test_itinerary_duration(self):
        """Test itinerary duration calculation"""
        start = timezone.now()
        end = start + timedelta(days=5)
        itinerary = Itinerary.objects.create(
            user=self.user,
            title='Test Trip',
            region='kenya',
            start_date=start,
            end_date=end
        )
        self.assertEqual(itinerary.duration_days, 6)  # 5 days + 1

    def test_itinerary_duration_missing_dates(self):
        """Duration should be 0 when dates are missing"""
        itinerary = Itinerary(user=self.user, title='No Dates', start_date=None, end_date=None)
        self.assertEqual(itinerary.duration_days, 0)

    def test_itinerary_str(self):
        """String repr includes title and username"""
        itinerary = Itinerary.objects.create(
            user=self.user,
            title='Short Getaway',
            region='kenya',
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=1)
        )
        self.assertIn('Short Getaway', str(itinerary))
        self.assertIn(self.user.username, str(itinerary))


class ItineraryItemModelTests(TestCase):
    """Test ItineraryItem model"""
    
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', email='test@example.com', password='pass123')
        self.itinerary = Itinerary.objects.create(
            user=self.user,
            title='Test Trip',
            region='kenya',
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=7)
        )
    
    def test_create_itinerary_item(self):
        """Test creating an itinerary item"""
        item = ItineraryItem.objects.create(
            itinerary=self.itinerary,
            item_type='hotel',
            title='Safari Park Hotel',
            start_datetime=timezone.now(),
            provider='Booking.com',
            price_ksh=8500.00
        )
        self.assertEqual(item.title, 'Safari Park Hotel')
        self.assertEqual(item.item_type, 'hotel')
        self.assertEqual(item.status, 'planned')
    
    def test_item_ordering(self):
        """Test items ordered by sort_order and start_datetime"""
        time1 = timezone.now()
        time2 = time1 + timedelta(hours=1)
        
        item2 = ItineraryItem.objects.create(
            itinerary=self.itinerary,
            item_type='flight',
            title='Morning Flight',
            start_datetime=time2,
            sort_order=2
        )
        item1 = ItineraryItem.objects.create(
            itinerary=self.itinerary,
            item_type='bus',
            title='Bus Ride',
            start_datetime=time1,
            sort_order=1
        )
        
        items = ItineraryItem.objects.all()
        self.assertEqual(items[0].id, item1.id)
        self.assertEqual(items[1].id, item2.id)

    def test_item_str_and_default_status(self):
        """String repr and default status"""
        item = ItineraryItem.objects.create(
            itinerary=self.itinerary,
            item_type='event',
            title='City Festival',
            start_datetime=timezone.now(),
        )
        self.assertIn('City Festival', str(item))
        self.assertEqual(item.status, 'planned')


class EventModelTests(TestCase):
    """Test Event model"""
    
    def test_create_event(self):
        """Test creating an event"""
        event = Event.objects.create(
            title='Kenya Jazz Festival',
            category='music',
            start_datetime=timezone.now() + timedelta(days=10),
            location_name='Safari Park',
            location_country='Kenya',
            provider='Eventbrite',
            provider_id='evt_001'
        )
        self.assertEqual(event.title, 'Kenya Jazz Festival')
        self.assertEqual(event.category, 'music')

    def test_event_str(self):
        """String repr includes title and date"""
        event = Event.objects.create(
            title='Food Fair',
            category='food',
            start_datetime=timezone.now() + timedelta(days=3),
            location_name='Village Market',
            location_country='Kenya',
            provider='Eventbrite',
            provider_id='evt_002'
        )
        self.assertIn('Food Fair', str(event))


class SearchCacheModelTests(TestCase):
    """Test SearchCache model"""
    
    def test_create_cache_entry(self):
        """Test creating a cache entry"""
        import hashlib
        import json
        
        query = {'origin': 'Nairobi', 'destination': 'Mombasa'}
        query_hash = hashlib.sha256(json.dumps(query, sort_keys=True).encode()).hexdigest()
        
        cache = SearchCache.objects.create(
            query_hash=query_hash,
            provider='buupass',
            query_json=query,
            result_json={'results': []},
            expires_at=timezone.now() + timedelta(hours=1)
        )
        self.assertEqual(cache.provider, 'buupass')
        self.assertFalse(cache.is_expired())
        self.assertEqual(cache.hit_count, 0)
    
    def test_cache_expiry(self):
        """Test cache expiry detection"""
        import hashlib
        import json
        
        query = {'test': 'query'}
        query_hash = hashlib.sha256(json.dumps(query, sort_keys=True).encode()).hexdigest()
        
        cache = SearchCache.objects.create(
            query_hash=query_hash,
            provider='booking',
            query_json=query,
            result_json={},
            expires_at=timezone.now() - timedelta(hours=1)  # Already expired
        )
        self.assertTrue(cache.is_expired())

    def test_unique_cache_constraint(self):
        """Duplicate provider/query_hash should raise"""
        import hashlib
        import json

        query = {'origin': 'Nairobi', 'destination': 'Mombasa'}
        query_hash = hashlib.sha256(json.dumps(query, sort_keys=True).encode()).hexdigest()
        SearchCache.objects.create(
            query_hash=query_hash,
            provider='amadeus',
            query_json=query,
            result_json={},
            expires_at=timezone.now() + timedelta(hours=1)
        )
        with self.assertRaises(IntegrityError):
            SearchCache.objects.create(
                query_hash=query_hash,
                provider='amadeus',
                query_json=query,
                result_json={},
                expires_at=timezone.now() + timedelta(hours=1)
            )


class BookingReferenceModelTests(TestCase):
    """Test BookingReference model"""

    def setUp(self):
        self.user = User.objects.create_user(username='booker', email='booker@example.com', password='pass123')
        self.itinerary = Itinerary.objects.create(
            user=self.user,
            title='Booking Trip',
            region='kenya',
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=2)
        )
        self.item = ItineraryItem.objects.create(
            itinerary=self.itinerary,
            item_type='hotel',
            title='Stay',
            start_datetime=timezone.now()
        )

    def test_booking_reference_str_prefers_reference(self):
        booking = BookingReference.objects.create(
            itinerary_item=self.item,
            provider='TestProvider',
            provider_booking_id='PROV123',
            booking_reference='REF999',
            confirmation_code='CONF1',
            booking_url='https://example.com/booking'
        )
        self.assertIn('REF999', str(booking))

    def test_booking_reference_str_fallback_provider_id(self):
        booking = BookingReference.objects.create(
            itinerary_item=self.item,
            provider='TestProvider',
            provider_booking_id='PROV123',
            booking_url='https://example.com/booking'
        )
        self.assertIn('PROV123', str(booking))


class TripFeedbackModelTests(TestCase):
    """Test TripFeedback model"""

    def test_feedback_defaults_and_str(self):
        user = User.objects.create_user(username='reviewer', email='rev@example.com', password='pass123')
        itinerary = Itinerary.objects.create(
            user=user,
            title='Review Trip',
            region='kenya',
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=1)
        )
        feedback = TripFeedback.objects.create(
            user=user,
            itinerary=itinerary,
            overall_rating=5,
            safety_rating=4,
            cost_rating=3,
            review_text='Nice trip.'
        )
        self.assertEqual(feedback.tags, [])
        self.assertIn('Review Trip', str(feedback))


class SearchStateTests(TestCase):
    """Test travel search state cache helpers"""

    def setUp(self):
        cache.clear()

    def test_store_and_get_results(self):
        store_last_results(
            user_id=1,
            action='search_flights',
            results=[{'id': 'flight_001', 'provider_id': 'PX1'}],
            metadata={'origin': 'NBO'}
        )
        session = get_last_results(1, 'search_flights')
        self.assertIsNotNone(session)
        self.assertEqual(session['results'][0]['id'], 'flight_001')
        self.assertEqual(session['metadata']['origin'], 'NBO')

    def test_find_result_by_provider_and_index(self):
        results = [
            {'id': 'flight_001', 'provider_id': 'PX1'},
            {'id': 'flight_002', 'provider_id': 'PX2', 'flight_number': 'KQ101'},
        ]
        store_last_results(2, 'search_flights', results, metadata={'origin': 'NBO'})
        found, meta = find_result(2, 'search_flights', 'PX2')
        self.assertEqual(found['id'], 'flight_002')
        self.assertEqual(meta['origin'], 'NBO')

        found, _ = find_result(2, 'search_flights', '2')
        self.assertEqual(found['id'], 'flight_002')

        found, _ = find_result(2, 'search_flights', 'KQ101')
        self.assertEqual(found['id'], 'flight_002')

    def test_store_ignores_missing_key_data(self):
        store_last_results(user_id=None, action=None, results=[{'id': 'x'}])
        self.assertIsNone(get_last_results(None, None))


class AmadeusClientTests(TestCase):
    """Test Amadeus client helpers"""

    def test_no_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            get_amadeus_client.cache_clear()
            self.assertFalse(has_amadeus_credentials())
            self.assertIsNone(get_amadeus_client())


class LLMComposerTests(TestCase):
    """Test LLM itinerary composer helpers"""

    def test_minify_results_adds_ids(self):
        composer = LLMComposer()
        results = {
            'buses': [{'company': 'Skyways', 'price_ksh': 1200, 'departure_time': '08:00', 'arrival_time': '12:00'}]
        }
        minified = composer._minify_results(results)
        self.assertIn('id', minified['buses'][0])
        self.assertIn('id', results['buses'][0])
        self.assertIn('time', minified['buses'][0])

    def test_rehydrate_items(self):
        composer = LLMComposer()
        original = {
            'hotels': [{'id': 'hotels_0', 'name': 'Hotel A', 'price_ksh': 5000}]
        }
        selected = [{'id': 'hotels_0', 'reason': 'Great value', 'type': 'hotel'}]
        items = composer._rehydrate_items(selected, original)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['ai_reasoning'], 'Great value')
        self.assertEqual(items[0]['_category'], 'hotel')

    def test_compose_itinerary_handles_llm_failure(self):
        composer = LLMComposer()
        with patch.object(composer.llm_client, 'generate_text', new=AsyncMock(side_effect=Exception('fail'))):
            items = async_to_sync(composer.compose_itinerary)(
                {'origin': 'Nairobi', 'destination': 'Mombasa', 'start_date': '2025-12-25', 'end_date': '2025-12-28'},
                {'buses': []}
            )
        self.assertEqual(items, [])


class RecommendationServiceTests(TestCase):
    """Test recommendation service helpers"""

    def test_summarize_context(self):
        service = RecommendationService()
        self.assertEqual(service._summarize_context([]), 'Nothing booked yet.')
        summary = service._summarize_context([{'title': 'Museum', 'time': 'Morning'}])
        self.assertIn('Museum', summary)

    def test_llm_suggestions_failure(self):
        service = RecommendationService()
        with patch.object(service.llm_client, 'generate_text', new=AsyncMock(side_effect=Exception('fail'))):
            result = async_to_sync(service._get_llm_suggestions)("prompt")
        self.assertEqual(result, [])

    def test_verify_activity_rate_limit(self):
        service = RecommendationService()
        with patch('orchestration.mcp_router.route_intent', new=AsyncMock(return_value={
            'status': 'success',
            'data': {'error': 'rate_limit_exceeded'}
        })):
            result = async_to_sync(service.verify_activity_online)('Diani', 1)
        self.assertFalse(result['verified'])


class PracticalServiceTests(TestCase):
    """Test visa and weather helpers"""

    def test_visa_service_from_csv(self):
        with tempfile.NamedTemporaryFile(mode='w', delete=False, newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=['Passport', 'Destination', 'Requirement'])
            writer.writeheader()
            writer.writerow({'Passport': 'US', 'Destination': 'KE', 'Requirement': '-1'})
            writer.writerow({'Passport': 'FR', 'Destination': 'KE', 'Requirement': '0'})
            writer.writerow({'Passport': 'KE', 'Destination': 'UG', 'Requirement': '90'})
            writer.writerow({'Passport': 'CA', 'Destination': 'KE', 'Requirement': 'eTA'})
            temp_path = handle.name

        service = VisaService()
        service.dataset_path = temp_path
        self.addCleanup(lambda: os.remove(temp_path))

        self.assertEqual(service.check_requirements('US', 'KE'), 'Visa on Arrival')
        self.assertEqual(service.check_requirements('FR', 'KE'), 'Visa Required')
        self.assertEqual(service.check_requirements('KE', 'UG'), 'Visa Free (90 days)')
        self.assertEqual(service.check_requirements('CA', 'KE'), 'eTA / E-Visa')

    def test_weather_service_success_and_error(self):
        service = WeatherService()
        with patch('travel.practical_service.route_intent', new=AsyncMock(return_value={'status': 'success', 'data': {'temp': 25}})):
            result = async_to_sync(service.get_trip_forecast)('Nairobi')
        self.assertEqual(result.get('temp'), 25)

        with patch('travel.practical_service.route_intent', new=AsyncMock(return_value={'status': 'error'})):
            result = async_to_sync(service.get_trip_forecast)('Nairobi')
        self.assertEqual(result.get('error'), 'Weather unavailable')


class ItineraryBuilderTests(TestCase):
    """Test itinerary builder behavior"""

    def setUp(self):
        self.user = User.objects.create_user(username='builder', email='builder@example.com', password='pass123')
        self.builder = ItineraryBuilder()

    def test_create_from_searches_fallback(self):
        search_results = {
            'buses': [{'company': 'Skyways', 'price_ksh': 2500, 'departure_time': '08:00', 'arrival_time': '12:00'}],
            'hotels': [{'name': 'Safari Park', 'price_ksh': 8500}],
            'flights': [{'airline': 'KQ', 'flight_number': 'KQ101', 'price_ksh': 9500}],
            'transfers': [{'provider': 'Karibu', 'vehicle_type': 'Sedan', 'price_ksh': 1800}],
            'events': [{'title': 'Jazz Night', 'price_ksh': 1200}],
        }

        with patch('travel.llm_composer.LLMComposer.compose_itinerary', new=AsyncMock(return_value=[])):
            itinerary = async_to_sync(self.builder.create_from_searches)(
                self.user.id,
                'Nairobi to Mombasa',
                'Nairobi',
                'Mombasa',
                '2025-12-25',
                '2025-12-28',
                search_results
            )

        items = list(itinerary.items.all())
        self.assertEqual(len(items), 5)
        self.assertTrue(all(item.start_datetime for item in items))
        self.assertEqual(itinerary.metadata.get('destination'), 'Mombasa')

    def test_create_from_searches_ai_path(self):
        ai_items = [
            {'_category': 'hotel', 'name': 'AI Hotel', 'price_ksh': 10000, 'provider': 'ai', 'booking_url': 'http://example.com'}
        ]
        with patch('travel.llm_composer.LLMComposer.compose_itinerary', new=AsyncMock(return_value=ai_items)):
            itinerary = async_to_sync(self.builder.create_from_searches)(
                self.user.id,
                'AI Trip',
                'Nairobi',
                'Kisumu',
                '2025-12-25',
                '2025-12-27',
                {'buses': [], 'hotels': [], 'flights': [], 'transfers': [], 'events': []}
            )

        items = list(itinerary.items.all())
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0].metadata.get('ai_selected'))


class ExportServiceTests(TestCase):
    """Test export services"""

    def setUp(self):
        self.user = User.objects.create_user(username='exporter', email='export@example.com', password='pass123')
        self.itinerary = Itinerary.objects.create(
            user=self.user,
            title='Export Trip',
            region='kenya',
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=2)
        )
        ItineraryItem.objects.create(
            itinerary=self.itinerary,
            item_type='event',
            title='Expo',
            start_datetime=timezone.now(),
            price_ksh=2000,
            description='Sample event'
        )
        self.export_service = ExportService()

    def test_export_json(self):
        data = async_to_sync(self.export_service.export_json)(self.itinerary.id)
        self.assertEqual(data['title'], 'Export Trip')
        self.assertEqual(len(data['items']), 1)
        self.assertIn('total_cost', data)

    def test_export_ical(self):
        ical = async_to_sync(self.export_service.export_ical)(self.itinerary.id)
        self.assertIn('BEGIN:VCALENDAR', ical)
        self.assertIn('END:VCALENDAR', ical)


class BookingOrchestratorTests(TestCase):
    """Test booking orchestration helpers"""

    def setUp(self):
        self.user = User.objects.create_user(username='booker2', email='booker2@example.com', password='pass123')
        self.itinerary = Itinerary.objects.create(
            user=self.user,
            title='Booking Trip',
            region='kenya',
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=1)
        )
        self.item = ItineraryItem.objects.create(
            itinerary=self.itinerary,
            item_type='hotel',
            title='Hotel',
            start_datetime=timezone.now(),
            metadata={'provider': 'booking', 'booking_url': 'http://example.com/room'}
        )
        self.orchestrator = BookingOrchestrator()

    def test_get_booking_url_affiliate(self):
        url = async_to_sync(self.orchestrator.get_booking_url)(self.item.id, self.user.id)
        self.assertIn('aid=MATHIA-TRAVEL-2025', url)

    def test_record_booking_and_status(self):
        booking = async_to_sync(self.orchestrator.record_booking)(self.item.id, 'CONF123', 'REF456')
        self.assertEqual(booking.status, 'confirmed')
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'booked')

        status = async_to_sync(self.orchestrator.get_booking_status)(self.item.id)
        self.assertEqual(status['status'], 'confirmed')

    def test_get_booking_status_not_booked(self):
        new_item = ItineraryItem.objects.create(
            itinerary=self.itinerary,
            item_type='flight',
            title='Flight',
            start_datetime=timezone.now()
        )
        status = async_to_sync(self.orchestrator.get_booking_status)(new_item.id)
        self.assertEqual(status['status'], 'not_booked')
