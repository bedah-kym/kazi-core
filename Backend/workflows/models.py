from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class WorkflowDraft(models.Model):
    """Draft workflow proposal built via chat before approval."""

    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('awaiting_confirmation', 'Awaiting Confirmation'),
        ('confirmed', 'Confirmed'),
        ('cancelled', 'Cancelled'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='workflow_drafts')
    room = models.ForeignKey('chatbot.Chatroom', on_delete=models.SET_NULL, null=True, blank=True)
    definition = models.JSONField(null=True, blank=True)
    context = models.JSONField(default=list)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='draft')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f"WorkflowDraft {self.id} ({self.user_id})"


class UserWorkflow(models.Model):
    """User-defined workflow definition and metadata."""

    STATUS_CHOICES = [
        ('active', 'Active'),
        ('paused', 'Paused'),
        ('failed', 'Failed'),
        ('deleted', 'Deleted'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='workflows')
    name = models.CharField(max_length=255)
    description = models.TextField()
    definition = models.JSONField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_executed_at = models.DateTimeField(null=True, blank=True)
    execution_count = models.IntegerField(default=0)

    created_from_room = models.ForeignKey('chatbot.Chatroom', on_delete=models.SET_NULL, null=True, blank=True)
    created_from_draft = models.ForeignKey(WorkflowDraft, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'status']),
        ]

    def __str__(self):
        return f"{self.name} ({self.user.username})"

    def get_triggers(self):
        return self.definition.get('triggers', [])

    def get_steps(self):
        return self.definition.get('steps', [])


class WorkflowTrigger(models.Model):
    """Registered triggers for workflows."""

    TRIGGER_TYPE_CHOICES = [
        ('webhook', 'Webhook'),
        ('schedule', 'Schedule'),
        ('manual', 'Manual'),
    ]
    SCHEDULE_STATUS_CHOICES = [
        ('active', 'Active'),
        ('paused', 'Paused'),
        ('unavailable', 'Unavailable'),
        ('deleted', 'Deleted'),
    ]

    workflow = models.ForeignKey(UserWorkflow, on_delete=models.CASCADE, related_name='registered_triggers')
    trigger_type = models.CharField(max_length=20, choices=TRIGGER_TYPE_CHOICES)
    service = models.CharField(max_length=50, blank=True)
    event = models.CharField(max_length=100, blank=True)

    config = models.JSONField(default=dict)
    webhook_secret = models.CharField(max_length=255, null=True, blank=True)
    webhook_url = models.URLField(null=True, blank=True)

    schedule_cron = models.CharField(max_length=100, null=True, blank=True)
    schedule_timezone = models.CharField(max_length=50, default='UTC')
    temporal_schedule_id = models.CharField(max_length=255, null=True, blank=True)
    schedule_status = models.CharField(max_length=20, choices=SCHEDULE_STATUS_CHOICES, default='active')
    schedule_last_error = models.TextField(null=True, blank=True)

    is_active = models.BooleanField(default=True)
    last_triggered_at = models.DateTimeField(null=True, blank=True)
    trigger_count = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [['workflow', 'service', 'event', 'schedule_cron']]

    def __str__(self):
        label = self.service + '.' + self.event if self.service and self.event else self.trigger_type
        return f"{label} for {self.workflow.name}"


class WorkflowExecution(models.Model):
    """Individual workflow run tracked in Django."""

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('waiting', 'Waiting'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ]

    workflow = models.ForeignKey(UserWorkflow, on_delete=models.CASCADE, related_name='executions')
    temporal_workflow_id = models.CharField(max_length=255, unique=True)
    temporal_run_id = models.CharField(max_length=255, null=True, blank=True)

    trigger_type = models.CharField(max_length=20, default='manual')
    trigger_data = models.JSONField(default=dict)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    result = models.JSONField(null=True, blank=True)
    result_summary = models.TextField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    current_step = models.CharField(max_length=120, null=True, blank=True)
    last_completed_step = models.CharField(max_length=120, null=True, blank=True)
    waiting_on = models.CharField(max_length=120, null=True, blank=True)
    attempts = models.JSONField(default=dict, blank=True)
    receipt_ids = models.JSONField(default=list, blank=True)
    failure_summary = models.TextField(null=True, blank=True)
    recovery_suggestion = models.TextField(null=True, blank=True)
    pending_approval = models.ForeignKey(
        'WorkflowApprovalRecord',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='active_executions',
    )

    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        return f"Execution {self.id} - {self.status}"


class DeferredWorkflowExecution(models.Model):
    """Queue ad-hoc workflows when Temporal is unavailable."""

    STATUS_CHOICES = [
        ('queued', 'Queued'),
        ('processing', 'Processing'),
        ('started', 'Started'),
        ('failed', 'Failed'),
        ('abandoned', 'Abandoned'),
    ]

    workflow = models.ForeignKey(UserWorkflow, on_delete=models.CASCADE, related_name='deferred_executions')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='deferred_workflows')
    room_id = models.IntegerField(null=True, blank=True)
    trigger_data = models.JSONField(default=dict)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='queued')
    attempts = models.IntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(null=True, blank=True)
    dead_letter_reason = models.TextField(null=True, blank=True)
    recovery_hint = models.TextField(null=True, blank=True)
    execution = models.ForeignKey(
        WorkflowExecution,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='deferred_source',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'next_attempt_at']),
        ]

    def __str__(self):
        return f"Deferred {self.id} ({self.status})"


class WorkflowApprovalRecord(models.Model):
    """Immutable review record for a workflow step that needed human approval."""

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('timed_out', 'Timed Out'),
        ('cancelled', 'Cancelled'),
    ]

    workflow = models.ForeignKey(UserWorkflow, on_delete=models.CASCADE, related_name='approval_records')
    execution = models.ForeignKey(WorkflowExecution, on_delete=models.CASCADE, related_name='approval_records')
    requested_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='requested_workflow_approvals')
    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_workflow_approvals',
    )

    step_id = models.CharField(max_length=120)
    service = models.CharField(max_length=50, blank=True)
    action = models.CharField(max_length=100)
    approval_message = models.TextField(blank=True)
    sanitized_params = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    review_comment = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    expires_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'expires_at']),
            models.Index(fields=['workflow', 'step_id']),
        ]

    def __str__(self):
        return f"Approval {self.id} ({self.step_id} - {self.status})"


class WorkflowImprovementSuggestion(models.Model):
    """Suggested workflow edits derived from operator feedback or failures."""

    STATUS_CHOICES = [
        ('proposed', 'Proposed'),
        ('dismissed', 'Dismissed'),
        ('accepted', 'Accepted'),
    ]

    workflow = models.ForeignKey(UserWorkflow, on_delete=models.CASCADE, related_name='improvement_suggestions')
    execution = models.ForeignKey(
        WorkflowExecution,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='improvement_suggestions',
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='workflow_suggestions')

    suggestion_type = models.CharField(max_length=50)
    title = models.CharField(max_length=200)
    summary = models.TextField()
    proposed_changes = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='proposed')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['workflow', 'status']),
        ]

    def __str__(self):
        return f"Suggestion {self.id} ({self.suggestion_type})"
