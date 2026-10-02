from django.db import models
from django.contrib.auth import get_user_model
from django.utils.timezone import now as timezone_now

User = get_user_model()


class WorkflowDraft(models.Model):
    """Draft workflow proposal built via chat before approval."""

    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('awaiting_confirmation', 'Awaiting Confirmation'),
        ('confirmed', 'Confirmed'),
        ('cancelled', 'Cancelled'),
    ]

    SOURCE_CHOICES = [
        ('chat', 'Chat'),
        ('explicit_save', 'Explicit Save'),
        ('statistical', 'Statistical'),
        ('reviewer', 'Reviewer'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='workflow_drafts')
    room = models.ForeignKey('chatbot.Chatroom', on_delete=models.SET_NULL, null=True, blank=True)
    definition = models.JSONField(null=True, blank=True)
    context = models.JSONField(default=list)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='draft')
    # v0.7 promotion pipeline (#156): where the draft came from, the staged
    # skill folder it ships with, and the six-part skill contract body.
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='chat')
    skill_name = models.CharField(max_length=100, blank=True)
    skill_contract = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f"WorkflowDraft {self.id} ({self.user_id})"


class WorkflowCandidate(models.Model):
    """A repeated tool pattern queued for promotion into a skill/routine.

    Mining never creates a live workflow: a candidate is a proposal, and a
    human (or an explicit save request) has to turn it into a draft and then
    confirm the draft before anything active exists.
    """

    STATUS_CHOICES = [
        ('candidate', 'Candidate'),
        ('drafted', 'Drafted'),
        ('discarded', 'Discarded'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='workflow_candidates')
    room_id = models.IntegerField(null=True, blank=True)
    pattern = models.JSONField(default=dict, blank=True)
    pattern_key = models.CharField(max_length=64)
    occurrences = models.PositiveIntegerField(default=0)
    success_rate = models.FloatField(default=0.0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='candidate')
    draft = models.ForeignKey(
        WorkflowDraft,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='promotion_candidates',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-occurrences', '-updated_at']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'pattern_key'],
                condition=models.Q(status='candidate'),
                name='uniq_candidate_per_user_pattern',
            ),
        ]

    def __str__(self):
        return f"Candidate {self.id} ({self.pattern_key[:12]})"


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
    definition_version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_executed_at = models.DateTimeField(null=True, blank=True)
    # Set when a paused workflow is manually reactivated; health windows start
    # no earlier than this so pre-recovery failures never re-pause the routine.
    reactivated_at = models.DateTimeField(null=True, blank=True)
    execution_count = models.IntegerField(default=0)

    created_from_room = models.ForeignKey('chatbot.Chatroom', on_delete=models.SET_NULL, null=True, blank=True)
    created_from_draft = models.ForeignKey(WorkflowDraft, on_delete=models.SET_NULL, null=True, blank=True)

    # Ad-hoc execution dedupe: sha256 digest of definition+trigger data. NULL
    # for scheduled/manual workflows; unique per user so a cache flush or slow
    # retry cannot admit a duplicate execution inside the dedupe window.
    idempotency_key = models.CharField(max_length=64, null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'status']),
        ]
        constraints = [
            models.UniqueConstraint(fields=['user', 'idempotency_key'], name='uniq_userworkflow_user_idemkey'),
        ]

    def __str__(self):
        return f"{self.name} ({self.user.username})"

    def get_triggers(self):
        return self.definition.get('triggers', [])

    def get_steps(self):
        return self.definition.get('steps', [])


class WorkflowVersion(models.Model):
    """Immutable snapshot of a workflow definition.

    A live definition is never edited in place: every change appends a row here
    and moves ``UserWorkflow.definition_version`` forward. Executions bind the
    version they started with, so a new version cannot reshape a running run.
    """

    workflow = models.ForeignKey(UserWorkflow, on_delete=models.CASCADE, related_name='versions')
    version = models.PositiveIntegerField()
    definition = models.JSONField()
    capabilities = models.JSONField(default=list, blank=True)
    change_summary = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_workflow_versions',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-version']
        constraints = [
            models.UniqueConstraint(fields=['workflow', 'version'], name='uniq_workflow_version'),
        ]

    def __str__(self):
        return f"{self.workflow.name} v{self.version}"


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
    definition_version = models.PositiveIntegerField(default=1)

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


