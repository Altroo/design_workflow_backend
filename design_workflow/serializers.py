from django.contrib.auth import get_user_model
from django.db.models import Sum
from django.utils import timezone
from rest_framework import serializers

from .models import (
    ChatMessage,
    ChatMessageAttachment,
    ChatThread,
    ChatThreadKind,
    Notification,
    Project,
    Task,
    TaskActivity,
    TaskAttachment,
    TaskChecklistItem,
    TaskLabel,
    TaskStatus,
    TimeEntry,
    TaskComment,
)

User = get_user_model()


class UserSummarySerializer(serializers.ModelSerializer):
    avatar = serializers.CharField(source="get_absolute_avatar_cropped_img", read_only=True)

    class Meta:
        model = User
        fields = ("id", "first_name", "last_name", "email", "role", "avatar")


class ProjectSummarySerializer(serializers.ModelSerializer):
    manager = UserSummarySerializer(read_only=True)
    total_logged_minutes = serializers.IntegerField(read_only=True)
    open_tasks_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Project
        fields = (
            "id", "name", "description", "manager", "start_date", "target_end_date",
            "priority", "status", "archived", "archived_at", "total_logged_minutes",
            "open_tasks_count", "created_at", "updated_at",
        )


class TaskLabelSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskLabel
        fields = ("id", "name", "color", "created_at", "updated_at")


class TaskChecklistItemSerializer(serializers.ModelSerializer):
    created_by = UserSummarySerializer(read_only=True)
    completed_by = UserSummarySerializer(read_only=True)

    class Meta:
        model = TaskChecklistItem
        fields = (
            "id", "title", "done", "sort_order", "created_by", "completed_by",
            "completed_at", "created_at", "updated_at",
        )


class TaskAttachmentSerializer(serializers.ModelSerializer):
    uploaded_by = UserSummarySerializer(read_only=True)
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = TaskAttachment
        fields = (
            "id", "uploaded_by", "file", "file_url", "name", "mime_type", "size",
            "created_at", "updated_at",
        )
        read_only_fields = ("file",)

    def get_file_url(self, obj):
        if not obj.file:
            return None
        request = self.context.get("request")
        url = obj.file.url
        return request.build_absolute_uri(url) if request else url


class TaskCardSerializer(serializers.ModelSerializer):
    project = ProjectSummarySerializer(read_only=True)
    current_assignee = UserSummarySerializer(read_only=True)
    labels = TaskLabelSerializer(many=True, read_only=True)
    checklist_items = TaskChecklistItemSerializer(many=True, read_only=True)
    attachments = TaskAttachmentSerializer(many=True, read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)
    cover_image_url = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = (
            "id", "project", "title", "description", "current_assignee", "status",
            "priority", "due_date", "estimated_minutes", "actual_minutes", "blocked_reason",
            "sort_order", "labels", "checklist_items", "attachments", "cover_image_url", "archived", "archived_at",
            "is_completed", "completed_at", "is_overdue", "created_at", "updated_at",
        )

    def get_cover_image_url(self, obj):
        request = self.context.get("request")
        if obj.cover_image:
            url = obj.cover_image.url
            return request.build_absolute_uri(url) if request else url
        first_image = next(
            (
                attachment
                for attachment in obj.attachments.all()
                if attachment.mime_type.startswith("image/")
            ),
            None,
        )
        if not first_image or not first_image.file:
            return None
        url = first_image.file.url
        return request.build_absolute_uri(url) if request else url


class TaskCommentSerializer(serializers.ModelSerializer):
    author = UserSummarySerializer(read_only=True)

    class Meta:
        model = TaskComment
        fields = ("id", "author", "body", "created_at", "updated_at")


class TimeEntrySerializer(serializers.ModelSerializer):
    user = UserSummarySerializer(read_only=True)

    class Meta:
        model = TimeEntry
        fields = ("id", "user", "minutes", "work_date", "note", "created_at", "updated_at")


class TaskActivitySerializer(serializers.ModelSerializer):
    actor = UserSummarySerializer(read_only=True)

    class Meta:
        model = TaskActivity
        fields = ("id", "actor", "action_type", "metadata", "created_at")


class ProjectTaskCommentSerializer(TaskCommentSerializer):
    task_id = serializers.IntegerField(read_only=True)
    task_title = serializers.CharField(source="task.title", read_only=True)

    class Meta(TaskCommentSerializer.Meta):
        fields = TaskCommentSerializer.Meta.fields + ("task_id", "task_title")


class ProjectTaskActivitySerializer(TaskActivitySerializer):
    task_id = serializers.IntegerField(read_only=True)
    task_title = serializers.CharField(source="task.title", read_only=True)

    class Meta(TaskActivitySerializer.Meta):
        fields = TaskActivitySerializer.Meta.fields + ("task_id", "task_title")


