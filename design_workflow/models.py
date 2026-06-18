from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone
from simple_history.models import HistoricalRecords


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


class SavedViewVisibility(models.TextChoices):
    PRIVATE = "private", "Private"
    TEAM = "team", "Team"


class SavedViewDensity(models.TextChoices):
    COMFORTABLE = "comfortable", "Comfortable"
    COMPACT = "compact", "Compact"


class TaskReviewState(models.TextChoices):
    NOT_SUBMITTED = "not_submitted", "Not submitted"
    NEEDS_REVIEW = "needs_review", "Needs review"
    CHANGES_REQUESTED = "changes_requested", "Changes requested"
    APPROVED = "approved", "Approved"


class ArtifactApprovalState(models.TextChoices):
    PENDING = "pending", "Pending"
    CHANGES_REQUESTED = "changes_requested", "Changes requested"
    APPROVED = "approved", "Approved"


class NotificationDigestFrequency(models.TextChoices):
    INSTANT = "instant", "Instant"
    DAILY = "daily", "Daily"
    WEEKLY = "weekly", "Weekly"
    OFF = "off", "Off"


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
    LABEL_UPDATED = "label_updated", "Label updated"
    CHECKLIST_UPDATED = "checklist_updated", "Checklist updated"
    ATTACHMENT_ADDED = "attachment_added", "Attachment added"
    TASK_ARCHIVED = "task_archived", "Task archived"
    REVIEW_UPDATED = "review_updated", "Review updated"
    ARTIFACT_VERSION_ADDED = "artifact_version_added", "Artifact version added"
    ANNOTATION_ADDED = "annotation_added", "Annotation added"


class NotificationType(models.TextChoices):
    TASK_ASSIGNED = "task_assigned", "Task assigned"
    TASK_REASSIGNED = "task_reassigned", "Task reassigned"
    TASK_DUE_SOON = "task_due_soon", "Task due soon"
    TASK_OVERDUE = "task_overdue", "Task overdue"
    TASK_COMMENT = "task_comment", "Task comment"
    TASK_STATUS = "task_status", "Task status"
    TASK_BLOCKED = "task_blocked", "Task blocked"
    CHAT_MESSAGE = "chat_message", "Chat message"
    REVIEW_REQUESTED = "review_requested", "Review requested"
    WORKFLOW_DIGEST = "workflow_digest", "Workflow digest"


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
    history = HistoricalRecords()

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
        return self.tasks.filter(archived=False).exclude(status=TaskStatus.DONE).count()


class TaskLabel(TimestampedModel):
    name = models.CharField(max_length=80, unique=True)
    color = models.CharField(max_length=16, default="#111827")
    history = HistoricalRecords()

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return self.name


