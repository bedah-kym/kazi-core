"""Travel app unit tests"""
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import datetime, timedelta
from .models import Itinerary, ItineraryItem, Event, SearchCache, BookingReference

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
