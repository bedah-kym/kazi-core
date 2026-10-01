import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workflows', '0009_promotion_pipeline'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='WorkflowTestRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('definition_version', models.PositiveIntegerField(default=1)),
                ('status', models.CharField(choices=[('passed', 'Passed'), ('failed', 'Failed')], default='passed', max_length=20)),
                ('inputs', models.JSONField(blank=True, default=dict)),
                ('output_preview', models.JSONField(blank=True, default=dict)),
                ('audit_trail', models.JSONField(blank=True, default=list)),
                ('approval_stop_point', models.CharField(blank=True, max_length=120)),
                ('failure_states', models.JSONField(blank=True, default=list)),
                ('summary', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('workflow', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='test_runs', to='workflows.userworkflow')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='RoutineCheckIn',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('prompted', 'Prompted'), ('answered', 'Answered'), ('paused', 'Paused')], default='prompted', max_length=20)),
                ('prompted_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('answered_at', models.DateTimeField(blank=True, null=True)),
                ('paused_workflow_ids', models.JSONField(blank=True, default=list)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='routine_check_ins', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-prompted_at'],
            },
        ),
        migrations.AddIndex(
            model_name='workflowtestrun',
            index=models.Index(fields=['workflow', 'definition_version', 'status'], name='workflows_w_workflo_6d74e6_idx'),
        ),
    ]
