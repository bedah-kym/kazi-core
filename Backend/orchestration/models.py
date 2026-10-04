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
            models.Index(fields=["user", "action", "created_at"]),
        ]

    def __str__(self):
        return f"ActionReceipt({self.user_id}, {self.action}, {self.status})"


class ShellHostGrant(models.Model):
    """A human's approval for one room's sandboxed shell to reach one host.

    Rows are never edited to widen access: a re-approval revokes the old row
    and adds a new one, so the table is the history.
    """

    room = models.ForeignKey("chatbot.Chatroom", on_delete=models.CASCADE, related_name="shell_host_grants")
    host = models.CharField(max_length=253)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["host"]
        indexes = [models.Index(fields=["room", "host"])]

    def __str__(self):
        return f"ShellHostGrant({self.room_id}, {self.host})"

# Create your models here.
