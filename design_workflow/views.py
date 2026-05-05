from datetime import datetime, time, timedelta
import re

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Max, Q, Sum
from django.http import Http404
from django.utils import timezone
from rest_framework import parsers, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    ArtifactApprovalState,
    AttachmentAnnotation,
    ChatMessage,
    ChatMessageAttachment,
    ChatMessageEdit,
    ChatMessageReaction,
    ChatMessageReminder,
    ChatThread,
    ChatThreadKind,
    Notification,
    NotificationPreference,
    NotificationType,
    Project,
    ProjectStatus,
    SavedView,
    SavedViewVisibility,
    Task,
    TaskActivityType,
    TaskAttachment,
    TaskArtifactVersion,
    TaskChecklist,
    TaskChecklistItem,
    TaskComment,
    TaskLabel,
    TaskReviewState,
    TaskStatus,
    TimeEntry,
)
from .permissions import IsManager, IsManagerOrReadOnly, can_mutate_task
from .serializers import (
    ChatMessageCreateSerializer,
    ChatMessageDecisionSerializer,
    ChatMessageReactionSerializer,
    ChatMessageReminderCreateSerializer,
    ChatMessageSerializer,
    ChatMessageUpdateSerializer,
    ChatThreadCreateSerializer,
    ChatThreadSerializer,
    ChecklistItemCreateSerializer,
    ChecklistItemUpdateSerializer,
    ChecklistCreateSerializer,
    CommentCreateSerializer,
    DashboardSummarySerializer,
    AttachmentAnnotationSerializer,
    NotificationItemSerializer,
    NotificationActionSerializer,
    NotificationPreferenceSerializer,
    NotificationSnoozeSerializer,
    ProjectDetailSerializer,
    ProjectSummarySerializer,
    ProjectWriteSerializer,
    SavedViewSerializer,
    TaskArchiveSerializer,
    TaskArtifactVersionSerializer,
    TaskCompletionSerializer,
    TaskAttachmentSerializer,
    TaskCardSerializer,
    TaskChecklistItemSerializer,
    TaskChecklistSerializer,
    TaskDetailSerializer,
    TaskLabelSerializer,
    TaskReassignSerializer,
    TaskReorderSerializer,
    TaskReviewUpdateSerializer,
    TaskStatusUpdateSerializer,
    TaskWriteSerializer,
    TimeEntryCreateSerializer,
    TimeEntrySerializer,
    TimeReportRowSerializer,
    UserSummarySerializer,
    WorkloadRowSerializer,
    WorkflowAnalyticsSerializer,
    WorkspaceSearchResultSerializer,
    TaskCommentSerializer,
)
from .services import (
    WORK_DAY_MINUTES,
    broadcast_task_event,
    create_notification,
    mark_notification_read,
    record_task_activity,
    related_task_user_ids,
    sync_task_work_session,
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


def average(values: list[float]) -> float:
    if not values:
        return 0
    return round(sum(values) / len(values), 1)


def days_between(start, end) -> float:
    if not start or not end or end < start:
        return 0
    return round((end - start).total_seconds() / 86400, 1)


def first_status_time(task: Task, target_status: str):
    for activity in task.activities.all().order_by("created_at"):
        metadata = activity.metadata or {}
        if metadata.get("status") == target_status or metadata.get("next") == target_status:
            return activity.created_at
    return None


def get_task_queryset():
    return (
        Task.objects.select_related(
            "project",
            "project__manager",
            "current_assignee",
            "review_requested_by",
            "review_approved_by",
            "source_chat_message",
            "source_chat_message__thread",
        )
        .prefetch_related(
            "labels",
            "checklists__created_by",
            "checklists__items__created_by",
            "checklists__items__completed_by",
            "checklist_items__created_by",
            "checklist_items__completed_by",
            "attachments__uploaded_by",
            "attachments__annotations",
            "artifact_versions__uploaded_by",
            "artifact_versions__approved_by",
            "artifact_versions__attachment",
        )
        .all()
    )


def get_task_detail_queryset():
    return (
        Task.objects.select_related(
            "project",
            "project__manager",
            "current_assignee",
            "review_requested_by",
            "review_approved_by",
            "source_chat_message",
            "source_chat_message__thread",
        )
        .prefetch_related(
            "labels",
            "checklists__created_by",
            "checklists__items__created_by",
            "checklists__items__completed_by",
            "checklist_items__created_by",
            "checklist_items__completed_by",
            "attachments__uploaded_by",
            "attachments__annotations",
            "artifact_versions__uploaded_by",
            "artifact_versions__approved_by",
            "artifact_versions__attachment__uploaded_by",
            "artifact_versions__attachment__annotations",
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
    if params.get("review_state"):
        queryset = queryset.filter(review_state=params.get("review_state"))
    if params.get("label"):
        queryset = queryset.filter(labels__id=params.get("label"))
    query = (params.get("q") or "").strip()
    if query:
        queryset = queryset.filter(
            Q(title__icontains=query)
            | Q(description__icontains=query)
            | Q(project__name__icontains=query)
            | Q(current_assignee__first_name__icontains=query)
            | Q(current_assignee__last_name__icontains=query)
            | Q(current_assignee__email__icontains=query)
            | Q(labels__name__icontains=query)
        ).distinct()
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


def apply_task_sorting(queryset, params):
    sort = params.get("sort")
    sort_map = {
        "due_date": ("due_date", "sort_order", "-created_at"),
        "-due_date": ("-due_date", "sort_order", "-created_at"),
        "priority": ("priority", "sort_order", "-created_at"),
        "-priority": ("-priority", "sort_order", "-created_at"),
        "title": ("title",),
        "-title": ("-title",),
        "updated_at": ("updated_at",),
        "-updated_at": ("-updated_at",),
        "sort_order": ("project_id", "sort_order", "-created_at"),
    }
    if sort in sort_map:
        return queryset.order_by(*sort_map[sort])
    return queryset


def is_manager_user(user) -> bool:
    return bool(user and user.is_authenticated and (user.role == "manager" or user.is_staff or getattr(user, "is_superuser", False)))


def get_accessible_project_queryset(user, queryset=None):
    queryset = queryset if queryset is not None else Project.objects.all()
    if is_manager_user(user):
        return queryset
    assigned_project_ids = Task.objects.filter(current_assignee=user).values("project_id")
    return queryset.filter(Q(manager=user) | Q(id__in=assigned_project_ids)).distinct()


def get_accessible_task_queryset(user, queryset=None):
    queryset = queryset if queryset is not None else Task.objects.all()
    if is_manager_user(user):
        return queryset
    return queryset.filter(Q(current_assignee=user) | Q(project__manager=user)).distinct()


def user_can_access_project_context(user, project: Project) -> bool:
    if not user or not user.is_authenticated or not project:
        return False
    if is_manager_user(user) or project.manager_id == user.id:
        return True
    return Task.objects.filter(project=project, current_assignee=user).exists()


def user_can_access_task_context(user, task: Task) -> bool:
    if not user or not user.is_authenticated or not task:
        return False
    return bool(
        is_manager_user(user)
        or task.current_assignee_id == user.id
        or task.project.manager_id == user.id
    )


def get_chat_thread_base_queryset():
    return ChatThread.objects.select_related(
        "project",
        "project__manager",
        "task",
        "task__project",
        "task__project__manager",
        "task__current_assignee",
    ).prefetch_related(
        "participants",
        "messages__sender",
        "messages__attachments",
        "messages__read_by",
        "messages__mentions",
        "messages__reply_to__sender",
    )


def get_chat_thread_queryset_for_user(user):
    queryset = get_chat_thread_base_queryset()
    access_filter = Q(kind=ChatThreadKind.PUBLIC) | Q(participants=user)
    if is_manager_user(user):
        access_filter |= Q(kind__in=(ChatThreadKind.PROJECT, ChatThreadKind.TASK))
    else:
        assigned_project_ids = Task.objects.filter(current_assignee=user).values("project_id")
        access_filter |= (
            Q(kind=ChatThreadKind.PROJECT, project__manager=user)
            | Q(kind=ChatThreadKind.PROJECT, project_id__in=assigned_project_ids)
            | Q(kind=ChatThreadKind.TASK, task__current_assignee=user)
            | Q(kind=ChatThreadKind.TASK, task__project__manager=user)
        )
    return queryset.filter(access_filter).distinct()


def can_access_chat_thread(user, thread: ChatThread) -> bool:
    if thread.kind == ChatThreadKind.PUBLIC:
        return True
    if thread.kind == ChatThreadKind.PRIVATE:
        return thread.participants.filter(id=user.id).exists()
    if thread.kind == ChatThreadKind.PROJECT and thread.project_id:
        return user_can_access_project_context(user, thread.project)
    if thread.kind == ChatThreadKind.TASK and thread.task_id:
        return user_can_access_task_context(user, thread.task)
    return False


def linked_thread_user_ids(thread: ChatThread) -> set[int]:
    ids = set(thread.participants.values_list("id", flat=True))
    if thread.kind == ChatThreadKind.PROJECT and thread.project_id:
        ids.add(thread.project.manager_id)
        ids.update(
            Task.objects.filter(project=thread.project, current_assignee_id__isnull=False)
            .values_list("current_assignee_id", flat=True)
        )
    if thread.kind == ChatThreadKind.TASK and thread.task_id:
        ids.update(related_task_user_ids(thread.task))
    return {user_id for user_id in ids if user_id}


def sync_linked_thread_participants(thread: ChatThread, actor=None) -> ChatThread:
    ids = linked_thread_user_ids(thread)
    if actor and getattr(actor, "id", None):
        ids.add(actor.id)
    if ids:
        thread.participants.add(*ids)
    return thread


def chat_thread_recipients(thread: ChatThread, sender):
    if thread.kind == ChatThreadKind.PUBLIC:
        return User.objects.none()
    sync_linked_thread_participants(thread)
    recipient_ids = linked_thread_user_ids(thread)
    if sender and getattr(sender, "id", None):
        recipient_ids.discard(sender.id)
    return User.objects.filter(id__in=recipient_ids, is_active=True)


def get_task_or_404(pk: int, user=None) -> Task:
    try:
        task = get_task_detail_queryset().get(pk=pk)
    except Task.DoesNotExist as exc:
        raise Http404 from exc
    if user is not None and not user_can_access_task_context(user, task):
        raise Http404
    return task


def get_thread_or_404(request, pk: int) -> ChatThread:
    try:
        thread = get_chat_thread_base_queryset().get(pk=pk)
    except ChatThread.DoesNotExist as exc:
        raise Http404 from exc
    if not can_access_chat_thread(request.user, thread):
        raise Http404
    return thread


def get_chat_message_queryset():
    return ChatMessage.objects.select_related(
        "thread",
        "sender",
        "reply_to",
        "reply_to__sender",
        "deleted_by",
        "edited_by",
        "decision_by",
    ).prefetch_related(
        "attachments",
        "read_by",
        "thread__participants",
        "mentions",
        "reactions__user",
        "reminders__task__project",
        "reminders__created_by",
        "edit_history",
    )


def get_chat_message_or_404(request, pk: int) -> ChatMessage:
    try:
        message = get_chat_message_queryset().get(pk=pk)
    except ChatMessage.DoesNotExist as exc:
        raise Http404 from exc
    if not can_access_chat_thread(request.user, message.thread):
        raise Http404
    return message


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
    participants = thread.participants.exclude(id=sender.id) if thread.kind != ChatThreadKind.PUBLIC else User.objects.filter(is_active=True).exclude(id=sender.id)
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


class SavedViewListCreateView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request):
        queryset = SavedView.objects.select_related("owner").filter(
            Q(owner=request.user) | Q(visibility=SavedViewVisibility.TEAM)
        )
        visibility = request.query_params.get("visibility")
        if visibility:
            queryset = queryset.filter(visibility=visibility)
        return Response(SavedViewSerializer(queryset, many=True, context={"request": request}).data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = SavedViewSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            if serializer.validated_data.get("is_default"):
                SavedView.objects.filter(owner=request.user, is_default=True).update(is_default=False)
            view = serializer.save(owner=request.user)
        return Response(SavedViewSerializer(view, context={"request": request}).data, status=status.HTTP_201_CREATED)


class SavedViewDetailView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    @staticmethod
    def get_object(request, pk: int) -> SavedView:
        try:
            view = SavedView.objects.select_related("owner").get(pk=pk)
        except SavedView.DoesNotExist as exc:
            raise Http404 from exc
        if view.owner_id == request.user.id:
            return view
        if view.visibility == SavedViewVisibility.TEAM and is_manager_user(request.user):
            return view
        raise Http404

    def patch(self, request, pk: int):
        view = self.get_object(request, pk)
        if view.owner_id != request.user.id and not is_manager_user(request.user):
            return Response(status=status.HTTP_403_FORBIDDEN)
        serializer = SavedViewSerializer(view, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            if serializer.validated_data.get("is_default"):
                SavedView.objects.filter(owner=view.owner, is_default=True).exclude(pk=view.pk).update(is_default=False)
            view = serializer.save()
        return Response(SavedViewSerializer(view, context={"request": request}).data, status=status.HTTP_200_OK)

    def delete(self, request, pk: int):
        view = self.get_object(request, pk)
        if view.owner_id != request.user.id and not is_manager_user(request.user):
            return Response(status=status.HTTP_403_FORBIDDEN)
        view.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkspaceSearchView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request):
        query = (request.query_params.get("q") or "").strip()
        if not query:
            return Response([], status=status.HTTP_200_OK)
        requested_types = {
            item.strip()
            for item in (request.query_params.get("types") or "task,project,user,chat,file").split(",")
            if item.strip()
        }
        results = []

        if "task" in requested_types:
            task_queryset = apply_task_filters(
                get_accessible_task_queryset(request.user, get_task_queryset()),
                request.query_params,
                request.user,
            ).filter(
                Q(title__icontains=query)
                | Q(description__icontains=query)
                | Q(project__name__icontains=query)
                | Q(labels__name__icontains=query)
            ).distinct()[:10]
            results.extend(
                {
                    "type": "task",
                    "id": task.id,
                    "title": task.title,
                    "subtitle": task.project.name,
                    "url": f"/dashboard/tasks/{task.id}",
                    "metadata": {
                        "status": task.status,
                        "priority": task.priority,
                        "review_state": task.review_state,
                        "due_date": task.due_date.isoformat() if task.due_date else None,
                    },
                }
                for task in task_queryset
            )

        if "project" in requested_types:
            project_queryset = get_accessible_project_queryset(
                request.user,
                Project.objects.select_related("manager"),
            ).filter(
                Q(name__icontains=query) | Q(description__icontains=query)
            )[:10]
            results.extend(
                {
                    "type": "project",
                    "id": project.id,
                    "title": project.name,
                    "subtitle": project.manager.email,
                    "url": f"/dashboard/projects/{project.id}",
                    "metadata": {"status": project.status, "priority": project.priority},
                }
                for project in project_queryset
            )

        if "user" in requested_types and is_manager_user(request.user):
            user_queryset = User.objects.filter(
                Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
                | Q(email__icontains=query),
                is_active=True,
            )[:10]
            results.extend(
                {
                    "type": "user",
                    "id": user.id,
                    "title": f"{user.first_name} {user.last_name}".strip() or user.email,
                    "subtitle": user.email,
                    "url": f"/dashboard/users/{user.id}",
                    "metadata": {"role": user.role},
                }
                for user in user_queryset
            )

        if "chat" in requested_types:
            accessible_thread_ids = get_chat_thread_queryset_for_user(request.user).values("id")
            chat_queryset = get_chat_message_queryset().filter(
                thread_id__in=accessible_thread_ids,
                deleted_at__isnull=True,
            ).filter(
                Q(body__icontains=query)
                | Q(sender__first_name__icontains=query)
                | Q(sender__last_name__icontains=query)
                | Q(sender__email__icontains=query)
            ).distinct().order_by("-created_at")[:10]
            results.extend(
                {
                    "type": "chat",
                    "id": message.id,
                    "title": message.body[:120],
                    "subtitle": message.thread.title or message.thread.kind,
                    "url": f"/dashboard/chat?thread={message.thread_id}&message={message.id}",
                    "metadata": {"thread_id": message.thread_id, "sender_id": message.sender_id},
                }
                for message in chat_queryset
            )

        if "file" in requested_types:
            accessible_thread_ids = get_chat_thread_queryset_for_user(request.user).values("id")
            accessible_task_ids = get_accessible_task_queryset(
                request.user,
                Task.objects.all(),
            ).values("id")
            task_files = TaskAttachment.objects.select_related("task", "task__project", "uploaded_by").filter(
                Q(task_id__in=accessible_task_ids),
                Q(name__icontains=query) | Q(task__title__icontains=query) | Q(task__project__name__icontains=query),
            )[:10]
            results.extend(
                {
                    "type": "file",
                    "id": attachment.id,
                    "title": attachment.name,
                    "subtitle": attachment.task.title,
                    "url": f"/dashboard/tasks/{attachment.task_id}",
                    "metadata": {"task_id": attachment.task_id, "project_id": attachment.task.project_id, "mime_type": attachment.mime_type},
                }
                for attachment in task_files
            )
            chat_files = ChatMessageAttachment.objects.select_related("message", "message__thread").filter(
                message__thread_id__in=accessible_thread_ids,
                name__icontains=query,
            ).distinct()[:10]
            results.extend(
                {
                    "type": "file",
                    "id": attachment.id,
                    "title": attachment.name,
                    "subtitle": attachment.message.thread.title or attachment.message.thread.kind,
                    "url": f"/dashboard/chat?thread={attachment.message.thread_id}&message={attachment.message_id}",
                    "metadata": {"thread_id": attachment.message.thread_id, "message_id": attachment.message_id, "mime_type": attachment.mime_type},
                }
                for attachment in chat_files
            )

        return Response(WorkspaceSearchResultSerializer(results[:40], many=True).data, status=status.HTTP_200_OK)


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
        queryset = get_accessible_project_queryset(
            request.user,
            Project.objects.select_related("manager").all(),
        )
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
        if not user_can_access_project_context(request.user, project):
            raise Http404
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
        queryset = apply_task_sorting(
            apply_task_filters(
                get_accessible_task_queryset(request.user, get_task_queryset()),
                request.query_params,
                request.user,
            ),
            request.query_params,
        )
        return Response(TaskCardSerializer(queryset, many=True, context={"request": request}).data, status=status.HTTP_200_OK)

    def post(self, request):
        data = request.data.copy()
        if request.user.role != "manager":
            data["current_assignee_id"] = request.user.id
            data.pop("estimated_minutes", None)
        serializer = TaskWriteSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        source_message = serializer.validated_data.get("source_chat_message")
        if source_message and (source_message.deleted_at or not can_access_chat_thread(request.user, source_message.thread)):
            return Response({"source_chat_message_id": ["Source message is not available."]}, status=status.HTTP_400_BAD_REQUEST)
        task = serializer.save(created_by=request.user, updated_by=request.user)
        activity_metadata = {"status": task.status, "assignee_id": task.current_assignee_id}
        if task.source_chat_message_id:
            activity_metadata |= {
                "source_chat_message_id": task.source_chat_message_id,
                "source_chat_thread_id": task.source_chat_message.thread_id,
            }
        record_task_activity(task, request.user, TaskActivityType.CREATED, activity_metadata)
        if task.current_assignee_id:
            create_notification(
                recipient=task.current_assignee,
                notification_type=NotificationType.TASK_ASSIGNED,
                task=task,
                project=task.project,
                payload={"title": task.title},
            )
        broadcast_task_event(task, "created")
        return Response(TaskDetailSerializer(get_task_detail_queryset().get(pk=task.pk), context={"request": request}).data, status=status.HTTP_201_CREATED)


class TaskDetailView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request, pk: int):
        return Response(TaskDetailSerializer(get_task_or_404(pk, request.user), context={"request": request}).data, status=status.HTTP_200_OK)

    def patch(self, request, pk: int):
        if request.user.role != "manager":
            return Response(status=status.HTTP_403_FORBIDDEN)
        task = get_task_or_404(pk, request.user)
        previous = {
            "status": task.status,
            "priority": task.priority,
            "due_date": task.due_date.isoformat() if task.due_date else None,
            "assignee_id": task.current_assignee_id,
            "label_ids": list(task.labels.values_list("id", flat=True)),
        }
        serializer = TaskWriteSerializer(task, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        source_message = serializer.validated_data.get("source_chat_message")
        if source_message and (source_message.deleted_at or not can_access_chat_thread(request.user, source_message.thread)):
            return Response({"source_chat_message_id": ["Source message is not available."]}, status=status.HTTP_400_BAD_REQUEST)
        task = serializer.save(updated_by=request.user)
        if previous["status"] != task.status:
            record_task_activity(task, request.user, TaskActivityType.STATUS_CHANGED, previous | {"next": task.status})
            sync_task_work_session(
                task,
                user=request.user,
                previous_status=previous["status"],
                next_status=task.status,
                event="status_changed",
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
        if "labels" in serializer.validated_data:
            record_task_activity(task, request.user, TaskActivityType.LABEL_UPDATED, {"previous": previous["label_ids"], "next": list(task.labels.values_list("id", flat=True))})
        broadcast_task_event(task, "updated")
        return Response(TaskDetailSerializer(get_task_detail_queryset().get(pk=task.pk), context={"request": request}).data, status=status.HTTP_200_OK)


class TaskStatusView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def patch(self, request, pk: int):
        task = get_task_or_404(pk, request.user)
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
        sync_task_work_session(
            task,
            user=request.user,
            previous_status=previous_status,
            next_status=task.status,
            event="status_changed",
        )
        if task.current_assignee_id and task.current_assignee_id != request.user.id:
            create_notification(recipient=task.current_assignee, notification_type=NotificationType.TASK_STATUS, task=task, project=task.project, payload={"status": task.status})
        broadcast_task_event(task, "status_changed", recipients=related_task_user_ids(task))
        return Response(TaskDetailSerializer(get_task_detail_queryset().get(pk=task.pk), context={"request": request}).data, status=status.HTTP_200_OK)


class TaskReorderView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def patch(self, request):
        serializer = TaskReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        moved_task_id = serializer.validated_data["moved_task_id"]
        ordered_items = serializer.validated_data["tasks"]
        ordered_ids = [item["id"] for item in ordered_items]

        with transaction.atomic():
            tasks_by_id = {
                task.id: task
                for task in get_accessible_task_queryset(
                    request.user,
                    Task.objects.select_related("project", "current_assignee")
                    .select_for_update(of=("self",))
                    .filter(id__in=ordered_ids),
                )
            }
            moved_task = tasks_by_id.get(moved_task_id) or get_task_or_404(moved_task_id, request.user)
            if not can_mutate_task(request.user, moved_task):
                return Response(status=status.HTTP_403_FORBIDDEN)

            updated_tasks = []
            updated_at = timezone.now()
            moved_previous_status = moved_task.status
            for item in ordered_items:
                task = tasks_by_id.get(item["id"])
                if task is None:
                    continue
                changed = False
                if task.status != item["status"]:
                    task.status = item["status"]
                    changed = True
                if task.sort_order != item["sort_order"]:
                    task.sort_order = item["sort_order"]
                    changed = True
                if changed:
                    task.updated_by = request.user
                    task.updated_at = updated_at
                    updated_tasks.append(task)

            if updated_tasks:
                Task.objects.bulk_update(updated_tasks, ["status", "sort_order", "updated_by", "updated_at"])

            moved_task.refresh_from_db()
            if moved_previous_status != moved_task.status:
                record_task_activity(
                    moved_task,
                    request.user,
                    TaskActivityType.STATUS_CHANGED,
                    {"previous_status": moved_previous_status, "status": moved_task.status, "event": "board_reorder"},
                )
                sync_task_work_session(
                    moved_task,
                    user=request.user,
                    previous_status=moved_previous_status,
                    next_status=moved_task.status,
                    event="board_reorder",
                )
                if moved_task.current_assignee_id and moved_task.current_assignee_id != request.user.id:
                    create_notification(
                        recipient=moved_task.current_assignee,
                        notification_type=NotificationType.TASK_STATUS,
                        task=moved_task,
                        project=moved_task.project,
                        payload={"status": moved_task.status},
                    )
            elif updated_tasks:
                record_task_activity(
                    moved_task,
                    request.user,
                    TaskActivityType.UPDATED,
                    {"event": "board_reorder", "sort_order": moved_task.sort_order},
                )

        if updated_tasks:
            broadcast_task_event(moved_task, "reordered")

        response_tasks = get_accessible_task_queryset(request.user, get_task_queryset()).filter(id__in=ordered_ids)
        return Response(TaskCardSerializer(response_tasks, many=True, context={"request": request}).data, status=status.HTTP_200_OK)


class TaskCompletionView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request, pk: int):
        task = get_task_or_404(pk, request.user)
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
        return Response(TaskDetailSerializer(get_task_detail_queryset().get(pk=task.pk), context={"request": request}).data, status=status.HTTP_200_OK)


class TaskArchiveView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request, pk: int):
        task = get_task_or_404(pk, request.user)
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
        return Response(TaskDetailSerializer(get_task_detail_queryset().get(pk=task.pk), context={"request": request}).data, status=status.HTTP_200_OK)


class TaskCoverImageView(APIView):
    permission_classes = (permissions.IsAuthenticated,)
    parser_classes = (parsers.MultiPartParser, parsers.FormParser)

    def post(self, request, pk: int):
        task = get_task_or_404(pk, request.user)
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
        return Response(TaskDetailSerializer(get_task_detail_queryset().get(pk=task.pk), context={"request": request}).data, status=status.HTTP_200_OK)

    def delete(self, request, pk: int):
        task = get_task_or_404(pk, request.user)
        if request.user.role != "manager" and task.current_assignee_id != request.user.id:
            return Response(status=status.HTTP_403_FORBIDDEN)
        if task.cover_image:
            task.cover_image.delete(save=False)
        task.cover_image = None
        task.updated_by = request.user
        task.save(update_fields=["cover_image", "updated_by", "updated_at"])
        record_task_activity(task, request.user, TaskActivityType.ATTACHMENT_ADDED, {"cover_image": True, "removed": True})
        broadcast_task_event(task, "cover_deleted", recipients=related_task_user_ids(task))
        return Response(TaskDetailSerializer(get_task_detail_queryset().get(pk=task.pk), context={"request": request}).data, status=status.HTTP_200_OK)


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


def ensure_default_checklist(task: Task, user) -> TaskChecklist:
    checklist = task.checklists.order_by("sort_order", "created_at").first()
    if checklist:
        return checklist
    return TaskChecklist.objects.create(
        task=task,
        title="Checklist",
        sort_order=0,
        created_by=user,
    )


class TaskChecklistGroupListView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request, pk: int):
        task = get_task_or_404(pk, request.user)
        return Response(TaskChecklistSerializer(task.checklists.all(), many=True).data, status=status.HTTP_200_OK)

    def post(self, request, pk: int):
        task = get_task_or_404(pk, request.user)
        if not can_mutate_task(request.user, task):
            return Response(status=status.HTTP_403_FORBIDDEN)
        serializer = ChecklistCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        checklist = serializer.save(task=task, created_by=request.user)
        record_task_activity(task, request.user, TaskActivityType.CHECKLIST_UPDATED, {"checklist_id": checklist.id, "action": "created"})
        broadcast_task_event(task, "checklist_updated", recipients=related_task_user_ids(task))
        return Response(TaskChecklistSerializer(checklist).data, status=status.HTTP_201_CREATED)


class TaskChecklistListView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request, pk: int):
        task = get_task_or_404(pk, request.user)
        return Response(TaskChecklistItemSerializer(task.checklist_items.all(), many=True).data, status=status.HTTP_200_OK)

    def post(self, request, pk: int):
        task = get_task_or_404(pk, request.user)
        if not can_mutate_task(request.user, task):
            return Response(status=status.HTTP_403_FORBIDDEN)
        serializer = ChecklistItemCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        checklist_id = serializer.validated_data.pop("checklist_id", None)
        if checklist_id:
            try:
                checklist = task.checklists.get(pk=checklist_id)
            except TaskChecklist.DoesNotExist as exc:
                raise Http404 from exc
        else:
            checklist = ensure_default_checklist(task, request.user)
        item = serializer.save(task=task, checklist=checklist, created_by=request.user)
        record_task_activity(task, request.user, TaskActivityType.CHECKLIST_UPDATED, {"item_id": item.id, "checklist_id": checklist.id, "action": "created"})
        broadcast_task_event(task, "checklist_updated", recipients=related_task_user_ids(task))
        return Response(TaskChecklistItemSerializer(item).data, status=status.HTTP_201_CREATED)


