import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def backfill_initial_versions(apps, schema_editor):
    UserWorkflow = apps.get_model('workflows', 'UserWorkflow')
    WorkflowVersion = apps.get_model('workflows', 'WorkflowVersion')
    existing = set(
        WorkflowVersion.objects.values_list('workflow_id', flat=True)
    )
    for workflow in UserWorkflow.objects.exclude(id__in=existing).iterator():
        WorkflowVersion.objects.create(
            workflow_id=workflow.id,
            version=workflow.definition_version or 1,
            definition=workflow.definition or {},
            change_summary='Initial definition',
        )


def drop_versions(apps, schema_editor):
    WorkflowVersion = apps.get_model('workflows', 'WorkflowVersion')
    WorkflowVersion.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('workflows', '0006_userworkflow_idempotency_key'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='WorkflowVersion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version', models.PositiveIntegerField()),
                ('definition', models.JSONField()),
                ('change_summary', models.CharField(blank=True, max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['-version'],
            },
        ),
        migrations.AddField(
            model_name='userworkflow',
            name='definition_version',
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name='workflowexecution',
            name='definition_version',
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name='workflowversion',
            name='created_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_workflow_versions', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='workflowversion',
            name='workflow',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='versions', to='workflows.userworkflow'),
        ),
        migrations.AddConstraint(
            model_name='workflowversion',
            constraint=models.UniqueConstraint(fields=('workflow', 'version'), name='uniq_workflow_version'),
        ),
        migrations.RunPython(backfill_initial_versions, drop_versions),
    ]
