from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("chatbot", "0013_roomcontext_memory_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="ActionReceipt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action", models.CharField(max_length=100)),
                ("service", models.CharField(blank=True, max_length=50)),
                ("params", models.JSONField(blank=True, default=dict)),
                ("result", models.JSONField(blank=True, default=dict)),
                ("status", models.CharField(choices=[("success", "Success"), ("error", "Error"), ("pending", "Pending"), ("cancelled", "Cancelled")], default="success", max_length=20)),
                ("reversible", models.BooleanField(default=False)),
                ("undo_action", models.CharField(blank=True, max_length=100)),
                ("undo_params", models.JSONField(blank=True, default=dict)),
                ("reason", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("room", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="chatbot.chatroom")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="action_receipts", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="actionreceipt",
            index=models.Index(fields=["user", "room", "created_at"], name="orchestration_actionreceipt_user_room_created_idx"),
        ),
    ]
