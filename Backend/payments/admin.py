"""
Django Admin configuration for Payment models
"""
from django.contrib import admin
from django.utils.html import format_html
from import_export import resources, fields
from import_export.admin import ImportExportModelAdmin
from .models import (
    LedgerAccount, JournalEntry, LedgerEntry, PaymentRequest,
    FeeSchedule, PaymentNotification, Dispute, ReconciliationDiscrepancy
)


# Import/Export Resources
class PaymentRequestResource(resources.ModelResource):
    class Meta:
        model = PaymentRequest
        fields = ['id', 'reference_id', 'issuer', 'payer_email', 'amount', 'currency', 'status', 'created_at', 'paid_at']
        export_order = fields


class JournalEntryResource(resources.ModelResource):
    class Meta:
        model = JournalEntry
        fields = ['id', 'reference_id', 'transaction_type', 'timestamp', 'is_reconciled', 'created_at']
        export_order = fields


@admin.register(LedgerAccount)
class LedgerAccountAdmin(admin.ModelAdmin):
    list_display = ['name', 'account_type', 'balance', 'currency', 'is_active', 'user']
    list_filter = ['account_type', 'is_active', 'currency']
    search_fields = ['name', 'user__username', 'user__email']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['-created_at']
    autocomplete_fields = ['user']

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
class JournalEntryAdmin(ImportExportModelAdmin):
    resource_class = JournalEntryResource
    list_display = ['reference_id', 'transaction_type', 'timestamp', 'is_reconciled', 'balance_check']
    list_filter = ['transaction_type', 'is_reconciled', 'timestamp']
    search_fields = ['reference_id', 'description', 'provider_reference']
    readonly_fields = ['reference_id', 'created_at', 'balance_check']
    inlines = [LedgerEntryInline]
    date_hierarchy = 'timestamp'
    ordering = ['-timestamp']

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
    ordering = ['-id']


@admin.register(PaymentRequest)
class PaymentRequestAdmin(ImportExportModelAdmin):
    resource_class = PaymentRequestResource
    list_display = ['reference_id', 'issuer', 'amount_display', 'status_badge', 'is_recurring', 'created_at']
    list_filter = ['status', 'is_recurring', 'currency', 'created_at']
    search_fields = ['reference_id', 'issuer__username', 'payer_email', 'description']
    readonly_fields = ['reference_id', 'created_at', 'paid_at']
    date_hierarchy = 'created_at'
    ordering = ['-created_at']
    autocomplete_fields = ['issuer', 'payer', 'parent_invoice']

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

    def amount_display(self, obj):
        return f"{obj.currency} {obj.amount:,.2f}"
    amount_display.short_description = 'Amount'

    def status_badge(self, obj):
        colors = {
            'PENDING': '#f39c12',
            'PAID': '#27ae60',
            'EXPIRED': '#95a5a6',
            'DISPUTED': '#e74c3c',
            'CANCELLED': '#95a5a6'
        }
        color = colors.get(obj.status, '#3498db')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 5px 10px; border-radius: 3px; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = 'Status'


@admin.register(FeeSchedule)
class FeeScheduleAdmin(admin.ModelAdmin):
    list_display = ['transaction_type', 'platform_fee', 'is_active', 'updated_at']
    list_filter = ['is_active', 'transaction_type']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['-updated_at']


@admin.register(PaymentNotification)
class PaymentNotificationAdmin(admin.ModelAdmin):
    list_display = ['user', 'notification_type', 'message_preview', 'is_read_badge', 'created_at']
    list_filter = ['notification_type', 'is_read', 'created_at']
    search_fields = ['user__username', 'user__email', 'message']
    readonly_fields = ['created_at']
    date_hierarchy = 'created_at'
    ordering = ['-created_at']
    autocomplete_fields = ['user']

    def message_preview(self, obj):
        return obj.message[:50] + '...' if len(obj.message) > 50 else obj.message
    message_preview.short_description = 'Message'

    def is_read_badge(self, obj):
        color = '#27ae60' if obj.is_read else '#e74c3c'
        status_text = 'Read' if obj.is_read else 'Unread'
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px;">{}</span>',
            color,
            status_text
        )
    is_read_badge.short_description = 'Status'


@admin.register(Dispute)
class DisputeAdmin(admin.ModelAdmin):
    list_display = ['id', 'transaction', 'reporter', 'status_badge', 'created_at', 'resolved_at']
    list_filter = ['status', 'created_at']
    search_fields = ['transaction__reference_id', 'reporter__username', 'reporter__email', 'reason']
    readonly_fields = ['created_at']
    date_hierarchy = 'created_at'
    ordering = ['-created_at']
    autocomplete_fields = ['transaction', 'reporter']

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

    def status_badge(self, obj):
        colors = {
            'OPEN': '#e74c3c',
            'INVESTIGATING': '#f39c12',
            'RESOLVED': '#27ae60',
            'WITHDRAWN': '#95a5a6'
        }
        color = colors.get(obj.status, '#3498db')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 5px 10px; border-radius: 3px;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = 'Status'


@admin.register(ReconciliationDiscrepancy)
class ReconciliationDiscrepancyAdmin(admin.ModelAdmin):
    list_display = ['date', 'difference_display', 'severity_badge', 'is_resolved', 'created_at']
    list_filter = ['severity', 'is_resolved', 'date']
    search_fields = ['details', 'resolution_notes']
    readonly_fields = ['created_at']
    date_hierarchy = 'date'
    ordering = ['-date']

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

    def difference_display(self, obj):
        color = '#e74c3c' if obj.difference < 0 else '#27ae60'
        return format_html(
            '<span style="color: {}; font-weight: bold;">{:,.2f}</span>',
            color,
            obj.difference
        )
    difference_display.short_description = 'Difference'

    def severity_badge(self, obj):
        colors = {
            'LOW': '#3498db',
            'MEDIUM': '#f39c12',
            'HIGH': '#e74c3c',
            'CRITICAL': '#c0392b'
        }
        color = colors.get(obj.severity, '#95a5a6')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px; text-transform: capitalize;">{}</span>',
            color,
            obj.severity
        )
    severity_badge.short_description = 'Severity'
