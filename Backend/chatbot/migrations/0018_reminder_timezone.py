from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chatbot', '0017_rename_chatbot_con_user_id_a1b2c3_idx_chatbot_con_user_id_187fae_idx_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='reminder',
            name='timezone',
            field=models.CharField(
                default='UTC',
                help_text="IANA timezone identifier (e.g., 'Africa/Nairobi', 'America/New_York')",
                max_length=50,
            ),
        ),
    ]
