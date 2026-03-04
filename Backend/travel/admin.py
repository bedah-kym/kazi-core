from django.contrib import admin
from django.utils.html import format_html
from import_export import resources
from import_export.admin import ImportExportModelAdmin
from .models import Itinerary, ItineraryItem, Event, SearchCache, BookingReference


# Export Resources
class ItineraryResource(resources.ModelResource):
    class Meta:
        model = Itinerary
        fields = ['id', 'title', 'user', 'region', 'status', 'start_date', 'end_date', 'created_at']
        export_order = fields


class BookingReferenceResource(resources.ModelResource):
    class Meta:
        model = BookingReference
        fields = ['id', 'provider', 'provider_booking_id', 'status', 'booked_at', 'confirmed_at']
        export_order = fields


@admin.register(Itinerary)
class ItineraryAdmin(ImportExportModelAdmin):
    resource_class = ItineraryResource
    list_display = ['title', 'user', 'region', 'status_badge', 'start_date', 'created_at']
    list_filter = ['status', 'region', 'created_at', 'start_date']
    search_fields = ['title', 'user__username', 'user__email', 'description']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['-created_at']
    autocomplete_fields = ['user']
    date_hierarchy = 'created_at'

    fieldsets = (
        ('Itinerary Details', {
            'fields': ('title', 'user', 'description', 'region')
        }),
        ('Dates', {
            'fields': ('start_date', 'end_date')
        }),
        ('Status', {
            'fields': ('status',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def status_badge(self, obj):
        colors = {
            'draft': '#95a5a6',
            'active': '#3498db',
            'completed': '#27ae60',
            'archived': '#e74c3c'
        }
        color = colors.get(obj.status, '#95a5a6')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 5px 10px; border-radius: 3px; text-transform: capitalize;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = 'Status'


@admin.register(ItineraryItem)
class ItineraryItemAdmin(admin.ModelAdmin):
    list_display = ['title', 'itinerary', 'type_badge', 'start_datetime', 'status_badge']
    list_filter = ['item_type', 'status', 'start_datetime', 'created_at']
    search_fields = ['title', 'itinerary__title', 'location_name', 'description', 'provider', 'provider_id']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['-start_datetime']
    autocomplete_fields = ['itinerary']
    date_hierarchy = 'start_datetime'

    fieldsets = (
        ('Item Details', {
            'fields': ('itinerary', 'title', 'description', 'item_type')
        }),
        ('Schedule', {
            'fields': ('start_datetime', 'end_datetime')
        }),
        ('Location', {
            'fields': ('location_name', 'location_latitude', 'location_longitude')
        }),
        ('Provider', {
            'fields': ('provider', 'provider_id', 'booking_url')
        }),
        ('Pricing', {
            'fields': ('price_ksh', 'price_currency')
        }),
        ('Status', {
            'fields': ('status',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def type_badge(self, obj):
        colors = {
            'bus': '#3498db',
            'flight': '#3498db',
            'hotel': '#2ecc71',
            'activity': '#f39c12',
            'event': '#9b59b6',
            'restaurant': '#e74c3c',
            'transfer': '#9b59b6',
            'other': '#95a5a6'
        }
        color = colors.get(obj.item_type, '#95a5a6')
        item_type = obj.get_item_type_display()
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px;">{}</span>',
            color,
            item_type
        )
    type_badge.short_description = 'Type'

    def status_badge(self, obj):
        colors = {
            'planned': '#f39c12',
            'booked': '#27ae60',
            'cancelled': '#e74c3c',
            'completed': '#3498db'
        }
        color = colors.get(obj.status, '#95a5a6')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = 'Status'


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ['title', 'location_country', 'start_datetime', 'category_badge', 'provider']
    list_filter = ['category', 'location_country', 'start_datetime', 'provider']
    search_fields = ['title', 'location_name', 'location_country', 'description']
    readonly_fields = ['created_at', 'updated_at', 'last_synced_at']
    ordering = ['-start_datetime']
    date_hierarchy = 'start_datetime'

    fieldsets = (
        ('Event Details', {
            'fields': ('title', 'description', 'category')
        }),
        ('Location', {
            'fields': ('location_name', 'location_country', 'location_latitude', 'location_longitude')
        }),
        ('Schedule', {
            'fields': ('start_datetime', 'end_datetime')
        }),
        ('Provider Info', {
            'fields': ('provider', 'provider_id')
        }),
        ('Media', {
            'fields': ('image_url',),
            'classes': ('collapse',)
        }),
        ('Sync Status', {
            'fields': ('last_synced_at',),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def category_badge(self, obj):
        colors = {
            'flights': '#3498db',
            'hotels': '#2ecc71',
            'activities': '#f39c12',
            'restaurants': '#e74c3c',
            'attractions': '#9b59b6'
        }
        color = colors.get(obj.category, '#95a5a6')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px;">{}</span>',
            color,
            obj.get_category_display() if hasattr(obj, 'get_category_display') else obj.category
        )
    category_badge.short_description = 'Category'


@admin.register(SearchCache)
class SearchCacheAdmin(admin.ModelAdmin):
    list_display = ['provider', 'hit_count_display', 'cache_status', 'created_at', 'expires_at']
    list_filter = ['provider', 'created_at', 'expires_at']
    search_fields = ['query_hash']
    readonly_fields = ['created_at', 'query_hash', 'query_json', 'result_json']
    ordering = ['-hit_count']

    fieldsets = (
        ('Cache Info', {
            'fields': ('provider', 'query_hash', 'query_json')
        }),
        ('Metrics', {
            'fields': ('hit_count', 'ttl_seconds')
        }),
        ('Results', {
            'fields': ('result_json',),
            'classes': ('collapse',)
        }),
        ('Expiry', {
            'fields': ('created_at', 'expires_at'),
        }),
    )

    def hit_count_display(self, obj):
        return format_html(
            '<span style="background-color: #3498db; color: white; padding: 3px 8px; border-radius: 3px; font-weight: bold;">{} hits</span>',
            obj.hit_count
        )
    hit_count_display.short_description = 'Hit Count'

    def cache_status(self, obj):
        from django.utils import timezone
        is_expired = obj.expires_at and obj.expires_at < timezone.now()
        color = '#e74c3c' if is_expired else '#27ae60'
        status = 'Expired' if is_expired else 'Valid'
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px;">{}</span>',
            color,
            status
        )
    cache_status.short_description = 'Status'


@admin.register(BookingReference)
class BookingReferenceAdmin(ImportExportModelAdmin):
    resource_class = BookingReferenceResource
    list_display = ['provider', 'provider_booking_id_display', 'status_badge', 'booked_at', 'confirmed_at']
    list_filter = ['provider', 'status', 'booked_at', 'confirmed_at']
    search_fields = ['provider_booking_id', 'booking_reference', 'confirmation_code', 'confirmation_email']
    readonly_fields = ['booked_at', 'confirmed_at']
    ordering = ['-booked_at']
    date_hierarchy = 'booked_at'

    fieldsets = (
        ('Booking Info', {
            'fields': ('itinerary_item', 'provider', 'provider_booking_id', 'booking_reference', 'confirmation_code')
        }),
        ('Status', {
            'fields': ('status',)
        }),
        ('Contact', {
            'fields': ('booking_url', 'confirmation_email')
        }),
        ('Metadata', {
            'fields': ('metadata',),
            'classes': ('collapse',)
        }),
        ('Dates', {
            'fields': ('booked_at', 'confirmed_at'),
            'classes': ('collapse',)
        }),
    )

    def provider_booking_id_display(self, obj):
        return format_html(
            '<code style="background-color: #ecf0f1; padding: 2px 5px; border-radius: 3px;">{}</code>',
            obj.provider_booking_id
        )
    provider_booking_id_display.short_description = 'Booking ID'

    def status_badge(self, obj):
        colors = {
            'pending': '#f39c12',
            'confirmed': '#27ae60',
            'cancelled': '#e74c3c',
            'failed': '#e74c3c'
        }
        color = colors.get(obj.status, '#95a5a6')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 5px 10px; border-radius: 3px; text-transform: capitalize;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = 'Status'

