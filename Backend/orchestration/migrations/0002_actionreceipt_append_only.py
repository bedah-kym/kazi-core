from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('orchestration', '0001_initial'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='actionreceipt',
            unique_together=set(),
        ),
    ]
