from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "event_type", "severity", "title", "is_read", "created_at")
    list_filter = ("event_type", "severity", "is_read", "is_dismissed")
    search_fields = ("user__username", "title", "body")
    raw_id_fields = ("user",)
    readonly_fields = ("created_at",)
