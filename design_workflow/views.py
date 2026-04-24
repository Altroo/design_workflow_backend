from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Count, Q, Sum
from django.http import Http404
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    Notification,
    NotificationType,
    Project,
    ProjectStatus,
    Task,
    TaskActivityType,
    TaskStatus,
    TimeEntry,
)
from .permissions import IsManager, IsManagerOrReadOnly, can_mutate_task
from .serializers import (
    CommentCreateSerializer,
    DashboardSummarySerializer,
    NotificationItemSerializer,
    ProjectDetailSerializer,
    ProjectSummarySerializer,
    ProjectWriteSerializer,
    TaskCardSerializer,
    TaskDetailSerializer,
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
    mark_notification_read,
    notification_exists_for_today,
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
        .prefetch_related("comments__author", "time_entries__user", "activities__actor")
        .all()
    )


def apply_task_filters(queryset, params, user):
    project_id = params.get("project")
    assignee_id = params.get("assignee")
    status_value = params.get("status")
    priority = params.get("priority")
    overdue = parse_bool(params.get("overdue"))
    blocked = parse_bool(params.get("blocked"))
    mine = parse_bool(params.get("mine"))
    start_date = params.get("start_date")
    end_date = params.get("end_date")

    if project_id:
        queryset = queryset.filter(project_id=project_id)
    if assignee_id:
        queryset = queryset.filter(current_assignee_id=assignee_id)
    if status_value:
        queryset = queryset.filter(status=status_value)
    if priority:
        queryset = queryset.filter(priority=priority)
    if overdue is True:
        queryset = queryset.filter(
            due_date__lt=timezone.localdate(),
        ).exclude(status=TaskStatus.DONE)
    if blocked is True:
        queryset = queryset.filter(status=TaskStatus.BLOCKED)
    if mine is True:
        queryset = queryset.filter(current_assignee=user)
    if start_date:
        queryset = queryset.filter(project__start_date__gte=start_date)
    if end_date:
        queryset = queryset.filter(project__target_end_date__lte=end_date)

    return queryset


class DashboardSummaryView(APIView):
    permission_classes = (IsManager,)

    def get(self, request):
        today = timezone.localdate()
        week_start = today - timedelta(days=today.weekday())
        tasks = Task.objects.select_related("project")
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
                TimeEntry.objects.filter(work_date__gte=week_start).aggregate(total=Sum("minutes"))["total"] or 0
            ),
            "recent_reassignments": Task.objects.filter(
                activities__action_type=TaskActivityType.REASSIGNED,
                activities__created_at__date__gte=week_start,
            ).distinct().count(),
        }
        serializer = DashboardSummarySerializer(payload)
        return Response(serializer.data, status=status.HTTP_200_OK)


class ProjectListCreateView(APIView):
    permission_classes = (IsManagerOrReadOnly,)

    def get(self, request):
        queryset = Project.objects.select_related("manager").all()
        archived = parse_bool(request.query_params.get("archived"))
        status_value = request.query_params.get("status")
        if archived is not None:
            queryset = queryset.filter(archived=archived)
        if status_value:
            queryset = queryset.filter(status=status_value)
        serializer = ProjectSummarySerializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = ProjectWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        project = serializer.save()
        return Response(
            ProjectSummarySerializer(project).data,
            status=status.HTTP_201_CREATED,
        )


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
        serializer = ProjectDetailSerializer(project)
        return Response(serializer.data, status=status.HTTP_200_OK)

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
        serializer = TaskCardSerializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        if request.user.role != "manager":
            return Response(status=status.HTTP_403_FORBIDDEN)

        serializer = TaskWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        task = serializer.save(created_by=request.user, updated_by=request.user)
        record_task_activity(
            task,
            request.user,
            TaskActivityType.CREATED,
            {"status": task.status, "assignee_id": task.current_assignee_id},
        )
        if task.current_assignee_id:
            create_notification(
                recipient=task.current_assignee,
                notification_type=NotificationType.TASK_ASSIGNED,
                task=task,
                project=task.project,
                payload={"title": task.title},
            )
        broadcast_task_event(task, "created")
        return Response(TaskDetailSerializer(task).data, status=status.HTTP_201_CREATED)


