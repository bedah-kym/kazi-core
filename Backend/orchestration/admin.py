from django.contrib import admin
from django.utils.html import format_html
from .models import ActionReceipt


@admin.register(ActionReceipt)
class ActionReceiptAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "room", "action", "status_badge", "reversible_badge", "created_at")
    list_filter = ("status", "action", "reversible", "created_at")
    search_fields = ("action", "user__username", "user__email", "room__name")
    readonly_fields = ("created_at", "updated_at")
    ordering = ["-created_at"]
    autocomplete_fields = ["user", "room"]
    date_hierarchy = "created_at"

    fieldsets = (
        ("Action Details", {
            "fields": ("user", "room", "action", "status")
        }),
        ("Options", {
            "fields": ("reversible",)
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",)
        }),
    )

    def status_badge(self, obj):
        colors = {
            'pending': '#f39c12',
            'completed': '#27ae60',
            'failed': '#e74c3c',
            'cancelled': '#95a5a6'
        }
        color = colors.get(obj.status, '#3498db')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 5px 10px; border-radius: 3px; text-transform: capitalize; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display() if hasattr(obj, 'get_status_display') else obj.status
        )
    status_badge.short_description = 'Status'

    def reversible_badge(self, obj):
        color = '#27ae60' if obj.reversible else '#e74c3c'
        status = 'Yes' if obj.reversible else 'No'
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px;">{}</span>',
            color,
            status
        )
    reversible_badge.short_description = 'Reversible'

