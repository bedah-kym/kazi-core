from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workflows', '0004_workflowapprovalrecord_agent_loop_approvals'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='workflowapprovalrecord',
            constraint=models.UniqueConstraint(
                condition=models.Q(('kind', 'agent_loop'), ('status', 'pending')),
                fields=('kind', 'room_id', 'requested_by'),
                name='uniq_agent_loop_pending_room_user',
            ),
        ),
    ]
