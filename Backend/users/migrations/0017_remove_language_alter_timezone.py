from django.db import migrations, models
import users.models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0016_invite_chain_system'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='userprofile',
            name='language',
        ),
        migrations.AlterField(
            model_name='userprofile',
            name='timezone',
            field=models.CharField(
                default='UTC',
                help_text="IANA timezone identifier (e.g., 'Africa/Nairobi', 'America/New_York')",
                max_length=50,
                validators=[users.models.validate_timezone],
            ),
        ),
    ]
