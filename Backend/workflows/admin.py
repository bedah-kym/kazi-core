from django.contrib import admin
from django.utils.html import format_html
from import_export import resources
from import_export.admin import ImportExportModelAdmin
from .models import WorkflowDraft, UserWorkflow, WorkflowExecution, WorkflowTrigger


# Export Resources
class UserWorkflowResource(resources.ModelResource):
    class Meta:
        model = UserWorkflow
        fields = ['id', 'user', 'name', 'status', 'created_at']
        export_order = fields


class WorkflowExecutionResource(resources.ModelResource):
    class Meta:
        model = WorkflowExecution
        fields = ['id', 'workflow', 'status', 'started_at', 'completed_at']
        export_order = fields


@admin.register(WorkflowDraft)
class WorkflowDraftAdmin(admin.ModelAdmin):
    list_display = ['id', 'name_display', 'created_at_display']
    list_filter = ['created_at']
    search_fields = ['id']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['-created_at']
    date_hierarchy = 'created_at'

    fieldsets = (
        ('Draft Details', {
            'fields': ('id',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def name_display(self, obj):
        try:
            name = str(obj)[:40] + '...' if len(str(obj)) > 40 else str(obj)
            return format_html(
                '<span style="color: #555;">{}</span>',
                name
            )
        except:
            return '-'
    name_display.short_description = 'Draft'

    def created_at_display(self, obj):
        return obj.created_at.strftime('%Y-%m-%d %H:%M')
    created_at_display.short_description = 'Created'


@admin.register(UserWorkflow)
class UserWorkflowAdmin(ImportExportModelAdmin):
    resource_class = UserWorkflowResource
    list_display = ['name', 'user', 'status_badge', 'execution_count', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['name', 'user__username', 'user__email', 'description']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['-created_at']
    autocomplete_fields = ['user']
    date_hierarchy = 'created_at'

    fieldsets = (
        ('Workflow Info', {
            'fields': ('user', 'name', 'description')
        }),
        ('Configuration', {
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
            'active': '#27ae60',
            'paused': '#f39c12',
            'archived': '#e74c3c'
        }
        color = colors.get(obj.status, '#3498db')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 5px 10px; border-radius: 3px; text-transform: capitalize; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display() if hasattr(obj, 'get_status_display') else obj.status
        )
    status_badge.short_description = 'Status'

    def execution_count(self, obj):
        count = WorkflowExecution.objects.filter(workflow=obj).count()
        color = '#3498db'
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px; font-weight: bold;">{} executions</span>',
            color,
            count
        )
    execution_count.short_description = 'Executions'


@admin.register(WorkflowExecution)
class WorkflowExecutionAdmin(ImportExportModelAdmin):
    resource_class = WorkflowExecutionResource
    list_display = ['id', 'workflow', 'status_badge', 'duration_display', 'started_at']
    list_filter = ['status', 'started_at', 'completed_at']
    search_fields = ['workflow__name', 'id']
    readonly_fields = ['created_at', 'updated_at', 'started_at', 'completed_at']
    ordering = ['-started_at']
    autocomplete_fields = ['workflow']
    date_hierarchy = 'started_at'

    fieldsets = (
        ('Execution Info', {
            'fields': ('workflow', 'id')
        }),
        ('Status', {
            'fields': ('status',)
        }),
        ('Timeline', {
            'fields': ('started_at', 'completed_at'),
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def status_badge(self, obj):
        colors = {
            'pending': '#f39c12',
            'running': '#3498db',
            'completed': '#27ae60',
            'failed': '#e74c3c',
            'cancelled': '#95a5a6'
        }
        color = colors.get(obj.status, '#95a5a6')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 5px 10px; border-radius: 3px; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display() if hasattr(obj, 'get_status_display') else obj.status
        )
    status_badge.short_description = 'Status'

    def duration_display(self, obj):
        if obj.started_at and obj.completed_at:
            duration = obj.completed_at - obj.started_at
            seconds = int(duration.total_seconds())
            minutes = seconds // 60
            seconds = seconds % 60
            return format_html(
                '<span style="color: #555; font-weight: bold;">{}m {}s</span>',
                minutes,
                seconds
            )
        return '-'
    duration_display.short_description = 'Duration'


@admin.register(WorkflowTrigger)
class WorkflowTriggerAdmin(admin.ModelAdmin):
    list_display = ['trigger_type_display', 'workflow', 'is_active_badge', 'created_at']
    list_filter = ['created_at', 'trigger_type']
    search_fields = ['workflow__name', 'trigger_type']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['-created_at']
    autocomplete_fields = ['workflow']
    date_hierarchy = 'created_at'

    fieldsets = (
        ('Trigger Info', {
            'fields': ('workflow', 'trigger_type')
        }),
        ('Configuration', {
            'fields': ('trigger_config', 'is_active'),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def trigger_type_display(self, obj):
        colors = {
            'webhook': '#3498db',
            'schedule': '#2ecc71',
            'manual': '#f39c12',
            'event': '#e74c3c'
        }
        color = colors.get(obj.trigger_type, '#95a5a6')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px;">{}</span>',
            color,
            obj.get_trigger_type_display() if hasattr(obj, 'get_trigger_type_display') else obj.trigger_type
        )
    trigger_type_display.short_description = 'Type'

    def is_active_badge(self, obj):
        color = '#27ae60' if obj.is_active else '#e74c3c'
        status = 'Active' if obj.is_active else 'Inactive'
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px;">{}</span>',
            color,
            status
        )
    is_active_badge.short_description = 'Active'