class TaskDetailSerializer(TaskCardSerializer):
    comments = TaskCommentSerializer(many=True, read_only=True)
    recent_activity = serializers.SerializerMethodField()
    time_entries = serializers.SerializerMethodField()
    contributors = serializers.SerializerMethodField()
    total_logged_minutes = serializers.IntegerField(source="actual_minutes", read_only=True)

    class Meta(TaskCardSerializer.Meta):
        fields = TaskCardSerializer.Meta.fields + (
            "comments", "recent_activity", "time_entries", "contributors", "total_logged_minutes",
        )

    def get_recent_activity(self, obj):
        activities = obj.activities.select_related("actor")[:20]
        return TaskActivitySerializer(activities, many=True).data

    def get_time_entries(self, obj):
        entries = obj.time_entries.select_related("user")[:50]
        return TimeEntrySerializer(entries, many=True).data

    def get_contributors(self, obj):
        contributor_ids = set(obj.time_entries.values_list("user_id", flat=True))
        commenter_ids = set(obj.comments.values_list("author_id", flat=True))
        if obj.current_assignee_id:
            contributor_ids.add(obj.current_assignee_id)
        contributor_ids.update(commenter_ids)
        contributors = User.objects.filter(id__in=contributor_ids).order_by("first_name", "last_name")
        return UserSummarySerializer(contributors, many=True).data


class ProjectDetailSerializer(ProjectSummarySerializer):
    tasks = serializers.SerializerMethodField()
    recent_comments = serializers.SerializerMethodField()
    recent_activity = serializers.SerializerMethodField()
    contributors = serializers.SerializerMethodField()

    class Meta(ProjectSummarySerializer.Meta):
        fields = ProjectSummarySerializer.Meta.fields + ("tasks", "recent_comments", "recent_activity", "contributors")

    def get_tasks(self, obj):
        tasks = obj.tasks.filter(archived=False).select_related("project__manager", "current_assignee").prefetch_related("labels", "checklist_items__created_by", "checklist_items__completed_by", "attachments__uploaded_by")
        return TaskCardSerializer(tasks, many=True, context=self.context).data

    def get_recent_comments(self, obj):
        comments = TaskComment.objects.filter(task__project=obj).select_related("author", "task").order_by("-created_at")[:20]
        return ProjectTaskCommentSerializer(comments, many=True).data

    def get_recent_activity(self, obj):
        activities = TaskActivity.objects.filter(task__project=obj).select_related("actor", "task").order_by("-created_at")[:30]
        return ProjectTaskActivitySerializer(activities, many=True).data

    def get_contributors(self, obj):
        contributor_ids = set(TaskComment.objects.filter(task__project=obj).values_list("author_id", flat=True))
        contributor_ids.update(TimeEntry.objects.filter(task__project=obj).values_list("user_id", flat=True))
        contributor_ids.update(Task.objects.filter(project=obj, current_assignee_id__isnull=False).values_list("current_assignee_id", flat=True))
        contributors = User.objects.filter(id__in=contributor_ids).order_by("first_name", "last_name")
        return UserSummarySerializer(contributors, many=True).data


class NotificationItemSerializer(serializers.ModelSerializer):
    task = TaskCardSerializer(read_only=True)
    project = ProjectSummarySerializer(read_only=True)
    is_read = serializers.BooleanField(read_only=True)

    class Meta:
        model = Notification
        fields = ("id", "type", "task", "project", "payload", "read_at", "is_read", "created_at")


class DashboardSummarySerializer(serializers.Serializer):
    active_projects = serializers.IntegerField()
    todo_tasks = serializers.IntegerField()
    in_progress_tasks = serializers.IntegerField()
    in_review_tasks = serializers.IntegerField()
    blocked_tasks = serializers.IntegerField()
    overdue_tasks = serializers.IntegerField()
    completed_tasks = serializers.IntegerField()
    week_logged_minutes = serializers.IntegerField()
    recent_reassignments = serializers.IntegerField()


class WorkloadRowSerializer(serializers.Serializer):
    user = UserSummarySerializer()
    open_tasks = serializers.IntegerField()
    overdue_tasks = serializers.IntegerField()
    estimated_minutes = serializers.IntegerField()
    actual_minutes = serializers.IntegerField()


class TimeReportRowSerializer(serializers.Serializer):
    project = ProjectSummarySerializer()
    minutes = serializers.IntegerField()


class ProjectWriteSerializer(serializers.ModelSerializer):
    manager_id = serializers.PrimaryKeyRelatedField(source="manager", queryset=User.objects.filter(is_active=True))

    class Meta:
        model = Project
        fields = ("name", "description", "manager_id", "start_date", "target_end_date", "priority", "status", "archived")

    def validate(self, attrs):
        start_date = attrs.get("start_date", getattr(self.instance, "start_date", None))
        target_end_date = attrs.get("target_end_date", getattr(self.instance, "target_end_date", None))
        if start_date and target_end_date and target_end_date < start_date:
            raise serializers.ValidationError({"target_end_date": "Target end date cannot be before start date."})
        return attrs


