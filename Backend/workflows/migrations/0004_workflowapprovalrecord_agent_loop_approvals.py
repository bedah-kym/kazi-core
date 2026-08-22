from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('workflows', '0003_human_gated_runtime'),
    ]

    operations = [
        migrations.AddField(
            model_name='workflowapprovalrecord',
            name='kind',
            field=models.CharField(choices=[('workflow', 'Workflow'), ('agent_loop', 'Agent Loop')], default='workflow', max_length=20),
        ),
        migrations.AddField(
            model_name='workflowapprovalrecord',
            name='room_id',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='workflowapprovalrecord',
            name='execution',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='approval_records', to='workflows.workflowexecution'),
        ),
        migrations.AlterField(
            model_name='workflowapprovalrecord',
            name='workflow',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='approval_records', to='workflows.userworkflow'),
        ),
        migrations.AddIndex(
            model_name='workflowapprovalrecord',
            index=models.Index(fields=['kind', 'room_id', 'status'], name='workflows_w_kind_3f9190_idx'),
        ),
    ]
