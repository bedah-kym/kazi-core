from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chatbot", "0009_message_audio_url_message_has_ai_voice_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="chatroom",
            name="encryption_key",
            field=models.TextField(blank=True),
        ),
    ]
