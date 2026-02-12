from django.contrib import admin
from .models import Itinerary, ItineraryItem, Event, SearchCache, BookingReference


@admin.register(Itinerary)
class ItineraryAdmin(admin.ModelAdmin):
    list_display = ['title', 'user', 'region', 'status', 'start_date', 'created_at']
    list_filter = ['status', 'region', 'created_at']
    search_fields = ['title', 'user__username', 'description']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['-created_at']


@admin.register(ItineraryItem)
class ItineraryItemAdmin(admin.ModelAdmin):
    list_display = ['title', 'itinerary', 'item_type', 'start_datetime', 'status']
    list_filter = ['item_type', 'status', 'start_datetime']
    search_fields = ['title', 'itinerary__title']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ['title', 'location_country', 'start_datetime', 'category', 'provider']
    list_filter = ['category', 'location_country', 'start_datetime']
    search_fields = ['title', 'location_name', 'description']
    readonly_fields = ['created_at', 'updated_at', 'last_synced_at']


@admin.register(SearchCache)
class SearchCacheAdmin(admin.ModelAdmin):
    list_display = ['provider', 'query_hash', 'hit_count', 'created_at', 'expires_at']
    list_filter = ['provider', 'created_at']
    search_fields = ['query_hash']
    readonly_fields = ['created_at', 'query_hash']


@admin.register(BookingReference)
class BookingReferenceAdmin(admin.ModelAdmin):
    list_display = ['provider', 'provider_booking_id', 'status', 'booked_at']
    list_filter = ['provider', 'status', 'booked_at']
    search_fields = ['provider_booking_id']
    readonly_fields = ['booked_at', 'confirmed_at']
