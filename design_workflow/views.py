from datetime import timedelta
import re

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model
from django.db.models import Q, Sum
from django.http import Http404
from django.utils import timezone
from rest_framework import parsers, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    ChatMessage,
    ChatMessageAttachment,
    ChatThread,
    ChatThreadKind,
    Notification,
    NotificationType,
    Project,
    ProjectStatus,
    Task,
    TaskActivityType,
    TaskAttachment,
    TaskChecklistItem,
    TaskLabel,
    TaskStatus,
    TimeEntry,
)
from .permissions import IsManager, IsManagerOrReadOnly, can_mutate_task
from .serializers import (
    ChatMessageCreateSerializer,
    ChatMessageSerializer,
    ChatThreadCreateSerializer,
    ChatThreadSerializer,
    ChecklistItemCreateSerializer,
    ChecklistItemUpdateSerializer,
    CommentCreateSerializer,
    DashboardSummarySerializer,
    NotificationItemSerializer,
    ProjectDetailSerializer,
    ProjectSummarySerializer,
    ProjectWriteSerializer,
    TaskArchiveSerializer,
    TaskCompletionSerializer,
    TaskAttachmentSerializer,
    TaskCardSerializer,
    TaskChecklistItemSerializer,
    TaskDetailSerializer,
    TaskLabelSerializer,
    TaskReassignSerializer,
    TaskStatusUpdateSerializer,
    TaskWriteSerializer,
    TimeEntryCreateSerializer,
    TimeEntrySerializer,
    TimeReportRowSerializer,
    UserSummarySerializer,
    WorkloadRowSerializer,
    TaskCommentSerializer,
)
from .services import (
    broadcast_task_event,
    create_notification,
    log_automatic_time_entry,
    mark_notification_read,
    record_task_activity,
    related_task_user_ids,
)

User = get_user_model()


def parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    if value.lower() in {"true", "1", "yes"}:
        return True
    if value.lower() in {"false", "0", "no"}:
        return False
    return None


def get_task_queryset():
    return (
        Task.objects.select_related("project", "project__manager", "current_assignee")
        .prefetch_related(
            "labels",
            "checklist_items__created_by",
            "checklist_items__completed_by",
            "attachments__uploaded_by",
            "comments__author",
            "time_entries__user",
            "activities__actor",
        )
        .all()
    )


def apply_task_filters(queryset, params, user):
    archived = parse_bool(params.get("archived"))
    queryset = queryset.filter(archived=False) if archived is None else queryset.filter(archived=archived)
    if params.get("project"):
        queryset = queryset.filter(project_id=params.get("project"))
    if params.get("assignee"):
        queryset = queryset.filter(current_assignee_id=params.get("assignee"))
    if params.get("status"):
        queryset = queryset.filter(status=params.get("status"))
    if params.get("priority"):
        queryset = queryset.filter(priority=params.get("priority"))
    if parse_bool(params.get("overdue")) is True:
        queryset = queryset.filter(due_date__lt=timezone.localdate()).exclude(status=TaskStatus.DONE)
    if parse_bool(params.get("blocked")) is True:
        queryset = queryset.filter(status=TaskStatus.BLOCKED)
    if parse_bool(params.get("mine")) is True:
        queryset = queryset.filter(current_assignee=user)
    if params.get("start_date"):
        queryset = queryset.filter(project__start_date__gte=params.get("start_date"))
    if params.get("end_date"):
        queryset = queryset.filter(project__target_end_date__lte=params.get("end_date"))
    return queryset


def get_task_or_404(pk: int) -> Task:
    try:
        return get_task_queryset().get(pk=pk)
    except Task.DoesNotExist as exc:
        raise Http404 from exc


def get_thread_or_404(request, pk: int) -> ChatThread:
    try:
        thread = (
            ChatThread.objects.prefetch_related(
                "participants",
                "messages__sender",
                "messages__attachments",
                "messages__read_by",
                "messages__mentions",
                "messages__reply_to__sender",
            )
            .get(pk=pk)
        )
    except ChatThread.DoesNotExist as exc:
        raise Http404 from exc
    if thread.kind == ChatThreadKind.PRIVATE and not thread.participants.filter(id=request.user.id).exists():
        raise Http404
    return thread


