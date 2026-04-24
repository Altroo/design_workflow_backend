from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.utils import timezone


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Priority(models.TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"
    URGENT = "urgent", "Urgent"


class ProjectStatus(models.TextChoices):
    PLANNED = "planned", "Planned"
    ACTIVE = "active", "Active"
    ON_HOLD = "on_hold", "On hold"
    COMPLETED = "completed", "Completed"
    ARCHIVED = "archived", "Archived"


class TaskStatus(models.TextChoices):
    BACKLOG = "backlog", "Backlog"
    TODO = "todo", "To do"
    IN_PROGRESS = "in_progress", "In progress"
    IN_REVIEW = "in_review", "In review"
    BLOCKED = "blocked", "Blocked"
    DONE = "done", "Done"


class TaskActivityType(models.TextChoices):
    CREATED = "created", "Created"
    UPDATED = "updated", "Updated"
    STATUS_CHANGED = "status_changed", "Status changed"
    PRIORITY_CHANGED = "priority_changed", "Priority changed"
    DUE_DATE_CHANGED = "due_date_changed", "Due date changed"
    REASSIGNED = "reassigned", "Reassigned"
    COMMENT_ADDED = "comment_added", "Comment added"
    TIME_LOGGED = "time_logged", "Time logged"
    PROJECT_CREATED = "project_created", "Project created"
    PROJECT_UPDATED = "project_updated", "Project updated"
    PROJECT_ARCHIVED = "project_archived", "Project archived"


class NotificationType(models.TextChoices):
    TASK_ASSIGNED = "task_assigned", "Task assigned"
    TASK_REASSIGNED = "task_reassigned", "Task reassigned"
    TASK_DUE_SOON = "task_due_soon", "Task due soon"
    TASK_OVERDUE = "task_overdue", "Task overdue"
    TASK_COMMENT = "task_comment", "Task comment"
    TASK_STATUS = "task_status", "Task status"
    TASK_BLOCKED = "task_blocked", "Task blocked"


class Project(TimestampedModel):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="managed_projects",
    )
    start_date = models.DateField(null=True, blank=True)
    target_end_date = models.DateField(null=True, blank=True)
    priority = models.CharField(
        max_length=16,
        choices=Priority.choices,
        default=Priority.MEDIUM,
        db_index=True,
    )
    status = models.CharField(
        max_length=16,
        choices=ProjectStatus.choices,
        default=ProjectStatus.PLANNED,
        db_index=True,
    )
    archived = models.BooleanField(default=False, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return self.name

    @property
    def total_logged_minutes(self) -> int:
        aggregate = self.tasks.aggregate(total=Sum("time_entries__minutes"))
        return int(aggregate["total"] or 0)

    @property
    def open_tasks_count(self) -> int:
        return self.tasks.exclude(status=TaskStatus.DONE).count()


class Task(TimestampedModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="tasks")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    current_assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="design_tasks",
    )
    status = models.CharField(
        max_length=16,
        choices=TaskStatus.choices,
        default=TaskStatus.BACKLOG,
        db_index=True,
    )
    priority = models.CharField(
        max_length=16,
        choices=Priority.choices,
        default=Priority.MEDIUM,
        db_index=True,
    )
    due_date = models.DateField(null=True, blank=True, db_index=True)
    estimated_minutes = models.PositiveIntegerField(default=0)
    actual_minutes = models.PositiveIntegerField(default=0)
    blocked_reason = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_design_tasks",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="updated_design_tasks",
    )

    class Meta:
        ordering = ("project_id", "sort_order", "-created_at")
        indexes = [
            models.Index(fields=("project", "status")),
            models.Index(fields=("current_assignee", "status")),
            models.Index(fields=("due_date", "status")),
        ]

    def __str__(self):
        return self.title

    @property
    def is_overdue(self) -> bool:
        return bool(
            self.due_date
            and self.status != TaskStatus.DONE
            and self.due_date < timezone.localdate()
        )

    def recalculate_actual_minutes(self, *, save: bool = True) -> int:
        aggregate = self.time_entries.aggregate(total=Sum("minutes"))
        self.actual_minutes = int(aggregate["total"] or 0)
        if save:
            self.save(update_fields=["actual_minutes", "updated_at"])
        return self.actual_minutes


class TimeEntry(TimestampedModel):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="time_entries")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="task_time_entries",
    )
    minutes = models.PositiveIntegerField()
    work_date = models.DateField(default=timezone.localdate)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ("-work_date", "-created_at")

    def __str__(self):
        return f"{self.task_id}:{self.user_id}:{self.minutes}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.task.recalculate_actual_minutes()

    def delete(self, *args, **kwargs):
        task = self.task
        super().delete(*args, **kwargs)
        task.recalculate_actual_minutes()


class TaskComment(TimestampedModel):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="task_comments",
    )
    body = models.TextField()

    class Meta:
        ordering = ("created_at",)


class TaskActivity(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="activities")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="task_activities",
    )
    action_type = models.CharField(max_length=32, choices=TaskActivityType.choices)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)


class Notification(models.Model):
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="design_notifications",
    )
    type = models.CharField(max_length=32, choices=NotificationType.choices, db_index=True)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="notifications",
        null=True,
        blank=True,
    )
    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name="notifications",
        null=True,
        blank=True,
    )
    payload = models.JSONField(default=dict, blank=True)
    read_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at",)

    @property
    def is_read(self) -> bool:
        return self.read_at is not None

