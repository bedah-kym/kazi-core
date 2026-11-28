from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orchestration', '0006_turn_tool_call'),
    ]

    operations = [
        migrations.AddField(
            model_name='turn',
            name='stream_seq',
            field=models.IntegerField(default=0),
        ),
    ]