def broadcast_chat_event(thread: ChatThread, payload: dict):
    channel_layer = get_channel_layer()
    if not channel_layer:
        return
    if thread.kind == ChatThreadKind.PUBLIC:
        async_to_sync(channel_layer.group_send)("chat_public", payload)
        return
    for user_id in thread.participants.values_list("id", flat=True):
        async_to_sync(channel_layer.group_send)(f"user_{user_id}", payload)


def broadcast_chat_message(message, request=None):
    payload = {
        "type": "chat.message",
        "message": ChatMessageSerializer(message, context={"request": request}).data,
    }
    broadcast_chat_event(message.thread, payload)


def extract_chat_mentions(body: str, thread: ChatThread, sender) -> list:
    if not body.strip():
        return []
    tokens = {match.lower() for match in re.findall(r"@([\w.\-]+)", body)}
    if not tokens:
        return []
    participants = thread.participants.exclude(id=sender.id) if thread.kind == ChatThreadKind.PRIVATE else User.objects.filter(is_active=True).exclude(id=sender.id)
    matched = []
    for user in participants:
        candidates = {
            user.first_name.lower(),
            user.last_name.lower(),
            user.email.split("@", 1)[0].lower(),
            f"{user.first_name}.{user.last_name}".strip(".").lower(),
            f"{user.first_name}_{user.last_name}".strip("_").lower(),
        }
        if tokens & {candidate for candidate in candidates if candidate}:
            matched.append(user)
    return matched


class DashboardSummaryView(APIView):
    permission_classes = (IsManager,)

    def get(self, request):
        today = timezone.localdate()
        week_start = today - timedelta(days=today.weekday())
        tasks = Task.objects.select_related("project").filter(archived=False)
        payload = {
            "active_projects": Project.objects.filter(
                archived=False,
                status__in=[ProjectStatus.PLANNED, ProjectStatus.ACTIVE, ProjectStatus.ON_HOLD],
            ).count(),
            "todo_tasks": tasks.filter(status=TaskStatus.TODO).count(),
            "in_progress_tasks": tasks.filter(status=TaskStatus.IN_PROGRESS).count(),
            "in_review_tasks": tasks.filter(status=TaskStatus.IN_REVIEW).count(),
            "blocked_tasks": tasks.filter(status=TaskStatus.BLOCKED).count(),
            "overdue_tasks": tasks.filter(due_date__lt=today).exclude(status=TaskStatus.DONE).count(),
            "completed_tasks": tasks.filter(status=TaskStatus.DONE).count(),
            "week_logged_minutes": int(
                TimeEntry.objects.filter(work_date__gte=week_start, task__archived=False).aggregate(total=Sum("minutes"))["total"] or 0
            ),
            "recent_reassignments": tasks.filter(
                activities__action_type=TaskActivityType.REASSIGNED,
                activities__created_at__date__gte=week_start,
            ).distinct().count(),
        }
        return Response(DashboardSummarySerializer(payload).data, status=status.HTTP_200_OK)


