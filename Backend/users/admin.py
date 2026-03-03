from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.utils.html import format_html
from import_export import resources
from import_export.admin import ImportExportModelAdmin
from .models import (
    UserProfile, Workspace, Wallet, WalletTransaction, GoalProfile,
    TrialApplication, TrialInvite
)


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = 'profile'
    readonly_fields = ['created_at', 'updated_at']


class UserAdmin(BaseUserAdmin):
    inlines = (UserProfileInline,)
    search_fields = ['username', 'email', 'first_name', 'last_name']
    ordering = ['-date_joined']


# Export Resources
class TrialApplicationResource(resources.ModelResource):
    class Meta:
        model = TrialApplication
        fields = ['id', 'name', 'email', 'company', 'status', 'created_at']
        export_order = fields


class TrialInviteResource(resources.ModelResource):
    class Meta:
        model = TrialInvite
        fields = ['id', 'email', 'status', 'sent_at', 'activated_at', 'trial_ends_at', 'sent_by']
        export_order = fields


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user_display', 'user_type_badge', 'industry', 'onboarding_badge', 'created_at']
    list_filter = ['user_type', 'industry', 'onboarding_completed', 'theme_preference']
    search_fields = ['user__username', 'user__email', 'company_name', 'role']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['-created_at']
    autocomplete_fields = ['user']

    fieldsets = (
        ('User', {'fields': ('user',)}),
        ('Profile Info', {'fields': ('bio', 'avatar', 'location', 'website')}),
        ('Professional', {'fields': ('user_type', 'industry', 'company_name', 'company_size', 'role')}),
        ('Social Links', {'fields': ('social_links', 'twitter_handle', 'linkedin_url', 'github_url')}),
        ('Preferences', {'fields': ('timezone', 'language', 'theme_preference', 'notification_preferences')}),
        ('Onboarding', {'fields': ('onboarding_completed', 'onboarding_step')}),
        ('Timestamps', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )

    def user_display(self, obj):
        return f"{obj.user.email} ({obj.user.get_full_name() or obj.user.username})"
    user_display.short_description = 'User'

    def user_type_badge(self, obj):
        colors = {
            'creator': '#3498db',
            'manager': '#2ecc71',
            'developer': '#e74c3c',
            'other': '#95a5a6'
        }
        color = colors.get(obj.user_type, '#3498db')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px;">{}</span>',
            color,
            obj.get_user_type_display()
        )
    user_type_badge.short_description = 'Type'

    def onboarding_badge(self, obj):
        color = '#27ae60' if obj.onboarding_completed else '#e74c3c'
        status = 'Completed' if obj.onboarding_completed else 'Pending'
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px;">{}</span>',
            color,
            status
        )
    onboarding_badge.short_description = 'Onboarding'


@admin.register(GoalProfile)
class GoalProfileAdmin(admin.ModelAdmin):
    list_display = ['workspace_link', 'industry', 'experience_level', 'target_revenue', 'ai_enabled_badge']
    list_filter = ['experience_level', 'industry', 'ai_personalization_enabled']
    search_fields = ['workspace__name', 'workspace__owner__email', 'skills', 'goals']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['-created_at']
    autocomplete_fields = ['workspace']

    fieldsets = (
        ('Workspace', {'fields': ('workspace',)}),
        ('Goals', {'fields': ('goals', 'custom_goals')}),
        ('Skills', {'fields': ('industry', 'skills', 'experience_level')}),
        ('Needs', {'fields': ('needs', 'custom_needs')}),
        ('Use Cases', {'fields': ('use_cases',)}),
        ('Roadmap', {'fields': ('roadmap',)}),
        ('Targets', {'fields': ('target_revenue', 'target_followers', 'target_clients', 'target_email_subscribers')}),
        ('AI', {'fields': ('ai_personalization_enabled',)}),
        ('Timestamps', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )

    def workspace_link(self, obj):
        return obj.workspace.name
    workspace_link.short_description = 'Workspace'

    def ai_enabled_badge(self, obj):
        color = '#27ae60' if obj.ai_personalization_enabled else '#95a5a6'
        status = 'Enabled' if obj.ai_personalization_enabled else 'Disabled'
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px;">{}</span>',
            color,
            status
        )
    ai_enabled_badge.short_description = 'AI Personalization'


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ['name', 'owner', 'plan_badge', 'account_type', 'onboarding_badge', 'trial_badge']
    list_filter = ['plan', 'account_type', 'onboarding_completed', 'trial_active']
    search_fields = ['name', 'owner__username', 'owner__email']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['-created_at']
    autocomplete_fields = ['owner']

    fieldsets = (
        ('Info', {'fields': ('name', 'owner')}),
        ('Plan & Type', {'fields': ('plan', 'account_type')}),
        ('Status', {'fields': ('onboarding_completed',)}),
        ('Trial', {'fields': ('trial_active', 'trial_started_at', 'trial_ends_at')}),
        ('Timestamps', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )

    def plan_badge(self, obj):
        colors = {
            'free': '#95a5a6',
            'pro': '#3498db',
            'enterprise': '#e74c3c'
        }
        color = colors.get(obj.plan, '#95a5a6')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px; text-transform: capitalize;">{}</span>',
            color,
            obj.plan
        )
    plan_badge.short_description = 'Plan'

    def onboarding_badge(self, obj):
        color = '#27ae60' if obj.onboarding_completed else '#e74c3c'
        status = 'Done' if obj.onboarding_completed else 'Pending'
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px;">{}</span>',
            color,
            status
        )
    onboarding_badge.short_description = 'Onboarding'

    def trial_badge(self, obj):
        color = '#27ae60' if obj.trial_active else '#95a5a6'
        status = 'Active' if obj.trial_active else 'Inactive'
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px;">{}</span>',
            color,
            status
        )
    trial_badge.short_description = 'Trial'


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ['workspace', 'currency', 'balance_display', 'created_at']
    list_filter = ['currency', 'created_at']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['-created_at']
    autocomplete_fields = ['workspace']

    def balance_display(self, obj):
        color = '#27ae60' if obj.balance > 0 else '#e74c3c'
        return format_html(
            '<span style="color: {}; font-weight: bold;">{} {}</span>',
            color,
            obj.currency,
            f"{obj.balance:,.2f}"
        )
    balance_display.short_description = 'Balance'


