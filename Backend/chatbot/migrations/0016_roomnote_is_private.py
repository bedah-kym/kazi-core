from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chatbot', '0015_contact'),
    ]

    operations = [
        migrations.AddField(
            model_name='roomnote',
            name='is_private',
            field=models.BooleanField(default=False, help_text='Private notes are not shared across linked rooms'),
        ),
    ]
