from django.contrib import admin
from django.utils.html import format_html
from import_export import resources
from import_export.admin import ImportExportModelAdmin
from .models import Message, Member, Chatroom


# Export Resources
class MessageResource(resources.ModelResource):
    class Meta:
        model = Message
        fields = ['id', 'member', 'content', 'created_at']
        export_order = fields


class ChatroomResource(resources.ModelResource):
    class Meta:
        model = Chatroom
        fields = ['id', 'user', 'created_at', 'updated_at']
        export_order = fields


@admin.register(Message)
class MessageAdmin(ImportExportModelAdmin):
    resource_class = MessageResource
    list_display = ['member_display', 'content_preview', 'timestamp_display', 'created_at']
    list_filter = ['created_at', 'member__chatroom']
    search_fields = ['member__user__username', 'member__user__email', 'content']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['-created_at']
    autocomplete_fields = ['member']
    date_hierarchy = 'created_at'

    fieldsets = (
        ('Message Details', {
            'fields': ('member', 'content')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def member_display(self, obj):
        return f"{obj.member.user.email if obj.member and obj.member.user else 'Unknown'}"
    member_display.short_description = 'Sent By'

    def content_preview(self, obj):
        preview = obj.content[:60] + '...' if len(obj.content) > 60 else obj.content
        return format_html(
            '<span style="color: #555; font-family: monospace; font-size: 0.9em;">{}</span>',
            preview
        )
    content_preview.short_description = 'Content'

    def timestamp_display(self, obj):
        return obj.created_at.strftime('%Y-%m-%d %H:%M')
    timestamp_display.short_description = 'Sent At'


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = ['user_display', 'chatroom', 'join_date_display', 'message_count']
    list_filter = ['chatroom', 'created_at']
    search_fields = ['user__username', 'user__email', 'chatroom__user__username']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['-created_at']
    autocomplete_fields = ['user', 'chatroom']
    date_hierarchy = 'created_at'

    fieldsets = (
        ('Member Info', {
            'fields': ('user', 'chatroom')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def user_display(self, obj):
        return f"{obj.user.email}"
    user_display.short_description = 'User'

    def join_date_display(self, obj):
        return obj.created_at.strftime('%Y-%m-%d')
    join_date_display.short_description = 'Joined'

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
    list_display = ['room_name', 'owner', 'member_count', 'message_count_display', 'created_at']
    list_filter = ['created_at']
    search_fields = ['user__username', 'user__email']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['-created_at']
    autocomplete_fields = ['user']
    date_hierarchy = 'created_at'

    fieldsets = (
        ('Room Info', {
            'fields': ('user',)
        }),
        ('Statistics', {
            'fields': ('member_count_readonly', 'message_count_readonly'),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def room_name(self, obj):
        return f"Room #{obj.id}"
    room_name.short_description = 'Chatroom'

    def owner(self, obj):
        return obj.user.email if obj.user else 'Unknown'
    owner.short_description = 'Owner'

    def member_count(self, obj):
        count = Member.objects.filter(chatroom=obj).count()
        color = '#3498db'
        weight = 'bold' if count > 5 else 'normal'
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px; font-weight: {};">{} members</span>',
            color,
            weight,
            count
        )
    member_count.short_description = 'Members'

    def message_count_display(self, obj):
        count = Message.objects.filter(member__chatroom=obj).count()
        color = '#27ae60' if count > 0 else '#95a5a6'
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px;">{} messages</span>',
            color,
            count
        )
    message_count_display.short_description = 'Messages'

    def member_count_readonly(self, obj):
        return Member.objects.filter(chatroom=obj).count()
    member_count_readonly.short_description = 'Total Members'

    def message_count_readonly(self, obj):
        return Message.objects.filter(member__chatroom=obj).count()
    message_count_readonly.short_description = 'Total Messages'

