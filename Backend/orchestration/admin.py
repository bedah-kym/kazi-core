from django.contrib import admin
from django.utils.html import format_html
from .models import ActionReceipt


@admin.register(ActionReceipt)
class ActionReceiptAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "room", "action", "status_badge", "reversible_badge", "created_at")
    list_filter = ("status", "action", "reversible", "created_at")
    search_fields = ("action", "service", "user__username", "user__email", "room__id")
    readonly_fields = ("created_at",)
    ordering = ["-created_at"]
    autocomplete_fields = ["user", "room"]
    date_hierarchy = "created_at"

    fieldsets = (
        ("Action Details", {
            "fields": ("user", "room", "action", "service", "status")
        }),
        ("Options", {
            "fields": ("reversible", "undo_action", "undo_params")
        }),
        ("Payload", {
            "fields": ("params", "result", "reason"),
            "classes": ("collapse",)
        }),
        ("Timestamps", {
            "fields": ("created_at",),
            "classes": ("collapse",)
        }),
    )

    def status_badge(self, obj):
        colors = {
            'success': '#27ae60',
            'error': '#e74c3c',
            'pending': '#f39c12',
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
