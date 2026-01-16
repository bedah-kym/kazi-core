"""
Django Admin configuration for Payment models
"""
from django.contrib import admin
from django.utils.html import format_html
from .models import (
    LedgerAccount, JournalEntry, LedgerEntry, PaymentRequest,
    FeeSchedule, PaymentNotification, Dispute, ReconciliationDiscrepancy
)


@admin.register(LedgerAccount)
class LedgerAccountAdmin(admin.ModelAdmin):
    list_display = ['name', 'account_type', 'balance', 'currency', 'is_active', 'user']
    list_filter = ['account_type', 'is_active', 'currency']
    search_fields = ['name', 'user__username', 'user__email']
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('Account Information', {
            'fields': ('name', 'account_type', 'user', 'currency')
        }),
        ('Balance', {
            'fields': ('balance', 'is_active')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


class LedgerEntryInline(admin.TabularInline):
    model = LedgerEntry
    extra = 0
    readonly_fields = ['ledger_account', 'amount', 'dr_cr']
    can_delete = False


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    list_display = ['reference_id', 'transaction_type', 'timestamp', 'is_reconciled', 'balance_check']
    list_filter = ['transaction_type', 'is_reconciled', 'timestamp']
    search_fields = ['reference_id', 'description', 'provider_reference']
    readonly_fields = ['reference_id', 'created_at', 'balance_check']
    inlines = [LedgerEntryInline]
    date_hierarchy = 'timestamp'
    
    def balance_check(self, obj):
        if obj.verify_balance():
            return format_html('<span style="color: green;">✓ Balanced</span>')
        return format_html('<span style="color: red;">✗ Unbalanced</span>')
    balance_check.short_description = 'Balance Status'


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = ['journal_entry', 'ledger_account', 'amount', 'dr_cr']
    list_filter = ['dr_cr']
    search_fields = ['journal_entry__reference_id', 'ledger_account__name']
    readonly_fields = ['journal_entry', 'ledger_account', 'amount', 'dr_cr']


@admin.register(PaymentRequest)
class PaymentRequestAdmin(admin.ModelAdmin):
    list_display = ['reference_id', 'issuer', 'amount', 'status', 'is_recurring', 'created_at']
    list_filter = ['status', 'is_recurring', 'currency', 'created_at']
    search_fields = ['reference_id', 'issuer__username', 'payer_email', 'description']
    readonly_fields = ['reference_id', 'created_at', 'paid_at']
    date_hierarchy = 'created_at'
    
    fieldsets = (
        ('Invoice Details', {
            'fields': ('reference_id', 'issuer', 'payer', 'payer_email', 'amount', 'currency', 'description')
        }),
        ('Status', {
            'fields': ('status', 'intasend_payment_link', 'intasend_invoice_id', 'journal_entry')
        }),
        ('Recurring', {
            'fields': ('is_recurring', 'recurrence_interval', 'next_billing_date', 'parent_invoice'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'expires_at', 'paid_at')
        }),
    )


@admin.register(FeeSchedule)
class FeeScheduleAdmin(admin.ModelAdmin):
    list_display = ['transaction_type', 'platform_fee', 'is_active', 'updated_at']
    list_filter = ['is_active', 'transaction_type']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(PaymentNotification)
class PaymentNotificationAdmin(admin.ModelAdmin):
    list_display = ['user', 'notification_type', 'message_preview', 'is_read', 'created_at']
    list_filter = ['notification_type', 'is_read', 'created_at']
    search_fields = ['user__username', 'message']
    readonly_fields = ['created_at']
    date_hierarchy = 'created_at'
    
    def message_preview(self, obj):
        return obj.message[:50] + '...' if len(obj.message) > 50 else obj.message
    message_preview.short_description = 'Message'


@admin.register(Dispute)
class DisputeAdmin(admin.ModelAdmin):
    list_display = ['id', 'transaction', 'reporter', 'status', 'created_at', 'resolved_at']
    list_filter = ['status', 'created_at']
    search_fields = ['transaction__reference_id', 'reporter__username', 'reason']
    readonly_fields = ['created_at']
    date_hierarchy = 'created_at'
    
    fieldsets = (
        ('Dispute Information', {
            'fields': ('transaction', 'reporter', 'reason', 'status')
        }),
        ('Resolution', {
            'fields': ('resolution_notes', 'resolved_by', 'resolved_at'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at',)
        }),
    )


@admin.register(ReconciliationDiscrepancy)
class ReconciliationDiscrepancyAdmin(admin.ModelAdmin):
    list_display = ['date', 'difference', 'severity', 'is_resolved', 'created_at']
    list_filter = ['severity', 'is_resolved', 'date']
    search_fields = ['details', 'resolution_notes']
    readonly_fields = ['created_at']
    date_hierarchy = 'date'
    
    fieldsets = (
        ('Discrepancy Details', {
            'fields': ('date', 'expected_balance', 'actual_balance', 'difference', 'severity')
        }),
        ('Analysis', {
            'fields': ('details',)
        }),
        ('Resolution', {
            'fields': ('is_resolved', 'resolution_notes', 'resolved_at'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at',)
        }),
    )
