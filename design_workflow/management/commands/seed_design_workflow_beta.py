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
    ChatMessageAttachment,
    ChatMessageReaction,
    ChatMessageReminder,
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
    SavedViewDensity,
    SavedViewVisibility,
    Task,
    TaskActivity,
    TaskActivityType,
    TaskArtifactVersion,
    TaskAttachment,
    TaskChecklist,
    TaskChecklistItem,
    TaskComment,
    TaskLabel,
    TaskReviewState,
    TaskStatus,
    TimeEntry,
)


DEFAULT_OWNER_EMAIL = "service-it@casadilusso.ma"
DEFAULT_PASSWORD = "DesignWorkflowBeta!2026"


class Command(BaseCommand):
    help = "Seed realistic Design Workflow beta data without clearing existing records."

    def add_arguments(self, parser):
        parser.add_argument("--owner-email", default=DEFAULT_OWNER_EMAIL)
        parser.add_argument("--password", default=DEFAULT_PASSWORD)
        parser.add_argument(
            "--force",
            action="store_true",
            help="Allow seeding when DEBUG is false.",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError("Refusing to seed beta data when DEBUG is false. Pass --force to override.")

        payload = seed_design_workflow_beta(
            owner_email=options["owner_email"],
            password=options["password"],
        )
        self.stdout.write(json.dumps(payload, indent=2, sort_keys=True, default=str))


def seed_design_workflow_beta(*, owner_email: str, password: str) -> dict:
    today = timezone.localdate()
    now = timezone.now()

    with transaction.atomic():
        users = seed_users(owner_email=owner_email, password=password)
        owner = users["owner"]

        labels = seed_labels()
        projects = seed_projects(users=users, today=today)
        tasks = seed_tasks(projects=projects, users=users, labels=labels, today=today, now=now)

        seed_checklists(tasks=tasks, users=users, now=now)
        seed_time_entries(tasks=tasks, users=users, today=today)
        seed_task_comments_and_activity(tasks=tasks, users=users)
        seed_artifacts(tasks=tasks, users=users, now=now)
        threads, messages = seed_chat(projects=projects, tasks=tasks, users=users, now=now)
        seed_message_to_task_source(tasks=tasks, messages=messages, users=users)
        seed_saved_views(owner=owner, projects=projects)
        seed_notifications(owner=owner, projects=projects, tasks=tasks, messages=messages, now=now)
        seed_notification_preferences(owner=owner)

    return {
        "owner_email": owner_email,
        "users": len(users),
        "projects": len(projects),
        "tasks": len(tasks),
        "labels": len(labels),
        "chat_threads": len(threads),
        "chat_messages": len(messages),
        "notifications": Notification.objects.filter(recipient=owner).count(),
        "saved_views": SavedView.objects.filter(owner=owner).count(),
    }


def seed_users(*, owner_email: str, password: str) -> dict:
    return {
        "owner": ensure_user(
            email=owner_email,
            password=password,
            first_name="Service",
            last_name="IT",
            role="manager",
            is_staff=True,
            reset_password=False,
        ),
        "maya": ensure_user(
            email="maya.idrissi@design-workflow.local",
            password=password,
            first_name="Maya",
            last_name="Idrissi",
            role="manager",
            is_staff=True,
            gender="F",
            reset_password=True,
        ),
        "omar": ensure_user(
            email="omar.benali@design-workflow.local",
            password=password,
            first_name="Omar",
            last_name="Benali",
            role="designer",
            is_staff=False,
            gender="H",
            reset_password=True,
        ),
        "lina": ensure_user(
            email="lina.elmansouri@design-workflow.local",
            password=password,
            first_name="Lina",
            last_name="El Mansouri",
            role="designer",
            is_staff=False,
            gender="F",
            reset_password=True,
        ),
        "nadia": ensure_user(
            email="nadia.qa@design-workflow.local",
            password=password,
            first_name="Nadia",
            last_name="Ait QA",
            role="designer",
            is_staff=False,
            gender="F",
            reset_password=True,
        ),
    }


def ensure_user(
    *,
    email: str,
    password: str,
    first_name: str,
    last_name: str,
    role: str,
    is_staff: bool,
    reset_password: bool,
    gender: str = "",
):
    User = get_user_model()
    user, created = User.objects.get_or_create(
        email=email,
        defaults={
            "first_name": first_name,
            "last_name": last_name,
            "role": role,
            "is_staff": is_staff,
            "is_active": True,
            "gender": gender,
            "can_view": True,
            "can_print": True,
            "can_create": True,
            "can_edit": True,
            "can_delete": True,
        },
    )

    if created or email != DEFAULT_OWNER_EMAIL:
        user.first_name = first_name
        user.last_name = last_name
        user.gender = gender
    if created or reset_password:
        user.set_password(password)

    user.role = role
    user.is_staff = is_staff
    user.is_active = True
    user.can_view = True
    user.can_print = True
    user.can_create = True
    user.can_edit = True
    user.can_delete = True
    user.save()
    return user


def seed_labels() -> dict:
    specs = {
        "Beta": "#2563eb",
        "Design review": "#7c3aed",
        "Blocked": "#dc2626",
        "Handoff": "#0f766e",
        "UX copy": "#d97706",
        "QA": "#475569",
    }
    labels = {}
    for name, color in specs.items():
        label, _ = TaskLabel.objects.update_or_create(name=name, defaults={"color": color})
        labels[name] = label
    return labels


def seed_projects(*, users: dict, today) -> dict:
    specs = {
        "beta_system": {
            "name": "Beta Launch Design System",
            "description": "Workspace-wide polish for the first beta group: navigation, cards, responsive QA, and handoff states.",
            "manager": users["owner"],
            "priority": Priority.URGENT,
            "status": ProjectStatus.ACTIVE,
            "start_delta": -14,
            "end_delta": 10,
        },
        "booking_flow": {
            "name": "Casa Di Lusso Booking Flow",
            "description": "Guest booking flow redesign with review approvals, annotations, and launch evidence.",
            "manager": users["maya"],
            "priority": Priority.HIGH,
            "status": ProjectStatus.ACTIVE,
            "start_delta": -7,
            "end_delta": 18,
        },
        "sales_collateral": {
            "name": "Sales Collateral Refresh",
            "description": "New pitch and export materials waiting on final brand direction.",
            "manager": users["owner"],
            "priority": Priority.MEDIUM,
            "status": ProjectStatus.ON_HOLD,
            "start_delta": -21,
            "end_delta": 28,
        },
        "qa_reference": {
            "name": "Archive QA Reference",
            "description": "Completed reference project used to verify done lanes, history, and reporting totals.",
            "manager": users["maya"],
            "priority": Priority.LOW,
            "status": ProjectStatus.COMPLETED,
            "start_delta": -35,
            "end_delta": -5,
        },
    }

    projects = {}
    for key, spec in specs.items():
        project, _ = Project.objects.get_or_create(
            name=spec["name"],
            defaults={"manager": spec["manager"]},
        )
        project.manager = spec["manager"]
        project.description = spec["description"]
        project.priority = spec["priority"]
        project.status = spec["status"]
        project.start_date = today + timedelta(days=spec["start_delta"])
        project.target_end_date = today + timedelta(days=spec["end_delta"])
        project.archived = False
        project.archived_at = None
        project.save()
        projects[key] = project
    return projects


def seed_tasks(*, projects: dict, users: dict, labels: dict, today, now) -> dict:
    specs = [
        {
            "key": "responsive_sweep",
            "project": "beta_system",
            "title": "Run responsive design sweep",
            "description": "Verify desktop, tablet, and mobile across dashboard, board, reports, team, chat, and notifications.",
            "assignee": "lina",
            "status": TaskStatus.IN_PROGRESS,
            "priority": Priority.URGENT,
            "due_delta": 1,
            "estimate": 420,
            "labels": ["Beta", "QA"],
            "sort_order": 10,
            "work_started_delta_hours": 7,
        },
        {
            "key": "kanban_search",
            "project": "beta_system",
            "title": "Make board search discoverable",
            "description": "Improve the Recherche control and verify saved filters, table mode, and archived toggles.",
            "assignee": "omar",
            "status": TaskStatus.TODO,
            "priority": Priority.HIGH,
            "due_delta": 4,
            "estimate": 240,
            "labels": ["Beta", "UX copy"],
            "sort_order": 20,
        },
        {
            "key": "review_mobile",
            "project": "beta_system",
            "title": "Mobile Kanban interaction polish",
            "description": "Bottom-sheet detail flow, horizontal lanes, and touch targets for beta testers.",
            "assignee": "lina",
            "status": TaskStatus.IN_REVIEW,
            "priority": Priority.HIGH,
            "due_delta": 2,
            "estimate": 360,
            "labels": ["Design review", "Beta"],
            "sort_order": 30,
            "review_state": TaskReviewState.NEEDS_REVIEW,
            "review_requested_by": "lina",
            "review_requested_delta_hours": 4,
        },
        {
            "key": "payment_icons",
            "project": "booking_flow",
            "title": "Payment icon set approval",
            "description": "Legal and brand sign-off needed before the payment screen can be shipped.",
            "assignee": "nadia",
            "status": TaskStatus.BLOCKED,
            "priority": Priority.URGENT,
            "due_delta": -2,
            "estimate": 180,
            "labels": ["Blocked", "Design review"],
            "sort_order": 40,
            "blocked_reason": "Waiting for final provider icon usage approval.",
            "review_state": TaskReviewState.CHANGES_REQUESTED,
        },
        {
            "key": "booking_empty_states",
            "project": "booking_flow",
            "title": "Booking empty-state illustrations",
            "description": "Prepare empty states for no rooms, no invoices, and failed availability lookups.",
            "assignee": "omar",
            "status": TaskStatus.BACKLOG,
            "priority": Priority.MEDIUM,
            "due_delta": 11,
            "estimate": 300,
            "labels": ["UX copy"],
            "sort_order": 50,
        },
        {
            "key": "booking_review",
            "project": "booking_flow",
            "title": "Guest checkout review handoff",
            "description": "Final review bundle for guest checkout before beta enablement.",
            "assignee": "maya",
            "status": TaskStatus.IN_REVIEW,
            "priority": Priority.HIGH,
            "due_delta": 3,
            "estimate": 540,
            "labels": ["Design review", "Handoff"],
            "sort_order": 60,
            "review_state": TaskReviewState.NEEDS_REVIEW,
            "review_requested_by": "maya",
            "review_requested_delta_hours": 9,
        },
        {
            "key": "launch_audit",
            "project": "beta_system",
            "title": "Initial workspace audit",
            "description": "Baseline audit completed before populating beta data.",
            "assignee": "nadia",
            "status": TaskStatus.DONE,
            "priority": Priority.MEDIUM,
            "due_delta": -4,
            "estimate": 180,
            "labels": ["QA"],
            "sort_order": 70,
            "review_state": TaskReviewState.APPROVED,
            "approved_by": "owner",
            "approved_delta_days": -3,
        },
        {
            "key": "handoff_deck",
            "project": "sales_collateral",
            "title": "Build French handoff deck",
            "description": "Finalize localized handoff deck for client-facing beta reviewers.",
            "assignee": "lina",
            "status": TaskStatus.TODO,
            "priority": Priority.MEDIUM,
            "due_delta": 6,
            "estimate": 420,
            "labels": ["Handoff", "UX copy"],
            "sort_order": 80,
        },
        {
            "key": "sales_export",
            "project": "sales_collateral",
            "title": "Export-ready pricing one pager",
            "description": "Create a PDF-safe one pager for sales review with compact metrics.",
            "assignee": "omar",
            "status": TaskStatus.IN_PROGRESS,
            "priority": Priority.MEDIUM,
            "due_delta": 8,
            "estimate": 360,
            "labels": ["Handoff"],
            "sort_order": 90,
            "work_started_delta_hours": 20,
        },
        {
            "key": "qa_archive",
            "project": "qa_reference",
            "title": "QA archive sign-off",
            "description": "Confirm completed reference data remains readable in reports and table view.",
            "assignee": "nadia",
            "status": TaskStatus.DONE,
            "priority": Priority.LOW,
            "due_delta": -8,
            "estimate": 120,
            "labels": ["QA"],
            "sort_order": 100,
            "review_state": TaskReviewState.APPROVED,
            "approved_by": "maya",
            "approved_delta_days": -7,
        },
        {
            "key": "source_task",
            "project": "beta_system",
            "title": "Decision from chat: keep compact sidebar",
            "description": "Task created from a pinned project chat decision.",
            "assignee": "omar",
            "status": TaskStatus.TODO,
            "priority": Priority.HIGH,
            "due_delta": 5,
            "estimate": 210,
            "labels": ["Beta"],
            "sort_order": 110,
        },
        {
            "key": "accessibility",
            "project": "beta_system",
            "title": "Keyboard and contrast verification",
            "description": "Pass through keyboard focus, status colors, avatars, and notification actions.",
            "assignee": "maya",
            "status": TaskStatus.BACKLOG,
            "priority": Priority.HIGH,
            "due_delta": 9,
            "estimate": 300,
            "labels": ["QA", "Beta"],
            "sort_order": 120,
        },
    ]

    tasks = {}
    for spec in specs:
        assignee = users[spec["assignee"]]
        creator = users["owner"]
        task, _ = Task.objects.get_or_create(
            project=projects[spec["project"]],
            title=spec["title"],
            defaults={"created_by": creator, "updated_by": creator},
        )
        task.description = spec["description"]
        task.current_assignee = assignee
        task.status = spec["status"]
        task.priority = spec["priority"]
        task.due_date = today + timedelta(days=spec["due_delta"])
        task.estimated_minutes = spec["estimate"]
        task.blocked_reason = spec.get("blocked_reason", "")
        task.sort_order = spec["sort_order"]
        task.archived = False
        task.archived_at = None
        task.updated_by = creator
        task.review_state = spec.get("review_state", TaskReviewState.NOT_SUBMITTED)
        task.review_requested_by = None
        task.review_requested_at = None
        task.review_approved_by = None
        task.review_approved_at = None
        task.work_started_at = None

        if "review_requested_by" in spec:
            task.review_requested_by = users[spec["review_requested_by"]]
            task.review_requested_at = now - timedelta(hours=spec["review_requested_delta_hours"])
        if "approved_by" in spec:
            task.review_approved_by = users[spec["approved_by"]]
            task.review_approved_at = now + timedelta(days=spec["approved_delta_days"])
        if "work_started_delta_hours" in spec:
            task.work_started_at = now - timedelta(hours=spec["work_started_delta_hours"])

        task.is_completed = spec["status"] == TaskStatus.DONE
        task.completed_at = now - timedelta(days=2) if task.is_completed else None
        task.save()
        task.labels.set([labels[name] for name in spec["labels"]])
        tasks[spec["key"]] = task
    return tasks


def seed_checklists(*, tasks: dict, users: dict, now):
    plans = {
        "review_mobile": (
            "Review handoff checklist",
            (
                ("Attach latest mobile capture", True, "lina"),
                ("Resolve footer spacing annotation", False, None),
                ("Confirm tablet board lanes", False, None),
            ),
        ),
        "payment_icons": (
            "Blocked approval checklist",
            (
                ("Collect provider usage rules", True, "nadia"),
                ("Replace unapproved icon mark", False, None),
                ("Request final manager approval", False, None),
            ),
        ),
        "booking_review": (
            "Final delivery checklist",
            (
                ("Package Figma review frame", True, "maya"),
                ("Add annotation summary", True, "maya"),
                ("Export final handoff assets", False, None),
            ),
        ),
        "responsive_sweep": (
            "Responsive QA checklist",
            (
                ("Desktop screenshots", True, "lina"),
                ("Tablet screenshots", True, "lina"),
                ("Mobile screenshots", False, None),
            ),
        ),
    }

    for task_key, (title, items) in plans.items():
        checklist, _ = TaskChecklist.objects.update_or_create(
            task=tasks[task_key],
            title=title,
            defaults={"created_by": users["owner"], "sort_order": 0},
        )
        for index, (item_title, done, completed_by_key) in enumerate(items):
            completed_by = users[completed_by_key] if completed_by_key else None
            TaskChecklistItem.objects.update_or_create(
                task=tasks[task_key],
                checklist=checklist,
                title=item_title,
                defaults={
                    "done": done,
                    "sort_order": index,
                    "created_by": users["owner"],
                    "completed_by": completed_by,
                    "completed_at": now - timedelta(hours=2 + index) if completed_by else None,
                },
            )


def seed_time_entries(*, tasks: dict, users: dict, today):
    entries = (
        ("responsive_sweep", "lina", 150, 0, "Desktop and tablet visual QA"),
        ("responsive_sweep", "owner", 45, 0, "Review notes and acceptance pass"),
        ("review_mobile", "lina", 210, -1, "Touch interaction polish"),
        ("payment_icons", "nadia", 90, -2, "Approval follow-up"),
        ("booking_review", "maya", 240, 0, "Review bundle preparation"),
        ("launch_audit", "nadia", 160, -4, "Initial workspace audit"),
        ("handoff_deck", "lina", 75, 0, "French slide copy"),
        ("sales_export", "omar", 190, -1, "Export layout refinement"),
        ("qa_archive", "nadia", 120, -8, "Reference sign-off"),
        ("source_task", "omar", 60, 0, "Decision task setup"),
    )

    for task_key, user_key, minutes, day_delta, note in entries:
        TimeEntry.objects.update_or_create(
            task=tasks[task_key],
            user=users[user_key],
            work_date=today + timedelta(days=day_delta),
            note=note,
            defaults={"minutes": minutes},
        )


def seed_task_comments_and_activity(*, tasks: dict, users: dict):
    comments = (
        ("review_mobile", "maya", "Please keep the mobile lanes swipeable but make the active task drawer easier to close."),
        ("review_mobile", "lina", "Added a second capture and left unresolved pins for footer spacing."),
        ("payment_icons", "owner", "This stays blocked until the provider confirms usage rights."),
        ("booking_review", "nadia", "QA pass should include the annotation overlay and approved version badge."),
        ("responsive_sweep", "owner", "Run the populated-data pass before any beta access is shared."),
    )
    for task_key, user_key, body in comments:
        TaskComment.objects.get_or_create(task=tasks[task_key], author=users[user_key], body=body)

    activities = (
        ("review_mobile", TaskActivityType.REVIEW_UPDATED, "Review requested by design"),
        ("payment_icons", TaskActivityType.STATUS_CHANGED, "Moved to blocked pending approval"),
        ("booking_review", TaskActivityType.ARTIFACT_VERSION_ADDED, "New artifact version uploaded"),
        ("source_task", TaskActivityType.CREATED, "Created from chat decision"),
    )
    for task_key, action_type, summary in activities:
        activity = TaskActivity.objects.filter(
            task=tasks[task_key],
            action_type=action_type,
            metadata__seed_key=f"beta:{task_key}:{action_type}",
        ).first()
        metadata = {"seed_key": f"beta:{task_key}:{action_type}", "summary": summary}
        if activity:
            activity.actor = users["owner"]
            activity.metadata = metadata
            activity.save(update_fields=["actor", "metadata"])
        else:
            TaskActivity.objects.create(
                task=tasks[task_key],
                actor=users["owner"],
                action_type=action_type,
                metadata=metadata,
            )


def seed_artifacts(*, tasks: dict, users: dict, now):
    artifact_specs = (
        {
            "task": "review_mobile",
            "file": "beta-mobile-review.svg",
            "title": "Beta mobile review",
            "fill": "#dbeafe",
            "version": 1,
            "state": ArtifactApprovalState.PENDING,
            "notes": "Mobile board polish ready for manager review.",
            "annotation": "Footer controls need 8px more breathing room on mobile.",
            "x": "74.00",
            "y": "82.00",
            "resolved": False,
        },
        {
            "task": "booking_review",
            "file": "booking-checkout-handoff.svg",
            "title": "Checkout handoff",
            "fill": "#dcfce7",
            "version": 2,
            "state": ArtifactApprovalState.APPROVED,
            "notes": "Approved handoff for the checkout beta.",
            "annotation": "Approved after replacing the payment status badge.",
            "x": "42.00",
            "y": "35.00",
            "resolved": True,
            "approved_by": "owner",
        },
        {
            "task": "payment_icons",
            "file": "payment-icon-set.svg",
            "title": "Payment icon set",
            "fill": "#fee2e2",
            "version": 1,
            "state": ArtifactApprovalState.CHANGES_REQUESTED,
            "notes": "Changes requested because one provider mark is not approved.",
            "annotation": "Replace this provider icon before approval.",
            "x": "58.00",
            "y": "44.00",
            "resolved": False,
        },
    )

    for spec in artifact_specs:
        task = tasks[spec["task"]]
        attachment = ensure_svg_attachment(
            task=task,
            uploader=task.current_assignee,
            file_name=spec["file"],
            title=spec["title"],
            fill=spec["fill"],
        )
        version, _ = TaskArtifactVersion.objects.update_or_create(
            task=task,
            version_number=spec["version"],
            defaults={
                "attachment": attachment,
                "uploaded_by": task.current_assignee,
                "notes": spec["notes"],
                "approval_state": spec["state"],
                "approved_by": users.get(spec.get("approved_by", "")),
                "approved_at": now - timedelta(days=1) if spec["state"] == ArtifactApprovalState.APPROVED else None,
            },
        )
        annotation, _ = AttachmentAnnotation.objects.update_or_create(
            attachment=attachment,
            version=version,
            body=spec["annotation"],
            defaults={
                "author": users["owner"],
                "x_percent": Decimal(spec["x"]),
                "y_percent": Decimal(spec["y"]),
                "resolved": spec["resolved"],
                "resolved_by": users["owner"] if spec["resolved"] else None,
                "resolved_at": now - timedelta(hours=12) if spec["resolved"] else None,
            },
        )
        annotation.save()


def ensure_svg_attachment(*, task, uploader, file_name: str, title: str, fill: str):
    content = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="960" height="540">'
        f'<rect width="960" height="540" fill="{fill}"/>'
        f'<rect x="72" y="72" width="816" height="396" rx="32" fill="#ffffff" stroke="#cbd5e1" stroke-width="3"/>'
        f'<rect x="112" y="122" width="260" height="64" rx="18" fill="#020617"/>'
        f'<rect x="112" y="218" width="680" height="36" rx="12" fill="#e2e8f0"/>'
        f'<rect x="112" y="286" width="520" height="36" rx="12" fill="#cbd5e1"/>'
        f'<text x="112" y="400" font-family="Arial" font-size="42" font-weight="700" fill="#020617">{title}</text>'
        f"</svg>"
    ).encode("utf-8")
    attachment = TaskAttachment.objects.filter(task=task, name=file_name).first()
    if not attachment:
        attachment = TaskAttachment(task=task, uploaded_by=uploader, name=file_name)
        attachment.file.save(file_name, ContentFile(content), save=False)
    elif not attachment.file:
        attachment.file.save(file_name, ContentFile(content), save=False)
    attachment.uploaded_by = uploader
    attachment.mime_type = "image/svg+xml"
    attachment.size = len(content)
    attachment.save()
    return attachment


