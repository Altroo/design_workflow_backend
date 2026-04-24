from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from .models import NotificationType, Task, TaskStatus
from .services import create_notification, notification_exists_for_today


@shared_task
def generate_due_task_notifications():
    today = timezone.localdate()
    due_soon_date = today + timedelta(days=2)
    tasks = Task.objects.select_related("project", "current_assignee").exclude(
        status=TaskStatus.DONE
    )

    for task in tasks:
        if not task.current_assignee_id:
            continue

        if task.due_date == due_soon_date and not notification_exists_for_today(
            recipient=task.current_assignee,
            notification_type=NotificationType.TASK_DUE_SOON,
            task=task,
        ):
            create_notification(
                recipient=task.current_assignee,
                notification_type=NotificationType.TASK_DUE_SOON,
                task=task,
                project=task.project,
                payload={"due_date": task.due_date.isoformat()},
            )

        if task.due_date and task.due_date < today and not notification_exists_for_today(
            recipient=task.current_assignee,
            notification_type=NotificationType.TASK_OVERDUE,
            task=task,
        ):
            create_notification(
                recipient=task.current_assignee,
                notification_type=NotificationType.TASK_OVERDUE,
                task=task,
                project=task.project,
                payload={"due_date": task.due_date.isoformat()},
            )
