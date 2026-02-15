"""
Travel Planner Data Models
Supports itinerary creation, item management, event discovery, and booking references
"""
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
import json


class Itinerary(models.Model):
    """
    User's travel itinerary (trip plan)
    """
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('completed', 'Completed'),
        ('archived', 'Archived'),
    ]
    
    REGION_CHOICES = [
        ('kenya', 'Kenya'),
        ('east_africa', 'East Africa'),
        ('africa', 'Africa'),
        ('worldwide', 'Worldwide'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='itineraries')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    region = models.CharField(max_length=50, choices=REGION_CHOICES, default='kenya')
    
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    
    budget_ksh = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    budget_currency = models.CharField(max_length=3, default='KES')
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    is_public = models.BooleanField(default=False)
    is_shared = models.BooleanField(default=False)
    shared_with = models.ManyToManyField(User, blank=True, related_name='shared_itineraries')
    
    # LLM-generated metadata (JSON)
    metadata = models.JSONField(default=dict, blank=True)  # {summary, highlights, risk_factors, etc.}
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['status']),
        ]
    
    def __str__(self):
        return f"{self.title} ({self.user.username})"
    
    @property
    def duration_days(self):
        if self.start_date and self.end_date:
            return (self.end_date.date() - self.start_date.date()).days + 1
        return 0


class ItineraryItem(models.Model):
    """
    Individual items in an itinerary (flights, hotels, buses, activities)
    """
    ITEM_TYPE_CHOICES = [
        ('bus', 'Bus Ticket'),
        ('hotel', 'Hotel'),
        ('flight', 'Flight'),
        ('transfer', 'Transfer'),
        ('event', 'Event'),
        ('activity', 'Activity'),
        ('restaurant', 'Restaurant'),
        ('other', 'Other'),
    ]
    
    STATUS_CHOICES = [
        ('planned', 'Planned'),
        ('booked', 'Booked'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]
    
    itinerary = models.ForeignKey(Itinerary, on_delete=models.CASCADE, related_name='items')
    
    item_type = models.CharField(max_length=20, choices=ITEM_TYPE_CHOICES)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    
    start_datetime = models.DateTimeField()
    end_datetime = models.DateTimeField(null=True, blank=True)
    
    location_name = models.CharField(max_length=255, blank=True, null=True)
    location_latitude = models.FloatField(null=True, blank=True)
    location_longitude = models.FloatField(null=True, blank=True)
    
    provider = models.CharField(max_length=100, blank=True, null=True)  # e.g., "Buupass", "Booking.com", "Duffel"
    provider_id = models.CharField(max_length=255, blank=True, null=True)  # e.g., route_id, hotel_id
    
    price_ksh = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    price_currency = models.CharField(max_length=3, default='KES')
    
    booking_url = models.URLField(blank=True, null=True)  # Affiliate link for booking
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='planned')
    
    # Detailed metadata (JSON)
    metadata = models.JSONField(default=dict, blank=True)  # {seats, hotel_rating, luggage_limit, etc.}
    
    sort_order = models.IntegerField(default=0)  # For manual reordering
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['itinerary', 'sort_order', 'start_datetime']
        indexes = [
            models.Index(fields=['itinerary', 'start_datetime']),
            models.Index(fields=['status']),
        ]
    
    def __str__(self):
        return f"{self.title} ({self.item_type})"


class Event(models.Model):
    """
    Discoverable events (concerts, conferences, sports, cultural events)
    Used for suggestions and add-to-itinerary
    """
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    category = models.CharField(max_length=100)  # music, sports, conference, cultural, food, etc.
    
    start_datetime = models.DateTimeField()
    end_datetime = models.DateTimeField(null=True, blank=True)
    
    location_name = models.CharField(max_length=255)
    location_latitude = models.FloatField(null=True, blank=True)
    location_longitude = models.FloatField(null=True, blank=True)
    location_country = models.CharField(max_length=100)
    
    ticket_price_ksh = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    ticket_url = models.URLField(blank=True, null=True)
    
    provider = models.CharField(max_length=100)  # Eventbrite, local scraper, etc.
    provider_id = models.CharField(max_length=255)
    
    image_url = models.URLField(blank=True, null=True)
    
    # Metadata (JSON)
    metadata = models.JSONField(default=dict, blank=True)  # {capacity, organizer, tags, etc.}
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)  # When we last fetched from provider
    
    class Meta:
        ordering = ['-start_datetime']
        indexes = [
            models.Index(fields=['location_country', 'start_datetime']),
            models.Index(fields=['category']),
        ]
    
    def __str__(self):
        return f"{self.title} ({self.start_datetime.strftime('%Y-%m-%d')})"