class ProjectListCreateView(APIView):
    permission_classes = (IsManagerOrReadOnly,)

    def get(self, request):
        queryset = Project.objects.select_related("manager").all()
        archived = parse_bool(request.query_params.get("archived"))
        if archived is not None:
            queryset = queryset.filter(archived=archived)
        if request.query_params.get("status"):
            queryset = queryset.filter(status=request.query_params.get("status"))
        return Response(ProjectSummarySerializer(queryset, many=True).data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = ProjectWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        project = serializer.save()
        return Response(ProjectSummarySerializer(project).data, status=status.HTTP_201_CREATED)


class ProjectDetailView(APIView):
    permission_classes = (IsManagerOrReadOnly,)

    @staticmethod
    def get_object(pk: int) -> Project:
        try:
            return Project.objects.select_related("manager").get(pk=pk)
        except Project.DoesNotExist as exc:
            raise Http404 from exc

    def get(self, request, pk: int):
        project = self.get_object(pk)
        return Response(ProjectDetailSerializer(project, context={"request": request}).data, status=status.HTTP_200_OK)

    def patch(self, request, pk: int):
        project = self.get_object(pk)
        was_archived = project.archived
        serializer = ProjectWriteSerializer(project, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        project = serializer.save()
        if project.archived and not was_archived:
            project.archived_at = timezone.now()
            project.status = ProjectStatus.ARCHIVED
            project.save(update_fields=["archived_at", "status", "updated_at"])
        return Response(ProjectSummarySerializer(project).data, status=status.HTTP_200_OK)


class TaskListCreateView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request):
        queryset = apply_task_filters(get_task_queryset(), request.query_params, request.user)
        return Response(TaskCardSerializer(queryset, many=True, context={"request": request}).data, status=status.HTTP_200_OK)

    def post(self, request):
        if request.user.role != "manager":
            return Response(status=status.HTTP_403_FORBIDDEN)
        serializer = TaskWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        task = serializer.save(created_by=request.user, updated_by=request.user)
        record_task_activity(task, request.user, TaskActivityType.CREATED, {"status": task.status, "assignee_id": task.current_assignee_id})
        if task.current_assignee_id:
            create_notification(
                recipient=task.current_assignee,
                notification_type=NotificationType.TASK_ASSIGNED,
                task=task,
                project=task.project,
                payload={"title": task.title},
            )
        broadcast_task_event(task, "created")
        return Response(TaskDetailSerializer(get_task_queryset().get(pk=task.pk), context={"request": request}).data, status=status.HTTP_201_CREATED)


class TaskDetailView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request, pk: int):
        return Response(TaskDetailSerializer(get_task_or_404(pk), context={"request": request}).data, status=status.HTTP_200_OK)

    def patch(self, request, pk: int):
        if request.user.role != "manager":
            return Response(status=status.HTTP_403_FORBIDDEN)
        task = get_task_or_404(pk)
        previous = {
            "status": task.status,
            "priority": task.priority,
            "due_date": task.due_date.isoformat() if task.due_date else None,
            "assignee_id": task.current_assignee_id,
            "label_ids": list(task.labels.values_list("id", flat=True)),
        }
        serializer = TaskWriteSerializer(task, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        task = serializer.save(updated_by=request.user)
        if previous["status"] != task.status:
            record_task_activity(task, request.user, TaskActivityType.STATUS_CHANGED, previous | {"next": task.status})
            if previous["status"] != TaskStatus.IN_PROGRESS and task.status == TaskStatus.IN_PROGRESS:
                log_automatic_time_entry(
                    task,
                    user=request.user,
                    minutes=15,
                    note="Automatic workflow entry: task moved to In progress.",
                    event="status_in_progress",
                )
        if previous["priority"] != task.priority:
            record_task_activity(task, request.user, TaskActivityType.PRIORITY_CHANGED, previous | {"next": task.priority})
        if previous["due_date"] != (task.due_date.isoformat() if task.due_date else None):
            record_task_activity(task, request.user, TaskActivityType.DUE_DATE_CHANGED, previous | {"next": task.due_date.isoformat() if task.due_date else None})
        if previous["assignee_id"] != task.current_assignee_id:
            record_task_activity(task, request.user, TaskActivityType.REASSIGNED, previous | {"next": task.current_assignee_id})
            if task.current_assignee_id:
                create_notification(
                    recipient=task.current_assignee,
                    notification_type=NotificationType.TASK_REASSIGNED,
                    task=task,
                    project=task.project,
                    payload={"reason": "Assignment updated from task editor."},
                )
            log_automatic_time_entry(
                task,
                user=request.user,
                minutes=5,
                note="Automatic workflow entry: assignee updated.",
                event="assignee_changed",
            )
        if "labels" in serializer.validated_data:
            record_task_activity(task, request.user, TaskActivityType.LABEL_UPDATED, {"previous": previous["label_ids"], "next": list(task.labels.values_list("id", flat=True))})
        broadcast_task_event(task, "updated")
        return Response(TaskDetailSerializer(get_task_queryset().get(pk=task.pk), context={"request": request}).data, status=status.HTTP_200_OK)


class TaskStatusView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def patch(self, request, pk: int):
        task = get_task_or_404(pk)
        if not can_mutate_task(request.user, task):
            return Response(status=status.HTTP_403_FORBIDDEN)
        serializer = TaskStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        previous_status = task.status
        task.status = serializer.validated_data["status"]
        if "blocked_reason" in serializer.validated_data:
            task.blocked_reason = serializer.validated_data["blocked_reason"]
        if "sort_order" in serializer.validated_data:
            task.sort_order = serializer.validated_data["sort_order"]
        task.updated_by = request.user
        task.save(update_fields=["status", "blocked_reason", "sort_order", "updated_by", "updated_at"])
        record_task_activity(task, request.user, TaskActivityType.STATUS_CHANGED, {"previous_status": previous_status, "status": task.status, "blocked_reason": task.blocked_reason})
        if previous_status != TaskStatus.IN_PROGRESS and task.status == TaskStatus.IN_PROGRESS:
            log_automatic_time_entry(
                task,
                user=request.user,
                minutes=15,
                note="Automatic workflow entry: task moved to In progress.",
                event="status_in_progress",
            )
        if task.current_assignee_id and task.current_assignee_id != request.user.id:
            create_notification(recipient=task.current_assignee, notification_type=NotificationType.TASK_STATUS, task=task, project=task.project, payload={"status": task.status})
        broadcast_task_event(task, "status_changed", recipients=related_task_user_ids(task))
        return Response(TaskDetailSerializer(get_task_queryset().get(pk=task.pk), context={"request": request}).data, status=status.HTTP_200_OK)


class TaskCompletionView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request, pk: int):
        task = get_task_or_404(pk)
        if not can_mutate_task(request.user, task):
            return Response(status=status.HTTP_403_FORBIDDEN)
        serializer = TaskCompletionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        task.is_completed = serializer.validated_data["is_completed"]
        task.completed_at = timezone.now() if task.is_completed else None
        task.updated_by = request.user
        task.save(update_fields=["is_completed", "completed_at", "updated_by", "updated_at"])
        record_task_activity(task, request.user, TaskActivityType.UPDATED, {"is_completed": task.is_completed})
        broadcast_task_event(task, "completion_changed", recipients=related_task_user_ids(task))
        return Response(TaskDetailSerializer(get_task_queryset().get(pk=task.pk), context={"request": request}).data, status=status.HTTP_200_OK)


