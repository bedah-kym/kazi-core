from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orchestration', '0004_turn'),
    ]

    operations = [
        migrations.AddField(
            model_name='turn',
            name='cancel_requested_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='turn',
            name='paused_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
