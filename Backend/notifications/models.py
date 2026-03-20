"""Unified notification model for all Mathia event types."""
from django.conf import settings
from django.db import models


class Notification(models.Model):
    """
    Single notification record that covers payments, reminders, messages,
    and system events.  Delivery tracking fields let the service layer
    know which channels have already fired.
    """

    EVENT_TYPES = [
        # Payment
        ("payment.deposit", "Deposit Received"),
        ("payment.withdrawal", "Withdrawal Processed"),
        ("payment.invoice", "Invoice Paid"),
        ("payment.error", "Payment Error"),
        # Reminder
        ("reminder.due", "Reminder Due"),
        # Message
        ("message.unread", "Unread Message"),
        ("message.mention", "Mentioned in Chat"),
        # System
        ("system.info", "System Info"),
        ("system.warning", "System Warning"),
    ]

    SEVERITY_CHOICES = [
        ("info", "Info"),
        ("success", "Success"),
        ("warning", "Warning"),
        ("error", "Error"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    event_type = models.CharField(max_length=30, choices=EVENT_TYPES, db_index=True)
    severity = models.CharField(
        max_length=10, choices=SEVERITY_CHOICES, default="info"
    )
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)

    # Typed nullable FKs — same pattern as PaymentNotification
    related_invoice = models.ForeignKey(
        "payments.PaymentRequest",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    related_journal = models.ForeignKey(
        "payments.JournalEntry",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    related_reminder = models.ForeignKey(
        "chatbot.Reminder",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    related_room = models.ForeignKey(
        "chatbot.Chatroom",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    related_message = models.ForeignKey(
        "chatbot.Message",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    metadata = models.JSONField(default=dict, blank=True)

    # Read / dismiss state
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    is_dismissed = models.BooleanField(default=False)

    # Delivery tracking
    delivered_ws = models.BooleanField(default=False)
    delivered_email = models.BooleanField(default=False)
    delivered_whatsapp = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "is_read", "-created_at"]),
            models.Index(fields=["user", "is_dismissed", "-created_at"]),
        ]

    def __str__(self):
        return f"[{self.event_type}] {self.title} → {self.user}"