class TaskArchiveView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request, pk: int):
        task = get_task_or_404(pk)
        if request.user.role != "manager" and task.current_assignee_id != request.user.id:
            return Response(status=status.HTTP_403_FORBIDDEN)
        serializer = TaskArchiveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        task.archived = serializer.validated_data["archived"]
        task.archived_at = timezone.now() if task.archived else None
        task.updated_by = request.user
        task.save(update_fields=["archived", "archived_at", "updated_by", "updated_at"])
        record_task_activity(task, request.user, TaskActivityType.TASK_ARCHIVED, {"archived": task.archived})
        broadcast_task_event(task, "archived" if task.archived else "restored", recipients=related_task_user_ids(task))
        return Response(TaskDetailSerializer(get_task_queryset().get(pk=task.pk), context={"request": request}).data, status=status.HTTP_200_OK)


class TaskCoverImageView(APIView):
    permission_classes = (permissions.IsAuthenticated,)
    parser_classes = (parsers.MultiPartParser, parsers.FormParser)

    def post(self, request, pk: int):
        task = get_task_or_404(pk)
        if request.user.role != "manager" and task.current_assignee_id != request.user.id:
            return Response(status=status.HTTP_403_FORBIDDEN)
        cover_image = request.FILES.get("cover_image")
        if not cover_image:
            return Response({"cover_image": ["Cover image file is required."]}, status=status.HTTP_400_BAD_REQUEST)
        task.cover_image = cover_image
        task.updated_by = request.user
        task.save(update_fields=["cover_image", "updated_by", "updated_at"])
        record_task_activity(task, request.user, TaskActivityType.ATTACHMENT_ADDED, {"cover_image": True, "name": cover_image.name})
        broadcast_task_event(task, "cover_updated", recipients=related_task_user_ids(task))
        return Response(TaskDetailSerializer(get_task_queryset().get(pk=task.pk), context={"request": request}).data, status=status.HTTP_200_OK)

    def delete(self, request, pk: int):
        task = get_task_or_404(pk)
        if request.user.role != "manager" and task.current_assignee_id != request.user.id:
            return Response(status=status.HTTP_403_FORBIDDEN)
        if task.cover_image:
            task.cover_image.delete(save=False)
        task.cover_image = None
        task.updated_by = request.user
        task.save(update_fields=["cover_image", "updated_by", "updated_at"])
        record_task_activity(task, request.user, TaskActivityType.ATTACHMENT_ADDED, {"cover_image": True, "removed": True})
        broadcast_task_event(task, "cover_deleted", recipients=related_task_user_ids(task))
        return Response(TaskDetailSerializer(get_task_queryset().get(pk=task.pk), context={"request": request}).data, status=status.HTTP_200_OK)


class TaskLabelListCreateView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request):
        return Response(TaskLabelSerializer(TaskLabel.objects.all(), many=True).data, status=status.HTTP_200_OK)

    def post(self, request):
        if request.user.role != "manager":
            return Response(status=status.HTTP_403_FORBIDDEN)
        serializer = TaskLabelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        label = serializer.save()
        return Response(TaskLabelSerializer(label).data, status=status.HTTP_201_CREATED)


