"""
Enterprise Payment System Models
Double-Entry Ledger implementation with ACID compliance
"""
from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.core.validators import MinValueValidator
from django.db.models import Sum, Q
from decimal import Decimal
import uuid

User = get_user_model()


class LedgerAccount(models.Model):
    """
    Represents an account in the Double-Entry Ledger system.
    Based on accounting equation: Assets = Liabilities + Equity
    """
    ACCOUNT_TYPES = (
        ('ASSET', 'Asset'),           # What we own (IntaSend cash)
        ('LIABILITY', 'Liability'),   # What we owe (User wallets)
        ('EQUITY', 'Equity'),          # Owner's stake
        ('INCOME', 'Income/Revenue'),  # Platform fees
        ('EXPENSE', 'Expense'),        # Transaction costs
    )
    
    name = models.CharField(max_length=255, unique=True)
    account_type = models.CharField(max_length=20, choices=ACCOUNT_TYPES)
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True,
                            help_text="User for personal wallet accounts")
    balance = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    currency = models.CharField(max_length=3, default='KES')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['account_type', 'name']
        indexes = [
            models.Index(fields=['account_type', 'is_active']),
            models.Index(fields=['user']),
        ]
    
    def __str__(self):
        return f"{self.get_account_type_display()}: {self.name} ({self.currency} {self.balance})"
    
    def get_balance(self):
        """Calculate current balance from ledger entries"""
        from django.db.models import Sum, Q
        debits = self.entries.filter(dr_cr='DEBIT').aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')
        credits = self.entries.filter(dr_cr='CREDIT').aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')
        
        # Balance calculation depends on account type
        if self.account_type in ['ASSET', 'EXPENSE']:
            return debits - credits
        else:  # LIABILITY, EQUITY, INCOME
            return credits - debits


class JournalEntry(models.Model):
    """
    Groups ledger entries for a single transaction.
    Ensures Credits = Debits (Double-Entry)
    """
    TRANSACTION_TYPES = (
        ('DEPOSIT', 'Deposit'),
        ('WITHDRAWAL', 'Withdrawal'),
        ('INVOICE_PAYMENT', 'Invoice Payment'),
        ('FEE', 'Fee'),
        ('REFUND', 'Refund'),
        ('DISPUTE_FREEZE', 'Dispute Freeze'),
        ('DISPUTE_RELEASE', 'Dispute Release'),
    )
    
    reference_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPES)
    description = models.TextField()
    timestamp = models.DateTimeField(default=timezone.now)
   
    is_reconciled = models.BooleanField(default=False)
    provider_reference = models.CharField(max_length=255, blank=True, 
                                         help_text="IntaSend tracking ID")
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['-timestamp']),
            models.Index(fields=['reference_id']),
            models.Index(fields=['is_reconciled']),
        ]
    
    def __str__(self):
        return f"{self.get_transaction_type_display()} - {self.reference_id} ({self.timestamp.date()})"
    
    def verify_balance(self):
        """Ensure debits = credits"""
        debits = self.ledger_entries.filter(dr_cr='DEBIT').aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')
        credits = self.ledger_entries.filter(dr_cr='CREDIT').aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')
        return debits == credits


class LedgerEntry(models.Model):
    """
    Individual debit or credit line in a Journal Entry
    """
    DR_CR_CHOICES = (
        ('DEBIT', 'Debit'),
        ('CREDIT', 'Credit'),
    )
    
    journal_entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, 
                                     related_name='ledger_entries')
    ledger_account = models.ForeignKey(LedgerAccount, on_delete=models.PROTECT,
                                      related_name='entries')
    amount = models.DecimalField(max_digits=15, decimal_places=2, 
                                 validators=[MinValueValidator(Decimal('0.01'))])
    dr_cr = models.CharField(max_length=6, choices=DR_CR_CHOICES)
    
    class Meta:
        ordering = ['journal_entry', 'id']
        indexes = [
            models.Index(fields=['journal_entry']),
            models.Index(fields=['ledger_account']),
        ]
    
    def __str__(self):
        return f"{self.dr_cr} {self.amount} - {self.ledger_account.name}"