class SearchCache(models.Model):
    """
    Cache for search results (buses, hotels, flights, transfers, events)
    Prevents excessive API calls for identical queries
    """
    PROVIDER_CHOICES = [
        ('buupass', 'Buupass'),
        ('booking', 'Booking.com'),
        ('amadeus', 'Amadeus'),
        ('duffel', 'Duffel'),
        ('karibu', 'Karibu Taxi'),
        ('eventbrite', 'Eventbrite'),
    ]
    
    query_hash = models.CharField(max_length=64, db_index=True)  # SHA256 of query
    provider = models.CharField(max_length=50, choices=PROVIDER_CHOICES)
    
    query_json = models.JSONField()  # Original query parameters
    result_json = models.JSONField()  # API response
    
    ttl_seconds = models.IntegerField(default=3600)  # 1 hour default
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    expires_at = models.DateTimeField(db_index=True)  # Query expiration timestamp
    
    hit_count = models.IntegerField(default=0)  # Number of times cache was used
    
    class Meta:
        unique_together = ['query_hash', 'provider']
        indexes = [
            models.Index(fields=['expires_at']),
            models.Index(fields=['provider', 'created_at']),
        ]
    
    def __str__(self):
        return f"{self.provider} cache: {self.query_hash[:16]}"
    
    def is_expired(self):
        return timezone.now() > self.expires_at


class BookingReference(models.Model):
    """
    Reference to an actual booking made through our platform
    Links itinerary item to provider's booking confirmation
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ]
    
    itinerary_item = models.OneToOneField(ItineraryItem, on_delete=models.CASCADE, related_name='booking_reference')
    
    provider = models.CharField(max_length=100)  # Buupass, Booking.com, etc.
    provider_booking_id = models.CharField(max_length=255)  # Confirmation ID from provider
    booking_reference = models.CharField(max_length=255, blank=True, null=True)  # External booking reference
    confirmation_code = models.CharField(max_length=255, blank=True, null=True)  # PNR or confirmation code
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    
    booking_url = models.URLField()  # Direct link to booking on provider site
    confirmation_email = models.EmailField(blank=True, null=True)
    
    metadata = models.JSONField(default=dict, blank=True)  # {cancellation_deadline, refund_policy, etc.}
    
    booked_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['-booked_at']
    
    def __str__(self):
        ref = self.booking_reference or self.provider_booking_id or self.confirmation_code or "unknown"
        return f"{self.provider} booking: {ref}"


class TripFeedback(models.Model):
    """
    User feedback for a completed itinerary.
    Collects ratings and qualitative data for future model training/sharing.
    """
    RATING_CHOICES = [(i, str(i)) for i in range(1, 6)]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='trip_feedback')
    itinerary = models.OneToOneField(Itinerary, on_delete=models.CASCADE, related_name='feedback')
    
    # Quantitative Ratings (1-5)
    overall_rating = models.IntegerField(choices=RATING_CHOICES)
    safety_rating = models.IntegerField(choices=RATING_CHOICES, help_text="1=Unsafe, 5=Very Safe")
    cost_rating = models.IntegerField(choices=RATING_CHOICES, help_text="1=Expensive, 5=Great Value")
    
    # Qualitative Data
    review_text = models.TextField(blank=True, help_text="General comments from conversation")
    
    # Structured Tags (Extracted by AI)
    # e.g., ["solo-friendly", "good-nightlife", "sketchy-at-night"]
    tags = models.JSONField(default=list, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['safety_rating']),
            models.Index(fields=['overall_rating']),
        ]
        
    def __str__(self):
        return f"Feedback for {self.itinerary.title} by {self.user.username}"
