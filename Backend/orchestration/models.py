import uuid

from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class Turn(models.Model):
    """Durable record of one logical chat turn (#216a).

    The coordinator is its only writer. ``version`` is the fencing token: a
    transition is a compare-and-set on ``(id, version)``, so a writer that lost
    the race changes nothing. States are deliberately coarse (D2); the
    in-flight phase stays in memory, frames and telemetry.
    """

    STATE_CHOICES = [
        ("queued", "Queued"),
        ("running", "Running"),
        ("paused", "Paused"),
        ("committing", "Committing"),
        ("completed", "Completed"),
        ("failed", "Failed"),
        ("cancelled", "Cancelled"),
        ("superseded", "Superseded"),
        ("expired", "Expired"),
    ]
    PAUSE_KIND_CHOICES = [
        ("approval", "Approval"),
        ("budget", "Budget"),
        ("quota", "Quota"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # The record outlives its room so a deletion mid-turn can be recorded
    # honestly as failed(room_gone) instead of erasing history.
    room = models.ForeignKey(
        "chatbot.Chatroom", null=True, on_delete=models.SET_NULL, related_name="turns",
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="turns")
    state = models.CharField(max_length=20, choices=STATE_CHOICES, default="queued")
    pause_kind = models.CharField(max_length=20, choices=PAUSE_KIND_CHOICES, null=True, blank=True)
    end_reason = models.CharField(max_length=64, null=True, blank=True)
    partial = models.BooleanField(default=False)
    version = models.IntegerField(default=0)
    # Ownership and leasing arrive in P3-4; both stay null here.
    owner_id = models.CharField(max_length=128, null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    # A person's stop is a flag beside the state, never a state transition: the
    # owner still writes the honest `cancelled(user_cancel)` ending (#216b-3).
    cancel_requested_at = models.DateTimeField(null=True, blank=True)
    # When the turn entered `paused`. A budget/quota "continue" is consent only
    # if it was sent after this stamp plus CONSENT_SKEW_SECONDS.
    paused_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    deadline_at = models.DateTimeField(null=True, blank=True)
    active_seconds = models.FloatField(default=0.0)
    attempt = models.IntegerField(default=0)
    budget = models.JSONField(default=dict, blank=True)
    tainted = models.BooleanField(default=False)
    model = models.CharField(max_length=100, blank=True, default="")
    # Retrieval calls the provider ran server-side (Anthropic web search) for
    # this turn. Phase 4 queries "was there no retrieval this turn?" from it.
    server_tool_uses = models.IntegerField(default=0)
    # P3-3 fills the encrypted pause body; it stays empty in this PR.
    paused_state_enc = models.TextField(null=True, blank=True)
    pause_expires_at = models.DateTimeField(null=True, blank=True)
    superseded_by = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="supersedes",
    )
    schema_version = models.IntegerField(default=1)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["room", "user"],
                condition=models.Q(state__in=["running", "committing"]),
                name="uniq_turn_active_room_user",
            ),
            models.UniqueConstraint(
                fields=["room", "user"],
                condition=models.Q(state="paused"),
                name="uniq_turn_paused_room_user",
            ),
        ]
        indexes = [models.Index(fields=["state", "lease_expires_at"])]

    def __str__(self):
        return f"Turn({self.id}, {self.state})"


class TurnToolCall(models.Model):
    """One tool call a Turn attempted, including the calls that never ran.

    The row is written once its outcome is known: a held call gets its row
    when it resolves. Rows are append-only; the bodies (``input`` and the text
    the model was shown) are encrypted with the room key, like chat messages.
    ``seq`` is the turn-local order and ``iteration`` the loop iteration, so
    the replay can keep the true order without replaying narration (D11).
    """

    STATUS_CHOICES = [
        ("ok", "Ok"),
        ("error", "Error"),
        ("denied", "Denied"),
        ("skipped", "Skipped"),
        ("declined", "Declined"),
        ("expired", "Expired"),
        ("superseded", "Superseded"),
        ("budget_skipped", "Budget skipped"),
        ("policy_stopped", "Policy stopped"),
        ("outcome_unknown", "Outcome unknown"),
    ]
    PROVENANCE_CHOICES = [
        ("main", "Main"),
        ("delegated", "Delegated"),
    ]
    DID_NOT_RUN = frozenset({
        "denied", "skipped", "declined", "expired", "superseded",
        "budget_skipped", "policy_stopped",
    })

    turn = models.ForeignKey(
        "orchestration.Turn", on_delete=models.CASCADE, related_name="tool_calls",
    )
    seq = models.PositiveIntegerField()
    iteration = models.PositiveIntegerField(default=0)
    call_id = models.CharField(max_length=128)
    name = models.CharField(max_length=100)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="ok")
    untrusted = models.BooleanField(default=False)
    retrieval = models.BooleanField(default=False)
    provenance = models.CharField(
        max_length=12, choices=PROVENANCE_CHOICES, default="main",
    )
    parent_call_id = models.CharField(max_length=128, blank=True, default="")
    input_enc = models.TextField(null=True, blank=True)
    shown_enc = models.TextField(null=True, blank=True)
    truncated = models.BooleanField(default=False)
    receipt = models.ForeignKey(
        "orchestration.ActionReceipt", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="tool_calls",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["turn_id", "seq"]
        constraints = [
            models.UniqueConstraint(
                fields=["turn", "seq"], name="uniq_turn_tool_call_seq",
            ),
        ]
        indexes = [models.Index(fields=["turn", "provenance"])]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("TurnToolCall rows are append-only")
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"TurnToolCall({self.turn_id}, {self.seq}, {self.name}, {self.status})"


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
