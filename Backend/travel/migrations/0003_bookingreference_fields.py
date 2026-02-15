from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('travel', '0002_tripfeedback'),
    ]

    operations = [
        migrations.AddField(
            model_name='bookingreference',
            name='booking_reference',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='bookingreference',
            name='confirmation_code',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]
