from django.contrib import admin, messages
from django.utils.html import format_html
from import_export import resources
from import_export.admin import ImportExportModelAdmin
from orchestration.personas import archive_persona, confirm_persona
from .grants import revoke_grant
from .models import (
    Handoff,
    Persona,
    PersonaRequest,
    RoutineCheckIn,
    StandingGrant,
    WorkflowApprovalRecord,
    WorkflowCandidate,
    WorkflowDraft,
    WorkflowExecution,
    WorkflowImprovementSuggestion,
    WorkflowTestRun,
    WorkflowTrigger,
    WorkflowVersion,
    UserWorkflow,
)


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


class WorkflowApprovalRecordResource(resources.ModelResource):
    class Meta:
        model = WorkflowApprovalRecord
        fields = ['id', 'workflow', 'execution', 'step_id', 'action', 'status', 'created_at']
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
        except Exception:
            return '-'
    name_display.short_description = 'Draft'

    def created_at_display(self, obj):
        return obj.created_at.strftime('%Y-%m-%d %H:%M')
    created_at_display.short_description = 'Created'


@admin.register(UserWorkflow)
class UserWorkflowAdmin(ImportExportModelAdmin):
    resource_class = UserWorkflowResource
    list_display = ['name', 'user', 'status_badge', 'execution_count', 'created_at', 'last_executed_at']
    list_filter = ['status', 'created_at']
    search_fields = ['name', 'user__username', 'user__email', 'description']
    readonly_fields = ['created_at', 'updated_at', 'execution_count', 'last_executed_at']
    ordering = ['-created_at']
    autocomplete_fields = ['user']
    date_hierarchy = 'created_at'

    fieldsets = (
        ('Workflow Info', {
            'fields': ('user', 'name', 'description')
        }),
        ('Configuration', {
            'fields': ('status', 'execution_count', 'last_executed_at')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def status_badge(self, obj):
        colors = {
            'active': '#27ae60',
            'paused': '#f39c12',
            'failed': '#e74c3c',
            'deleted': '#95a5a6'
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
    list_display = ['id', 'workflow', 'status_badge', 'current_step', 'waiting_on', 'duration_display', 'started_at']
    list_filter = ['status', 'started_at', 'completed_at']
    search_fields = ['workflow__name', 'id']
    readonly_fields = ['started_at', 'completed_at']
    ordering = ['-started_at']
    autocomplete_fields = ['workflow']
    date_hierarchy = 'started_at'

    fieldsets = (
        ('Execution Info', {
            'fields': ('workflow', 'temporal_workflow_id', 'temporal_run_id')
        }),
        ('Trigger', {
            'fields': ('trigger_type', 'trigger_data')
        }),
        ('Status', {
            'fields': (
                'status',
                'current_step',
                'last_completed_step',
                'waiting_on',
                'pending_approval',
                'attempts',
                'receipt_ids',
                'result_summary',
                'failure_summary',
                'recovery_suggestion',
                'result',
                'error_message',
            )
        }),
        ('Timeline', {
            'fields': ('started_at', 'completed_at'),
        }),
    )

    def status_badge(self, obj):
        colors = {
            'pending': '#f39c12',
            'running': '#3498db',
            'waiting': '#8e44ad',
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
    list_display = ['trigger_type_display', 'workflow', 'schedule_status_badge', 'is_active_badge', 'created_at']
    list_filter = ['created_at', 'trigger_type']
    search_fields = ['workflow__name', 'trigger_type']
    readonly_fields = ['created_at', 'updated_at', 'last_triggered_at', 'trigger_count']
    ordering = ['-created_at']
    autocomplete_fields = ['workflow']
    date_hierarchy = 'created_at'

    fieldsets = (
        ('Trigger Info', {
            'fields': ('workflow', 'trigger_type', 'service', 'event')
        }),
        ('Configuration', {
            'fields': (
                'config',
                'webhook_secret',
                'webhook_url',
                'schedule_cron',
                'schedule_timezone',
                'temporal_schedule_id',
                'schedule_status',
                'schedule_last_error',
                'is_active',
            ),
            'classes': ('collapse',)
        }),
        ('Activity', {
            'fields': ('last_triggered_at', 'trigger_count'),
            'classes': ('collapse',)
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
            'manual': '#f39c12'
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

    def schedule_status_badge(self, obj):
        colors = {
            'active': '#27ae60',
            'paused': '#f39c12',
            'unavailable': '#e74c3c',
            'deleted': '#95a5a6',
        }
        status = getattr(obj, 'schedule_status', 'active')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px;">{}</span>',
            colors.get(status, '#3498db'),
            status.title(),
        )
    schedule_status_badge.short_description = 'Schedule'


@admin.register(WorkflowApprovalRecord)
class WorkflowApprovalRecordAdmin(ImportExportModelAdmin):
    resource_class = WorkflowApprovalRecordResource
    list_display = ['id', 'workflow', 'execution', 'step_id', 'action', 'status', 'expires_at', 'created_at']
    list_filter = ['status', 'action', 'created_at']
    search_fields = ['workflow__name', 'execution__id', 'step_id', 'action']
    readonly_fields = ['created_at', 'reviewed_at']
    autocomplete_fields = ['workflow', 'execution', 'requested_by', 'reviewed_by']


@admin.register(WorkflowImprovementSuggestion)
class WorkflowImprovementSuggestionAdmin(admin.ModelAdmin):
    list_display = ['id', 'workflow', 'suggestion_type', 'status', 'created_at']
    list_filter = ['status', 'suggestion_type', 'created_at']
    search_fields = ['workflow__name', 'title', 'summary']
    readonly_fields = ['created_at']
    autocomplete_fields = ['workflow', 'execution', 'user']


@admin.register(Persona)
class PersonaAdmin(admin.ModelAdmin):
    list_display = ['name', 'user', 'status_badge', 'risk_ceiling', 'room', 'created_at']
    list_filter = ['status', 'risk_ceiling', 'created_at']
    search_fields = ['name', 'user__username', 'description']
    readonly_fields = ['created_at', 'updated_at']
    autocomplete_fields = ['user', 'room', 'created_from']
    actions = ['activate_personas', 'archive_personas']

    @admin.action(description="Activate selected personas (human promotion)")
    def activate_personas(self, request, queryset):
        for persona in queryset:
            confirm_persona(persona)
        self.message_user(request, f"Activated {queryset.count()} persona(s).")

    @admin.action(description="Archive selected personas")
    def archive_personas(self, request, queryset):
        for persona in queryset:
            archive_persona(persona)
        self.message_user(request, f"Archived {queryset.count()} persona(s).")

    def status_badge(self, obj):
        colors = {'draft': '#95a5a6', 'active': '#27ae60', 'archived': '#e74c3c'}
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px;">{}</span>',
            colors.get(obj.status, '#3498db'),
            obj.get_status_display(),
        )
    status_badge.short_description = 'Status'


@admin.register(PersonaRequest)
class PersonaRequestAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'user', 'status', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['name', 'user__username', 'description']
    readonly_fields = ['reviewed_by', 'reviewed_at', 'created_at']
    autocomplete_fields = ['user', 'room']
    actions = ['approve_requests', 'reject_requests']

    @admin.action(description="Approve: create a draft persona for the requester")
    def approve_requests(self, request, queryset):
        from django.utils import timezone

        from orchestration.personas import create_persona

        created = 0
        for persona_request in queryset.filter(status='pending'):
            try:
                create_persona(
                    persona_request.user,
                    name=persona_request.name[:100],
                    description=persona_request.description,
                )
            except Exception as exc:
                self.message_user(
                    request,
                    f"Could not create persona for {persona_request.user}: {exc}",
                    level=messages.ERROR,
                )
                continue
            persona_request.status = 'approved'
            persona_request.reviewed_by = request.user
            persona_request.reviewed_at = timezone.now()
            persona_request.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])
            created += 1
        self.message_user(request, f"Created {created} draft persona(s) for the requester(s).")

    @admin.action(description="Reject selected persona requests")
    def reject_requests(self, request, queryset):
        from django.utils import timezone

        updated = queryset.filter(status='pending').update(
            status='rejected', reviewed_by=request.user, reviewed_at=timezone.now(),
        )
        self.message_user(request, f"Rejected {updated} persona request(s).")


@admin.register(Handoff)
class HandoffAdmin(admin.ModelAdmin):
    list_display = ['id', 'to_persona', 'requested_by', 'status', 'outcome', 'budget_used', 'created_at']
    list_filter = ['status', 'outcome', 'created_at']
    search_fields = ['to_persona__name', 'requested_by__username']
    readonly_fields = [
        'result', 'artifacts', 'receipts', 'budget_used', 'stopped_reason',
        'created_at', 'updated_at', 'completed_at',
    ]
    autocomplete_fields = ['from_persona', 'to_persona', 'requested_by']


@admin.register(StandingGrant)
class StandingGrantAdmin(admin.ModelAdmin):
    list_display = ['id', 'workflow', 'user', 'decision', 'status', 'workflow_version', 'lapse_reason', 'expires_at']
    list_filter = ['decision', 'status', 'created_at']
    search_fields = ['workflow__name', 'user__username', 'lapse_reason']
    readonly_fields = ['created_at', 'updated_at', 'lapsed_at']
    autocomplete_fields = ['workflow', 'user', 'approval_record']
    actions = ['revoke_grants']

    @admin.action(description="Revoke selected grants")
    def revoke_grants(self, request, queryset):
        for grant in queryset:
            revoke_grant(grant)
        self.message_user(request, f"Revoked {queryset.count()} grant(s).")


@admin.register(WorkflowVersion)
class WorkflowVersionAdmin(admin.ModelAdmin):
    list_display = ['workflow', 'version', 'change_summary', 'created_by', 'created_at']
    list_filter = ['created_at']
    search_fields = ['workflow__name', 'change_summary']
    readonly_fields = ['workflow', 'version', 'definition', 'capabilities', 'change_summary', 'created_by', 'created_at']


@admin.register(WorkflowTestRun)
class WorkflowTestRunAdmin(admin.ModelAdmin):
    list_display = ['workflow', 'definition_version', 'status', 'approval_stop_point', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['workflow__name', 'summary']
    readonly_fields = [
        'workflow', 'definition_version', 'status', 'inputs', 'output_preview',
        'audit_trail', 'approval_stop_point', 'failure_states', 'summary',
        'created_at', 'completed_at',
    ]


@admin.register(RoutineCheckIn)
class RoutineCheckInAdmin(admin.ModelAdmin):
    list_display = ['user', 'status', 'prompted_at', 'answered_at']
    list_filter = ['status']
    search_fields = ['user__username']
    readonly_fields = ['status', 'prompted_at', 'answered_at', 'paused_workflow_ids', 'created_at']


@admin.register(WorkflowCandidate)
class WorkflowCandidateAdmin(admin.ModelAdmin):
    list_display = ['user', 'tool_sequence_display', 'occurrences', 'success_rate', 'status', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['user__username']
    readonly_fields = ['pattern', 'pattern_key', 'occurrences', 'success_rate', 'created_at', 'updated_at']
    autocomplete_fields = ['user', 'draft']

    def tool_sequence_display(self, obj):
        return ', '.join(obj.pattern.get('tool_sequence') or [])
    tool_sequence_display.short_description = 'Tool sequence'
