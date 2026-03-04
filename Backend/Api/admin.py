from django.contrib import admin
from django.utils.html import format_html
from .models import MathiaReply


@admin.register(MathiaReply)
class MathiaReplyAdmin(admin.ModelAdmin):
    list_display = ['id', 'sender', 'chatid', 'message_preview']
    list_filter = ['sender']
    search_fields = ['sender', 'message', 'command', 'chatid']
    ordering = ['-id']

    fieldsets = (
        ('Reply Details', {
            'fields': ('sender', 'chatid', 'command', 'message')
        }),
    )

    def message_preview(self, obj):
        preview = obj.message[:60] + '...' if len(obj.message) > 60 else obj.message
        return format_html(
            '<span style="color: #555; font-family: monospace; font-size: 0.9em;">{}</span>',
            preview
        )
    message_preview.short_description = 'Message'

