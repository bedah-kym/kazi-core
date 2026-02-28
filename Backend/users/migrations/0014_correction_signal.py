# Generated migration for CorrectionSignal model (Phase 3A learning)

from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0013_workspace_cost_optimization'),
    ]

    operations = [
        migrations.CreateModel(
            name='CorrectionSignal',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('intent_action', models.CharField(help_text='e.g., search_flights, send_email', max_length=100)),
                ('correction_type', models.CharField(choices=[('parameter', 'Parameter Correction'), ('result_selection', 'Result Selection'), ('preference', 'Preference Discovery'), ('workflow', 'Workflow Adjustment'), ('confirmation', 'Negative Confirmation')], max_length=30)),
                ('data', models.JSONField(blank=True, default=dict)),
                ('original_ai_reasoning', models.TextField(blank=True, help_text='What was the AI thinking?')),
                ('user_explanation', models.TextField(blank=True, help_text='Why did user correct?')),
                ('confidence', models.IntegerField(default=8, help_text='How confident is this signal? 1-10')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='correction_signals', to=settings.AUTH_USER_MODEL)),
                ('workspace', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='users.workspace')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='correctionsignal',
            index=models.Index(fields=['user', 'created_at'], name='users_corre_user_id_created_idx'),
        ),
        migrations.AddIndex(
            model_name='correctionsignal',
            index=models.Index(fields=['user', 'intent_action'], name='users_corre_user_id_intent_idx'),
        ),
        migrations.AddIndex(
            model_name='correctionsignal',
            index=models.Index(fields=['correction_type'], name='users_corre_correction_type_idx'),
        ),
    ]
