from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import users.models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('users', '0015_userprofile_ai_personalization_delete_goalprofile'),
    ]

    operations = [
        # Add invite chain fields to UserProfile
        migrations.AddField(
            model_name='userprofile',
            name='invited_by',
            field=models.ForeignKey(
                blank=True,
                help_text='User who invited this person',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='referred_users',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='invite_depth',
            field=models.PositiveIntegerField(
                default=0,
                help_text='0 = admin-seeded (can send platform invites), 1+ = user-invited (room invites only)',
            ),
        ),
        # Create PlatformInvite model
        migrations.CreateModel(
            name='PlatformInvite',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('email', models.EmailField(max_length=254)),
                ('token', models.CharField(default=users.models.generate_trial_token, max_length=64, unique=True)),
                ('status', models.CharField(choices=[('sent', 'Sent'), ('activated', 'Activated'), ('expired', 'Expired'), ('revoked', 'Revoked')], default='sent', max_length=20)),
                ('invite_depth', models.PositiveIntegerField(help_text='Depth of the invited user (inviter depth + 1)')),
                ('activated_at', models.DateTimeField(blank=True, null=True)),
                ('sent_at', models.DateTimeField(auto_now_add=True)),
                ('expires_at', models.DateTimeField()),
                ('invited_by', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sent_platform_invites', to=settings.AUTH_USER_MODEL)),
                ('activated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='activated_platform_invites', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-sent_at'],
            },
        ),
    ]
