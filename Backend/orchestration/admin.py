from django.contrib import admin

from .models import ActionReceipt


@admin.register(ActionReceipt)
class ActionReceiptAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "room", "action", "status", "reversible", "created_at")
    list_filter = ("status", "action", "reversible")
    search_fields = ("action", "user__username", "user__email")
