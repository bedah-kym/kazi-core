import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workflows', '0008_capability_manifest'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='workflowdraft',
            name='source',
            field=models.CharField(choices=[('chat', 'Chat'), ('explicit_save', 'Explicit Save'), ('statistical', 'Statistical'), ('reviewer', 'Reviewer')], default='chat', max_length=20),
        ),
        migrations.AddField(
            model_name='workflowdraft',
            name='skill_name',
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name='workflowdraft',
            name='skill_contract',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.CreateModel(
            name='WorkflowCandidate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('room_id', models.IntegerField(blank=True, null=True)),
                ('pattern', models.JSONField(blank=True, default=dict)),
                ('pattern_key', models.CharField(max_length=64)),
                ('occurrences', models.PositiveIntegerField(default=0)),
                ('success_rate', models.FloatField(default=0.0)),
                ('status', models.CharField(choices=[('candidate', 'Candidate'), ('drafted', 'Drafted'), ('discarded', 'Discarded')], default='candidate', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('draft', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='promotion_candidates', to='workflows.workflowdraft')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='workflow_candidates', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-occurrences', '-updated_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='workflowcandidate',
            constraint=models.UniqueConstraint(condition=models.Q(('status', 'candidate')), fields=('user', 'pattern_key'), name='uniq_candidate_per_user_pattern'),
        ),
    ]
