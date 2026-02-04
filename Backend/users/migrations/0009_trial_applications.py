from django.db import migrations, models
import django.db.models.deletion
import users.models
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0008_alter_userprofile_avatar_userintegration'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='workspace',
            name='trial_active',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='workspace',
            name='trial_ends_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='workspace',
            name='trial_started_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='TrialApplication',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120)),
                ('email', models.EmailField(max_length=254)),
                ('role', models.CharField(blank=True, max_length=120)),
                ('company', models.CharField(blank=True, max_length=180)),
                ('industry', models.CharField(blank=True, max_length=120)),
                ('team_size', models.CharField(blank=True, max_length=50)),
                ('current_stack', models.CharField(blank=True, help_text='Tools they use today', max_length=255)),
                ('primary_use_case', models.TextField(blank=True)),
                ('pain_points', models.TextField(blank=True)),
                ('success_metric', models.CharField(blank=True, max_length=255)),
                ('budget_readiness', models.CharField(blank=True, max_length=120)),
                ('go_live_timeframe', models.CharField(blank=True, max_length=120)),
                ('heard_from', models.CharField(blank=True, max_length=120)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected')], default='pending', max_length=20)),
                ('notes', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='TrialInvite',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('email', models.EmailField(max_length=254)),
                ('token', models.CharField(default=users.models.generate_trial_token, max_length=64, unique=True)),
                ('status', models.CharField(choices=[('sent', 'Sent'), ('activated', 'Activated'), ('expired', 'Expired')], default='sent', max_length=20)),
                ('sent_at', models.DateTimeField(blank=True, null=True)),
                ('activated_at', models.DateTimeField(blank=True, null=True)),
                ('trial_ends_at', models.DateTimeField(blank=True, null=True)),
                ('used', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('activated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='activated_trial_invites', to=settings.AUTH_USER_MODEL)),
                ('application', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='invites', to='users.trialapplication')),
                ('sent_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sent_trial_invites', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