class TaskChecklistDetailView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get_object(self, request, pk: int, item_id: int):
        task = get_task_or_404(pk, request.user)
        try:
            return task, task.checklist_items.get(pk=item_id)
        except TaskChecklistItem.DoesNotExist as exc:
            raise Http404 from exc

    def patch(self, request, pk: int, item_id: int):
        task, item = self.get_object(request, pk, item_id)
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
        task, item = self.get_object(request, pk, item_id)
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
        task = get_task_or_404(pk, request.user)
        return Response(TaskAttachmentSerializer(task.attachments.all(), many=True, context={"request": request}).data, status=status.HTTP_200_OK)

    def post(self, request, pk: int):
        task = get_task_or_404(pk, request.user)
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

    def post(self, request, pk: int, attachment_id: int):
        task = get_task_or_404(pk, request.user)
        if not can_mutate_task(request.user, task):
            return Response(status=status.HTTP_403_FORBIDDEN)
        try:
            attachment = task.attachments.get(pk=attachment_id)
        except TaskAttachment.DoesNotExist as exc:
            raise Http404 from exc
        is_image_attachment = attachment.mime_type.startswith("image/") or bool(re.search(r"\.(avif|bmp|gif|jpe?g|png|svg|webp)$", attachment.name, re.IGNORECASE))
        if not is_image_attachment:
            return Response({"attachment": ["Only image attachments can be used as cover images."]}, status=status.HTTP_400_BAD_REQUEST)
        attachment.file.open("rb")
        try:
            task.cover_image.save(attachment.name, ContentFile(attachment.file.read()), save=False)
        finally:
            attachment.file.close()
        task.updated_by = request.user
        task.save(update_fields=["cover_image", "updated_by", "updated_at"])
        record_task_activity(task, request.user, TaskActivityType.ATTACHMENT_ADDED, {"cover_image": True, "attachment_id": attachment.id, "name": attachment.name})
        broadcast_task_event(task, "cover_updated", recipients=related_task_user_ids(task))
        return Response(TaskDetailSerializer(get_task_detail_queryset().get(pk=task.pk), context={"request": request}).data, status=status.HTTP_200_OK)

    def delete(self, request, pk: int, attachment_id: int):
        task = get_task_or_404(pk, request.user)
        if not can_mutate_task(request.user, task):
            return Response(status=status.HTTP_403_FORBIDDEN)
        try:
            attachment = task.attachments.get(pk=attachment_id)
        except TaskAttachment.DoesNotExist as exc:
            raise Http404 from exc
        attachment.delete()
        broadcast_task_event(task, "attachment_deleted", recipients=related_task_user_ids(task))
        return Response(status=status.HTTP_204_NO_CONTENT)


