from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from .models import Notification, TaskActivity, TaskActivityType, TimeEntry

User = get_user_model()


def broadcast_to_users(user_ids: list[int], message: dict) -> None:
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    for user_id in {user_id for user_id in user_ids if user_id}:
        async_to_sync(channel_layer.group_send)(
            f"user_{user_id}",
            {"type": "receive_group_message", "message": message},
        )


def broadcast_task_event(task, event_type: str, *, recipients: list[int] | None = None) -> None:
    if recipients is None:
        recipients = list(
            User.objects.filter(is_active=True).values_list("id", flat=True)
        )

    message = {
        "type": "TASK_EVENT",
        "event": event_type,
        "task_id": task.id,
        "project_id": task.project_id,
        "status": task.status,
        "assignee_id": task.current_assignee_id,
    }
    broadcast_to_users(recipients, message)


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
    if notification.read_at is None:
        notification.read_at = timezone.now()
        notification.save(update_fields=["read_at"])
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
    if task.current_assignee_id:
        ids.append(task.current_assignee_id)
    commenter_ids = task.comments.values_list("author_id", flat=True)
    time_logger_ids = task.time_entries.values_list("user_id", flat=True)
    ids.extend(commenter_ids)
    ids.extend(time_logger_ids)
    return [user_id for user_id in set(ids) if user_id]