def seed_chat(*, projects: dict, tasks: dict, users: dict, now):
    public_thread = get_or_create_public_thread(users=users)
    beta_thread = get_or_create_project_thread(projects["beta_system"], users=users)
    booking_thread = get_or_create_project_thread(projects["booking_flow"], users=users)
    review_thread = get_or_create_task_thread(tasks["review_mobile"], users=users)
    direct_thread = get_or_create_direct_thread(users=users)

    messages = {
        "public_intro": ensure_message(
            thread=public_thread,
            sender=users["owner"],
            body="Beta workspace is populated for the full visual review pass.",
            created_at=now - timedelta(hours=28),
            read_by=(users["owner"], users["maya"]),
        ),
        "public_qa": ensure_message(
            thread=public_thread,
            sender=users["lina"],
            body="I will leave the screenshots and notes in the review tasks instead of clearing anything.",
            created_at=now - timedelta(hours=25),
            mentions=(users["owner"],),
            read_by=(users["lina"],),
        ),
        "beta_decision": ensure_message(
            thread=beta_thread,
            sender=users["maya"],
            body="Decision: keep the compact sidebar behavior, but every dashboard card must use one spacing rhythm.",
            created_at=now - timedelta(hours=22),
            is_decision=True,
            decision_by=users["owner"],
            read_by=(users["maya"], users["owner"]),
        ),
        "beta_source": ensure_message(
            thread=beta_thread,
            sender=users["owner"],
            body="Source note for task creation: compact sidebar behavior needs a tracked card.",
            created_at=now - timedelta(hours=21),
            mentions=(users["omar"],),
            read_by=(users["owner"],),
        ),
        "booking_review": ensure_message(
            thread=booking_thread,
            sender=users["nadia"],
            body="Checkout handoff has one approved artifact and one unresolved QA question.",
            created_at=now - timedelta(hours=12),
            read_by=(users["nadia"], users["maya"]),
        ),
        "review_pin": ensure_message(
            thread=review_thread,
            sender=users["lina"],
            body="Latest mobile capture is attached. The only open point is the footer control spacing.",
            created_at=now - timedelta(hours=6),
            mentions=(users["owner"], users["maya"]),
            read_by=(users["lina"],),
        ),
        "direct_check": ensure_message(
            thread=direct_thread,
            sender=users["owner"],
            body="Please verify the online badge is green and the offline badge is neutral gray.",
            created_at=now - timedelta(hours=4),
            mentions=(users["maya"],),
            read_by=(users["owner"],),
        ),
    }

    ensure_chat_attachment(messages["review_pin"])
    ChatMessageReaction.objects.get_or_create(message=messages["beta_decision"], user=users["owner"], emoji="ok")
    ensure_reminder(
        message=messages["review_pin"],
        task=tasks["review_mobile"],
        created_by=users["owner"],
        remind_at=now + timedelta(hours=18),
        note="Review mobile footer spacing before beta access.",
    )

    threads = (public_thread, beta_thread, booking_thread, review_thread, direct_thread)
    for thread in threads:
        last_message = thread.messages.order_by("-created_at").first()
        if last_message:
            thread.updated_at = last_message.created_at
            thread.save(update_fields=["updated_at"])
    return threads, messages


