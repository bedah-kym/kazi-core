from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from .models import (
    UserProfile, Workspace, Wallet, WalletTransaction, GoalProfile,
    TrialApplication, TrialInvite
)

class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = 'profile'

class UserAdmin(BaseUserAdmin):
    inlines = (UserProfileInline,)

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'user_type', 'industry', 'onboarding_completed', 'created_at']
    list_filter = ['user_type', 'industry', 'onboarding_completed', 'theme_preference']
    search_fields = ['user__username', 'user__email', 'company_name', 'role']
    fieldsets = (
        ('User', {'fields': ('user',)}),
        ('Profile Info', {'fields': ('bio', 'avatar', 'location', 'website')}),
        ('Professional', {'fields': ('user_type', 'industry', 'company_name', 'company_size', 'role')}),
        ('Social Links', {'fields': ('social_links', 'twitter_handle', 'linkedin_url', 'github_url')}),
        ('Preferences', {'fields': ('timezone', 'language', 'theme_preference', 'notification_preferences')}),
        ('Onboarding', {'fields': ('onboarding_completed', 'onboarding_step')}),
    )

@admin.register(GoalProfile)
class GoalProfileAdmin(admin.ModelAdmin):
    list_display = ['workspace', 'industry', 'experience_level', 'target_revenue', 'ai_personalization_enabled']
    list_filter = ['experience_level', 'industry', 'ai_personalization_enabled']
    search_fields = ['workspace__name', 'skills', 'goals']
    fieldsets = (
        ('Workspace', {'fields': ('workspace',)}),
        ('Goals', {'fields': ('goals', 'custom_goals')}),
        ('Skills', {'fields': ('industry', 'skills', 'experience_level')}),
        ('Needs', {'fields': ('needs', 'custom_needs')}),
        ('Use Cases', {'fields': ('use_cases',)}),
        ('Roadmap', {'fields': ('roadmap',)}),
        ('Targets', {'fields': ('target_revenue', 'target_followers', 'target_clients', 'target_email_subscribers')}),
        ('AI', {'fields': ('ai_personalization_enabled',)}),
    )

@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ['name', 'owner', 'plan', 'account_type', 'onboarding_completed', 'trial_active', 'trial_ends_at']
    list_filter = ['plan', 'account_type', 'onboarding_completed', 'trial_active']
    search_fields = ['name', 'owner__username', 'owner__email']
    fieldsets = (
        ('Info', {'fields': ('name', 'owner')}),
        ('Plan & Type', {'fields': ('plan', 'account_type')}),
        ('Status', {'fields': ('onboarding_completed',)}),
        ('Trial', {'fields': ('trial_active', 'trial_started_at', 'trial_ends_at')}),
    )

@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ['workspace', 'currency', 'balance']

@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    list_display = ['wallet', 'type', 'amount', 'currency', 'status', 'created_at']
    list_filter = ['type', 'status', 'currency']


@admin.action(description="Mark selected applications as approved (no invite sent)")
def approve_apps(modeladmin, request, queryset):
    queryset.update(status='approved')


@admin.register(TrialApplication)
class TrialApplicationAdmin(admin.ModelAdmin):
    list_display = ['name', 'email', 'company', 'team_size', 'status', 'created_at']
    list_filter = ['status', 'industry', 'team_size']
    search_fields = ['name', 'email', 'company', 'primary_use_case']
    actions = [approve_apps]


@admin.register(TrialInvite)
class TrialInviteAdmin(admin.ModelAdmin):
    list_display = ['email', 'status', 'sent_at', 'activated_at', 'trial_ends_at', 'sent_by']
    list_filter = ['status']
    search_fields = ['email', 'token']