class TaskWriteSerializer(serializers.ModelSerializer):
    project_id = serializers.PrimaryKeyRelatedField(source="project", queryset=Project.objects.all())
    current_assignee_id = serializers.PrimaryKeyRelatedField(source="current_assignee", queryset=User.objects.filter(is_active=True), required=False, allow_null=True)
    label_ids = serializers.PrimaryKeyRelatedField(source="labels", queryset=TaskLabel.objects.all(), many=True, required=False)

    class Meta:
        model = Task
        fields = (
            "project_id", "title", "description", "current_assignee_id", "status", "priority",
            "due_date", "estimated_minutes", "blocked_reason", "sort_order", "label_ids", "archived",
        )

    def validate(self, attrs):
        due_date = attrs.get("due_date", getattr(self.instance, "due_date", None))
        project = attrs.get("project", getattr(self.instance, "project", None))
        if due_date and project and project.start_date and due_date < project.start_date:
            raise serializers.ValidationError({"due_date": "Due date cannot be before project start date."})
        return attrs


class TaskStatusUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=TaskStatus.choices)
    blocked_reason = serializers.CharField(required=False, allow_blank=True)
    sort_order = serializers.IntegerField(required=False, min_value=0)


class TaskCompletionSerializer(serializers.Serializer):
    is_completed = serializers.BooleanField(default=True)


class TaskArchiveSerializer(serializers.Serializer):
    archived = serializers.BooleanField(default=True)


class TaskReassignSerializer(serializers.Serializer):
    assignee_id = serializers.PrimaryKeyRelatedField(queryset=User.objects.filter(is_active=True), source="assignee")
    reason = serializers.CharField()


class CommentCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskComment
        fields = ("body",)


class TimeEntryCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = TimeEntry
        fields = ("minutes", "work_date", "note")

    def validate_minutes(self, value):
        if value <= 0:
            raise serializers.ValidationError("Minutes must be greater than zero.")
        return value


class ChecklistItemCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskChecklistItem
        fields = ("title", "done", "sort_order")


class ChecklistItemUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskChecklistItem
        fields = ("title", "done", "sort_order")


class ChatAttachmentSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = ChatMessageAttachment
        fields = ("id", "file", "file_url", "name", "mime_type", "size", "created_at", "updated_at")
        read_only_fields = ("file",)

    def get_file_url(self, obj):
        if not obj.file:
            return None
        request = self.context.get("request")
        url = obj.file.url
        return request.build_absolute_uri(url) if request else url


class ChatMessageReplySerializer(serializers.ModelSerializer):
    sender = UserSummarySerializer(read_only=True)
    is_deleted = serializers.SerializerMethodField()
    body = serializers.SerializerMethodField()

    class Meta:
        model = ChatMessage
        fields = ("id", "sender", "body", "deleted_at", "is_deleted", "created_at")

    def get_is_deleted(self, obj):
        return bool(obj.deleted_at)

    def get_body(self, obj):
        return "Message deleted" if obj.deleted_at else obj.body


class ChatMessageSerializer(serializers.ModelSerializer):
    sender = UserSummarySerializer(read_only=True)
    attachments = ChatAttachmentSerializer(many=True, read_only=True)
    read_by = UserSummarySerializer(many=True, read_only=True)
    mentions = UserSummarySerializer(many=True, read_only=True)
    reply_to = ChatMessageReplySerializer(read_only=True)
    is_read = serializers.BooleanField(read_only=True)
    is_deleted = serializers.SerializerMethodField()
    body = serializers.SerializerMethodField()

    class Meta:
        model = ChatMessage
        fields = (
            "id",
            "thread",
            "sender",
            "body",
            "attachments",
            "read_by",
            "mentions",
            "reply_to",
            "is_read",
            "deleted_at",
            "is_deleted",
            "created_at",
            "updated_at",
        )

    def get_is_deleted(self, obj):
        return bool(obj.deleted_at)

    def get_body(self, obj):
        return "Message deleted" if obj.deleted_at else obj.body


class ChatThreadSerializer(serializers.ModelSerializer):
    participants = UserSummarySerializer(many=True, read_only=True)
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()

    class Meta:
        model = ChatThread
        fields = ("id", "kind", "title", "participants", "last_message", "unread_count", "created_at", "updated_at")

    def get_last_message(self, obj):
        message = obj.messages.select_related("sender", "reply_to", "reply_to__sender").prefetch_related("attachments", "read_by", "mentions").last()
        return ChatMessageSerializer(message, context=self.context).data if message else None

    def get_unread_count(self, obj):
        user = self.context.get("request").user if self.context.get("request") else None
        if not user or not user.is_authenticated:
            return 0
        return obj.messages.exclude(sender=user).exclude(read_by=user).count()


class ChatThreadCreateSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=ChatThreadKind.choices, default=ChatThreadKind.PRIVATE)
    title = serializers.CharField(required=False, allow_blank=True)
    recipient_id = serializers.PrimaryKeyRelatedField(queryset=User.objects.filter(is_active=True), source="recipient", required=False)


class ChatMessageCreateSerializer(serializers.Serializer):
    body = serializers.CharField(required=False, allow_blank=True)
    reply_to_id = serializers.PrimaryKeyRelatedField(queryset=ChatMessage.objects.all(), source="reply_to", required=False, allow_null=True)