def get_or_create_public_thread(*, users: dict):
    thread = ChatThread.objects.filter(
        kind=ChatThreadKind.PUBLIC,
        title="Canal public",
        project__isnull=True,
        task__isnull=True,
    ).first()
    if not thread:
        thread = ChatThread.objects.create(kind=ChatThreadKind.PUBLIC, title="Canal public")
    thread.participants.add(*users.values())
    return thread


def get_or_create_project_thread(project, *, users: dict):
    thread, _ = ChatThread.objects.get_or_create(
        kind=ChatThreadKind.PROJECT,
        project=project,
        defaults={"title": project.name},
    )
    thread.title = project.name
    thread.participants.add(*users.values())
    thread.save()
    return thread


def get_or_create_task_thread(task, *, users: dict):
    thread, _ = ChatThread.objects.get_or_create(
        kind=ChatThreadKind.TASK,
        task=task,
        defaults={"title": task.title},
    )
    thread.title = task.title
    thread.participants.add(*users.values())
    thread.save()
    return thread


def get_or_create_direct_thread(*, users: dict):
    thread = ChatThread.objects.filter(kind=ChatThreadKind.PRIVATE, title="Service IT / Maya").first()
    if not thread:
        thread = ChatThread.objects.create(kind=ChatThreadKind.PRIVATE, title="Service IT / Maya")
    thread.participants.add(users["owner"], users["maya"])
    return thread


