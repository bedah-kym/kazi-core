from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class ActionReceipt(models.Model):
    """Audit record for user-facing actions."""

    STATUS_CHOICES = [
        ("success", "Success"),
        ("error", "Error"),
        ("pending", "Pending"),
        ("cancelled", "Cancelled"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="action_receipts")
    room = models.ForeignKey("chatbot.Chatroom", on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=100)
    service = models.CharField(max_length=50, blank=True)
    params = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="success")
    reversible = models.BooleanField(default=False)
    undo_action = models.CharField(max_length=100, blank=True)
    undo_params = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "room", "created_at"]),
        ]

    def __str__(self):
        return f"ActionReceipt({self.user_id}, {self.action}, {self.status})"

# Create your models here.