@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    list_display = ['wallet', 'type_badge', 'amount_display', 'status_badge', 'created_at']
    list_filter = ['type', 'status', 'currency', 'created_at']
    search_fields = ['wallet__workspace__name', 'reference_id']
    readonly_fields = ['created_at']
    date_hierarchy = 'created_at'
    ordering = ['-created_at']
    autocomplete_fields = ['wallet']

    def type_badge(self, obj):
        colors = {
            'deposit': '#27ae60',
            'withdrawal': '#e74c3c',
            'payment': '#3498db',
            'refund': '#f39c12'
        }
        color = colors.get(obj.type, '#95a5a6')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px; text-transform: capitalize;">{}</span>',
            color,
            obj.get_type_display()
        )
    type_badge.short_description = 'Type'

    def amount_display(self, obj):
        return f"{obj.currency} {obj.amount:,.2f}"
    amount_display.short_description = 'Amount'

    def status_badge(self, obj):
        colors = {
            'pending': '#f39c12',
            'completed': '#27ae60',
            'failed': '#e74c3c'
        }
        color = colors.get(obj.status, '#95a5a6')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px; text-transform: capitalize;">{}</span>',
            color,
            obj.status
        )
    status_badge.short_description = 'Status'


@admin.action(description="Approve selected applications")
def approve_apps(modeladmin, request, queryset):
    queryset.update(status='approved')


@admin.register(TrialApplication)
class TrialApplicationAdmin(ImportExportModelAdmin):
    resource_class = TrialApplicationResource
    list_display = ['name', 'email', 'company', 'team_size', 'status_badge', 'created_at']
    list_filter = ['status', 'industry', 'team_size', 'created_at']
    search_fields = ['name', 'email', 'company', 'primary_use_case', 'message']
    readonly_fields = ['created_at', 'updated_at']
    date_hierarchy = 'created_at'
    ordering = ['-created_at']
    actions = [approve_apps]

    def status_badge(self, obj):
        colors = {
            'pending': '#f39c12',
            'approved': '#27ae60',
            'rejected': '#e74c3c',
            'invited': '#3498db'
        }
        color = colors.get(obj.status, '#95a5a6')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 5px 10px; border-radius: 3px; text-transform: capitalize; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = 'Status'


@admin.register(TrialInvite)
class TrialInviteAdmin(ImportExportModelAdmin):
    resource_class = TrialInviteResource
    list_display = ['email', 'status_badge', 'sent_at', 'activated_at', 'trial_ends_display', 'sent_by']
    list_filter = ['status', 'sent_at', 'activated_at']
    search_fields = ['email', 'token']
    readonly_fields = ['token', 'created_at', 'updated_at']
    date_hierarchy = 'sent_at'
    ordering = ['-sent_at']
    autocomplete_fields = ['sent_by']

    def status_badge(self, obj):
        colors = {
            'pending': '#f39c12',
            'accepted': '#27ae60',
            'expired': '#e74c3c',
            'revoked': '#95a5a6'
        }
        color = colors.get(obj.status, '#3498db')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 5px 10px; border-radius: 3px; text-transform: capitalize; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = 'Status'

    def trial_ends_display(self, obj):
        if not obj.trial_ends_at:
            return '-'
        color = '#27ae60' if obj.trial_ends_at.date() > __import__('django.utils.timezone', fromlist=['now']).now().date() else '#e74c3c'
        return format_html(
            '<span style="color: {};">{}</span>',
            color,
            obj.trial_ends_at.strftime('%Y-%m-%d')
        )
    trial_ends_display.short_description = 'Trial Ends'