def ensure_message(
    *,
    thread,
    sender,
    body: str,
    created_at,
    mentions=(),
    read_by=(),
    is_decision=False,
    decision_by=None,
):
    message = ChatMessage.objects.filter(thread=thread, sender=sender, body=body).first()
    if not message:
        message = ChatMessage.objects.create(thread=thread, sender=sender, body=body)
    message.body = body
    message.decision_by = decision_by if is_decision else None
    message.decision_at = created_at + timedelta(minutes=10) if is_decision else None
    message.save()
    message.mentions.set(mentions)
    message.read_by.add(*read_by)
    ChatMessage.objects.filter(pk=message.pk).update(created_at=created_at, updated_at=created_at)
    message.refresh_from_db()
    return message


def ensure_chat_attachment(message):
    file_name = "chat-mobile-spacing-note.svg"
    if ChatMessageAttachment.objects.filter(message=message, name=file_name).exists():
        return
    content = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="480" height="220">'
        b'<rect width="480" height="220" fill="#f8fafc"/>'
        b'<rect x="40" y="40" width="400" height="140" rx="24" fill="#e2e8f0"/>'
        b'<text x="58" y="118" font-size="26" fill="#020617">Footer spacing note</text>'
        b"</svg>"
    )
    attachment = ChatMessageAttachment(message=message, name=file_name, mime_type="image/svg+xml", size=len(content))
    attachment.file.save(file_name, ContentFile(content), save=True)


