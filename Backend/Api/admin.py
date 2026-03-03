from django.contrib import admin
from django.utils.html import format_html
from .models import MathiaReply


@admin.register(MathiaReply)
class MathiaReplyAdmin(admin.ModelAdmin):
    list_display = ['reply_preview', 'created_at_display', 'id']
    list_filter = ['created_at']
    search_fields = ['id']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['-created_at']
    date_hierarchy = 'created_at'

    fieldsets = (
        ('Reply Details', {
            'fields': ('id',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def reply_preview(self, obj):
        # Show a preview of the data if it's a string, or JSON representation otherwise
        try:
            preview = str(obj)[:60] + '...' if len(str(obj)) > 60 else str(obj)
            return format_html(
                '<span style="color: #555; font-family: monospace; font-size: 0.9em;">{}</span>',
                preview
            )
        except:
            return '-'
    reply_preview.short_description = 'Reply'

    def created_at_display(self, obj):
        return obj.created_at.strftime('%Y-%m-%d %H:%M:%S')
    created_at_display.short_description = 'Created'

