from datetime import datetime, time, timedelta

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import Notification, TaskActivity, TaskActivityType, TaskStatus, TimeEntry
from .permissions import can_mutate_task

User = get_user_model()
WORK_DAY_MINUTES = 8 * 60
SATURDAY_WORK_MINUTES = 4 * 60
WORK_WEEK_MINUTES = (WORK_DAY_MINUTES * 5) + SATURDAY_WORK_MINUTES
WORK_SCHEDULE = {
    **{
        weekday: ((time(9), time(13)), (time(14), time(18)))
        for weekday in range(5)
    },
    5: ((time(9), time(13)),),
    6: (),
}


def broadcast_to_users(user_ids: list[int], message: dict) -> None:
    recipients = {user_id for user_id in user_ids if user_id}
    def send():
        channel_layer = get_channel_layer()
        if channel_layer is not None:
            for user_id in recipients:
                async_to_sync(channel_layer.group_send)(
                    f"user_{user_id}",
                    {"type": "receive_group_message", "message": message},
                )
    # A connected client must never refetch uncommitted or rolled-back data.
    transaction.on_commit(send, robust=True)


def broadcast_workflow_event(scope: str, *, recipients=None) -> None:
    message = {"type": "WORKFLOW_EVENT", "scope": scope}
    if recipients is not None:
        broadcast_to_users(recipients, message)
    else:
        broadcast_workspace_message(message)


def broadcast_workspace_message(message: dict) -> None:
    def send():
        channel_layer = get_channel_layer()
        if channel_layer is not None:
            async_to_sync(channel_layer.group_send)("workflow", {"type": "receive_group_message", "message": message})
    transaction.on_commit(send, robust=True)


def broadcast_task_event(task, event_type: str, *, recipients: list[int] | None = None) -> None:
    # All authenticated users may view the board, including read-only viewers
    # and managers who are not card members. Mutation rights remain unchanged.

    message = {
        "type": "TASK_EVENT",
        "event": event_type,
        "task_id": task.id,
        "project_id": task.project_id,
        "status": task.status,
        "assignee_id": task.current_assignee_id,
    }
    broadcast_workspace_message(message)


def record_task_activity(task, actor, action_type: str, metadata: dict | None = None):
    return TaskActivity.objects.create(
        task=task,
        actor=actor,
        action_type=action_type,
        metadata=metadata or {},
    )


def log_automatic_time_entry(
    task,
    *,
    user,
    minutes: int,
    note: str,
    event: str,
):
    if not user or minutes <= 0:
        return None
    time_entry = TimeEntry.objects.create(
        task=task,
        user=user,
        minutes=minutes,
        work_date=timezone.localdate(),
        note=note,
    )
    task.recalculate_actual_minutes()
    record_task_activity(
        task,
        user,
        TaskActivityType.TIME_LOGGED,
        {"time_entry_id": time_entry.id, "minutes": time_entry.minutes, "event": event},
    )
    broadcast_task_event(task, "time_logged", recipients=related_task_user_ids(task))
    return time_entry


def count_working_minutes(start, end) -> int:
    """Count only scheduled studio time between two aware datetimes."""
    local_start = timezone.localtime(start)
    local_end = timezone.localtime(end)
    if local_end <= local_start:
        return 0

    total_minutes = 0
    current_date = local_start.date()
    while current_date <= local_end.date():
        for window_start, window_end in WORK_SCHEDULE[current_date.weekday()]:
            starts_at = datetime.combine(current_date, window_start, tzinfo=local_start.tzinfo)
            ends_at = datetime.combine(current_date, window_end, tzinfo=local_start.tzinfo)
            overlap_start = max(local_start, starts_at)
            overlap_end = min(local_end, ends_at)
            if overlap_end > overlap_start:
                total_minutes += int((overlap_end - overlap_start).total_seconds() // 60)
        current_date += timedelta(days=1)
    return total_minutes


def sync_task_work_session(task, *, user, previous_status: str, next_status: str, event: str):
    from .time_tracking import reconcile_task_work_sessions
    return reconcile_task_work_sessions(task, event=event)


def create_notification(
    *,
    recipient,
    notification_type: str,
    task=None,
    project=None,
    payload: dict | None = None,
):
    notification = Notification.objects.create(
        recipient=recipient,
        type=notification_type,
        task=task,
        project=project,
        payload=payload or {},
    )
    broadcast_to_users(
        [recipient.id],
        {
            "type": "NOTIFICATION",
            "event": "new",
            "notification_id": notification.id,
        },
    )
    return notification


def mark_notification_read(notification: Notification) -> Notification:
    if notification.read_at is None or notification.snoozed_until is not None:
        notification.read_at = timezone.now()
        notification.snoozed_until = None
        notification.save(update_fields=["read_at", "snoozed_until"])
        broadcast_to_users(
            [notification.recipient_id],
            {
                "type": "NOTIFICATION",
                "event": "read",
                "notification_id": notification.id,
            },
        )
    return notification


def notification_exists_for_today(*, recipient, notification_type: str, task=None) -> bool:
    today = timezone.localdate()
    query = Notification.objects.filter(
        recipient=recipient,
        type=notification_type,
        created_at__date=today,
    )
    if task is not None:
        query = query.filter(task=task)
    return query.exists()


def related_task_user_ids(task) -> list[int]:
    ids = [task.project.manager_id]
    ids.extend(member.id for member in task.project.collaborators.all() if member.is_active)
    if task.current_assignee_id:
        ids.append(task.current_assignee_id)
    commenter_ids = task.comments.values_list("author_id", flat=True)
    time_logger_ids = task.time_entries.values_list("user_id", flat=True)
    ids.extend(commenter_ids)
    ids.extend(time_logger_ids)
    return [
        member.id for member in User.objects.filter(id__in=set(ids), is_active=True)
        if can_mutate_task(member, task)
    ]
