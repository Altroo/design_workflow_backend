from django.contrib import admin
from .models import AuditEvent


@admin.register(AuditEvent)
class ChatAuditAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "actor_id",
        "application",
        "tool",
        "outcome",
        "resource",
        "record_id",
    )
    list_filter = ("application", "tool", "outcome")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