class TaskReviewView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request, pk: int):
        task = get_task_or_404(pk, request.user)
        if not can_mutate_task(request.user, task):
            return Response(status=status.HTTP_403_FORBIDDEN)
        serializer = TaskReviewUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        previous_state = task.review_state
        next_state = serializer.validated_data["review_state"]
        task.review_state = next_state
        update_fields = ["review_state", "updated_by", "updated_at"]
        task.updated_by = request.user
        if next_state == "needs_review":
            task.review_requested_by = request.user
            task.review_requested_at = timezone.now()
            task.review_approved_by = None
            task.review_approved_at = None
            update_fields.extend(["review_requested_by", "review_requested_at", "review_approved_by", "review_approved_at"])
        elif next_state == "approved":
            task.review_approved_by = request.user
            task.review_approved_at = timezone.now()
            update_fields.extend(["review_approved_by", "review_approved_at"])
        elif next_state == "not_submitted":
            task.review_requested_by = None
            task.review_requested_at = None
            task.review_approved_by = None
            task.review_approved_at = None
            update_fields.extend(["review_requested_by", "review_requested_at", "review_approved_by", "review_approved_at"])
        elif next_state == "changes_requested":
            task.review_approved_by = None
            task.review_approved_at = None
            update_fields.extend(["review_approved_by", "review_approved_at"])
        task.save(update_fields=update_fields)
        notes = serializer.validated_data.get("notes", "")
        record_task_activity(
            task,
            request.user,
            TaskActivityType.REVIEW_UPDATED,
            {"previous_state": previous_state, "review_state": next_state, "notes": notes},
        )
        recipients = set(related_task_user_ids(task))
        if next_state == "needs_review":
            recipients.add(task.project.manager_id)
        for recipient in User.objects.filter(id__in=[user_id for user_id in recipients if user_id != request.user.id]):
            create_notification(
                recipient=recipient,
                notification_type=NotificationType.REVIEW_REQUESTED,
                task=task,
                project=task.project,
                payload={"review_state": next_state, "notes": notes},
            )
        broadcast_task_event(task, "review_updated", recipients=list(recipients))
        return Response(TaskDetailSerializer(get_task_detail_queryset().get(pk=task.pk), context={"request": request}).data, status=status.HTTP_200_OK)


