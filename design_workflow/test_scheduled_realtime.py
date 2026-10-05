from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from django.utils import timezone

from .models import (
    ChatMessage, ChatMessageReminder, ChatThread, ChatThreadKind, Notification,
    NotificationDigestFrequency, NotificationPreference, NotificationType, Project, Task, TaskStatus,
)
from .services import create_notification
from .tasks import (
    deliver_due_chat_reminders, generate_due_task_notifications,
    generate_notification_digests, resurface_snoozed_notifications,
)
from .tests import make_designer


pytestmark = pytest.mark.django_db


def shared_task_fixture():
    owner = make_designer("due-owner@example.test")
    collaborator = make_designer("due-collaborator@example.test")
    assignee = make_designer("due-assignee@example.test")
    project = Project.objects.create(name="Scheduled shared work", manager=owner)
    project.collaborators.add(owner, collaborator, assignee)
    task = Task.objects.create(
        project=project, title="Scheduled card", created_by=owner, updated_by=owner,
        current_assignee=assignee, due_date=timezone.localdate() + timedelta(days=2),
    )
    return owner, collaborator, assignee, project, task


def reminder_fixture():
    creator = make_designer("reminder-creator@example.test")
    other = make_designer("reminder-other@example.test")
    thread = ChatThread.objects.create(kind=ChatThreadKind.PRIVATE)
    thread.participants.add(creator, other)
    message = ChatMessage.objects.create(thread=thread, sender=other, body="Remember this")
    reminder = ChatMessageReminder.objects.create(
        message=message, created_by=creator, note="My reminder",
        remind_at=timezone.now() - timedelta(minutes=1),
    )
    return creator, thread, message, reminder


@pytest.mark.parametrize("overdue", [False, True])
def test_due_notifications_reach_each_shared_worker_once(overdue, django_capture_on_commit_callbacks):
    owner, collaborator, assignee, _, task = shared_task_fixture()
    if overdue:
        task.due_date = timezone.localdate() - timedelta(days=1)
        task.save(update_fields=["due_date"])
    with patch("design_workflow.services.get_channel_layer") as layer:
        layer.return_value.group_send = AsyncMock()
        with django_capture_on_commit_callbacks(execute=True):
            assert generate_due_task_notifications() == 3
            assert generate_due_task_notifications() == 0
        assert {call.args[0] for call in layer.return_value.group_send.call_args_list} == {
            f"user_{user.pk}" for user in (owner, collaborator, assignee)
        }
    notices = Notification.objects.all()
    assert set(notices.values_list("recipient_id", flat=True)) == {owner.pk, collaborator.pk, assignee.pk}
    assert set(notices.values_list("type", flat=True)) == {
        NotificationType.TASK_OVERDUE if overdue else NotificationType.TASK_DUE_SOON,
    }


def test_due_notifications_respect_active_members_and_due_preferences():
    owner, collaborator, assignee, _, _ = shared_task_fixture()
    collaborator.is_active = False
    collaborator.save(update_fields=["is_active"])
    NotificationPreference.objects.create(user=assignee, due_soon=False)
    # Disabling digest emails does not disable in-app deadline alerts.
    NotificationPreference.objects.create(user=owner, digest_frequency=NotificationDigestFrequency.OFF)
    assert generate_due_task_notifications() == 1
    assert Notification.objects.get().recipient_id == owner.pk


@pytest.mark.parametrize("ignored", ["task_archive", "project_archive", "done", "no_date", "future"])
def test_due_notifications_ignore_ineligible_cards(ignored):
    _, _, _, project, task = shared_task_fixture()
    if ignored == "task_archive":
        task.archived = True
    elif ignored == "project_archive":
        project.archived = True
        project.save(update_fields=["archived"])
    elif ignored == "done":
        task.status = TaskStatus.DONE
    elif ignored == "no_date":
        task.due_date = None
    else:
        task.due_date = timezone.localdate() + timedelta(days=5)
    task.save()
    assert generate_due_task_notifications() == 0
    assert not Notification.objects.exists()


def test_due_notifications_include_owner_without_assignee():
    owner, collaborator, _, project, task = shared_task_fixture()
    project.collaborators.set([collaborator])
    task.current_assignee = None
    task.save(update_fields=["current_assignee"])
    assert generate_due_task_notifications() == 2
    assert set(Notification.objects.values_list("recipient_id", flat=True)) == {owner.pk, collaborator.pk}


def test_reminder_delivers_once_and_stays_incomplete(django_capture_on_commit_callbacks):
    creator, thread, message, reminder = reminder_fixture()
    NotificationPreference.objects.create(user=creator, mentions=False, due_soon=False, digest_frequency=NotificationDigestFrequency.OFF)
    with patch("design_workflow.services.get_channel_layer") as layer:
        layer.return_value.group_send = AsyncMock()
        with django_capture_on_commit_callbacks(execute=True):
            assert deliver_due_chat_reminders() == 1
            assert deliver_due_chat_reminders() == 0
        publications = [call.args for call in layer.return_value.group_send.call_args_list]
        assert (f"user_{creator.pk}", {
            "type": "receive_group_message",
            "message": {"type": "NOTIFICATION", "event": "new", "notification_id": Notification.objects.get().pk},
        }) in publications
    notice = Notification.objects.get()
    assert notice.recipient_id == creator.pk
    assert notice.type == NotificationType.CHAT_MESSAGE
    assert notice.payload == {
        "kind": "reminder", "reminder_id": reminder.pk, "thread_id": thread.pk,
        "message_id": message.pk, "title": "Rappel de message", "note": "My reminder",
    }
    reminder.refresh_from_db()
    assert reminder.delivered_at is not None
    assert reminder.done_at is None


