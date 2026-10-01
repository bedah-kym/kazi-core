from django.db import migrations, models


def _derived_capabilities(definition):
    capabilities = set()
    for step in (definition or {}).get("steps", []):
        if not isinstance(step, dict):
            continue
        action = str(step.get("action") or "").strip().lower()
        if not action:
            continue
        service = str(step.get("service") or "").strip().lower()
        if service == "mailgun":
            service = "gmail"
        capabilities.add(f"{service}:{action}" if service else f":{action}")
    return sorted(capabilities)


def backfill_capabilities(apps, schema_editor):
    WorkflowVersion = apps.get_model('workflows', 'WorkflowVersion')
    for version in WorkflowVersion.objects.all().iterator():
        declared = (version.definition or {}).get("capabilities")
        if isinstance(declared, list):
            version.capabilities = sorted({
                str(value).strip().lower() for value in declared if isinstance(value, str)
            })
        else:
            version.capabilities = _derived_capabilities(version.definition)
        version.save(update_fields=['capabilities'])


def clear_capabilities(apps, schema_editor):
    WorkflowVersion = apps.get_model('workflows', 'WorkflowVersion')
    WorkflowVersion.objects.update(capabilities=[])


class Migration(migrations.Migration):

    dependencies = [
        ('workflows', '0007_workflow_definition_versioning'),
    ]

    operations = [
        migrations.AddField(
            model_name='workflowversion',
            name='capabilities',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='workflowimprovementsuggestion',
            name='capability_delta',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.RunPython(backfill_capabilities, clear_capabilities),
    ]
