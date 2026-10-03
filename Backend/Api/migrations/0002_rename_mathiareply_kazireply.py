from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('Api', '0001_initial'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='MathiaReply',
            new_name='KaziReply',
        ),
    ]
