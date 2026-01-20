"""REST Framework serializers for travel planner endpoints"""
from rest_framework import serializers
from django.contrib.auth.models import User
from .models import Itinerary, ItineraryItem, Event, SearchCache, BookingReference


class ItineraryItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = ItineraryItem
        fields = [
            'id', 'item_type', 'title', 'description',
            'start_datetime', 'end_datetime',
            'location_name', 'location_latitude', 'location_longitude',
            'provider', 'provider_id', 'price_ksh', 'price_currency',
            'booking_url', 'status', 'metadata', 'sort_order',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class BookingReferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookingReference
        fields = [
            'id', 'provider', 'provider_booking_id', 'status',
            'booking_url', 'confirmation_email', 'metadata',
            'booked_at', 'confirmed_at'
        ]
        read_only_fields = ['id', 'booked_at', 'confirmed_at']


class ItinerarySerializer(serializers.ModelSerializer):
    items = ItineraryItemSerializer(many=True, read_only=True, source='items.all')
    duration_days = serializers.IntegerField(read_only=True)
    username = serializers.CharField(source='user.username', read_only=True)
    
    class Meta:
        model = Itinerary
        fields = [
            'id', 'user', 'username', 'title', 'description', 'region',
            'start_date', 'end_date', 'duration_days',
            'budget_ksh', 'budget_currency',
            'status', 'is_public', 'is_shared',
            'metadata', 'items',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'user', 'created_at', 'updated_at']


class EventSerializer(serializers.ModelSerializer):
    class Meta:
        model = Event
        fields = [
            'id', 'title', 'description', 'category',
            'start_datetime', 'end_datetime',
            'location_name', 'location_latitude', 'location_longitude',
            'location_country',
            'ticket_price_ksh', 'ticket_url',
            'provider', 'provider_id',
            'image_url', 'metadata',
            'created_at', 'last_synced_at'
        ]
        read_only_fields = ['id', 'created_at', 'last_synced_at']


class SearchCacheSerializer(serializers.ModelSerializer):
    class Meta:
        model = SearchCache
        fields = [
            'id', 'query_hash', 'provider',
            'query_json', 'result_json',
            'ttl_seconds', 'created_at', 'expires_at',
            'hit_count'
        ]
        read_only_fields = ['id', 'query_hash', 'created_at', 'expires_at', 'hit_count']