class TaskArtifactVersionListView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request, pk: int):
        task = get_task_or_404(pk, request.user)
        versions = task.artifact_versions.select_related("uploaded_by", "approved_by", "attachment__uploaded_by").prefetch_related("attachment__annotations")
        return Response(TaskArtifactVersionSerializer(versions, many=True, context={"request": request}).data, status=status.HTTP_200_OK)

    def post(self, request, pk: int):
        task = get_task_or_404(pk, request.user)
        if not can_mutate_task(request.user, task):
            return Response(status=status.HTTP_403_FORBIDDEN)
        serializer = TaskArtifactVersionSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        attachment = serializer.validated_data.get("attachment")
        if attachment and attachment.task_id != task.id:
            return Response({"attachment_id": ["Attachment must belong to this task."]}, status=status.HTTP_400_BAD_REQUEST)
        next_version = int(task.artifact_versions.aggregate(value=Max("version_number"))["value"] or 0) + 1
        version = serializer.save(task=task, version_number=next_version, uploaded_by=request.user)
        update_fields = []
        if version.approval_state == ArtifactApprovalState.APPROVED:
            version.approved_by = request.user
            version.approved_at = timezone.now()
            update_fields = ["approved_by", "approved_at", "updated_at"]
            version.save(update_fields=update_fields)
        record_task_activity(
            task,
            request.user,
            TaskActivityType.ARTIFACT_VERSION_ADDED,
            {"version_id": version.id, "version_number": version.version_number, "approval_state": version.approval_state},
        )
        broadcast_task_event(task, "artifact_version_added", recipients=related_task_user_ids(task))
        return Response(TaskArtifactVersionSerializer(version, context={"request": request}).data, status=status.HTTP_201_CREATED)


