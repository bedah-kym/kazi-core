import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workflows', '0010_routine_contract'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='StandingGrant',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('workflow_version', models.PositiveIntegerField(default=1)),
                ('trigger_scope', models.JSONField(blank=True, default=dict)),
                ('capability_scope', models.JSONField(blank=True, default=list)),
                ('decision', models.CharField(choices=[('allow_once', 'Allow Once'), ('always_allow', 'Always Allow'), ('deny', 'Deny')], max_length=20)),
                ('status', models.CharField(choices=[('active', 'Active'), ('lapsed', 'Lapsed'), ('revoked', 'Revoked')], default='active', max_length=20)),
                ('lapse_reason', models.CharField(blank=True, max_length=255)),
                ('expires_at', models.DateTimeField(blank=True, null=True)),
                ('lapsed_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('approval_record', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='standing_grants', to='workflows.workflowapprovalrecord')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='standing_grants', to=settings.AUTH_USER_MODEL)),
                ('workflow', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='standing_grants', to='workflows.userworkflow')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='standinggrant',
            index=models.Index(fields=['workflow', 'status'], name='workflows_s_workflo_7f6e2a_idx'),
        ),
        migrations.AddIndex(
            model_name='standinggrant',
            index=models.Index(fields=['status', 'expires_at'], name='workflows_s_status_9c3f21_idx'),
        ),
    ]
