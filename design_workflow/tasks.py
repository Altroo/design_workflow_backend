from datetime import timedelta

from celery import shared_task
from django.core.mail import send_mail
from django.db.models import Count
from django.utils import timezone

from .models import Notification, NotificationDigestFrequency, NotificationPreference, NotificationType, Task, TaskStatus
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


def digest_window_for(frequency: str):
    now = timezone.now()
    if frequency == NotificationDigestFrequency.WEEKLY:
        start = now - timedelta(days=7)
    else:
        start = now - timedelta(days=1)
    return start, now


def digest_payload_for(user, frequency: str) -> dict | None:
    start, end = digest_window_for(frequency)
    queryset = (
        Notification.objects.filter(recipient=user, created_at__gte=start, created_at__lte=end)
        .exclude(type=NotificationType.WORKFLOW_DIGEST)
        .select_related("task", "project")
    )
    total = queryset.count()
    if total == 0:
        return None
    by_type = {
        row["type"]: row["total"]
        for row in queryset.values("type").annotate(total=Count("id")).order_by()
    }
    unread_count = queryset.filter(read_at__isnull=True).count()
    task_ids = list(queryset.filter(task_id__isnull=False).values_list("task_id", flat=True).distinct()[:8])
    project_ids = list(queryset.filter(project_id__isnull=False).values_list("project_id", flat=True).distinct()[:8])
    return {
        "title": "Workflow digest",
        "frequency": frequency,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "total_count": total,
        "unread_count": unread_count,
        "by_type": by_type,
        "task_ids": task_ids,
        "project_ids": project_ids,
    }


def notification_type_label(notification_type: str) -> str:
    try:
        return NotificationType(notification_type).label
    except ValueError:
        return notification_type.replace("_", " ").title()


def digest_email_body(payload: dict) -> str:
    by_type = payload.get("by_type") or {}
    lines = [
        "Design Workflow digest",
        "",
        f"Frequency: {payload.get('frequency', 'digest')}",
        f"Notifications: {payload.get('total_count', 0)}",
        f"Unread: {payload.get('unread_count', 0)}",
        "",
        "Breakdown:",
    ]
    for notification_type, total in sorted(by_type.items()):
        lines.append(f"- {notification_type_label(notification_type)}: {total}")
    task_ids = payload.get("task_ids") or []
    project_ids = payload.get("project_ids") or []
    if task_ids:
        lines.extend(["", f"Task references: {', '.join(str(task_id) for task_id in task_ids)}"])
    if project_ids:
        lines.extend(["", f"Project references: {', '.join(str(project_id) for project_id in project_ids)}"])
    return "\n".join(lines)


def send_digest_email(user, payload: dict) -> int:
    if not user.email:
        return 0
    total = payload.get("total_count", 0)
    subject = f"Design Workflow digest: {total} update{'s' if total != 1 else ''}"
    return send_mail(
        subject,
        digest_email_body(payload),
        None,
        [user.email],
        fail_silently=True,
    )


@shared_task
def generate_notification_digests(frequency: str | None = None, send_email: bool = True):
    today = timezone.localdate()
    preferences = NotificationPreference.objects.select_related("user").filter(
        user__is_active=True,
    ).exclude(digest_frequency=NotificationDigestFrequency.INSTANT).exclude(
        digest_frequency=NotificationDigestFrequency.OFF,
    )
    if frequency:
        preferences = preferences.filter(digest_frequency=frequency)
    created_count = 0
    for preference in preferences:
        digest_frequency = preference.digest_frequency
        if digest_frequency == NotificationDigestFrequency.WEEKLY and today.weekday() != 0:
            continue
        if notification_exists_for_today(
            recipient=preference.user,
            notification_type=NotificationType.WORKFLOW_DIGEST,
        ):
            continue
        payload = digest_payload_for(preference.user, digest_frequency)
        if not payload:
            continue
        notification = create_notification(
            recipient=preference.user,
            notification_type=NotificationType.WORKFLOW_DIGEST,
            payload=payload,
        )
        if send_email and send_digest_email(preference.user, payload):
            notification.payload = {
                **notification.payload,
                "email_sent": True,
                "email_sent_at": timezone.now().isoformat(),
            }
            notification.save(update_fields=["payload"])
        created_count += 1
    return created_count