class TaskChecklistListView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request, pk: int):
        task = get_task_or_404(pk)
        return Response(TaskChecklistItemSerializer(task.checklist_items.all(), many=True).data, status=status.HTTP_200_OK)

    def post(self, request, pk: int):
        task = get_task_or_404(pk)
        if not can_mutate_task(request.user, task):
            return Response(status=status.HTTP_403_FORBIDDEN)
        serializer = ChecklistItemCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = serializer.save(task=task, created_by=request.user)
        record_task_activity(task, request.user, TaskActivityType.CHECKLIST_UPDATED, {"item_id": item.id, "action": "created"})
        broadcast_task_event(task, "checklist_updated", recipients=related_task_user_ids(task))
        return Response(TaskChecklistItemSerializer(item).data, status=status.HTTP_201_CREATED)


class TaskChecklistDetailView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get_object(self, pk: int, item_id: int):
        task = get_task_or_404(pk)
        try:
            return task, task.checklist_items.get(pk=item_id)
        except TaskChecklistItem.DoesNotExist as exc:
            raise Http404 from exc

    def patch(self, request, pk: int, item_id: int):
        task, item = self.get_object(pk, item_id)
        if not can_mutate_task(request.user, task):
            return Response(status=status.HTTP_403_FORBIDDEN)
        was_done = item.done
        serializer = ChecklistItemUpdateSerializer(item, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        item = serializer.save()
        if item.done and not was_done:
            item.completed_by = request.user
            item.completed_at = timezone.now()
            item.save(update_fields=["completed_by", "completed_at", "updated_at"])
        if not item.done and was_done:
            item.completed_by = None
            item.completed_at = None
            item.save(update_fields=["completed_by", "completed_at", "updated_at"])
        record_task_activity(task, request.user, TaskActivityType.CHECKLIST_UPDATED, {"item_id": item.id, "action": "updated"})
        broadcast_task_event(task, "checklist_updated", recipients=related_task_user_ids(task))
        return Response(TaskChecklistItemSerializer(item).data, status=status.HTTP_200_OK)

    def delete(self, request, pk: int, item_id: int):
        task, item = self.get_object(pk, item_id)
        if not can_mutate_task(request.user, task):
            return Response(status=status.HTTP_403_FORBIDDEN)
        item_id_value = item.id
        item.delete()
        record_task_activity(task, request.user, TaskActivityType.CHECKLIST_UPDATED, {"item_id": item_id_value, "action": "deleted"})
        broadcast_task_event(task, "checklist_updated", recipients=related_task_user_ids(task))
        return Response(status=status.HTTP_204_NO_CONTENT)


class TaskAttachmentsView(APIView):
    permission_classes = (permissions.IsAuthenticated,)
    parser_classes = (parsers.MultiPartParser, parsers.FormParser)

    def get(self, request, pk: int):
        task = get_task_or_404(pk)
        return Response(TaskAttachmentSerializer(task.attachments.all(), many=True, context={"request": request}).data, status=status.HTTP_200_OK)

    def post(self, request, pk: int):
        task = get_task_or_404(pk)
        if not can_mutate_task(request.user, task):
            return Response(status=status.HTTP_403_FORBIDDEN)
        upload = request.FILES.get("file")
        if not upload:
            return Response({"file": ["This field is required."]}, status=status.HTTP_400_BAD_REQUEST)
        attachment = TaskAttachment.objects.create(
            task=task,
            uploaded_by=request.user,
            file=upload,
            name=request.data.get("name") or upload.name,
            mime_type=getattr(upload, "content_type", "") or "",
            size=getattr(upload, "size", 0) or 0,
        )
        record_task_activity(task, request.user, TaskActivityType.ATTACHMENT_ADDED, {"attachment_id": attachment.id, "name": attachment.name})
        broadcast_task_event(task, "attachment_added", recipients=related_task_user_ids(task))
        return Response(TaskAttachmentSerializer(attachment, context={"request": request}).data, status=status.HTTP_201_CREATED)


class TaskAttachmentDetailView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def delete(self, request, pk: int, attachment_id: int):
        task = get_task_or_404(pk)
        if not can_mutate_task(request.user, task):
            return Response(status=status.HTTP_403_FORBIDDEN)
        try:
            attachment = task.attachments.get(pk=attachment_id)
        except TaskAttachment.DoesNotExist as exc:
            raise Http404 from exc
        attachment.delete()
        broadcast_task_event(task, "attachment_deleted", recipients=related_task_user_ids(task))
        return Response(status=status.HTTP_204_NO_CONTENT)


class TaskReassignView(APIView):
    permission_classes = (IsManager,)

    def post(self, request, pk: int):
        task = get_task_or_404(pk)
        serializer = TaskReassignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        assignee = serializer.validated_data["assignee"]
        reason = serializer.validated_data["reason"]
        previous_assignee_id = task.current_assignee_id
        task.current_assignee = assignee
        task.updated_by = request.user
        task.save(update_fields=["current_assignee", "updated_by", "updated_at"])
        record_task_activity(task, request.user, TaskActivityType.REASSIGNED, {"previous_assignee_id": previous_assignee_id, "assignee_id": assignee.id, "reason": reason})
        create_notification(recipient=assignee, notification_type=NotificationType.TASK_REASSIGNED, task=task, project=task.project, payload={"reason": reason})
        log_automatic_time_entry(
            task,
            user=request.user,
            minutes=5,
            note="Automatic workflow entry: task reassigned.",
            event="task_reassigned",
        )
        broadcast_task_event(task, "reassigned")
        return Response(TaskDetailSerializer(get_task_queryset().get(pk=task.pk), context={"request": request}).data, status=status.HTTP_200_OK)


class TaskCommentsView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request, pk: int):
        task = get_task_or_404(pk)
        return Response(TaskCommentSerializer(task.comments.select_related("author"), many=True).data, status=status.HTTP_200_OK)

    def post(self, request, pk: int):
        task = get_task_or_404(pk)
        if not can_mutate_task(request.user, task):
            return Response(status=status.HTTP_403_FORBIDDEN)
        serializer = CommentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        comment = serializer.save(task=task, author=request.user)
        record_task_activity(task, request.user, TaskActivityType.COMMENT_ADDED, {"comment_id": comment.id})
        recipient_ids = [user_id for user_id in related_task_user_ids(task) if user_id != request.user.id]
        for recipient in User.objects.filter(id__in=recipient_ids):
            create_notification(recipient=recipient, notification_type=NotificationType.TASK_COMMENT, task=task, project=task.project, payload={"comment_id": comment.id})
        broadcast_task_event(task, "comment_added", recipients=related_task_user_ids(task))
        return Response(TaskCommentSerializer(comment).data, status=status.HTTP_201_CREATED)


