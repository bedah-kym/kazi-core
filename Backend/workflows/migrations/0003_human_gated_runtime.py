from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("workflows", "0002_deferredworkflowexecution"),
    ]

    operations = [
        migrations.AddField(
            model_name="workflowtrigger",
            name="schedule_last_error",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="workflowtrigger",
            name="schedule_status",
            field=models.CharField(
                choices=[
                    ("active", "Active"),
                    ("paused", "Paused"),
                    ("unavailable", "Unavailable"),
                    ("deleted", "Deleted"),
                ],
                default="active",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="workflowexecution",
            name="attempts",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="workflowexecution",
            name="current_step",
            field=models.CharField(blank=True, max_length=120, null=True),
        ),
        migrations.AddField(
            model_name="workflowexecution",
            name="failure_summary",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="workflowexecution",
            name="last_completed_step",
            field=models.CharField(blank=True, max_length=120, null=True),
        ),
        migrations.AddField(
            model_name="workflowexecution",
            name="receipt_ids",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="workflowexecution",
            name="recovery_suggestion",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="workflowexecution",
            name="result_summary",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="workflowexecution",
            name="waiting_on",
            field=models.CharField(blank=True, max_length=120, null=True),
        ),
        migrations.AddField(
            model_name="deferredworkflowexecution",
            name="dead_letter_reason",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="deferredworkflowexecution",
            name="recovery_hint",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="WorkflowApprovalRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("step_id", models.CharField(max_length=120)),
                ("service", models.CharField(blank=True, max_length=50)),
                ("action", models.CharField(max_length=100)),
                ("approval_message", models.TextField(blank=True)),
                ("sanitized_params", models.JSONField(blank=True, default=dict)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("approved", "Approved"),
                            ("rejected", "Rejected"),
                            ("timed_out", "Timed Out"),
                            ("cancelled", "Cancelled"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("review_comment", models.TextField(blank=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "execution",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="approval_records", to="workflows.workflowexecution"),
                ),
                (
                    "requested_by",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="requested_workflow_approvals", to=settings.AUTH_USER_MODEL),
                ),
                (
                    "reviewed_by",
                    models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reviewed_workflow_approvals", to=settings.AUTH_USER_MODEL),
                ),
                (
                    "workflow",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="approval_records", to="workflows.userworkflow"),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="workflowapprovalrecord",
            index=models.Index(fields=["status", "expires_at"], name="workflows__status_a91d5e_idx"),
        ),
        migrations.AddIndex(
            model_name="workflowapprovalrecord",
            index=models.Index(fields=["workflow", "step_id"], name="workflows__workflo_0aa786_idx"),
        ),
        migrations.AddField(
            model_name="workflowexecution",
            name="pending_approval",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="active_executions", to="workflows.workflowapprovalrecord"),
        ),
        migrations.CreateModel(
            name="WorkflowImprovementSuggestion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("suggestion_type", models.CharField(max_length=50)),
                ("title", models.CharField(max_length=200)),
                ("summary", models.TextField()),
                ("proposed_changes", models.JSONField(blank=True, default=dict)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("proposed", "Proposed"),
                            ("dismissed", "Dismissed"),
                            ("accepted", "Accepted"),
                        ],
                        default="proposed",
                        max_length=20,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "execution",
                    models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="improvement_suggestions", to="workflows.workflowexecution"),
                ),
                (
                    "user",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="workflow_suggestions", to=settings.AUTH_USER_MODEL),
                ),
                (
                    "workflow",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="improvement_suggestions", to="workflows.userworkflow"),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="workflowimprovementsuggestion",
            index=models.Index(fields=["user", "status"], name="workflows__user_id_6abce3_idx"),
        ),
        migrations.AddIndex(
            model_name="workflowimprovementsuggestion",
            index=models.Index(fields=["workflow", "status"], name="workflows__workflo_605af4_idx"),
        ),
    ]
