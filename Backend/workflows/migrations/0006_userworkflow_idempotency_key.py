from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workflows', '0005_workflowapprovalrecord_agent_loop_unique'),
    ]

    operations = [
        migrations.AddField(
            model_name='userworkflow',
            name='idempotency_key',
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddConstraint(
            model_name='userworkflow',
            constraint=models.UniqueConstraint(fields=('user', 'idempotency_key'), name='uniq_userworkflow_user_idemkey'),
        ),
    ]
