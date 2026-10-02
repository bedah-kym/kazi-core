import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workflows', '0013_workflow_reactivated_at'),
        ('chatbot', '__first__'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Persona',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('description', models.TextField(blank=True)),
                ('tool_scope', models.JSONField(blank=True, default=list)),
                ('risk_ceiling', models.CharField(choices=[('low', 'Low'), ('medium', 'Medium'), ('high', 'High')], default='medium', max_length=20)),
                ('approval_boundary', models.JSONField(blank=True, default=list)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('active', 'Active'), ('archived', 'Archived')], default='draft', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_from', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='duplicates', to='workflows.persona')),
                ('room', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='persona', to='chatbot.chatroom')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='personas', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='Handoff',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('room_id', models.IntegerField(blank=True, null=True)),
                ('task', models.JSONField(blank=True, default=dict)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('in_progress', 'In Progress'), ('completed', 'Completed'), ('failed', 'Failed')], default='pending', max_length=20)),
                ('result', models.JSONField(blank=True, default=dict)),
                ('artifacts', models.JSONField(blank=True, default=list)),
                ('receipts', models.JSONField(blank=True, default=list)),
                ('outcome', models.CharField(blank=True, max_length=20)),
                ('budget_used', models.IntegerField(default=0)),
                ('stopped_reason', models.CharField(blank=True, max_length=50)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('from_persona', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sent_handoffs', to='workflows.persona')),
                ('requested_by', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='requested_handoffs', to=settings.AUTH_USER_MODEL)),
                ('to_persona', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='received_handoffs', to='workflows.persona')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddField(
            model_name='workflowdraft',
            name='owner_persona',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='owned_drafts', to='workflows.persona'),
        ),
        migrations.AddConstraint(
            model_name='persona',
            constraint=models.UniqueConstraint(fields=('user', 'name'), name='uniq_persona_user_name'),
        ),
    ]