class WorkflowTestRun(models.Model):
    """A dry run of a routine with side-effect steps stubbed (v0.7 W-F, #204).

    A routine cannot be enabled under a standing grant until a test run for the
    current definition version passes. The row records what the run selected,
    what it would produce, where it would stop for approval, and how it fails.
    """

    STATUS_CHOICES = [
        ('passed', 'Passed'),
        ('failed', 'Failed'),
    ]

    workflow = models.ForeignKey(UserWorkflow, on_delete=models.CASCADE, related_name='test_runs')
    definition_version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='passed')
    inputs = models.JSONField(default=dict, blank=True)
    output_preview = models.JSONField(default=dict, blank=True)
    audit_trail = models.JSONField(default=list, blank=True)
    approval_stop_point = models.CharField(max_length=120, blank=True)
    failure_states = models.JSONField(default=list, blank=True)
    summary = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['workflow', 'definition_version', 'status']),
        ]

    def __str__(self):
        return f"Test run {self.id} ({self.status})"


class RoutineCheckIn(models.Model):
    """Pause-on-absence state for a user's routines (v0.7 W-F/W-C, #204/#157).

    After a long idle period Kazi asks once whether routines should keep
    running. No answer inside the prompt window pauses them; nothing silently
    keeps executing for an absent owner.
    """

    STATUS_CHOICES = [
        ('prompted', 'Prompted'),
        ('answered', 'Answered'),
        ('paused', 'Paused'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='routine_check_ins')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='prompted')
    prompted_at = models.DateTimeField(default=timezone_now)
    answered_at = models.DateTimeField(null=True, blank=True)
    paused_workflow_ids = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-prompted_at']

    def __str__(self):
        return f"Routine check-in {self.id} ({self.status})"


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
    """Immutable review record for a step that needed human approval.

    Covers two kinds of approval:
      - ``workflow``    — a step inside a durable workflow run (has workflow
        + execution FKs, written by the Temporal integration).
      - ``agent_loop``  — a high-risk tool paused inside the ReAct agent loop
        (no workflow/execution; scoped by ``room_id`` and the requesting user,
        with the serialized loop state held in ``metadata``).
    """

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('timed_out', 'Timed Out'),
        ('cancelled', 'Cancelled'),
    ]

    KIND_CHOICES = [
        ('workflow', 'Workflow'),
        ('agent_loop', 'Agent Loop'),
    ]

    workflow = models.ForeignKey(
        UserWorkflow,
        on_delete=models.CASCADE,
        related_name='approval_records',
        null=True,
        blank=True,
    )
    execution = models.ForeignKey(
        WorkflowExecution,
        on_delete=models.CASCADE,
        related_name='approval_records',
        null=True,
        blank=True,
    )
    requested_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='requested_workflow_approvals')
    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_workflow_approvals',
    )

    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default='workflow')
    room_id = models.IntegerField(null=True, blank=True)
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
            models.Index(fields=['kind', 'room_id', 'status']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['kind', 'room_id', 'requested_by'],
                condition=models.Q(kind='agent_loop', status='pending'),
                name='uniq_agent_loop_pending_room_user',
            ),
        ]

    def __str__(self):
        return f"Approval {self.id} ({self.step_id} - {self.status})"


class StandingGrant(models.Model):
    """A durable per-rule permission (v0.7 W-E, issue #159).

    Created only from a human decision on a real approval card. Scoped to
    ``(workflow, workflow_version, trigger, capability)`` — never a global
    unlock. ``allow_once`` lapses after one use; ``always_allow`` lapses on
    expiry, an out-of-scope attempt, or a new workflow version. Every
    autonomous run writes a receipt via ``WorkflowApprovalRecord``.
    """

    DECISION_CHOICES = [
        ('allow_once', 'Allow Once'),
        ('always_allow', 'Always Allow'),
        ('deny', 'Deny'),
    ]
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('lapsed', 'Lapsed'),
        ('revoked', 'Revoked'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='standing_grants')
    workflow = models.ForeignKey(UserWorkflow, on_delete=models.CASCADE, related_name='standing_grants')
    workflow_version = models.PositiveIntegerField(default=1)
    trigger_scope = models.JSONField(default=dict, blank=True)
    capability_scope = models.JSONField(default=list, blank=True)
    decision = models.CharField(max_length=20, choices=DECISION_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    lapse_reason = models.CharField(max_length=255, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    lapsed_at = models.DateTimeField(null=True, blank=True)
    approval_record = models.ForeignKey(
        WorkflowApprovalRecord,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='standing_grants',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['workflow', 'status']),
            models.Index(fields=['status', 'expires_at']),
        ]

    def __str__(self):
        return f"StandingGrant {self.id} ({self.decision})"


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
    capability_delta = models.JSONField(default=dict, blank=True)
    # Reviewer bookkeeping: shadow-replay result, cited metric before/after.
    metadata = models.JSONField(default=dict, blank=True)
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
