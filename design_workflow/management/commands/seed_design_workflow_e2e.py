import json
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from design_workflow.models import (
    ArtifactApprovalState,
    AttachmentAnnotation,
    ChatMessage,
    ChatThread,
    ChatThreadKind,
    Notification,
    NotificationDigestFrequency,
    NotificationPreference,
    NotificationType,
    Priority,
    Project,
    ProjectStatus,
    SavedView,
    SavedViewVisibility,
    Task,
    TaskArtifactVersion,
    TaskAttachment,
    TaskChecklist,
    TaskChecklistItem,
    TaskReviewState,
    TaskStatus,
    TimeEntry,
)


DEFAULT_MANAGER_EMAIL = "manager.e2e@design-workflow.local"
DEFAULT_DESIGNER_EMAIL = "designer.e2e@design-workflow.local"
DEFAULT_PASSWORD = "DesignWorkflowE2E!2026"
PROJECT_NAME = "E2E Premium Review Studio"


class Command(BaseCommand):
    help = "Seed deterministic Design Workflow data for local Playwright coverage."

    def add_arguments(self, parser):
        parser.add_argument("--manager-email", default=DEFAULT_MANAGER_EMAIL)
        parser.add_argument("--designer-email", default=DEFAULT_DESIGNER_EMAIL)
        parser.add_argument("--password", default=DEFAULT_PASSWORD)
        parser.add_argument(
            "--force",
            action="store_true",
            help="Allow seeding when DEBUG is false.",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError("Refusing to seed E2E data when DEBUG is false. Pass --force to override.")

        payload = seed_design_workflow_e2e(
            manager_email=options["manager_email"],
            designer_email=options["designer_email"],
            password=options["password"],
        )
        self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))


def upsert_user(*, email: str, password: str, first_name: str, last_name: str, role: str, is_staff: bool):
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        email=email,
        defaults={
            "first_name": first_name,
            "last_name": last_name,
            "role": role,
            "is_staff": is_staff,
            "is_active": True,
            "can_view": True,
            "can_print": True,
            "can_create": True,
            "can_edit": True,
            "can_delete": True,
        },
    )
    user.first_name = first_name
    user.last_name = last_name
    user.role = role
    user.is_staff = is_staff
    user.is_active = True
    user.can_view = True
    user.can_print = True
    user.can_create = True
    user.can_edit = True
    user.can_delete = True
    user.set_password(password)
    user.save()
    return user


