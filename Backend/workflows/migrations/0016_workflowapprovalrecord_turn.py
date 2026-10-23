import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orchestration', '0004_turn'),
        ('workflows', '0015_persona_avatar_skills_request'),
    ]

    operations = [
        migrations.AddField(
            model_name='workflowapprovalrecord',
            name='turn',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='approval_records',
                to='orchestration.turn',
            ),
        ),
    ]