class TaskTimeEntriesView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request, pk: int):
        task = get_task_or_404(pk)
        return Response(TimeEntrySerializer(task.time_entries.select_related("user"), many=True).data, status=status.HTTP_200_OK)

    def post(self, request, pk: int):
        task = get_task_or_404(pk)
        if not can_mutate_task(request.user, task):
            return Response(status=status.HTTP_403_FORBIDDEN)
        serializer = TimeEntryCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        time_entry = serializer.save(task=task, user=request.user)
        record_task_activity(task, request.user, TaskActivityType.TIME_LOGGED, {"time_entry_id": time_entry.id, "minutes": time_entry.minutes})
        broadcast_task_event(task, "time_logged", recipients=related_task_user_ids(task))
        task.refresh_from_db()
        return Response(TimeEntrySerializer(time_entry).data, status=status.HTTP_201_CREATED)


class WorkloadView(APIView):
    permission_classes = (IsManager,)

    def get(self, request):
        users = User.objects.filter(is_active=True).order_by("first_name", "last_name")
        rows = []
        today = timezone.localdate()
        for user in users:
            assigned_tasks = Task.objects.filter(current_assignee=user, archived=False).exclude(status=TaskStatus.DONE)
            rows.append({
                "user": UserSummarySerializer(user).data,
                "open_tasks": assigned_tasks.count(),
                "overdue_tasks": assigned_tasks.filter(due_date__lt=today).count(),
                "estimated_minutes": int(assigned_tasks.aggregate(total=Sum("estimated_minutes"))["total"] or 0),
                "actual_minutes": int(TimeEntry.objects.filter(user=user, task__archived=False).aggregate(total=Sum("minutes"))["total"] or 0),
            })
        return Response(WorkloadRowSerializer(rows, many=True).data, status=status.HTTP_200_OK)