def seed_design_workflow_e2e(*, manager_email: str, designer_email: str, password: str) -> dict:
    now = timezone.now()
    with transaction.atomic():
        manager = upsert_user(
            email=manager_email,
            password=password,
            first_name="E2E",
            last_name="Manager",
            role="manager",
            is_staff=True,
        )
        designer = upsert_user(
            email=designer_email,
            password=password,
            first_name="E2E",
            last_name="Designer",
            role="designer",
            is_staff=False,
        )

        project, _ = Project.objects.get_or_create(
            name=PROJECT_NAME,
            manager=manager,
            defaults={
                "description": "Seeded data for premium workflow browser coverage.",
                "priority": Priority.HIGH,
                "status": ProjectStatus.ACTIVE,
                "start_date": timezone.localdate() - timedelta(days=5),
                "target_end_date": timezone.localdate() + timedelta(days=12),
            },
        )
        project.description = "Seeded data for premium workflow browser coverage."
        project.priority = Priority.HIGH
        project.status = ProjectStatus.ACTIVE
        project.archived = False
        project.save()

        review_task, _ = Task.objects.get_or_create(
            project=project,
            title="E2E Review Approval Card",
            defaults={
                "description": "Seeded card with artifacts, review state, and annotations.",
                "current_assignee": designer,
                "status": TaskStatus.IN_REVIEW,
                "priority": Priority.HIGH,
                "due_date": timezone.localdate() + timedelta(days=3),
                "estimated_minutes": 480,
                "actual_minutes": 90,
                "review_state": TaskReviewState.NEEDS_REVIEW,
                "review_requested_by": designer,
                "review_requested_at": now - timedelta(hours=3),
                "created_by": manager,
                "updated_by": manager,
            },
        )
        review_task.description = "Seeded card with artifacts, review state, and annotations."
        review_task.current_assignee = designer
        review_task.status = TaskStatus.IN_REVIEW
        review_task.priority = Priority.HIGH
        review_task.review_state = TaskReviewState.NEEDS_REVIEW
        review_task.review_requested_by = designer
        review_task.review_requested_at = now - timedelta(hours=3)
        review_task.review_approved_by = None
        review_task.review_approved_at = None
        review_task.due_date = timezone.localdate() + timedelta(days=3)
        review_task.estimated_minutes = 480
        review_task.updated_by = manager
        review_task.save()

        checklist, _ = TaskChecklist.objects.get_or_create(
            task=review_task,
            title="E2E handoff checklist",
            defaults={"created_by": manager, "sort_order": 0},
        )
        for index, title in enumerate(("Review annotation pins", "Approve latest artifact", "Confirm handoff notes")):
            TaskChecklistItem.objects.get_or_create(
                task=review_task,
                checklist=checklist,
                title=title,
                defaults={"created_by": manager, "sort_order": index, "done": index == 0},
            )

        TimeEntry.objects.get_or_create(
            task=review_task,
            user=designer,
            work_date=timezone.localdate(),
            note="E2E seeded active review time",
            defaults={"minutes": 90},
        )

        attachment = TaskAttachment.objects.filter(task=review_task, name="e2e-material-board.svg").first()
        if not attachment:
            attachment = TaskAttachment(
                task=review_task,
                uploaded_by=designer,
                name="e2e-material-board.svg",
                mime_type="image/svg+xml",
                size=320,
            )
            attachment.file.save(
                "e2e-material-board.svg",
                ContentFile(
                    b'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360">'
                    b'<rect width="640" height="360" fill="#f8fafc"/>'
                    b'<rect x="60" y="60" width="220" height="140" rx="18" fill="#0f766e"/>'
                    b'<rect x="340" y="90" width="220" height="160" rx="18" fill="#d97706"/>'
                    b'<text x="60" y="300" font-size="32" fill="#0f172a">E2E Material Board</text>'
                    b"</svg>"
                ),
                save=True,
            )

        version, _ = TaskArtifactVersion.objects.get_or_create(
            task=review_task,
            version_number=1,
            defaults={
                "attachment": attachment,
                "uploaded_by": designer,
                "notes": "Initial seeded artifact version.",
                "approval_state": ArtifactApprovalState.PENDING,
            },
        )
        version.attachment = attachment
        version.uploaded_by = designer
        version.notes = "Initial seeded artifact version."
        version.approval_state = ArtifactApprovalState.PENDING
        version.approved_by = None
        version.approved_at = None
        version.save()

        annotation, _ = AttachmentAnnotation.objects.get_or_create(
            attachment=attachment,
            version=version,
            body="E2E pin: tighten spacing around logo.",
            defaults={
                "author": manager,
                "x_percent": Decimal("35.00"),
                "y_percent": Decimal("48.00"),
                "resolved": False,
            },
        )
        annotation.author = manager
        annotation.x_percent = Decimal("35.00")
        annotation.y_percent = Decimal("48.00")
        annotation.resolved = False
        annotation.save()

        project_thread, _ = ChatThread.objects.get_or_create(
            kind=ChatThreadKind.PROJECT,
            project=project,
            defaults={"title": f"{PROJECT_NAME} chat"},
        )
        project_thread.title = f"{PROJECT_NAME} chat"
        project_thread.participants.add(manager, designer)
        project_thread.save()

        task_thread, _ = ChatThread.objects.get_or_create(
            kind=ChatThreadKind.TASK,
            task=review_task,
            defaults={"title": "E2E Review Approval Card chat"},
        )
        task_thread.title = "E2E Review Approval Card chat"
        task_thread.participants.add(manager, designer)
        task_thread.save()

        source_message = ChatMessage.objects.filter(
            thread=project_thread,
            sender=manager,
            body__startswith="E2E source message: convert this decision into a task.",
        ).first()
        if not source_message:
            source_message = ChatMessage.objects.create(
                thread=project_thread,
                sender=manager,
                body="E2E source message: convert this decision into a task.",
            )
        source_message.read_by.add(manager)
        source_message.mentions.add(designer)

        source_task, _ = Task.objects.get_or_create(
            project=project,
            title="E2E Task From Chat Source",
            defaults={
                "description": "Seeded task created from a project chat source message.",
                "current_assignee": designer,
                "status": TaskStatus.TODO,
                "priority": Priority.MEDIUM,
                "due_date": timezone.localdate() + timedelta(days=5),
                "estimated_minutes": 240,
                "created_by": manager,
                "updated_by": manager,
                "source_chat_message": source_message,
            },
        )
        source_task.description = "Seeded task created from a project chat source message."
        source_task.current_assignee = designer
        source_task.status = TaskStatus.TODO
        source_task.source_chat_message = source_message
        source_task.updated_by = manager
        source_task.save()
        source_message.body = f"E2E source message: convert this decision into a task. #T{source_task.id}"
        source_message.save(update_fields=["body", "updated_at"])

        SavedView.objects.update_or_create(
            owner=manager,
            name="E2E Review Queue",
            defaults={
                "visibility": SavedViewVisibility.TEAM,
                "filters": {"review_state": TaskReviewState.NEEDS_REVIEW, "project": project.id},
                "sort": {"field": "due_date", "direction": "asc"},
                "is_default": True,
            },
        )

        NotificationPreference.objects.update_or_create(
            user=manager,
            defaults={
                "mentions": True,
                "assignments": True,
                "review_requests": True,
                "due_soon": True,
                "digest_frequency": NotificationDigestFrequency.DAILY,
            },
        )
        review_notification = upsert_seed_notification(
            recipient=manager,
            notification_type=NotificationType.REVIEW_REQUESTED,
            task=review_task,
            project=project,
            payload={"review_state": TaskReviewState.NEEDS_REVIEW, "notes": "Seeded review request"},
        )
        chat_notification = upsert_seed_notification(
            recipient=designer,
            notification_type=NotificationType.CHAT_MESSAGE,
            task=None,
            project=project,
            payload={
                "thread_id": project_thread.id,
                "message_id": source_message.id,
                "title": "E2E source message",
            },
        )

    return {
        "manager_email": manager_email,
        "designer_email": designer_email,
        "password": password,
        "project_id": project.id,
        "review_task_id": review_task.id,
        "source_task_id": source_task.id,
        "source_chat_thread_id": project_thread.id,
        "source_chat_message_id": source_message.id,
        "annotation_id": annotation.id,
        "artifact_version_id": version.id,
        "review_notification_id": review_notification.id,
        "chat_notification_id": chat_notification.id,
    }


def upsert_seed_notification(*, recipient, notification_type: str, task, project, payload: dict) -> Notification:
    queryset = Notification.objects.filter(
        recipient=recipient,
        type=notification_type,
        task=task,
        project=project,
    ).order_by("id")
    notification = queryset.first()
    if notification is None:
        return Notification.objects.create(
            recipient=recipient,
            type=notification_type,
            task=task,
            project=project,
            payload=payload,
        )

    notification.payload = payload
    notification.read_at = None
    notification.snoozed_until = None
    notification.action_taken_at = None
    notification.action_taken_by = None
    notification.save(update_fields=["payload", "read_at", "snoozed_until", "action_taken_at", "action_taken_by"])
    queryset.exclude(pk=notification.pk).delete()
    return notification