class AttachmentAnnotationListView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    @staticmethod
    def get_attachment(request, pk: int) -> TaskAttachment:
        try:
            attachment = TaskAttachment.objects.select_related("task", "task__project").get(pk=pk)
        except TaskAttachment.DoesNotExist as exc:
            raise Http404 from exc
        if not user_can_access_task_context(request.user, attachment.task):
            raise Http404
        return attachment

    def get(self, request, pk: int):
        attachment = self.get_attachment(request, pk)
        annotations = attachment.annotations.select_related("author", "resolved_by", "version")
        return Response(AttachmentAnnotationSerializer(annotations, many=True).data, status=status.HTTP_200_OK)

    def post(self, request, pk: int):
        attachment = self.get_attachment(request, pk)
        if not can_mutate_task(request.user, attachment.task):
            return Response(status=status.HTTP_403_FORBIDDEN)
        serializer = AttachmentAnnotationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        version = serializer.validated_data.get("version")
        if version and (version.task_id != attachment.task_id or version.attachment_id not in {None, attachment.id}):
            return Response({"version_id": ["Version must belong to this attachment or task."]}, status=status.HTTP_400_BAD_REQUEST)
        annotation = serializer.save(attachment=attachment, author=request.user)
        if annotation.resolved:
            annotation.resolved_by = request.user
            annotation.resolved_at = timezone.now()
            annotation.save(update_fields=["resolved_by", "resolved_at", "updated_at"])
        record_task_activity(
            attachment.task,
            request.user,
            TaskActivityType.ANNOTATION_ADDED,
            {"annotation_id": annotation.id, "attachment_id": attachment.id, "version_id": version.id if version else None},
        )
        broadcast_task_event(attachment.task, "annotation_added", recipients=related_task_user_ids(attachment.task))
        return Response(AttachmentAnnotationSerializer(annotation).data, status=status.HTTP_201_CREATED)


