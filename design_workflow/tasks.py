from datetime import timedelta

from celery import shared_task
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from .models import (
    ChatMessageReminder,
    Notification,
    NotificationDigestFrequency,
    NotificationPreference,
    NotificationType,
    Task,
    TaskStatus,
)
from .services import (
    broadcast_to_users,
    create_notification,
    notification_exists_for_today,
)


@shared_task
def generate_due_task_notifications():
    today = timezone.localdate()
    due_soon_date = today + timedelta(days=2)
    tasks = Task.objects.filter(
        Q(due_date=due_soon_date) | Q(due_date__lt=today),
        archived=False,
        project__archived=False,
    ).exclude(status=TaskStatus.DONE)
    created_count = 0
    for task_id in tasks.values_list("pk", flat=True).iterator():
        # Serialize overlapping scheduler runs before checking today's notices.
        with transaction.atomic():
            task = (
                tasks.select_for_update(of=("self",))
                .select_related("project")
                .filter(pk=task_id)
                .first()
            )
            if task is None:
                continue
            if task.due_date == due_soon_date:
                notification_type = NotificationType.TASK_DUE_SOON
            elif task.due_date and task.due_date < today:
                notification_type = NotificationType.TASK_OVERDUE
            else:
                continue
            member_ids = {task.project.manager_id, task.current_assignee_id}
            member_ids.update(task.project.collaborators.values_list("pk", flat=True))
            opted_out = NotificationPreference.objects.filter(due_soon=False).values(
                "user_id"
            )
            recipients = (
                get_user_model()
                .objects.filter(pk__in=member_ids, is_active=True)
                .exclude(pk__in=opted_out)
            )
            for recipient in recipients:
                if notification_exists_for_today(
                    recipient=recipient, notification_type=notification_type, task=task
                ):
                    continue
                create_notification(
                    recipient=recipient,
                    notification_type=notification_type,
                    task=task,
                    project=task.project,
                    payload={"due_date": task.due_date.isoformat()},
                )
                created_count += 1
    return created_count


@shared_task
def deliver_due_chat_reminders():
    """Deliver explicit personal reminders once, without marking them done."""
    from .views import broadcast_chat_event, can_access_chat_thread

    now = timezone.now()
    pending = ChatMessageReminder.objects.filter(
        remind_at__lte=now,
        delivered_at__isnull=True,
        done_at__isnull=True,
        created_by__is_active=True,
        message__deleted_at__isnull=True,
    )
    delivered_count = 0
    for reminder_id in pending.values_list("pk", flat=True).iterator():
        with transaction.atomic():
            reminder = (
                pending.select_for_update(of=("self",))
                .select_related(
                    "created_by",
                    "message__thread__project",
                    "message__thread__task__project",
                    "task__project",
                )
                .filter(pk=reminder_id)
                .first()
            )
            if reminder is None:
                continue
            thread = reminder.message.thread
            if not can_access_chat_thread(reminder.created_by, thread):
                continue
            task = reminder.task
            create_notification(
                recipient=reminder.created_by,
                notification_type=NotificationType.CHAT_MESSAGE,
                task=task,
                project=task.project if task else thread.project,
                payload={
                    "kind": "reminder",
                    "reminder_id": reminder.pk,
                    "thread_id": thread.pk,
                    "message_id": reminder.message_id,
                    "title": "Rappel de message",
                    "note": reminder.note,
                },
            )
            reminder.delivered_at = now
            reminder.save(update_fields=["delivered_at", "updated_at"])
            broadcast_chat_event(
                thread,
                {
                    "type": "chat.reminder",
                    "thread_id": thread.pk,
                    "message_id": reminder.message_id,
                    "reminder_id": reminder.pk,
                },
            )
            delivered_count += 1
    return delivered_count


@shared_task
def resurface_snoozed_notifications():
    """Restore a snoozed notice and its live unread badge when its time arrives."""
    pending = Notification.objects.filter(
        snoozed_until__lte=timezone.now(), recipient__is_active=True
    )
    resurfaced_count = 0
    for notification_id in pending.values_list("pk", flat=True).iterator():
        with transaction.atomic():
            notification = (
                pending.select_for_update(of=("self",))
                .filter(pk=notification_id)
                .first()
            )
            if notification is None:
                continue
            notification.snoozed_until = None
            notification.read_at = None
            notification.save(update_fields=["snoozed_until", "read_at"])
            broadcast_to_users(
                [notification.recipient_id],
                {
                    "type": "NOTIFICATION",
                    "event": "resurfaced",
                    "notification_id": notification.pk,
                },
            )
            resurfaced_count += 1
    return resurfaced_count


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
        Notification.objects.filter(
            recipient=user, created_at__gte=start, created_at__lte=end
        )
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
    task_ids = list(
        queryset.filter(task_id__isnull=False)
        .values_list("task_id", flat=True)
        .distinct()[:8]
    )
    project_ids = list(
        queryset.filter(project_id__isnull=False)
        .values_list("project_id", flat=True)
        .distinct()[:8]
    )
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
        lines.extend(
            ["", f"Task references: {', '.join(str(task_id) for task_id in task_ids)}"]
        )
    if project_ids:
        lines.extend(
            [
                "",
                f"Project references: {', '.join(str(project_id) for project_id in project_ids)}",
            ]
        )
    return "\n".join(lines)


def send_digest_email(user, payload: dict) -> int:
    if not user.email:
        return 0
    total = payload.get("total_count", 0)
    subject = f"Design Workflow digest: {total} update{'s' if total != 1 else ''}"
    try:
        return send_mail(
            subject,
            digest_email_body(payload),
            None,
            [user.email],
        )
    except OSError:
        # A delivery failure must not interrupt notification digest generation.
        return 0


@shared_task
def generate_notification_digests(
    frequency: str | None = None, send_email: bool = True
):
    today = timezone.localdate()
    preferences = (
        NotificationPreference.objects.select_related("user")
        .filter(
            user__is_active=True,
        )
        .exclude(digest_frequency=NotificationDigestFrequency.INSTANT)
        .exclude(
            digest_frequency=NotificationDigestFrequency.OFF,
        )
    )
    if frequency:
        preferences = preferences.filter(digest_frequency=frequency)
    created_count = 0
    for preference in preferences:
        digest_frequency = preference.digest_frequency
        if (
            digest_frequency == NotificationDigestFrequency.WEEKLY
            and today.weekday() != 0
        ):
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
            broadcast_to_users(
                [notification.recipient_id],
                {
                    "type": "NOTIFICATION",
                    "event": "updated",
                    "notification_id": notification.pk,
                },
            )
        created_count += 1
    return created_count
