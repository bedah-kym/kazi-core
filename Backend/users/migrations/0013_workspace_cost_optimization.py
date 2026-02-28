# Generated migration for Workspace cost optimization fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0009_trial_applications'),
    ]

    operations = [
        migrations.AddField(
            model_name='workspace',
            name='moderation_enabled',
            field=models.BooleanField(default=False, help_text='Enable AI moderation (HF tokens required)'),
        ),
        migrations.AddField(
            model_name='workspace',
            name='idle_nudges_enabled',
            field=models.BooleanField(default=True, help_text='Enable idle nudge suggestions'),
        ),
        migrations.AddField(
            model_name='workspace',
            name='proactive_suggestions_enabled',
            field=models.BooleanField(default=True, help_text='Enable proactive workflow suggestions'),
        ),
    ]