class TaskReassignView(APIView):
    permission_classes = (IsManager,)

    def post(self, request, pk: int):
        task = get_task_or_404(pk, request.user)
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
        broadcast_task_event(task, "reassigned")
        return Response(TaskDetailSerializer(get_task_detail_queryset().get(pk=task.pk), context={"request": request}).data, status=status.HTTP_200_OK)


class TaskCommentsView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request, pk: int):
        task = get_task_or_404(pk, request.user)
        return Response(TaskCommentSerializer(task.comments.select_related("author"), many=True).data, status=status.HTTP_200_OK)

    def post(self, request, pk: int):
        task = get_task_or_404(pk, request.user)
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
        if request.user.role != "manager":
            return Response(status=status.HTTP_403_FORBIDDEN)
        task = get_task_or_404(pk, request.user)
        return Response(TimeEntrySerializer(task.time_entries.select_related("user"), many=True).data, status=status.HTTP_200_OK)

    def post(self, request, pk: int):
        if request.user.role != "manager":
            return Response(status=status.HTTP_403_FORBIDDEN)
        task = get_task_or_404(pk, request.user)
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


class WorkflowAnalyticsReportView(APIView):
    permission_classes = (IsManager,)

    def get(self, request):
        queryset = Task.objects.select_related(
            "project",
            "project__manager",
            "current_assignee",
        ).prefetch_related(
            "activities",
            "time_entries",
        ).filter(archived=False)
        if request.query_params.get("start_date"):
            queryset = queryset.filter(created_at__date__gte=request.query_params.get("start_date"))
        if request.query_params.get("end_date"):
            queryset = queryset.filter(created_at__date__lte=request.query_params.get("end_date"))

        tasks = list(queryset)
        now = timezone.now()
        lead_times = []
        cycle_times = []
        blocked_minutes = 0
        status_counts = {status_value: 0 for status_value, _label in TaskStatus.choices}

        for task in tasks:
            status_counts[task.status] = status_counts.get(task.status, 0) + 1
            completed_at = task.completed_at
            if not completed_at and task.status == TaskStatus.DONE:
                completed_at = task.updated_at
            if completed_at:
                lead_times.append(days_between(task.created_at, completed_at))
                cycle_started_at = first_status_time(task, TaskStatus.IN_PROGRESS) or task.work_started_at or task.created_at
                cycle_times.append(days_between(cycle_started_at, completed_at))
            if task.status == TaskStatus.BLOCKED:
                blocked_started_at = first_status_time(task, TaskStatus.BLOCKED) or task.updated_at or task.created_at
                blocked_minutes += max(0, int((now - blocked_started_at).total_seconds() // 60))

        unresolved_reviews = [
            task for task in tasks
            if task.review_state in {TaskReviewState.NEEDS_REVIEW, TaskReviewState.CHANGES_REQUESTED}
        ]
        pending_review_minutes = [
            max(0, int((now - task.review_requested_at).total_seconds() // 60))
            for task in unresolved_reviews
            if task.review_requested_at
        ]
        total_estimated = sum(task.estimated_minutes for task in tasks)
        total_actual = sum(task.actual_minutes for task in tasks)
        remaining_by_user: dict[int, dict] = {}
        for task in tasks:
            if not task.current_assignee_id or task.status == TaskStatus.DONE:
                continue
            remaining = max(task.estimated_minutes - task.actual_minutes, 0) or task.estimated_minutes
            row = remaining_by_user.setdefault(
                task.current_assignee_id,
                {
                    "user": UserSummarySerializer(task.current_assignee).data,
                    "open_tasks": 0,
                    "overdue_tasks": 0,
                    "remaining_minutes": 0,
                    "capacity_minutes": WORK_DAY_MINUTES * 5,
                },
            )
            row["open_tasks"] += 1
            row["remaining_minutes"] += remaining
            if task.is_overdue:
                row["overdue_tasks"] += 1

        capacity = []
        today = timezone.localdate()
        for row in remaining_by_user.values():
            load_percent = round((row["remaining_minutes"] / row["capacity_minutes"]) * 100, 1) if row["capacity_minutes"] else 0
            forecast_days = round(row["remaining_minutes"] / WORK_DAY_MINUTES, 1) if row["remaining_minutes"] else 0
            capacity.append(row | {
                "load_percent": load_percent,
                "forecast_days": forecast_days,
                "risk": "high" if load_percent >= 100 or row["overdue_tasks"] else "medium" if load_percent >= 75 else "normal",
            })

        forecast = sorted(capacity, key=lambda row: (-row["load_percent"], -row["overdue_tasks"], row["user"]["email"]))
        payload = {
            "generated_at": now,
            "tasks_sampled": len(tasks),
            "lead_time_days": average(lead_times),
            "cycle_time_days": average(cycle_times),
            "blocked_tasks": status_counts.get(TaskStatus.BLOCKED, 0),
            "blocked_time_minutes": blocked_minutes,
            "review_bottlenecks": {
                "needs_review": sum(1 for task in tasks if task.review_state == TaskReviewState.NEEDS_REVIEW),
                "changes_requested": sum(1 for task in tasks if task.review_state == TaskReviewState.CHANGES_REQUESTED),
                "approved": sum(1 for task in tasks if task.review_state == TaskReviewState.APPROVED),
                "pending_review_minutes": sum(pending_review_minutes),
                "average_pending_review_minutes": int(sum(pending_review_minutes) / len(pending_review_minutes)) if pending_review_minutes else 0,
            },
            "estimate_vs_actual": {
                "estimated_minutes": total_estimated,
                "actual_minutes": total_actual,
                "variance_minutes": total_actual - total_estimated,
                "actual_to_estimate_ratio": round(total_actual / total_estimated, 2) if total_estimated else 0,
            },
            "capacity": sorted(capacity, key=lambda row: row["user"]["email"]),
            "designer_forecast": forecast[:8],
            "status_counts": status_counts,
        }
        return Response(WorkflowAnalyticsSerializer(payload).data, status=status.HTTP_200_OK)


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


class NotificationSnoozeView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request, pk: int):
        try:
            notification = Notification.objects.get(pk=pk, recipient=request.user)
        except Notification.DoesNotExist as exc:
            raise Http404 from exc
        serializer = NotificationSnoozeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        notification.snoozed_until = serializer.validated_data["snoozed_until"]
        notification.save(update_fields=["snoozed_until"])
        return Response(NotificationItemSerializer(notification, context={"request": request}).data, status=status.HTTP_200_OK)


class NotificationActionView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request, pk: int):
        try:
            notification = Notification.objects.select_related("task", "task__project", "project").get(pk=pk, recipient=request.user)
        except Notification.DoesNotExist as exc:
            raise Http404 from exc
        serializer = NotificationActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action = serializer.validated_data["action"]
        task = notification.task

        if action == "mark_read":
            mark_notification_read(notification)
        elif action == "accept_assignment":
            if not task:
                return Response({"task": ["This notification is not linked to a task."]}, status=status.HTTP_400_BAD_REQUEST)
            if task.current_assignee_id not in {None, request.user.id} and not is_manager_user(request.user):
                return Response(status=status.HTTP_403_FORBIDDEN)
            if task.current_assignee_id is None:
                task.current_assignee = request.user
                task.updated_by = request.user
                task.save(update_fields=["current_assignee", "updated_by", "updated_at"])
                record_task_activity(task, request.user, TaskActivityType.REASSIGNED, {"assignee_id": request.user.id, "source": "notification_action"})
                broadcast_task_event(task, "reassigned", recipients=related_task_user_ids(task))
            mark_notification_read(notification)
        elif action == "move_status":
            if not task:
                return Response({"task": ["This notification is not linked to a task."]}, status=status.HTTP_400_BAD_REQUEST)
            if not can_mutate_task(request.user, task):
                return Response(status=status.HTTP_403_FORBIDDEN)
            previous_status = task.status
            task.status = serializer.validated_data["status"]
            task.updated_by = request.user
            task.save(update_fields=["status", "updated_by", "updated_at"])
            record_task_activity(task, request.user, TaskActivityType.STATUS_CHANGED, {"previous_status": previous_status, "status": task.status, "source": "notification_action"})
            sync_task_work_session(task, user=request.user, previous_status=previous_status, next_status=task.status, event="notification_action")
            broadcast_task_event(task, "status_changed", recipients=related_task_user_ids(task))
            mark_notification_read(notification)
        elif action == "comment":
            if not task:
                return Response({"task": ["This notification is not linked to a task."]}, status=status.HTTP_400_BAD_REQUEST)
            if not can_mutate_task(request.user, task):
                return Response(status=status.HTTP_403_FORBIDDEN)
            comment = TaskComment.objects.create(task=task, author=request.user, body=serializer.validated_data["body"])
            record_task_activity(task, request.user, TaskActivityType.COMMENT_ADDED, {"comment_id": comment.id, "source": "notification_action"})
            broadcast_task_event(task, "comment_added", recipients=related_task_user_ids(task))
            mark_notification_read(notification)

        notification.action_taken_at = timezone.now()
        notification.action_taken_by = request.user
        notification.save(update_fields=["action_taken_at", "action_taken_by"])
        return Response(NotificationItemSerializer(notification, context={"request": request}).data, status=status.HTTP_200_OK)


class NotificationPreferenceView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request):
        preferences, _ = NotificationPreference.objects.get_or_create(user=request.user)
        return Response(NotificationPreferenceSerializer(preferences).data, status=status.HTTP_200_OK)

    def patch(self, request):
        preferences, _ = NotificationPreference.objects.get_or_create(user=request.user)
        serializer = NotificationPreferenceSerializer(preferences, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(NotificationPreferenceSerializer(preferences).data, status=status.HTTP_200_OK)


class ChatThreadListView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request):
        public, _ = ChatThread.objects.get_or_create(kind=ChatThreadKind.PUBLIC, defaults={"title": "Studio public"})
        queryset = get_chat_thread_queryset_for_user(request.user)
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
        if kind == ChatThreadKind.PROJECT:
            project = serializer.validated_data.get("project")
            if not project:
                return Response({"project_id": ["This field is required for project threads."]}, status=status.HTTP_400_BAD_REQUEST)
            if not user_can_access_project_context(request.user, project):
                return Response(status=status.HTTP_403_FORBIDDEN)
            thread, created = ChatThread.objects.get_or_create(
                kind=ChatThreadKind.PROJECT,
                project=project,
                defaults={"title": serializer.validated_data.get("title") or project.name},
            )
            if not thread.title:
                thread.title = project.name
                thread.save(update_fields=["title", "updated_at"])
            sync_linked_thread_participants(thread, actor=request.user)
            return Response(
                ChatThreadSerializer(thread, context={"request": request}).data,
                status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
            )
        if kind == ChatThreadKind.TASK:
            task = serializer.validated_data.get("task")
            if not task:
                return Response({"task_id": ["This field is required for task threads."]}, status=status.HTTP_400_BAD_REQUEST)
            if not user_can_access_task_context(request.user, task):
                return Response(status=status.HTTP_403_FORBIDDEN)
            thread, created = ChatThread.objects.get_or_create(
                kind=ChatThreadKind.TASK,
                task=task,
                defaults={
                    "project": task.project,
                    "title": serializer.validated_data.get("title") or task.title,
                },
            )
            fields_to_update = []
            if thread.project_id != task.project_id:
                thread.project = task.project
                fields_to_update.append("project")
            if not thread.title:
                thread.title = task.title
                fields_to_update.append("title")
            if fields_to_update:
                thread.save(update_fields=[*fields_to_update, "updated_at"])
            sync_linked_thread_participants(thread, actor=request.user)
            return Response(
                ChatThreadSerializer(thread, context={"request": request}).data,
                status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
            )
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
        queryset = get_chat_message_queryset().filter(thread=thread).order_by("-id")
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
        sender_id = request.query_params.get("sender_id")
        if sender_id and sender_id.isdigit():
            queryset = queryset.filter(sender_id=int(sender_id))
        date_from = request.query_params.get("date_from")
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)
        date_to = request.query_params.get("date_to")
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)
        if parse_bool(request.query_params.get("has_files")):
            queryset = queryset.filter(attachments__isnull=False).distinct()
        if parse_bool(request.query_params.get("has_images")):
            queryset = queryset.filter(
                Q(attachments__mime_type__startswith="image/")
                | Q(attachments__name__iendswith=".png")
                | Q(attachments__name__iendswith=".jpg")
                | Q(attachments__name__iendswith=".jpeg")
                | Q(attachments__name__iendswith=".gif")
                | Q(attachments__name__iendswith=".webp")
                | Q(attachments__name__iendswith=".bmp")
                | Q(attachments__name__iendswith=".svg")
            ).distinct()
        if parse_bool(request.query_params.get("decisions")):
            queryset = queryset.filter(decision_at__isnull=False)
        reference = (request.query_params.get("reference") or "").strip()
        if reference:
            queryset = queryset.filter(body__icontains=reference)
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
        recipients = list(chat_thread_recipients(thread, request.user))
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
        message = get_chat_message_queryset().get(pk=message.pk)
        broadcast_chat_message(message, request)
        return Response(ChatMessageSerializer(message, context={"request": request}).data, status=status.HTTP_201_CREATED)


