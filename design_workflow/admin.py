from django.contrib import admin
from django.contrib.admin.sites import AlreadyRegistered
from simple_history.admin import SimpleHistoryAdmin

from .models import (
    AttachmentAnnotation,
    ChatMessage,
    ChatMessageAttachment,
    ChatMessageEdit,
    ChatMessageReaction,
    ChatMessageReminder,
    ChatThread,
    Notification,
    NotificationPreference,
    Project,
    SavedView,
    Task,
    TaskActivity,
    TaskAttachment,
    TaskArtifactVersion,
    TaskChecklist,
    TaskChecklistItem,
    TaskComment,
    TaskLabel,
    TimeEntry,
)


HISTORY_FIELDS = (
    "history_id",
    "history_date",
    "history_change_reason",
    "history_type",
    "history_user",
)


def _history_readonly_fields(model):
    return [
        field.name
        for field in model._meta.get_fields()
        if hasattr(field, "name")
        and getattr(field, "concrete", False)
        and not field.many_to_many
    ] + list(HISTORY_FIELDS)


def _history_admin_class(model, display_fields, list_filter=(), search_fields=()):
    attrs = {
        "__doc__": f"Read-only admin for viewing historical {model.__name__} records.",
        "list_display": (
            "history_id",
            *display_fields,
            "history_type",
            "history_date",
            "history_user",
        ),
        "list_filter": ("history_type", "history_date", *list_filter),
        "search_fields": search_fields,
        "readonly_fields": _history_readonly_fields(model),
        "ordering": ("-history_date", "-history_id"),
        "has_add_permission": lambda self, request: False,
        "has_delete_permission": lambda self, request, obj=None: False,
        "has_change_permission": lambda self, request, obj=None: False,
    }
    return type(f"Historical{model.__name__}Admin", (admin.ModelAdmin,), attrs)


def register_history_admin(model, *, display_fields=("id",), list_filter=(), search_fields=()):
    admin_class = _history_admin_class(model, display_fields, list_filter, search_fields)
    try:
        admin.site.register(model.history.model, admin_class)
    except AlreadyRegistered:
        pass


@admin.register(Project)
class ProjectAdmin(SimpleHistoryAdmin):
    list_display = ("name", "manager", "status", "priority", "archived", "target_end_date")
    list_filter = ("status", "priority", "archived")
    search_fields = ("name", "description")


@admin.register(Task)
class TaskAdmin(SimpleHistoryAdmin):
    list_display = ("title", "project", "current_assignee", "status", "priority", "due_date", "actual_minutes")
    list_filter = ("status", "priority", "project")
    search_fields = ("title", "description", "project__name")


@admin.register(TimeEntry)
class TimeEntryAdmin(SimpleHistoryAdmin):
    list_display = ("task", "user", "minutes", "work_date", "created_at")
    list_filter = ("work_date",)


@admin.register(TaskComment)
class TaskCommentAdmin(SimpleHistoryAdmin):
    list_display = ("task", "author", "created_at")
    search_fields = ("task__title", "author__email", "body")


@admin.register(TaskActivity)
class TaskActivityAdmin(SimpleHistoryAdmin):
    list_display = ("task", "actor", "action_type", "created_at")
    list_filter = ("action_type",)


@admin.register(Notification)
class NotificationAdmin(SimpleHistoryAdmin):
    list_display = ("recipient", "type", "task", "read_at", "snoozed_until", "action_taken_at", "created_at")
    list_filter = ("type", "read_at", "snoozed_until")


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(SimpleHistoryAdmin):
    list_display = ("user", "mentions", "assignments", "review_requests", "due_soon", "digest_frequency")
    list_filter = ("digest_frequency", "mentions", "assignments", "review_requests", "due_soon")


@admin.register(SavedView)
class SavedViewAdmin(SimpleHistoryAdmin):
    list_display = ("name", "owner", "visibility", "density", "is_default", "updated_at")
    list_filter = ("visibility", "density", "is_default")
    search_fields = ("name", "owner__email")



@admin.register(TaskLabel)
class TaskLabelAdmin(SimpleHistoryAdmin):
    list_display = ("name", "color", "created_at")
    search_fields = ("name",)


@admin.register(TaskChecklistItem)
class TaskChecklistItemAdmin(SimpleHistoryAdmin):
    list_display = ("task", "checklist", "title", "done", "sort_order", "created_by")
    list_filter = ("done",)
    search_fields = ("title", "task__title")


@admin.register(TaskChecklist)
class TaskChecklistAdmin(SimpleHistoryAdmin):
    list_display = ("task", "title", "sort_order", "created_by", "created_at")
    search_fields = ("title", "task__title")


@admin.register(TaskAttachment)
class TaskAttachmentAdmin(SimpleHistoryAdmin):
    list_display = ("task", "name", "uploaded_by", "size", "created_at")
    search_fields = ("name", "task__title")


@admin.register(TaskArtifactVersion)
class TaskArtifactVersionAdmin(SimpleHistoryAdmin):
    list_display = ("task", "version_number", "approval_state", "uploaded_by", "approved_by", "created_at")
    list_filter = ("approval_state",)
    search_fields = ("task__title", "notes", "attachment__name")


@admin.register(AttachmentAnnotation)
class AttachmentAnnotationAdmin(SimpleHistoryAdmin):
    list_display = ("attachment", "author", "x_percent", "y_percent", "resolved", "created_at")
    list_filter = ("resolved",)
    search_fields = ("body", "attachment__name", "attachment__task__title")