@pytest.mark.parametrize("ignored", ["deleted", "inactive", "no_access", "done", "future", "no_date", "no_creator"])
def test_reminder_skips_ineligible_delivery(ignored):
    creator, thread, message, reminder = reminder_fixture()
    if ignored == "deleted":
        message.deleted_at = timezone.now()
        message.save(update_fields=["deleted_at"])
    elif ignored == "inactive":
        creator.is_active = False
        creator.save(update_fields=["is_active"])
    elif ignored == "no_access":
        thread.participants.remove(creator)
    elif ignored == "done":
        reminder.done_at = timezone.now()
    elif ignored == "future":
        reminder.remind_at = timezone.now() + timedelta(minutes=10)
    elif ignored == "no_date":
        reminder.remind_at = None
    else:
        reminder.created_by = None
    reminder.save()
    assert deliver_due_chat_reminders() == 0
    assert not Notification.objects.exists()
    reminder.refresh_from_db()
    assert reminder.delivered_at is None


def test_removed_project_collaborator_does_not_receive_chat_reminder():
    creator, thread, _, reminder = reminder_fixture()
    owner = make_designer("reminder-project-owner@example.test")
    project = Project.objects.create(name="Private project conversation", manager=owner)
    thread.kind = ChatThreadKind.PROJECT
    thread.project = project
    thread.save()
    # Historical participants do not retain access to linked project chats.
    assert thread.participants.filter(pk=creator.pk).exists()
    assert deliver_due_chat_reminders() == 0
    assert not Notification.objects.exists()
    reminder.refresh_from_db()
    assert reminder.delivered_at is None


def test_failed_delivery_rolls_back_notification_and_allows_retry(django_capture_on_commit_callbacks):
    _, _, _, reminder = reminder_fixture()

    def fail_after_insert(**kwargs):
        create_notification(**kwargs)
        raise RuntimeError("Delivery transaction failed")

    with patch("design_workflow.services.get_channel_layer") as layer:
        layer.return_value.group_send = AsyncMock()
        with django_capture_on_commit_callbacks(execute=True):
            with patch("design_workflow.tasks.create_notification", side_effect=fail_after_insert):
                with pytest.raises(RuntimeError, match="Delivery transaction failed"):
                    deliver_due_chat_reminders()
        layer.return_value.group_send.assert_not_called()
    assert not Notification.objects.exists()
    reminder.refresh_from_db()
    assert reminder.delivered_at is None
    assert deliver_due_chat_reminders() == 1


def test_snoozed_notice_resurfaces_once_and_publishes_unread_change(django_capture_on_commit_callbacks):
    user = make_designer("snooze-user@example.test")
    notice = Notification.objects.create(
        recipient=user, type=NotificationType.TASK_MENTION,
        read_at=timezone.now(), snoozed_until=timezone.now() - timedelta(minutes=1),
    )
    with patch("design_workflow.services.get_channel_layer") as layer:
        layer.return_value.group_send = AsyncMock()
        with django_capture_on_commit_callbacks(execute=True):
            assert resurface_snoozed_notifications() == 1
            assert resurface_snoozed_notifications() == 0
        layer.return_value.group_send.assert_awaited_once_with(f"user_{user.pk}", {
            "type": "receive_group_message",
            "message": {"type": "NOTIFICATION", "event": "resurfaced", "notification_id": notice.pk},
        })
    notice.refresh_from_db()
    assert notice.snoozed_until is None
    assert notice.read_at is None


@pytest.mark.parametrize("ignored", ["inactive", "future", "not_snoozed"])
def test_snooze_expiry_skips_ineligible_notices(ignored):
    user = make_designer("snooze-skip@example.test")
    snoozed_until = timezone.now() - timedelta(minutes=1)
    if ignored == "inactive":
        user.is_active = False
        user.save(update_fields=["is_active"])
    elif ignored == "future":
        snoozed_until = timezone.now() + timedelta(minutes=10)
    else:
        snoozed_until = None
    notice = Notification.objects.create(recipient=user, type=NotificationType.TASK_MENTION, snoozed_until=snoozed_until)
    assert resurface_snoozed_notifications() == 0
    notice.refresh_from_db()
    assert notice.snoozed_until == snoozed_until


def test_digest_email_delivery_metadata_is_published(django_capture_on_commit_callbacks):
    user = make_designer("digest-live@example.test")
    NotificationPreference.objects.create(user=user, digest_frequency=NotificationDigestFrequency.DAILY)
    Notification.objects.create(recipient=user, type=NotificationType.TASK_MENTION)
    with patch("design_workflow.tasks.send_digest_email", return_value=1), patch("design_workflow.services.get_channel_layer") as layer:
        layer.return_value.group_send = AsyncMock()
        with django_capture_on_commit_callbacks(execute=True):
            assert generate_notification_digests() == 1
        published = [call.args[1]["message"] for call in layer.return_value.group_send.call_args_list]
    digest = Notification.objects.get(type=NotificationType.WORKFLOW_DIGEST)
    assert digest.payload["email_sent"] is True
    assert {"type": "NOTIFICATION", "event": "updated", "notification_id": digest.pk} in published