def ensure_reminder(*, message, task, created_by, remind_at, note: str):
    reminder = ChatMessageReminder.objects.filter(message=message, task=task, created_by=created_by, note=note).first()
    if reminder:
        reminder.remind_at = remind_at
        reminder.done_at = None
        reminder.save(update_fields=["remind_at", "done_at", "updated_at"])
    else:
        ChatMessageReminder.objects.create(
            message=message,
            task=task,
            created_by=created_by,
            remind_at=remind_at,
            note=note,
        )


def seed_message_to_task_source(*, tasks: dict, messages: dict, users: dict):
    task = tasks["source_task"]
    task.source_chat_message = messages["beta_source"]
    task.updated_by = users["owner"]
    task.save(update_fields=["source_chat_message", "updated_by", "updated_at"])


def seed_saved_views(*, owner, projects: dict):
    SavedView.objects.filter(owner=owner, is_default=True).update(is_default=False)
    SavedView.objects.update_or_create(
        owner=owner,
        name="Beta review queue",
        defaults={
            "visibility": SavedViewVisibility.TEAM,
            "filters": {"review_state": TaskReviewState.NEEDS_REVIEW, "archived": False},
            "sort": {"field": "due_date", "direction": "asc"},
            "density": SavedViewDensity.COMFORTABLE,
            "collapsed_lanes": [],
            "show_archived": False,
            "is_default": True,
        },
    )
    SavedView.objects.update_or_create(
        owner=owner,
        name="Blocked and overdue",
        defaults={
            "visibility": SavedViewVisibility.PRIVATE,
            "filters": {"status": TaskStatus.BLOCKED, "overdue": True, "archived": False},
            "sort": {"field": "priority", "direction": "desc"},
            "density": SavedViewDensity.COMPACT,
            "collapsed_lanes": [TaskStatus.DONE],
            "show_archived": False,
            "is_default": False,
        },
    )
    SavedView.objects.update_or_create(
        owner=owner,
        name="Booking flow launch",
        defaults={
            "visibility": SavedViewVisibility.TEAM,
            "filters": {"project": projects["booking_flow"].id, "archived": False},
            "sort": {"field": "updated_at", "direction": "desc"},
            "density": SavedViewDensity.COMFORTABLE,
            "collapsed_lanes": [TaskStatus.BACKLOG],
            "show_archived": False,
            "is_default": False,
        },
    )