class SavedView(TimestampedModel):
    name = models.CharField(max_length=120)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="design_saved_views",
    )
    visibility = models.CharField(
        max_length=16,
        choices=SavedViewVisibility.choices,
        default=SavedViewVisibility.PRIVATE,
        db_index=True,
    )
    filters = models.JSONField(default=dict, blank=True)
    sort = models.JSONField(default=dict, blank=True)
    density = models.CharField(
        max_length=16,
        choices=SavedViewDensity.choices,
        default=SavedViewDensity.COMFORTABLE,
    )
    collapsed_lanes = models.JSONField(default=list, blank=True)
    show_archived = models.BooleanField(default=False)
    is_default = models.BooleanField(default=False, db_index=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ("-is_default", "name")
        constraints = (
            models.UniqueConstraint(fields=("owner", "name"), name="unique_design_saved_view_owner_name"),
        )
        indexes = (
            models.Index(fields=("owner", "visibility")),
            models.Index(fields=("owner", "is_default")),
        )

    def __str__(self):
        return self.name


class Task(TimestampedModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="tasks")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    cover_image = models.ImageField(
        upload_to="design_workflow/task_covers/%Y/%m/",
        null=True,
        blank=True,
    )
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
    work_started_at = models.DateTimeField(null=True, blank=True)
    review_state = models.CharField(
        max_length=24,
        choices=TaskReviewState.choices,
        default=TaskReviewState.NOT_SUBMITTED,
        db_index=True,
    )
    review_requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_design_task_reviews",
    )
    review_requested_at = models.DateTimeField(null=True, blank=True)
    review_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_design_task_reviews",
    )
    review_approved_at = models.DateTimeField(null=True, blank=True)
    blocked_reason = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    labels = models.ManyToManyField(TaskLabel, blank=True, related_name="tasks")
    archived = models.BooleanField(default=False, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    is_completed = models.BooleanField(default=False, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
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
    source_chat_message = models.ForeignKey(
        "ChatMessage",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_tasks",
    )
    history = HistoricalRecords()

    class Meta:
        ordering = ("project_id", "sort_order", "-created_at")
        indexes = [
            models.Index(fields=("project", "status")),
            models.Index(fields=("current_assignee", "status")),
            models.Index(fields=("due_date", "status")),
            models.Index(fields=("archived", "status")),
            models.Index(fields=("review_state", "status")),
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


class TaskChecklistItem(TimestampedModel):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="checklist_items")
    checklist = models.ForeignKey(
        "TaskChecklist",
        on_delete=models.CASCADE,
        related_name="items",
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=255)
    done = models.BooleanField(default=False, db_index=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_task_checklist_items",
    )
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="completed_task_checklist_items",
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ("sort_order", "created_at")

    def __str__(self):
        return self.title


class TaskChecklist(TimestampedModel):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="checklists")
    title = models.CharField(max_length=255)
    sort_order = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_task_checklists",
    )
    history = HistoricalRecords()

    class Meta:
        ordering = ("sort_order", "created_at")

    def __str__(self):
        return self.title


class TaskAttachment(TimestampedModel):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="attachments")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="uploaded_task_attachments",
    )
    file = models.FileField(upload_to="design_workflow/task_attachments/%Y/%m/")
    name = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=120, blank=True)
    size = models.PositiveIntegerField(default=0)
    history = HistoricalRecords()

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return self.name


class TaskArtifactVersion(TimestampedModel):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="artifact_versions")
    attachment = models.ForeignKey(
        TaskAttachment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="artifact_versions",
    )
    version_number = models.PositiveIntegerField()
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="uploaded_design_artifact_versions",
    )
    notes = models.TextField(blank=True)
    approval_state = models.CharField(
        max_length=24,
        choices=ArtifactApprovalState.choices,
        default=ArtifactApprovalState.PENDING,
        db_index=True,
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_design_artifact_versions",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ("-version_number", "-created_at")
        constraints = (
            models.UniqueConstraint(fields=("task", "version_number"), name="unique_design_task_artifact_version"),
        )
        indexes = (
            models.Index(fields=("task", "approval_state")),
        )

    def __str__(self):
        return f"{self.task_id}:v{self.version_number}"


class AttachmentAnnotation(TimestampedModel):
    attachment = models.ForeignKey(TaskAttachment, on_delete=models.CASCADE, related_name="annotations")
    version = models.ForeignKey(
        TaskArtifactVersion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="annotations",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="design_attachment_annotations",
    )
    x_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=(MinValueValidator(0), MaxValueValidator(100)),
    )
    y_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=(MinValueValidator(0), MaxValueValidator(100)),
    )
    body = models.TextField()
    resolved = models.BooleanField(default=False, db_index=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_design_attachment_annotations",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ("created_at",)
        indexes = (
            models.Index(fields=("attachment", "resolved")),
        )

    def __str__(self):
        return f"{self.attachment_id}:{self.x_percent},{self.y_percent}"


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
    history = HistoricalRecords()

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
    history = HistoricalRecords()

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
    history = HistoricalRecords()

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
    snoozed_until = models.DateTimeField(null=True, blank=True, db_index=True)
    action_taken_at = models.DateTimeField(null=True, blank=True)
    action_taken_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="acted_design_notifications",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ("-created_at",)

    @property
    def is_read(self) -> bool:
        return self.read_at is not None


class NotificationPreference(TimestampedModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="+",
    )
    mentions = models.BooleanField(default=True)
    assignments = models.BooleanField(default=True)
    review_requests = models.BooleanField(default=True)
    due_soon = models.BooleanField(default=True)
    digest_frequency = models.CharField(
        max_length=16,
        choices=NotificationDigestFrequency.choices,
        default=NotificationDigestFrequency.INSTANT,
    )
    history = HistoricalRecords()

    def __str__(self):
        return f"{self.user_id}:{self.digest_frequency}"



class ChatThreadKind(models.TextChoices):
    PUBLIC = "public", "Public"
    PRIVATE = "private", "Private"
    PROJECT = "project", "Project"
    TASK = "task", "Task"


class ChatThread(TimestampedModel):
    kind = models.CharField(max_length=16, choices=ChatThreadKind.choices, db_index=True)
    title = models.CharField(max_length=255, blank=True)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="chat_threads",
    )
    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="chat_threads",
    )
    participants = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="design_chat_threads",
    )
    history = HistoricalRecords()

    class Meta:
        ordering = ("-updated_at",)
        indexes = (
            models.Index(fields=("kind", "project")),
            models.Index(fields=("kind", "task")),
        )
        constraints = (
            models.UniqueConstraint(
                fields=("kind", "project"),
                condition=Q(kind=ChatThreadKind.PROJECT, project__isnull=False),
                name="unique_design_project_chat_thread",
            ),
            models.UniqueConstraint(
                fields=("kind", "task"),
                condition=Q(kind=ChatThreadKind.TASK, task__isnull=False),
                name="unique_design_task_chat_thread",
            ),
        )

    def __str__(self):
        return self.title or self.kind