class TimeReportView(APIView):
    permission_classes = (IsManager,)

    def get(self, request):
        queryset = Project.objects.select_related("manager").annotate(minutes=Sum("tasks__time_entries__minutes", filter=Q(tasks__archived=False)))
        if request.query_params.get("start_date"):
            queryset = queryset.filter(tasks__time_entries__work_date__gte=request.query_params.get("start_date"))
        if request.query_params.get("end_date"):
            queryset = queryset.filter(tasks__time_entries__work_date__lte=request.query_params.get("end_date"))
        rows = [{"project": project, "minutes": int(project.minutes or 0)} for project in queryset.distinct()]
        return Response(TimeReportRowSerializer(rows, many=True).data, status=status.HTTP_200_OK)


class NotificationListView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request):
        queryset = Notification.objects.filter(recipient=request.user).select_related("task__project__manager", "task__current_assignee", "project__manager")
        if parse_bool(request.query_params.get("unread")) is True:
            queryset = queryset.filter(read_at__isnull=True)
        return Response(NotificationItemSerializer(queryset, many=True, context={"request": request}).data, status=status.HTTP_200_OK)


class NotificationReadView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request, pk: int):
        try:
            notification = Notification.objects.get(pk=pk, recipient=request.user)
        except Notification.DoesNotExist as exc:
            raise Http404 from exc
        mark_notification_read(notification)
        return Response(NotificationItemSerializer(notification, context={"request": request}).data, status=status.HTTP_200_OK)


