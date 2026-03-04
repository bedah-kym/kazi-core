from django.contrib import admin
from django.utils.html import format_html
from import_export import resources
from import_export.admin import ImportExportModelAdmin
from .models import Message, Member, Chatroom


# Export Resources
class MessageResource(resources.ModelResource):
    class Meta:
        model = Message
        fields = ['id', 'member', 'content', 'timestamp', 'parent', 'is_voice', 'has_ai_voice']
        export_order = fields


class ChatroomResource(resources.ModelResource):
    class Meta:
        model = Chatroom
        fields = ['id']
        export_order = fields


@admin.register(Message)
class MessageAdmin(ImportExportModelAdmin):
    resource_class = MessageResource
    list_display = ['member_display', 'content_preview', 'timestamp_display', 'is_voice', 'has_ai_voice']
    list_filter = ['is_voice', 'has_ai_voice', 'timestamp']
    search_fields = ['member__User__username', 'member__User__email', 'content', 'audio_url', 'voice_transcript']
    readonly_fields = ['timestamp']
    ordering = ['-timestamp']
    autocomplete_fields = ['member']
    date_hierarchy = 'timestamp'

    fieldsets = (
        ('Message Details', {
            'fields': ('member', 'content', 'parent')
        }),
        ('Voice', {
            'fields': ('is_voice', 'audio_url', 'voice_transcript', 'has_ai_voice'),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('timestamp',),
            'classes': ('collapse',)
        }),
    )

    def member_display(self, obj):
        if obj.member and obj.member.User:
            return f"{obj.member.User.email}"
        return 'Unknown'
    member_display.short_description = 'Sent By'

    def content_preview(self, obj):
        preview = obj.content[:60] + '...' if len(obj.content) > 60 else obj.content
        return format_html(
            '<span style="color: #555; font-family: monospace; font-size: 0.9em;">{}</span>',
            preview
        )
    content_preview.short_description = 'Content'

    def timestamp_display(self, obj):
        return obj.timestamp.strftime('%Y-%m-%d %H:%M')
    timestamp_display.short_description = 'Sent At'
    timestamp_display.admin_order_field = 'timestamp'


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = ['user_display', 'last_seen_display', 'message_count']
    list_filter = ['last_seen']
    search_fields = ['User__username', 'User__email']
    readonly_fields = ['last_seen']
    ordering = ['-last_seen']
    autocomplete_fields = ['User']
    date_hierarchy = 'last_seen'

    fieldsets = (
        ('Member Info', {
            'fields': ('User',)
        }),
        ('Activity', {
            'fields': ('last_seen',),
            'classes': ('collapse',)
        }),
    )

    def user_display(self, obj):
        return f"{obj.User.email}"
    user_display.short_description = 'User'

    def last_seen_display(self, obj):
        if not obj.last_seen:
            return '-'
        return obj.last_seen.strftime('%Y-%m-%d')
    last_seen_display.short_description = 'Last Seen'

    def message_count(self, obj):
        count = Message.objects.filter(member=obj).count()
        color = '#27ae60' if count > 0 else '#95a5a6'
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px; font-weight: bold;">{} messages</span>',
            color,
            count
        )
    message_count.short_description = 'Activity'


@admin.register(Chatroom)
class ChatroomAdmin(ImportExportModelAdmin):
    resource_class = ChatroomResource
    list_display = ['id', 'participants_count', 'messages_count']
    search_fields = ['id', 'participants__User__username', 'participants__User__email']
    readonly_fields = ['encryption_key', 'participants_count', 'messages_count']
    ordering = ['-id']
    filter_horizontal = ['participants']

    fieldsets = (
        ('Members', {
            'fields': ('participants',)
        }),
        ('Security', {
            'fields': ('encryption_key',)
        }),
        ('Statistics', {
            'fields': ('participants_count', 'messages_count'),
            'classes': ('collapse',)
        }),
    )

    def participants_count(self, obj):
        return obj.participants.count()
    participants_count.short_description = 'Members'

    def messages_count(self, obj):
        return obj.chats.count()
    messages_count.short_description = 'Messages'