class ChatMessageReadView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request, pk: int):
        message = get_chat_message_or_404(request, pk)
        message.read_by.add(request.user)
        payload = {"type": "chat.read", "message_id": message.id, "thread_id": message.thread_id, "user_id": request.user.id}
        broadcast_chat_event(message.thread, payload)
        return Response(ChatMessageSerializer(message, context={"request": request}).data, status=status.HTTP_200_OK)


class ChatMessageDeleteView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request, pk: int):
        message = get_chat_message_or_404(request, pk)
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


class ChatMessageUpdateView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def patch(self, request, pk: int):
        message = get_chat_message_or_404(request, pk)
        if message.sender_id != request.user.id or message.deleted_at:
            return Response(status=status.HTTP_403_FORBIDDEN)
        serializer = ChatMessageUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_body = serializer.validated_data["body"].strip()
        if new_body == message.body:
            return Response(ChatMessageSerializer(message, context={"request": request}).data, status=status.HTTP_200_OK)
        ChatMessageEdit.objects.create(
            message=message,
            edited_by=request.user,
            previous_body=message.body,
            new_body=new_body,
        )
        message.body = new_body
        message.edited_by = request.user
        message.edited_at = timezone.now()
        message.save(update_fields=["body", "edited_by", "edited_at", "updated_at"])
        message = get_chat_message_queryset().get(pk=message.pk)
        payload = {"type": "chat.updated", "message_id": message.id, "thread_id": message.thread_id}
        broadcast_chat_event(message.thread, payload)
        return Response(ChatMessageSerializer(message, context={"request": request}).data, status=status.HTTP_200_OK)


