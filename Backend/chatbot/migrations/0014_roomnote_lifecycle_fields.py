from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chatbot', '0013_roomcontext_memory_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='roomnote',
            name='is_archived',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='roomnote',
            name='last_accessed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name='roomnote',
            index=models.Index(
                fields=['is_archived', 'priority', 'note_type'],
                name='chatbot_roo_is_arch_idx',
            ),
        ),
    ]
