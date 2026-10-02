from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workflows', '0012_suggestion_metadata'),
    ]

    operations = [
        migrations.AddField(
            model_name='userworkflow',
            name='reactivated_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