class ChatMessageReactionView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request, pk: int):
        message = get_chat_message_or_404(request, pk)
        if message.deleted_at:
            return Response(status=status.HTTP_400_BAD_REQUEST)
        serializer = ChatMessageReactionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        emoji = serializer.validated_data["emoji"]
        reaction, created = ChatMessageReaction.objects.get_or_create(message=message, user=request.user, emoji=emoji)
        if not created:
            reaction.delete()
        message = get_chat_message_queryset().get(pk=message.pk)
        payload = {"type": "chat.reaction", "message_id": message.id, "thread_id": message.thread_id}
        broadcast_chat_event(message.thread, payload)
        return Response(ChatMessageSerializer(message, context={"request": request}).data, status=status.HTTP_200_OK)


class ChatMessageDecisionView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request, pk: int):
        message = get_chat_message_or_404(request, pk)
        if message.deleted_at:
            return Response(status=status.HTTP_400_BAD_REQUEST)
        serializer = ChatMessageDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if serializer.validated_data["is_decision"]:
            message.decision_by = request.user
            message.decision_at = timezone.now()
        else:
            message.decision_by = None
            message.decision_at = None
        message.save(update_fields=["decision_by", "decision_at", "updated_at"])
        message = get_chat_message_queryset().get(pk=message.pk)
        payload = {"type": "chat.decision", "message_id": message.id, "thread_id": message.thread_id}
        broadcast_chat_event(message.thread, payload)
        return Response(ChatMessageSerializer(message, context={"request": request}).data, status=status.HTTP_200_OK)


class ChatMessageReminderView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request, pk: int):
        message = get_chat_message_or_404(request, pk)
        if message.deleted_at:
            return Response(status=status.HTTP_400_BAD_REQUEST)
        serializer = ChatMessageReminderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        task = serializer.validated_data.get("task")
        remind_at = serializer.validated_data.get("remind_at")
        if remind_at is None and task and task.due_date:
            remind_at = timezone.make_aware(datetime.combine(task.due_date, time(hour=9)))
        reminder = ChatMessageReminder.objects.create(
            message=message,
            task=task,
            created_by=request.user,
            remind_at=remind_at,
            note=serializer.validated_data.get("note", ""),
        )
        payload = {"type": "chat.reminder", "message_id": message.id, "thread_id": message.thread_id, "reminder_id": reminder.id}
        broadcast_chat_event(message.thread, payload)
        return Response(ChatMessageSerializer(get_chat_message_queryset().get(pk=message.pk), context={"request": request}).data, status=status.HTTP_201_CREATED)
