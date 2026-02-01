from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chatbot', '0010_chatroom_encryption_key_text'),
    ]

    operations = [
        migrations.AddField(
            model_name='message',
            name='parent',
            field=models.ForeignKey(
                related_name='replies',
                null=True,
                blank=True,
                on_delete=models.SET_NULL,
                to='chatbot.message'
            ),
        ),
    ]