class PaymentRequest(models.Model):
    """
    Invoice/Payment Request sent to users
    """
    STATUS_CHOICES = (
        ('PENDING', 'Pending'),
        ('PAID', 'Paid'),
        ('EXPIRED', 'Expired'),
        ('DISPUTED', 'Disputed'),
        ('CANCELLED', 'Cancelled'),
    )
    
    RECURRENCE_INTERVALS = (
        ('NONE', 'One-time'),
        ('MONTHLY', 'Monthly'),
        ('QUARTERLY', 'Quarterly'),
        ('YEARLY', 'Yearly'),
    )
    
    reference_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    issuer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='issued_invoices')
    payer_email = models.EmailField(blank=True, help_text="Optional: email to send invoice to")
    payer = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='received_invoices')
    
    amount = models.DecimalField(max_digits=12, decimal_places=2, 
                                validators=[MinValueValidator(Decimal('1.00'))])
    currency = models.CharField(max_length=3, default='KES')
    description = models.TextField()
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    intasend_payment_link = models.URLField(max_length=500, blank=True)
    intasend_invoice_id = models.CharField(max_length=255, blank=True)
    
    # Recurring billing
    is_recurring = models.BooleanField(default=False)
    recurrence_interval = models.CharField(max_length=20, choices=RECURRENCE_INTERVALS,
                                          default='NONE')
    next_billing_date = models.DateField(null=True, blank=True)
    parent_invoice = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True,
                                      help_text="Original invoice if this is recurring")
    
    # Linked journal entry when paid
    journal_entry = models.ForeignKey(JournalEntry, on_delete=models.SET_NULL, 
                                     null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    paid_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['issuer', '-created_at']),
            models.Index(fields=['is_recurring', 'next_billing_date']),
        ]
    
    def __str__(self):
        return f"Invoice {self.reference_id} - {self.amount} {self.currency} ({self.status})"


class FeeSchedule(models.Model):
    """
    Platform fee configuration
    """
    TRANSACTION_TYPES = (
        ('DEPOSIT', 'Deposit'),
        ('WITHDRAWAL', 'Withdrawal'),
        ('INVOICE', 'Invoice Payment'),
    )
    
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPES, unique=True)
    platform_fee = models.DecimalField(max_digits=10, decimal_places=2,
                                      help_text="Flat fee in KES")
    is_active = models.BooleanField(default=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name_plural = "Fee Schedules"
    
    def __str__(self):
        return f"{self.get_transaction_type_display()} - {self.platform_fee} KES"


class PaymentNotification(models.Model):
    """
    Real-time payment notifications for users
    """
    NOTIFICATION_TYPES = (
        ('INFO', 'Information'),
        ('WARNING', 'Warning'),
        ('SUCCESS', 'Success'),
        ('ERROR', 'Error'),
    )
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='payment_notifications')
    message = models.TextField()
    notification_type = models.CharField(max_length=10, choices=NOTIFICATION_TYPES, default='INFO')
    
    related_journal = models.ForeignKey(JournalEntry, on_delete=models.SET_NULL, 
                                       null=True, blank=True)
    related_invoice = models.ForeignKey(PaymentRequest, on_delete=models.SET_NULL,
                                       null=True, blank=True)
    
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'is_read', '-created_at']),
        ]
    
    def __str__(self):
        return f"{self.notification_type} for {self.user.username}: {self.message[:50]}"


class Dispute(models.Model):
    """
    Tracks disputed transactions
    """
    STATUS_CHOICES = (
        ('OPEN', 'Open'),
        ('INVESTIGATING', 'Under Investigation'),
        ('RESOLVED', 'Resolved'),
        ('WITHDRAWN', 'Withdrawn'),
    )
    
    transaction = models.ForeignKey(JournalEntry, on_delete=models.CASCADE,
                                   related_name='disputes')
    reporter = models.ForeignKey(User, on_delete=models.CASCADE)
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='OPEN')
    
    resolution_notes = models.TextField(blank=True)
    resolved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='resolved_disputes')
    
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['transaction']),
        ]
    
    def __str__(self):
        return f"Dispute #{self.id} - {self.status} ({self.transaction.reference_id})"


class ReconciliationDiscrepancy(models.Model):
    """
    Logs discovered during nightly reconciliation
    """
    SEVERITY_LEVELS = (
        ('LOW', 'Low'),
        ('MEDIUM', 'Medium'),
        ('HIGH', 'High'),
        ('CRITICAL', 'Critical'),
    )
    
    date = models.DateField()
    expected_balance = models.DecimalField(max_digits=15, decimal_places=2)
    actual_balance = models.DecimalField(max_digits=15, decimal_places=2)
    difference = models.DecimalField(max_digits=15, decimal_places=2)
    severity = models.CharField(max_length=10, choices=SEVERITY_LEVELS)
    
    details = models.TextField(help_text="AI-generated analysis")
    is_resolved = models.BooleanField(default=False)
    resolution_notes = models.TextField(blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['-date']
        verbose_name_plural = "Reconciliation Discrepancies"
    
    def __str__(self):
        return f"Discrepancy on {self.date}: {self.difference} KES ({self.severity})"
