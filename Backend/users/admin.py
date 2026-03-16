from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.utils.html import format_html
from import_export import resources
from import_export.admin import ImportExportModelAdmin
from .models import (
    UserProfile, Workspace, Wallet, WalletTransaction,
    TrialApplication, TrialInvite, PlatformInvite
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
    list_display = ['user_display', 'user_type_badge', 'industry', 'invite_depth', 'invited_by_display', 'onboarding_badge', 'created_at']
    list_filter = ['user_type', 'industry', 'onboarding_completed', 'theme_preference', 'invite_depth']
    search_fields = ['user__username', 'user__email', 'company_name', 'role']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['-created_at']
    autocomplete_fields = ['user']

    fieldsets = (
        ('User', {'fields': ('user',)}),
        ('Profile Info', {'fields': ('bio', 'avatar', 'location', 'website')}),
        ('Professional', {'fields': ('user_type', 'industry', 'company_name', 'company_size', 'role')}),
        ('Invite Chain', {'fields': ('invited_by', 'invite_depth')}),
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
            'personal': '#3498db',
            'team': '#2ecc71',
            'business': '#e74c3c'
        }
        color = colors.get(obj.user_type, '#3498db')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px;">{}</span>',
            color,
            obj.get_user_type_display()
        )
    user_type_badge.short_description = 'Type'

    def invited_by_display(self, obj):
        if obj.invited_by:
            return obj.invited_by.email
        return '-'
    invited_by_display.short_description = 'Invited By'

    def onboarding_badge(self, obj):
        color = '#27ae60' if obj.onboarding_completed else '#e74c3c'
        status = 'Completed' if obj.onboarding_completed else 'Pending'
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px;">{}</span>',
            color,
            status
        )
    onboarding_badge.short_description = 'Onboarding'


@admin.register(PlatformInvite)
class PlatformInviteAdmin(admin.ModelAdmin):
    list_display = ['email', 'invited_by', 'invite_depth', 'status_badge', 'sent_at', 'activated_at', 'expires_at']
    list_filter = ['status', 'invite_depth', 'sent_at']
    search_fields = ['email', 'invited_by__username', 'invited_by__email']
    readonly_fields = ['token', 'sent_at']
    ordering = ['-sent_at']

    def status_badge(self, obj):
        colors = {'sent': '#f39c12', 'activated': '#27ae60', 'expired': '#e74c3c', 'revoked': '#95a5a6'}
        color = colors.get(obj.status, '#3498db')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 5px 10px; border-radius: 3px; text-transform: capitalize; font-weight: bold;">{}</span>',
            color, obj.get_status_display()
        )
    status_badge.short_description = 'Status'


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ['name', 'owner', 'plan_badge', 'account_type', 'onboarding_badge', 'trial_badge']
    list_filter = ['plan', 'account_type', 'onboarding_completed', 'trial_active']
    search_fields = ['name', 'owner__username', 'owner__email']
    readonly_fields = ['created_at']
    ordering = ['-created_at']
    autocomplete_fields = ['owner']

    fieldsets = (
        ('Info', {'fields': ('name', 'owner')}),
        ('Plan & Type', {'fields': ('plan', 'account_type')}),
        ('Status', {'fields': ('onboarding_completed',)}),
        ('Trial', {'fields': ('trial_active', 'trial_started_at', 'trial_ends_at')}),
        ('Timestamps', {'fields': ('created_at',), 'classes': ('collapse',)}),
    )

    def plan_badge(self, obj):
        colors = {
            'free': '#95a5a6',
            'trial': '#f39c12',
            'pro': '#3498db',
            'agency': '#e74c3c'
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
    search_fields = ['workspace__name', 'workspace__owner__email']
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
    search_fields = ['wallet__workspace__name', 'reference']
    readonly_fields = ['created_at']
    date_hierarchy = 'created_at'
    ordering = ['-created_at']
    autocomplete_fields = ['wallet']

    def type_badge(self, obj):
        colors = {
            'CREDIT': '#27ae60',
            'DEBIT': '#e74c3c'
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
            'PENDING': '#f39c12',
            'COMPLETED': '#27ae60',
            'FAILED': '#e74c3c'
        }
        color = colors.get(obj.status, '#95a5a6')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px; text-transform: capitalize;">{}</span>',
            color,
            obj.get_status_display()
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
    search_fields = ['name', 'email', 'company', 'primary_use_case', 'pain_points', 'notes']
    readonly_fields = ['created_at']
    date_hierarchy = 'created_at'
    ordering = ['-created_at']
    actions = [approve_apps]

    def status_badge(self, obj):
        colors = {
            'pending': '#f39c12',
            'approved': '#27ae60',
            'rejected': '#e74c3c'
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
    readonly_fields = ['token', 'created_at']
    date_hierarchy = 'sent_at'
    ordering = ['-sent_at']
    autocomplete_fields = ['sent_by']

    def status_badge(self, obj):
        colors = {
            'sent': '#f39c12',
            'activated': '#27ae60',
            'expired': '#e74c3c'
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