def seed_notifications(*, owner, projects: dict, tasks: dict, messages: dict, now):
    specs = (
        (
            NotificationType.REVIEW_REQUESTED,
            projects["beta_system"],
            tasks["review_mobile"],
            {"seed_key": "beta-review-mobile", "title": "Mobile Kanban interaction polish", "review_state": TaskReviewState.NEEDS_REVIEW},
        ),
        (
            NotificationType.TASK_BLOCKED,
            projects["booking_flow"],
            tasks["payment_icons"],
            {"seed_key": "payment-icons-blocked", "reason": tasks["payment_icons"].blocked_reason},
        ),
        (
            NotificationType.TASK_OVERDUE,
            projects["booking_flow"],
            tasks["payment_icons"],
            {"seed_key": "payment-icons-overdue", "due_date": str(tasks["payment_icons"].due_date)},
        ),
        (
            NotificationType.TASK_DUE_SOON,
            projects["beta_system"],
            tasks["responsive_sweep"],
            {"seed_key": "responsive-sweep-due", "due_date": str(tasks["responsive_sweep"].due_date)},
        ),
        (
            NotificationType.CHAT_MESSAGE,
            projects["beta_system"],
            None,
            {
                "seed_key": "beta-chat-source",
                "thread_id": messages["beta_source"].thread_id,
                "message_id": messages["beta_source"].id,
                "title": "New decision source message",
            },
        ),
    )

    for notification_type, project, task, payload in specs:
        notification = Notification.objects.filter(
            recipient=owner,
            type=notification_type,
            project=project,
            task=task,
            payload__seed_key=payload["seed_key"],
        ).first()
        if notification:
            notification.payload = payload
            notification.read_at = None
            notification.snoozed_until = None
            notification.action_taken_at = None
            notification.action_taken_by = None
            notification.save(update_fields=["payload", "read_at", "snoozed_until", "action_taken_at", "action_taken_by"])
        else:
            notification = Notification.objects.create(
                recipient=owner,
                type=notification_type,
                project=project,
                task=task,
                payload=payload,
            )
        Notification.objects.filter(pk=notification.pk).update(created_at=now - timedelta(minutes=15))


def seed_notification_preferences(*, owner):
    NotificationPreference.objects.update_or_create(
        user=owner,
        defaults={
            "mentions": True,
            "assignments": True,
            "review_requests": True,
            "due_soon": True,
            "digest_frequency": NotificationDigestFrequency.INSTANT,
        },
    )

