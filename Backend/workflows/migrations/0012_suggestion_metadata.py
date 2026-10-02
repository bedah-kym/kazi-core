from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workflows', '0011_standing_grant'),
    ]

    operations = [
        migrations.AddField(
            model_name='workflowimprovementsuggestion',
            name='metadata',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