class ChatMessage(TimestampedModel):
    thread = models.ForeignKey(ChatThread, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="design_chat_messages",
    )
    reply_to = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="replies",
    )
    body = models.TextField(blank=True)
    read_by = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="read_design_chat_messages",
    )
    mentions = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="mentioned_design_chat_messages",
    )
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="deleted_design_chat_messages",
    )
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="edited_design_chat_messages",
    )
    edited_at = models.DateTimeField(null=True, blank=True, db_index=True)
    decision_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="decision_design_chat_messages",
    )
    decision_at = models.DateTimeField(null=True, blank=True, db_index=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ("created_at",)

    def __str__(self):
        return f"{self.thread_id}:{self.sender_id}"

    @property
    def is_read(self) -> bool:
        participant_count = self.thread.participants.count()
        if self.thread.kind == ChatThreadKind.PUBLIC:
            return False
        return self.read_by.count() >= participant_count


class ChatMessageAttachment(TimestampedModel):
    message = models.ForeignKey(ChatMessage, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to="design_workflow/chat_attachments/%Y/%m/")
    name = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=120, blank=True)
    size = models.PositiveIntegerField(default=0)
    history = HistoricalRecords()

    class Meta:
        ordering = ("created_at",)

    def __str__(self):
        return self.name


class ChatMessageEdit(TimestampedModel):
    message = models.ForeignKey(ChatMessage, on_delete=models.CASCADE, related_name="edit_history")
    edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="design_chat_message_edits",
    )
    previous_body = models.TextField(blank=True)
    new_body = models.TextField(blank=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.message_id}:{self.edited_by_id}"


class ChatMessageReaction(TimestampedModel):
    message = models.ForeignKey(ChatMessage, on_delete=models.CASCADE, related_name="reactions")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="design_chat_reactions",
    )
    emoji = models.CharField(max_length=16)
    history = HistoricalRecords()

    class Meta:
        ordering = ("created_at",)
        constraints = (
            models.UniqueConstraint(fields=("message", "user", "emoji"), name="unique_design_chat_reaction"),
        )

    def __str__(self):
        return f"{self.message_id}:{self.emoji}"


class ChatMessageReminder(TimestampedModel):
    message = models.ForeignKey(ChatMessage, on_delete=models.CASCADE, related_name="reminders")
    task = models.ForeignKey(
        Task,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="chat_reminders",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_design_chat_reminders",
    )
    remind_at = models.DateTimeField(null=True, blank=True, db_index=True)
    note = models.CharField(max_length=255, blank=True)
    done_at = models.DateTimeField(null=True, blank=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ("remind_at", "created_at")

    def __str__(self):
        return f"{self.message_id}:{self.remind_at}"
