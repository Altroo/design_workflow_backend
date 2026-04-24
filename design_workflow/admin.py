from django.contrib import admin

from .models import Notification, Project, Task, TaskActivity, TaskComment, TimeEntry


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
    list_display = ("recipient", "type", "task", "read_at", "created_at")
    list_filter = ("type", "read_at")

