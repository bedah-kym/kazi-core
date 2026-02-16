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
    error_message = models.TextField(null=True, blank=True)

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
