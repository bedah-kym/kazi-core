from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('chatbot', '0014_roomnote_lifecycle_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='Contact',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('email', models.EmailField(blank=True, default='', max_length=254)),
                ('phone', models.CharField(blank=True, default='', max_length=20)),
                ('label', models.CharField(blank=True, default='', help_text="E.g. 'colleague', 'client', 'friend'", max_length=100)),
                ('source', models.CharField(choices=[('manual', 'Manual'), ('ai_extracted', 'AI Extracted')], default='manual', max_length=15)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('room', models.ForeignKey(blank=True, help_text='Null = global contact', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='contacts', to='chatbot.chatroom')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='contacts', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.AddIndex(
            model_name='contact',
            index=models.Index(fields=['user', 'name'], name='chatbot_con_user_id_a1b2c3_idx'),
        ),
        migrations.AddIndex(
            model_name='contact',
            index=models.Index(fields=['user', 'email'], name='chatbot_con_user_id_d4e5f6_idx'),
        ),
        migrations.AddIndex(
            model_name='contact',
            index=models.Index(fields=['user', 'phone'], name='chatbot_con_user_id_g7h8i9_idx'),
        ),
    ]