class ChatThreadListView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request):
        public, _ = ChatThread.objects.get_or_create(kind=ChatThreadKind.PUBLIC, defaults={"title": "Studio public"})
        queryset = (
            ChatThread.objects.prefetch_related(
                "participants",
                "messages__sender",
                "messages__attachments",
                "messages__read_by",
                "messages__mentions",
                "messages__reply_to__sender",
            )
            .filter(Q(kind=ChatThreadKind.PUBLIC) | Q(participants=request.user))
            .distinct()
        )
        if not queryset.filter(pk=public.pk).exists():
            queryset = ChatThread.objects.filter(pk=public.pk) | queryset
        return Response(ChatThreadSerializer(queryset, many=True, context={"request": request}).data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = ChatThreadCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        kind = serializer.validated_data["kind"]
        if kind == ChatThreadKind.PUBLIC:
            thread, _ = ChatThread.objects.get_or_create(
                kind=ChatThreadKind.PUBLIC,
                defaults={"title": serializer.validated_data.get("title") or "Studio public"},
            )
            return Response(ChatThreadSerializer(thread, context={"request": request}).data, status=status.HTTP_200_OK)
        recipient = serializer.validated_data.get("recipient")
        if not recipient:
            return Response({"recipient_id": ["This field is required for private threads."]}, status=status.HTTP_400_BAD_REQUEST)
        existing = ChatThread.objects.filter(kind=ChatThreadKind.PRIVATE, participants=request.user).filter(participants=recipient).first()
        if existing:
            return Response(ChatThreadSerializer(existing, context={"request": request}).data, status=status.HTTP_200_OK)
        thread = ChatThread.objects.create(kind=ChatThreadKind.PRIVATE, title=serializer.validated_data.get("title", ""))
        thread.participants.add(request.user, recipient)
        return Response(ChatThreadSerializer(thread, context={"request": request}).data, status=status.HTTP_201_CREATED)


class ChatMessagesView(APIView):
    permission_classes = (permissions.IsAuthenticated,)
    parser_classes = (parsers.MultiPartParser, parsers.FormParser, parsers.JSONParser)

    def get(self, request, pk: int):
        thread = get_thread_or_404(request, pk)
        queryset = thread.messages.select_related("sender", "reply_to", "reply_to__sender", "deleted_by").prefetch_related("attachments", "read_by", "mentions").order_by("-id")
        before_id = request.query_params.get("before_id")
        if before_id and before_id.isdigit():
            queryset = queryset.filter(id__lt=int(before_id))
        search = (request.query_params.get("q") or "").strip()
        if search:
            queryset = queryset.filter(
                Q(body__icontains=search)
                | Q(sender__first_name__icontains=search)
                | Q(sender__last_name__icontains=search)
                | Q(sender__email__icontains=search)
                | Q(attachments__name__icontains=search)
            ).distinct()
        try:
            limit = max(10, min(int(request.query_params.get("limit", 40)), 60))
        except (TypeError, ValueError):
            limit = 40
        messages = list(queryset[:limit])
        messages.reverse()
        return Response(ChatMessageSerializer(messages, many=True, context={"request": request}).data, status=status.HTTP_200_OK)

    def post(self, request, pk: int):
        thread = get_thread_or_404(request, pk)
        serializer = ChatMessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        body = serializer.validated_data.get("body", "")
        files = request.FILES.getlist("files") or ([request.FILES["file"]] if "file" in request.FILES else [])
        reply_to = serializer.validated_data.get("reply_to")
        if reply_to and reply_to.thread_id != thread.id:
            return Response({"reply_to_id": ["Reply target must belong to this thread."]}, status=status.HTTP_400_BAD_REQUEST)
        if not body.strip() and not files:
            return Response({"body": ["Message or attachment required."]}, status=status.HTTP_400_BAD_REQUEST)
        message = ChatMessage.objects.create(thread=thread, sender=request.user, body=body.strip(), reply_to=reply_to)
        message.read_by.add(request.user)
        for upload in files:
            ChatMessageAttachment.objects.create(
                message=message,
                file=upload,
                name=upload.name,
                mime_type=getattr(upload, "content_type", "") or "",
                size=getattr(upload, "size", 0) or 0,
            )
        mentioned_users = extract_chat_mentions(body, thread, request.user)
        if mentioned_users:
            message.mentions.add(*mentioned_users)
        thread.save(update_fields=["updated_at"])
        recipients = list(thread.participants.exclude(id=request.user.id)) if thread.kind == ChatThreadKind.PRIVATE else []
        for recipient in recipients:
            create_notification(
                recipient=recipient,
                notification_type=NotificationType.CHAT_MESSAGE,
                payload={"thread_id": thread.id, "message_id": message.id, "title": request.user.first_name or request.user.email},
            )
        for recipient in mentioned_users:
            if recipient.id == request.user.id:
                continue
            create_notification(
                recipient=recipient,
                notification_type=NotificationType.CHAT_MESSAGE,
                payload={"thread_id": thread.id, "message_id": message.id, "title": f"@ mention from {request.user.first_name or request.user.email}"},
            )
        message = ChatMessage.objects.select_related("thread", "sender", "reply_to", "reply_to__sender").prefetch_related("attachments", "read_by", "thread__participants", "mentions").get(pk=message.pk)
        broadcast_chat_message(message, request)
        return Response(ChatMessageSerializer(message, context={"request": request}).data, status=status.HTTP_201_CREATED)


class ChatMessageReadView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request, pk: int):
        try:
            message = ChatMessage.objects.select_related("thread", "sender", "reply_to", "reply_to__sender").prefetch_related("attachments", "read_by", "thread__participants", "mentions").get(pk=pk)
        except ChatMessage.DoesNotExist as exc:
            raise Http404 from exc
        if message.thread.kind == ChatThreadKind.PRIVATE and not message.thread.participants.filter(id=request.user.id).exists():
            return Response(status=status.HTTP_403_FORBIDDEN)
        message.read_by.add(request.user)
        payload = {"type": "chat.read", "message_id": message.id, "thread_id": message.thread_id, "user_id": request.user.id}
        broadcast_chat_event(message.thread, payload)
        return Response(ChatMessageSerializer(message, context={"request": request}).data, status=status.HTTP_200_OK)


class ChatMessageDeleteView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request, pk: int):
        try:
            message = ChatMessage.objects.select_related("thread", "sender", "reply_to", "reply_to__sender").prefetch_related("attachments", "read_by", "thread__participants", "mentions").get(pk=pk)
        except ChatMessage.DoesNotExist as exc:
            raise Http404 from exc
        if message.thread.kind == ChatThreadKind.PRIVATE and not message.thread.participants.filter(id=request.user.id).exists():
            return Response(status=status.HTTP_403_FORBIDDEN)
        if message.sender_id != request.user.id and not request.user.is_staff and not request.user.is_superuser:
            return Response(status=status.HTTP_403_FORBIDDEN)
        if not message.deleted_at:
            message.deleted_at = timezone.now()
            message.deleted_by = request.user
            message.body = ""
            message.save(update_fields=["deleted_at", "deleted_by", "body", "updated_at"])
        payload = {"type": "chat.deleted", "message_id": message.id, "thread_id": message.thread_id}
        broadcast_chat_event(message.thread, payload)
        return Response(ChatMessageSerializer(message, context={"request": request}).data, status=status.HTTP_200_OK)
