from django.db import migrations

def consolidate_social_links(apps, schema_editor):
    UserProfile = apps.get_model('users', 'UserProfile')
    for profile in UserProfile.objects.all():
        social_links = profile.social_links or {}
        
        updated = False
        if profile.twitter_handle and 'twitter' not in social_links:
            social_links['twitter'] = profile.twitter_handle
            updated = True
        
        if profile.linkedin_url and 'linkedin' not in social_links:
            social_links['linkedin'] = profile.linkedin_url
            updated = True
            
        if profile.github_url and 'github' not in social_links:
            social_links['github'] = profile.github_url
            updated = True
            
        if updated:
            profile.social_links = social_links
            profile.save()

class Migration(migrations.Migration):

    dependencies = [
        ('users', '0006_userprofile_company_name_userprofile_company_size_and_more'),
    ]

    operations = [
        migrations.RunPython(consolidate_social_links),
    ]