class TaskDetailView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    @staticmethod
    def get_object(pk: int) -> Task:
        try:
            return get_task_queryset().get(pk=pk)
        except Task.DoesNotExist as exc:
            raise Http404 from exc

    def get(self, request, pk: int):
        task = self.get_object(pk)
        return Response(TaskDetailSerializer(task).data, status=status.HTTP_200_OK)

    def patch(self, request, pk: int):
        if request.user.role != "manager":
            return Response(status=status.HTTP_403_FORBIDDEN)

        task = self.get_object(pk)
        previous = {
            "status": task.status,
            "priority": task.priority,
            "due_date": task.due_date.isoformat() if task.due_date else None,
            "assignee_id": task.current_assignee_id,
        }
        serializer = TaskWriteSerializer(task, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        task = serializer.save(updated_by=request.user)
        if previous["status"] != task.status:
            record_task_activity(task, request.user, TaskActivityType.STATUS_CHANGED, previous | {"next": task.status})
        if previous["priority"] != task.priority:
            record_task_activity(task, request.user, TaskActivityType.PRIORITY_CHANGED, previous | {"next": task.priority})
        if previous["due_date"] != (task.due_date.isoformat() if task.due_date else None):
            record_task_activity(task, request.user, TaskActivityType.DUE_DATE_CHANGED, previous | {"next": task.due_date.isoformat() if task.due_date else None})
        if previous["assignee_id"] != task.current_assignee_id:
            record_task_activity(task, request.user, TaskActivityType.REASSIGNED, previous | {"next": task.current_assignee_id})
        broadcast_task_event(task, "updated")
        return Response(TaskDetailSerializer(task).data, status=status.HTTP_200_OK)


class TaskStatusView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    @staticmethod
    def get_object(pk: int) -> Task:
        try:
            return Task.objects.select_related("project", "current_assignee").get(pk=pk)
        except Task.DoesNotExist as exc:
            raise Http404 from exc

    def patch(self, request, pk: int):
        task = self.get_object(pk)
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
        record_task_activity(
            task,
            request.user,
            TaskActivityType.STATUS_CHANGED,
            {"previous_status": previous_status, "status": task.status, "blocked_reason": task.blocked_reason},
        )
        if task.current_assignee_id and task.current_assignee_id != request.user.id:
            create_notification(
                recipient=task.current_assignee,
                notification_type=NotificationType.TASK_STATUS,
                task=task,
                project=task.project,
                payload={"status": task.status},
            )
        broadcast_task_event(task, "status_changed", recipients=related_task_user_ids(task))
        return Response(TaskDetailSerializer(Task.objects.get(pk=task.pk)).data, status=status.HTTP_200_OK)


class TaskReassignView(APIView):
    permission_classes = (IsManager,)

    @staticmethod
    def get_object(pk: int) -> Task:
        try:
            return Task.objects.select_related("project", "current_assignee").get(pk=pk)
        except Task.DoesNotExist as exc:
            raise Http404 from exc

    def post(self, request, pk: int):
        task = self.get_object(pk)
        serializer = TaskReassignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        assignee = serializer.validated_data["assignee"]
        reason = serializer.validated_data["reason"]
        previous_assignee_id = task.current_assignee_id
        task.current_assignee = assignee
        task.updated_by = request.user
        task.save(update_fields=["current_assignee", "updated_by", "updated_at"])
        record_task_activity(
            task,
            request.user,
            TaskActivityType.REASSIGNED,
            {
                "previous_assignee_id": previous_assignee_id,
                "assignee_id": assignee.id,
                "reason": reason,
            },
        )
        create_notification(
            recipient=assignee,
            notification_type=NotificationType.TASK_REASSIGNED,
            task=task,
            project=task.project,
            payload={"reason": reason},
        )
        broadcast_task_event(task, "reassigned")
        return Response(TaskDetailSerializer(Task.objects.get(pk=task.pk)).data, status=status.HTTP_200_OK)


class TaskCommentsView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    @staticmethod
    def get_object(pk: int) -> Task:
        try:
            return Task.objects.select_related("project", "current_assignee").get(pk=pk)
        except Task.DoesNotExist as exc:
            raise Http404 from exc

    def get(self, request, pk: int):
        task = self.get_object(pk)
        serializer = TaskCommentSerializer(task.comments.select_related("author"), many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request, pk: int):
        task = self.get_object(pk)
        if not can_mutate_task(request.user, task):
            return Response(status=status.HTTP_403_FORBIDDEN)
        serializer = CommentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        comment = serializer.save(task=task, author=request.user)
        record_task_activity(
            task,
            request.user,
            TaskActivityType.COMMENT_ADDED,
            {"comment_id": comment.id},
        )
        recipient_ids = [user_id for user_id in related_task_user_ids(task) if user_id != request.user.id]
        for recipient in User.objects.filter(id__in=recipient_ids):
            create_notification(
                recipient=recipient,
                notification_type=NotificationType.TASK_COMMENT,
                task=task,
                project=task.project,
                payload={"comment_id": comment.id},
            )
        broadcast_task_event(task, "comment_added", recipients=related_task_user_ids(task))
        return Response(TaskCommentSerializer(comment).data, status=status.HTTP_201_CREATED)


class TaskTimeEntriesView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    @staticmethod
    def get_object(pk: int) -> Task:
        try:
            return Task.objects.select_related("project", "current_assignee").get(pk=pk)
        except Task.DoesNotExist as exc:
            raise Http404 from exc

    def get(self, request, pk: int):
        task = self.get_object(pk)
        serializer = TimeEntrySerializer(task.time_entries.select_related("user"), many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request, pk: int):
        task = self.get_object(pk)
        if not can_mutate_task(request.user, task):
            return Response(status=status.HTTP_403_FORBIDDEN)
        serializer = TimeEntryCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        time_entry = serializer.save(task=task, user=request.user)
        record_task_activity(
            task,
            request.user,
            TaskActivityType.TIME_LOGGED,
            {"time_entry_id": time_entry.id, "minutes": time_entry.minutes},
        )
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
            assigned_tasks = Task.objects.filter(current_assignee=user).exclude(status=TaskStatus.DONE)
            rows.append(
                {
                    "user": UserSummarySerializer(user).data,
                    "open_tasks": assigned_tasks.count(),
                    "overdue_tasks": assigned_tasks.filter(due_date__lt=today).count(),
                    "estimated_minutes": int(assigned_tasks.aggregate(total=Sum("estimated_minutes"))["total"] or 0),
                    "actual_minutes": int(
                        TimeEntry.objects.filter(user=user).aggregate(total=Sum("minutes"))["total"] or 0
                    ),
                }
            )
        serializer = WorkloadRowSerializer(rows, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class TimeReportView(APIView):
    permission_classes = (IsManager,)

    def get(self, request):
        queryset = Project.objects.select_related("manager").annotate(
            minutes=Sum("tasks__time_entries__minutes")
        )
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        if start_date:
            queryset = queryset.filter(tasks__time_entries__work_date__gte=start_date)
        if end_date:
            queryset = queryset.filter(tasks__time_entries__work_date__lte=end_date)
        rows = [
            {
                "project": project,
                "minutes": int(project.minutes or 0),
            }
            for project in queryset.distinct()
        ]
        serializer = TimeReportRowSerializer(rows, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class NotificationListView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request):
        queryset = Notification.objects.filter(recipient=request.user).select_related(
            "task__project__manager",
            "task__current_assignee",
            "project__manager",
        )
        unread = parse_bool(request.query_params.get("unread"))
        if unread is True:
            queryset = queryset.filter(read_at__isnull=True)
        serializer = NotificationItemSerializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class NotificationReadView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request, pk: int):
        try:
            notification = Notification.objects.get(pk=pk, recipient=request.user)
        except Notification.DoesNotExist as exc:
            raise Http404 from exc
        mark_notification_read(notification)
        return Response(NotificationItemSerializer(notification).data, status=status.HTTP_200_OK)
