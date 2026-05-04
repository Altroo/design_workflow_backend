from django.contrib import admin

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


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "manager", "status", "priority", "archived", "target_end_date")
    list_filter = ("status", "priority", "archived")
    search_fields = ("name", "description")


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("title", "project", "current_assignee", "status", "priority", "due_date", "actual_minutes")
    list_filter = ("status", "priority", "project")
    search_fields = ("title", "description", "project__name")


@admin.register(TimeEntry)
class TimeEntryAdmin(admin.ModelAdmin):
    list_display = ("task", "user", "minutes", "work_date", "created_at")
    list_filter = ("work_date",)


@admin.register(TaskComment)
class TaskCommentAdmin(admin.ModelAdmin):
    list_display = ("task", "author", "created_at")
    search_fields = ("task__title", "author__email", "body")


@admin.register(TaskActivity)
class TaskActivityAdmin(admin.ModelAdmin):
    list_display = ("task", "actor", "action_type", "created_at")
    list_filter = ("action_type",)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("recipient", "type", "task", "read_at", "snoozed_until", "action_taken_at", "created_at")
    list_filter = ("type", "read_at", "snoozed_until")


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ("user", "mentions", "assignments", "review_requests", "due_soon", "digest_frequency")
    list_filter = ("digest_frequency", "mentions", "assignments", "review_requests", "due_soon")


@admin.register(SavedView)
class SavedViewAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "visibility", "density", "is_default", "updated_at")
    list_filter = ("visibility", "density", "is_default")
    search_fields = ("name", "owner__email")



@admin.register(TaskLabel)
class TaskLabelAdmin(admin.ModelAdmin):
    list_display = ("name", "color", "created_at")
    search_fields = ("name",)


@admin.register(TaskChecklistItem)
class TaskChecklistItemAdmin(admin.ModelAdmin):
    list_display = ("task", "checklist", "title", "done", "sort_order", "created_by")
    list_filter = ("done",)
    search_fields = ("title", "task__title")


@admin.register(TaskChecklist)
class TaskChecklistAdmin(admin.ModelAdmin):
    list_display = ("task", "title", "sort_order", "created_by", "created_at")
    search_fields = ("title", "task__title")


@admin.register(TaskAttachment)
class TaskAttachmentAdmin(admin.ModelAdmin):
    list_display = ("task", "name", "uploaded_by", "size", "created_at")
    search_fields = ("name", "task__title")


@admin.register(TaskArtifactVersion)
class TaskArtifactVersionAdmin(admin.ModelAdmin):
    list_display = ("task", "version_number", "approval_state", "uploaded_by", "approved_by", "created_at")
    list_filter = ("approval_state",)
    search_fields = ("task__title", "notes", "attachment__name")


@admin.register(AttachmentAnnotation)
class AttachmentAnnotationAdmin(admin.ModelAdmin):
    list_display = ("attachment", "author", "x_percent", "y_percent", "resolved", "created_at")
    list_filter = ("resolved",)
    search_fields = ("body", "attachment__name", "attachment__task__title")


@admin.register(ChatThread)
class ChatThreadAdmin(admin.ModelAdmin):
    list_display = ("id", "kind", "title", "project", "task", "updated_at")
    list_filter = ("kind",)
    search_fields = ("title", "project__name", "task__title")


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ("thread", "sender", "decision_at", "edited_at", "created_at")
    search_fields = ("body", "sender__email")


@admin.register(ChatMessageAttachment)
class ChatMessageAttachmentAdmin(admin.ModelAdmin):
    list_display = ("message", "name", "size", "created_at")
    search_fields = ("name",)


@admin.register(ChatMessageEdit)
class ChatMessageEditAdmin(admin.ModelAdmin):
    list_display = ("message", "edited_by", "created_at")
    search_fields = ("previous_body", "new_body", "edited_by__email")


@admin.register(ChatMessageReaction)
class ChatMessageReactionAdmin(admin.ModelAdmin):
    list_display = ("message", "user", "emoji", "created_at")
    list_filter = ("emoji",)


@admin.register(ChatMessageReminder)
class ChatMessageReminderAdmin(admin.ModelAdmin):
    list_display = ("message", "task", "created_by", "remind_at", "done_at")
    list_filter = ("done_at", "remind_at")