@admin.register(ChatThread)
class ChatThreadAdmin(SimpleHistoryAdmin):
    list_display = ("id", "kind", "title", "project", "task", "updated_at")
    list_filter = ("kind",)
    search_fields = ("title", "project__name", "task__title")


@admin.register(ChatMessage)
class ChatMessageAdmin(SimpleHistoryAdmin):
    list_display = ("thread", "sender", "decision_at", "edited_at", "created_at")
    search_fields = ("body", "sender__email")


@admin.register(ChatMessageAttachment)
class ChatMessageAttachmentAdmin(SimpleHistoryAdmin):
    list_display = ("message", "name", "size", "created_at")
    search_fields = ("name",)


@admin.register(ChatMessageEdit)
class ChatMessageEditAdmin(SimpleHistoryAdmin):
    list_display = ("message", "edited_by", "created_at")
    search_fields = ("previous_body", "new_body", "edited_by__email")


@admin.register(ChatMessageReaction)
class ChatMessageReactionAdmin(SimpleHistoryAdmin):
    list_display = ("message", "user", "emoji", "created_at")
    list_filter = ("emoji",)


@admin.register(ChatMessageReminder)
class ChatMessageReminderAdmin(SimpleHistoryAdmin):
    list_display = ("message", "task", "created_by", "remind_at", "done_at")
    list_filter = ("done_at", "remind_at")


register_history_admin(
    Project,
    display_fields=("id", "name", "manager", "status", "priority", "archived", "target_end_date"),
    list_filter=("status", "priority", "archived"),
    search_fields=("name", "description"),
)
register_history_admin(
    Task,
    display_fields=("id", "title", "project", "current_assignee", "status", "priority", "due_date", "actual_minutes"),
    list_filter=("status", "priority", "project"),
    search_fields=("title", "description", "project__name"),
)
register_history_admin(
    TimeEntry,
    display_fields=("id", "task", "user", "minutes", "work_date"),
    list_filter=("work_date",),
)
register_history_admin(
    TaskComment,
    display_fields=("id", "task", "author", "created_at"),
    search_fields=("task__title", "author__email", "body"),
)
register_history_admin(
    TaskActivity,
    display_fields=("id", "task", "actor", "action_type", "created_at"),
    list_filter=("action_type",),
)
register_history_admin(
    Notification,
    display_fields=("id", "recipient", "type", "task", "read_at", "snoozed_until", "created_at"),
    list_filter=("type", "read_at", "snoozed_until"),
)
register_history_admin(
    NotificationPreference,
    display_fields=("id", "user", "mentions", "assignments", "review_requests", "due_soon", "digest_frequency"),
    list_filter=("digest_frequency", "mentions", "assignments", "review_requests", "due_soon"),
)
register_history_admin(
    SavedView,
    display_fields=("id", "name", "owner", "visibility", "density", "is_default", "updated_at"),
    list_filter=("visibility", "density", "is_default"),
    search_fields=("name", "owner__email"),
)
register_history_admin(
    TaskLabel,
    display_fields=("id", "name", "color", "created_at"),
    search_fields=("name",),
)
register_history_admin(
    TaskChecklistItem,
    display_fields=("id", "task", "checklist", "title", "done", "sort_order", "created_by"),
    list_filter=("done",),
    search_fields=("title", "task__title"),
)
register_history_admin(
    TaskChecklist,
    display_fields=("id", "task", "title", "sort_order", "created_by", "created_at"),
    search_fields=("title", "task__title"),
)
register_history_admin(
    TaskAttachment,
    display_fields=("id", "task", "name", "uploaded_by", "size", "created_at"),
    search_fields=("name", "task__title"),
)
register_history_admin(
    TaskArtifactVersion,
    display_fields=("id", "task", "version_number", "approval_state", "uploaded_by", "approved_by", "created_at"),
    list_filter=("approval_state",),
    search_fields=("task__title", "notes", "attachment__name"),
)
register_history_admin(
    AttachmentAnnotation,
    display_fields=("id", "attachment", "author", "x_percent", "y_percent", "resolved", "created_at"),
    list_filter=("resolved",),
    search_fields=("body", "attachment__name", "attachment__task__title"),
)
register_history_admin(
    ChatThread,
    display_fields=("id", "kind", "title", "project", "task", "updated_at"),
    list_filter=("kind",),
    search_fields=("title", "project__name", "task__title"),
)
register_history_admin(
    ChatMessage,
    display_fields=("id", "thread", "sender", "decision_at", "edited_at", "created_at"),
    search_fields=("body", "sender__email"),
)
register_history_admin(
    ChatMessageAttachment,
    display_fields=("id", "message", "name", "size", "created_at"),
    search_fields=("name",),
)
register_history_admin(
    ChatMessageEdit,
    display_fields=("id", "message", "edited_by", "created_at"),
    search_fields=("previous_body", "new_body", "edited_by__email"),
)
register_history_admin(
    ChatMessageReaction,
    display_fields=("id", "message", "user", "emoji", "created_at"),
    list_filter=("emoji",),
)
register_history_admin(
    ChatMessageReminder,
    display_fields=("id", "message", "task", "created_by", "remind_at", "done_at"),
    list_filter=("done_at", "remind_at"),
)
