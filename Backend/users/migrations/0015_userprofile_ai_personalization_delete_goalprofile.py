"""
Add ai_personalization_enabled to UserProfile, migrate data from GoalProfile, then delete GoalProfile.
"""
from django.db import migrations, models


def copy_goalprofile_data(apps, schema_editor):
    """Copy ai_personalization_enabled and industry from GoalProfile to UserProfile."""
    GoalProfile = apps.get_model('users', 'GoalProfile')
    UserProfile = apps.get_model('users', 'UserProfile')

    for gp in GoalProfile.objects.select_related('workspace__user').all():
        try:
            profile = UserProfile.objects.get(user=gp.workspace.user)
            profile.ai_personalization_enabled = gp.ai_personalization_enabled
            if gp.industry and not profile.industry:
                profile.industry = gp.industry
            profile.save(update_fields=['ai_personalization_enabled', 'industry'])
        except UserProfile.DoesNotExist:
            pass


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0014_correction_signal'),
    ]

    operations = [
        # Step 1: Add ai_personalization_enabled to UserProfile
        migrations.AddField(
            model_name='userprofile',
            name='ai_personalization_enabled',
            field=models.BooleanField(
                default=True,
                help_text='Allow AI to use profile data for personalized suggestions',
            ),
        ),
        # Step 2: Copy data from GoalProfile -> UserProfile
        migrations.RunPython(copy_goalprofile_data, noop),
        # Step 3: Delete GoalProfile model
        migrations.DeleteModel(
            name='GoalProfile',
        ),
    ]
