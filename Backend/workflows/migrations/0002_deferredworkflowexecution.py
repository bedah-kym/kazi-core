from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('workflows', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='DeferredWorkflowExecution',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('room_id', models.IntegerField(blank=True, null=True)),
                ('trigger_data', models.JSONField(default=dict)),
                ('status', models.CharField(choices=[('queued', 'Queued'), ('processing', 'Processing'), ('started', 'Started'), ('failed', 'Failed'), ('abandoned', 'Abandoned')], default='queued', max_length=20)),
                ('attempts', models.IntegerField(default=0)),
                ('next_attempt_at', models.DateTimeField(blank=True, null=True)),
                ('last_attempt_at', models.DateTimeField(blank=True, null=True)),
                ('last_error', models.TextField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('execution', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='deferred_source', to='workflows.workflowexecution')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='deferred_workflows', to='auth.user')),
                ('workflow', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='deferred_executions', to='workflows.userworkflow')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='deferredworkflowexecution',
            index=models.Index(fields=['status', 'next_attempt_at'], name='workflows__status_8e49c0_idx'),
        ),
    ]
