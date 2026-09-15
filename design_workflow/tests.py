from datetime import datetime, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.test import APIClient

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
    Task,
    TaskActivity,
    TaskActivityType,
    TaskArtifactVersion,
    TaskAttachment,
    TaskChecklist,
    TaskChecklistItem,
    TaskLabel,
    TaskReviewState,
    TaskComment,
    TaskStatus,
    TimeEntry,
)
from design_workflow.tasks import generate_notification_digests
from design_workflow.services import count_working_minutes

User = get_user_model()


pytestmark = pytest.mark.django_db


def make_manager(email: str = "manager@test.com"):
    return User.objects.create_user(
        email=email,
        password="securepass123",
        first_name="Manager",
        last_name="User",
        is_staff=True,
        role="manager",
    )


def make_designer(email: str = "designer@test.com"):
    return User.objects.create_user(
        email=email,
        password="securepass123",
        first_name="Designer",
        last_name="User",
        is_staff=False,
        role="designer",
    )


class TestProjectDetailPayload:
    def test_project_detail_includes_rollups(self):
        manager = make_manager()
        designer = make_designer()
        project = Project.objects.create(
            name="Showroom refresh",
            description="Internal redesign",
            manager=manager,
            priority=Priority.HIGH,
            status=ProjectStatus.ACTIVE,
        )
        task = Task.objects.create(
            project=project,
            title="Moodboard",
            description="Prepare moodboard",
            current_assignee=designer,
            status=TaskStatus.IN_PROGRESS,
            priority=Priority.HIGH,
            estimated_minutes=120,
            created_by=manager,
            updated_by=manager,
        )
        comment = TaskComment.objects.create(
            task=task,
            author=designer,
            body="Waiting for fabric samples.",
        )
        TimeEntry.objects.create(
            task=task,
            user=designer,
            minutes=45,
            note="Initial concepts",
        )
        TaskActivity.objects.create(
            task=task,
            actor=designer,
            action_type=TaskActivityType.COMMENT_ADDED,
            metadata={"comment_id": comment.id},
        )

        client = APIClient()
        client.force_authenticate(user=manager)
        response = client.get(f"/api/design-workflow/projects/{project.id}/")

        assert response.status_code == 200
        assert response.data["id"] == project.id
        assert len(response.data["tasks"]) == 1
        assert len(response.data["recent_comments"]) == 1
        assert response.data["recent_comments"][0]["task_id"] == task.id
        assert response.data["recent_comments"][0]["task_title"] == task.title
        assert response.data["recent_comments"][0]["body"] == "Waiting for fabric samples."
        assert len(response.data["recent_activity"]) == 1
        assert response.data["recent_activity"][0]["task_id"] == task.id
        assert response.data["recent_activity"][0]["task_title"] == task.title
        contributor_ids = {item["id"] for item in response.data["contributors"]}
        assert contributor_ids == {designer.id}

    def test_designer_can_read_project_rollups(self):
        manager = make_manager("manager-2@test.com")
        designer = make_designer("designer-2@test.com")
        project = Project.objects.create(
            name="Visual guidelines",
            description="Brand refresh",
            manager=manager,
            priority=Priority.MEDIUM,
            status=ProjectStatus.ACTIVE,
        )
        task = Task.objects.create(
            project=project,
            title="Deck cleanup",
            current_assignee=designer,
            status=TaskStatus.TODO,
            priority=Priority.MEDIUM,
            created_by=manager,
            updated_by=manager,
        )
        TaskActivity.objects.create(
            task=task,
            actor=manager,
            action_type=TaskActivityType.CREATED,
            metadata={"status": task.status},
        )

        client = APIClient()
        client.force_authenticate(user=designer)
        response = client.get(f"/api/design-workflow/projects/{project.id}/")

        assert response.status_code == 200
        assert response.data["tasks"][0]["id"] == task.id
        assert response.data["recent_activity"][0]["task_title"] == task.title


class TestProjectArchiving:
    def test_manager_archives_project_and_all_related_tasks(self):
        manager = make_manager("manager-project-archive@test.com")
        designer = make_designer("designer-project-archive@test.com")
        project = Project.objects.create(
            name="Retired showroom project",
            manager=manager,
            priority=Priority.HIGH,
            status=ProjectStatus.ACTIVE,
        )
        task = Task.objects.create(
            project=project,
            title="Running layout task",
            current_assignee=designer,
            status=TaskStatus.IN_PROGRESS,
            priority=Priority.HIGH,
            created_by=manager,
            updated_by=manager,
        )
        completed_task = Task.objects.create(
            project=project,
            title="Completed layout task",
            current_assignee=designer,
            status=TaskStatus.DONE,
            priority=Priority.MEDIUM,
            created_by=manager,
            updated_by=manager,
        )

        client = APIClient()
        client.force_authenticate(user=manager)
        response = client.patch(
            f"/api/design-workflow/projects/{project.id}/",
            {"archived": True},
            format="json",
        )

        assert response.status_code == 200
        project.refresh_from_db()
        task.refresh_from_db()
        completed_task.refresh_from_db()
        assert project.archived is True
        assert project.archived_at is not None
        assert project.status == ProjectStatus.ARCHIVED
        assert task.archived is True
        assert completed_task.archived is True
        assert task.archived_at == project.archived_at
        assert completed_task.archived_at == project.archived_at
        assert TaskActivity.objects.filter(
            task__in=[task, completed_task],
            action_type=TaskActivityType.PROJECT_ARCHIVED,
        ).count() == 2

    def test_designer_cannot_archive_project(self):
        manager = make_manager("manager-project-archive-denied@test.com")
        designer = make_designer("designer-project-archive-denied@test.com")
        project = Project.objects.create(
            name="Protected showroom project",
            manager=manager,
            priority=Priority.MEDIUM,
            status=ProjectStatus.ACTIVE,
        )

        client = APIClient()
        client.force_authenticate(user=designer)
        response = client.patch(
            f"/api/design-workflow/projects/{project.id}/",
            {"archived": True},
            format="json",
        )

        assert response.status_code == 403
        project.refresh_from_db()
        assert project.archived is False

    def test_task_cannot_be_restored_until_project_is_unarchived(self):
        manager = make_manager("manager-project-restore@test.com")
        project = Project.objects.create(
            name="Archived showroom project",
            manager=manager,
            priority=Priority.HIGH,
            status=ProjectStatus.ARCHIVED,
            archived=True,
            archived_at=timezone.now(),
        )
        task = Task.objects.create(
            project=project,
            title="Archived layout task",
            status=TaskStatus.TODO,
            priority=Priority.HIGH,
            archived=True,
            archived_at=timezone.now(),
            created_by=manager,
            updated_by=manager,
        )
        client = APIClient()
        client.force_authenticate(user=manager)

        blocked_response = client.post(
            f"/api/design-workflow/tasks/{task.id}/archive/",
            {"archived": False},
            format="json",
        )

        assert blocked_response.status_code == 400
        task.refresh_from_db()
        assert task.archived is True

        project_response = client.patch(
            f"/api/design-workflow/projects/{project.id}/",
            {"archived": False},
            format="json",
        )
        assert project_response.status_code == 200
        project.refresh_from_db()
        task.refresh_from_db()
        assert project.archived is False
        assert project.archived_at is None
        assert project.status == ProjectStatus.PLANNED
        assert task.archived is True

        restored_response = client.post(
            f"/api/design-workflow/tasks/{task.id}/archive/",
            {"archived": False},
            format="json",
        )
        assert restored_response.status_code == 200
        task.refresh_from_db()
        assert task.archived is False

    def test_project_delete_is_not_available(self):
        manager = make_manager("manager-project-no-delete@test.com")
        project = Project.objects.create(
            name="Archive-only project",
            manager=manager,
            priority=Priority.MEDIUM,
            status=ProjectStatus.ACTIVE,
        )
        client = APIClient()
        client.force_authenticate(user=manager)

        response = client.delete(f"/api/design-workflow/projects/{project.id}/")

        assert response.status_code == 405
        assert Project.objects.filter(id=project.id).exists()


class TestTaskLabelManagement:
    def test_manager_can_update_label(self):
        manager = make_manager("manager-label-update@test.com")
        label = TaskLabel.objects.create(name="Old label", color="#64748b", created_by=manager)
        client = APIClient()
        client.force_authenticate(user=manager)

        response = client.patch(
            f"/api/design-workflow/labels/{label.id}/",
            {"name": "Client review", "color": "#4f46e5"},
            format="json",
        )

        assert response.status_code == 200
        label.refresh_from_db()
        assert label.name == "Client review"
        assert label.color == "#4f46e5"

    def test_user_cannot_update_another_users_label(self):
        manager = make_manager("manager-label-owner@test.com")
        designer = make_designer("designer-label-update@test.com")
        label = TaskLabel.objects.create(name="Protected label", color="#64748b", created_by=manager)
        client = APIClient()
        client.force_authenticate(user=designer)

        response = client.patch(
            f"/api/design-workflow/labels/{label.id}/",
            {"name": "Changed"},
            format="json",
        )

        assert response.status_code == 404
        label.refresh_from_db()
        assert label.name == "Protected label"

    def test_labels_are_private_and_names_can_repeat_between_users(self):
        manager = make_manager("manager-label-private@test.com")
        designer = make_designer("designer-label-private@test.com")
        TaskLabel.objects.create(name="Client", color="#4f46e5", created_by=manager)
        client = APIClient()
        client.force_authenticate(user=designer)

        created = client.post(
            "/api/design-workflow/labels/",
            {"name": "Client", "color": "#0891b2"},
            format="json",
        )
        response = client.get("/api/design-workflow/labels/")

        assert created.status_code == 201
        assert created.data["created_by"]["id"] == designer.id
        assert [item["id"] for item in response.data] == [created.data["id"]]


class TestChatPrivateThreadRecipients:
    def test_inactive_recipient_cannot_be_added_to_private_thread(self):
        manager = make_manager("manager-private-recipient@test.com")
        inactive_user = make_designer("inactive-private-recipient@test.com")
        inactive_user.is_active = False
        inactive_user.save(update_fields=["is_active"])

        client = APIClient()
        client.force_authenticate(user=manager)
        response = client.post(
            "/api/design-workflow/chat/threads/",
            {"kind": "private", "recipient_id": inactive_user.id},
            format="json",
        )

        assert response.status_code == 400
        assert not ChatThread.objects.filter(kind=ChatThreadKind.PRIVATE, participants=inactive_user).exists()


class TestTaskCreation:
    def test_designer_can_create_a_project(self):
        designer = make_designer("designer-project-create@test.com")
        client = APIClient()
        client.force_authenticate(user=designer)

        response = client.post(
            "/api/design-workflow/projects/",
            {
                "name": "New shared project",
                "description": "Created without a special workflow role",
                "manager_id": designer.id,
                "priority": Priority.MEDIUM,
                "status": ProjectStatus.PLANNED,
            },
            format="json",
        )

        assert response.status_code == 201
        assert response.data["manager"]["id"] == designer.id

    def test_designer_can_create_task_for_another_user_with_an_estimate(self):
        manager = make_manager("manager-task-create@test.com")
        designer = make_designer("designer-task-create@test.com")
        other_designer = make_designer("other-designer-task-create@test.com")
        project = Project.objects.create(
            name="Quick board cards",
            description="Flexible intake",
            manager=manager,
            priority=Priority.MEDIUM,
            status=ProjectStatus.ACTIVE,
        )

        client = APIClient()
        client.force_authenticate(user=designer)
        response = client.post(
            "/api/design-workflow/tasks/",
            {
                "project_id": project.id,
                "title": "Draft homepage hero",
                "description": "",
                "current_assignee_id": other_designer.id,
                "status": TaskStatus.TODO,
                "priority": Priority.MEDIUM,
                "estimated_minutes": 60,
                "sort_order": 0,
            },
            format="json",
        )

        assert response.status_code == 201
        task = Task.objects.get(title="Draft homepage hero")
        assert task.created_by == designer
        assert task.current_assignee == other_designer
        assert task.estimated_minutes == 60
        assert response.data["current_assignee"]["id"] == other_designer.id


class TestTaskChecklists:
    def test_manager_can_delete_checklist_group_with_items(self):
        manager = make_manager("manager-checklist-delete@test.com")
        designer = make_designer("designer-checklist-delete@test.com")
        project = Project.objects.create(
            name="Checklist controls",
            description="Task detail checklist management",
            manager=manager,
            priority=Priority.MEDIUM,
            status=ProjectStatus.ACTIVE,
        )
        task = Task.objects.create(
            project=project,
            title="Final delivery checklist",
            current_assignee=designer,
            status=TaskStatus.TODO,
            priority=Priority.MEDIUM,
            created_by=manager,
            updated_by=manager,
        )
        checklist = TaskChecklist.objects.create(task=task, title="Final delivery checklist", created_by=manager)
        TaskChecklistItem.objects.create(task=task, checklist=checklist, title="Confirm handoff", created_by=manager)

        client = APIClient()
        client.force_authenticate(user=manager)
        response = client.delete(f"/api/design-workflow/tasks/{task.id}/checklists/{checklist.id}/")

        assert response.status_code == 204
        assert not TaskChecklist.objects.filter(pk=checklist.id).exists()
        assert task.checklist_items.count() == 0
        activity = TaskActivity.objects.filter(task=task, action_type=TaskActivityType.CHECKLIST_UPDATED).latest("created_at")
        assert activity.metadata["action"] == "deleted"

    def test_unassigned_designer_cannot_delete_checklist_group(self):
        manager = make_manager("manager-checklist-denied@test.com")
        designer = make_designer("designer-checklist-denied@test.com")
        project_owner = make_designer("project-owner-checklist-denied@test.com")
        project = Project.objects.create(
            name="Checklist permissions",
            description="Task detail checklist permissions",
            manager=project_owner,
            priority=Priority.MEDIUM,
            status=ProjectStatus.ACTIVE,
        )
        task = Task.objects.create(
            project=project,
            title="Locked checklist",
            current_assignee=designer,
            status=TaskStatus.TODO,
            priority=Priority.MEDIUM,
            created_by=manager,
            updated_by=manager,
        )
        checklist = TaskChecklist.objects.create(task=task, title="Locked checklist", created_by=manager)

        client = APIClient()
        client.force_authenticate(user=project_owner)
        response = client.delete(f"/api/design-workflow/tasks/{task.id}/checklists/{checklist.id}/")

        assert response.status_code == 403
        assert TaskChecklist.objects.filter(pk=checklist.id).exists()


class TestTaskWorkDayAutomation:
    def test_in_progress_starts_session_without_fake_time_entry(self):
        manager = make_manager("manager-work-session@test.com")
        designer = make_designer("designer-work-session@test.com")
        project = Project.objects.create(
            name="Villa living room",
            description="Interior design",
            manager=manager,
            priority=Priority.MEDIUM,
            status=ProjectStatus.ACTIVE,
        )
        task = Task.objects.create(
            project=project,
            title="Concept board",
            current_assignee=designer,
            status=TaskStatus.TODO,
            priority=Priority.MEDIUM,
            created_by=manager,
            updated_by=manager,
        )

        client = APIClient()
        client.force_authenticate(user=designer)
        response = client.patch(
            f"/api/design-workflow/tasks/{task.id}/status/",
            {"status": TaskStatus.IN_PROGRESS},
            format="json",
        )

        assert response.status_code == 200
        task.refresh_from_db()
        assert task.work_started_at is not None
        assert task.time_entries.count() == 0

    def test_leaving_in_progress_logs_one_work_day(self, monkeypatch):
        manager = make_manager("manager-work-close@test.com")
        designer = make_designer("designer-work-close@test.com")
        project = Project.objects.create(
            name="Villa bedroom",
            description="Interior design",
            manager=manager,
            priority=Priority.MEDIUM,
            status=ProjectStatus.ACTIVE,
        )
        task = Task.objects.create(
            project=project,
            title="Render draft",
            current_assignee=designer,
            status=TaskStatus.TODO,
            priority=Priority.MEDIUM,
            created_by=manager,
            updated_by=manager,
        )

        client = APIClient()
        client.force_authenticate(user=designer)
        response = client.patch(
            f"/api/design-workflow/tasks/{task.id}/status/",
            {"status": TaskStatus.IN_PROGRESS},
            format="json",
        )
        assert response.status_code == 200

        start = timezone.make_aware(datetime(2026, 9, 14, 9, 0))
        end = timezone.make_aware(datetime(2026, 9, 14, 18, 0))
        Task.objects.filter(pk=task.pk).update(work_started_at=start)
        monkeypatch.setattr("design_workflow.services.timezone.now", lambda: end)

        response = client.patch(
            f"/api/design-workflow/tasks/{task.id}/status/",
            {"status": TaskStatus.IN_REVIEW},
            format="json",
        )

        assert response.status_code == 200
        task.refresh_from_db()
        assert task.work_started_at is None
        assert task.actual_minutes == 480
        assert task.time_entries.count() == 1
        assert task.time_entries.first().minutes == 480

    def test_work_schedule_counts_weekdays_saturday_and_skips_sunday(self):
        monday = timezone.make_aware(datetime(2026, 9, 14, 9, 0))
        monday_end = timezone.make_aware(datetime(2026, 9, 14, 18, 0))
        saturday = timezone.make_aware(datetime(2026, 9, 19, 9, 0))
        saturday_end = timezone.make_aware(datetime(2026, 9, 19, 13, 0))
        sunday = timezone.make_aware(datetime(2026, 9, 20, 9, 0))
        sunday_end = timezone.make_aware(datetime(2026, 9, 20, 18, 0))

        assert count_working_minutes(monday, monday_end) == 480
        assert count_working_minutes(saturday, saturday_end) == 240
        assert count_working_minutes(sunday, sunday_end) == 0

    def test_designer_cannot_manually_log_time(self):
        manager = make_manager("manager-manual-time@test.com")
        designer = make_designer("designer-manual-time@test.com")
        project = Project.objects.create(
            name="Villa hallway",
            description="Interior design",
            manager=manager,
            priority=Priority.MEDIUM,
            status=ProjectStatus.ACTIVE,
        )
        task = Task.objects.create(
            project=project,
            title="Material board",
            current_assignee=designer,
            status=TaskStatus.TODO,
            priority=Priority.MEDIUM,
            created_by=manager,
            updated_by=manager,
        )

        client = APIClient()
        client.force_authenticate(user=designer)
        response = client.post(
            f"/api/design-workflow/tasks/{task.id}/time-entries/",
            {"minutes": 540, "note": "Manual day"},
            format="json",
        )

        assert response.status_code == 403
        assert task.time_entries.count() == 0


class TestPremiumBoardViews:
    def test_saved_views_create_default_and_team_permissions(self):
        manager = make_manager("manager-saved-view@test.com")
        designer = make_designer("designer-saved-view@test.com")

        client = APIClient()
        client.force_authenticate(user=designer)
        response = client.post(
            "/api/design-workflow/views/",
            {
                "name": "My urgent work",
                "visibility": "private",
                "filters": {"mine": True, "priority": "urgent"},
                "sort": {"field": "due_date", "direction": "asc"},
                "density": "compact",
                "collapsed_lanes": ["done"],
                "is_default": True,
            },
            format="json",
        )

        assert response.status_code == 201
        first_view = SavedView.objects.get(owner=designer, name="My urgent work")
        assert first_view.is_default is True

        response = client.post(
            "/api/design-workflow/views/",
            {"name": "Team blocked", "visibility": "team", "filters": {"blocked": True}},
            format="json",
        )
        assert response.status_code == 400

        response = client.post(
            "/api/design-workflow/views/",
            {"name": "My review", "visibility": "private", "filters": {"review_state": "needs_review"}, "is_default": True},
            format="json",
        )
        assert response.status_code == 201
        first_view.refresh_from_db()
        assert first_view.is_default is False

        client.force_authenticate(user=manager)
        response = client.post(
            "/api/design-workflow/views/",
            {"name": "Studio reviews", "visibility": "team", "filters": {"review_state": "needs_review"}},
            format="json",
        )
        assert response.status_code == 201

        client.force_authenticate(user=designer)
        response = client.get("/api/design-workflow/views/")
        assert response.status_code == 200
        names = {item["name"] for item in response.data}
        assert {"My review", "Studio reviews"}.issubset(names)

    def test_workspace_search_returns_cross_entity_results(self):
        manager = make_manager("manager-search@test.com")
        designer = make_designer("designer-search@test.com")
        project = Project.objects.create(
            name="Atrium redesign",
            description="Lobby concept",
            manager=manager,
            priority=Priority.HIGH,
            status=ProjectStatus.ACTIVE,
        )
        task = Task.objects.create(
            project=project,
            title="Atrium lighting board",
            description="Searchable card",
            current_assignee=designer,
            status=TaskStatus.TODO,
            priority=Priority.HIGH,
            created_by=manager,
            updated_by=manager,
        )
        TaskAttachment.objects.create(
            task=task,
            uploaded_by=designer,
            file="design_workflow/task_attachments/atrium.png",
            name="atrium-reference.png",
            mime_type="image/png",
            size=12,
        )
        thread = ChatThread.objects.create(kind=ChatThreadKind.PUBLIC, title="Studio public")
        ChatMessage.objects.create(thread=thread, sender=designer, body="Atrium approval note")

        client = APIClient()
        client.force_authenticate(user=manager)
        response = client.get("/api/design-workflow/search/?q=atrium")

        assert response.status_code == 200
        result_types = {item["type"] for item in response.data}
        assert {"task", "project", "file", "chat"}.issubset(result_types)

    def test_workspace_search_can_match_tasks_by_assignee(self):
        manager = make_manager("manager-search-assignee@test.com")
        designer = make_designer("nadia.assignee@test.com")
        designer.first_name = "Nadia"
        designer.last_name = "Bennani"
        designer.save(update_fields=["first_name", "last_name"])
        project = Project.objects.create(
            name="Quiet showroom",
            description="Neutral context",
            manager=manager,
            priority=Priority.MEDIUM,
            status=ProjectStatus.ACTIVE,
        )
        task = Task.objects.create(
            project=project,
            title="Palette layout",
            description="Surface selection",
            current_assignee=designer,
            status=TaskStatus.TODO,
            priority=Priority.MEDIUM,
            created_by=manager,
            updated_by=manager,
        )

        client = APIClient()
        client.force_authenticate(user=manager)
        response = client.get("/api/design-workflow/search/?q=nadia&types=task")

        assert response.status_code == 200
        assert [item["id"] for item in response.data if item["type"] == "task"] == [task.id]

    def test_workspace_search_scopes_designer_results_to_accessible_work(self):
        manager = make_manager("manager-search-scope@test.com")
        designer = make_designer("designer-search-scope@test.com")
        outsider = make_designer("outsider-search-scope@test.com")
        visible_project = Project.objects.create(
            name="Studio visible project",
            description="Visible context",
            manager=manager,
            priority=Priority.HIGH,
            status=ProjectStatus.ACTIVE,
        )
        visible_task = Task.objects.create(
            project=visible_project,
            title="Studio visible task",
            description="Designer can find this",
            current_assignee=designer,
            status=TaskStatus.TODO,
            priority=Priority.HIGH,
            created_by=manager,
            updated_by=manager,
        )
        TaskAttachment.objects.create(
            task=visible_task,
            uploaded_by=designer,
            file="design_workflow/task_attachments/studio-visible.png",
            name="studio-visible.png",
            mime_type="image/png",
            size=12,
        )
        hidden_project = Project.objects.create(
            name="Studio hidden project",
            description="Hidden context",
            manager=manager,
            priority=Priority.HIGH,
            status=ProjectStatus.ACTIVE,
        )
        hidden_task = Task.objects.create(
            project=hidden_project,
            title="Studio hidden task",
            description="Designer must not find this",
            current_assignee=outsider,
            status=TaskStatus.TODO,
            priority=Priority.HIGH,
            created_by=manager,
            updated_by=manager,
        )
        TaskAttachment.objects.create(
            task=hidden_task,
            uploaded_by=outsider,
            file="design_workflow/task_attachments/studio-hidden.png",
            name="studio-hidden.png",
            mime_type="image/png",
            size=12,
        )

        client = APIClient()
        client.force_authenticate(user=designer)
        response = client.get("/api/design-workflow/search/?q=studio")

        assert response.status_code == 200
        result_titles = {item["title"] for item in response.data}
        assert {"Studio visible task", "Studio visible project", "studio-visible.png"}.issubset(result_titles)
        assert "Studio hidden task" not in result_titles
        assert "Studio hidden project" not in result_titles
        assert "studio-hidden.png" not in result_titles


class TestWorkflowAccessContracts:
    def test_designer_read_access_is_limited_to_assigned_project_context(self):
        manager = make_manager("manager-access@test.com")
        designer = make_designer("designer-access@test.com")
        outsider = make_designer("outsider-access@test.com")
        visible_project = Project.objects.create(
            name="Accessible project",
            description="Assigned work",
            manager=manager,
            priority=Priority.MEDIUM,
            status=ProjectStatus.ACTIVE,
        )
        visible_task = Task.objects.create(
            project=visible_project,
            title="Accessible task",
            current_assignee=designer,
            status=TaskStatus.TODO,
            priority=Priority.MEDIUM,
            created_by=manager,
            updated_by=manager,
        )
        hidden_project = Project.objects.create(
            name="Restricted project",
            description="Unassigned work",
            manager=manager,
            priority=Priority.MEDIUM,
            status=ProjectStatus.ACTIVE,
        )
        hidden_task = Task.objects.create(
            project=hidden_project,
            title="Restricted task",
            current_assignee=outsider,
            status=TaskStatus.TODO,
            priority=Priority.MEDIUM,
            created_by=manager,
            updated_by=manager,
        )
        hidden_attachment = TaskAttachment.objects.create(
            task=hidden_task,
            uploaded_by=outsider,
            file="design_workflow/task_attachments/restricted.png",
            name="restricted.png",
            mime_type="image/png",
            size=12,
        )

        client = APIClient()
        client.force_authenticate(user=designer)

        assert client.get(f"/api/design-workflow/projects/{visible_project.id}/").status_code == 200
        assert client.get(f"/api/design-workflow/tasks/{visible_task.id}/").status_code == 200
        assert client.get(f"/api/design-workflow/projects/{hidden_project.id}/").status_code == 404
        assert client.get(f"/api/design-workflow/tasks/{hidden_task.id}/").status_code == 404
        assert client.get(f"/api/design-workflow/attachments/{hidden_attachment.id}/annotations/").status_code == 404

        list_response = client.get("/api/design-workflow/tasks/")
        assert list_response.status_code == 200
        task_ids = {item["id"] for item in list_response.data}
        assert visible_task.id in task_ids
        assert hidden_task.id not in task_ids


class TestLinkedChatWorkflow:
    def test_public_chat_thread_is_unique(self):
        ChatThread.objects.create(kind=ChatThreadKind.PUBLIC, title="Studio public")

        with pytest.raises(IntegrityError), transaction.atomic():
            ChatThread.objects.create(kind=ChatThreadKind.PUBLIC, title="Duplicate public")

    def test_project_and_task_threads_are_accessible_to_work_context_users(self):
        manager = make_manager("manager-linked-chat@test.com")
        designer = make_designer("designer-linked-chat@test.com")
        outsider = make_designer("outsider-linked-chat@test.com")
        project = Project.objects.create(
            name="Linked chat project",
            description="Room context",
            manager=manager,
            priority=Priority.MEDIUM,
            status=ProjectStatus.ACTIVE,
        )
        task = Task.objects.create(
            project=project,
            title="Linked chat card",
            current_assignee=designer,
            status=TaskStatus.TODO,
            priority=Priority.MEDIUM,
            created_by=manager,
            updated_by=manager,
        )

        client = APIClient()
        client.force_authenticate(user=manager)
        project_response = client.post(
            "/api/design-workflow/chat/threads/",
            {"kind": ChatThreadKind.PROJECT, "project_id": project.id},
            format="json",
        )
        assert project_response.status_code == 201
        project_thread = ChatThread.objects.get(kind=ChatThreadKind.PROJECT, project=project)
        assert project_thread.participants.filter(id__in=[manager.id, designer.id]).count() == 2

        client.force_authenticate(user=designer)
        list_response = client.get("/api/design-workflow/chat/threads/")
        assert list_response.status_code == 200
        assert project_thread.id in {item["id"] for item in list_response.data}

        task_response = client.post(
            "/api/design-workflow/chat/threads/",
            {"kind": ChatThreadKind.TASK, "task_id": task.id},
            format="json",
        )
        assert task_response.status_code == 201
        assert task_response.data["task"]["id"] == task.id

        message_response = client.post(
            f"/api/design-workflow/chat/threads/{project_thread.id}/messages/",
            {"body": "Please review the linked project room."},
            format="json",
        )
        assert message_response.status_code == 201
        assert Notification.objects.filter(
            recipient=manager,
            type=NotificationType.CHAT_MESSAGE,
            payload__thread_id=project_thread.id,
        ).exists()

        client.force_authenticate(user=outsider)
        forbidden_response = client.post(
            "/api/design-workflow/chat/threads/",
            {"kind": ChatThreadKind.PROJECT, "project_id": project.id},
            format="json",
        )
        assert forbidden_response.status_code == 403

    def test_task_created_from_chat_message_keeps_source_link(self):
        manager = make_manager("manager-message-source@test.com")
        designer = make_designer("designer-message-source@test.com")
        project = Project.objects.create(
            name="Chat source project",
            description="Source link",
            manager=manager,
            priority=Priority.MEDIUM,
            status=ProjectStatus.ACTIVE,
        )
        thread = ChatThread.objects.create(kind=ChatThreadKind.PUBLIC, title="Studio public")
        message = ChatMessage.objects.create(thread=thread, sender=designer, body="Turn this into a task.")

        client = APIClient()
        client.force_authenticate(user=manager)
        response = client.post(
            "/api/design-workflow/tasks/",
            {
                "project_id": project.id,
                "title": "Task from chat",
                "description": message.body,
                "current_assignee_id": designer.id,
                "status": TaskStatus.BACKLOG,
                "priority": Priority.MEDIUM,
                "source_chat_message_id": message.id,
            },
            format="json",
        )

        assert response.status_code == 201
        task = Task.objects.get(title="Task from chat")
        assert task.source_chat_message == message
        assert response.data["source_chat_message_id"] == message.id
        assert response.data["source_chat_thread_id"] == thread.id
        assert TaskActivity.objects.filter(
            task=task,
            action_type=TaskActivityType.CREATED,
            metadata__source_chat_message_id=message.id,
        ).exists()


class TestDesignReviewWorkflow:
    def test_task_review_state_is_separate_from_board_status_and_notifies_manager(self):
        manager = make_manager("manager-review@test.com")
        designer = make_designer("designer-review@test.com")
        project = Project.objects.create(
            name="Suite review",
            description="Design review",
            manager=manager,
            priority=Priority.MEDIUM,
            status=ProjectStatus.ACTIVE,
        )
        task = Task.objects.create(
            project=project,
            title="Suite render",
            current_assignee=designer,
            status=TaskStatus.IN_PROGRESS,
            priority=Priority.HIGH,
            created_by=manager,
            updated_by=manager,
        )

        client = APIClient()
        client.force_authenticate(user=designer)
        response = client.post(
            f"/api/design-workflow/tasks/{task.id}/review/",
            {"review_state": TaskReviewState.NEEDS_REVIEW, "notes": "Ready for approval"},
            format="json",
        )

        assert response.status_code == 200
        task.refresh_from_db()
        assert task.status == TaskStatus.IN_PROGRESS
        assert task.review_state == TaskReviewState.NEEDS_REVIEW
        assert task.review_requested_by == designer
        assert Notification.objects.filter(recipient=manager, type=NotificationType.REVIEW_REQUESTED, task=task).exists()

        activity_count = TaskActivity.objects.filter(task=task).count()
        notification_count = Notification.objects.filter(task=task).count()
        repeated = client.post(
            f"/api/design-workflow/tasks/{task.id}/review/",
            {"review_state": TaskReviewState.NEEDS_REVIEW},
            format="json",
        )
        assert repeated.status_code == 200
        assert TaskActivity.objects.filter(task=task).count() == activity_count
        assert Notification.objects.filter(task=task).count() == notification_count

        client.force_authenticate(user=manager)
        response = client.post(
            f"/api/design-workflow/tasks/{task.id}/review/",
            {"review_state": TaskReviewState.APPROVED},
            format="json",
        )

        assert response.status_code == 200
        task.refresh_from_db()
        assert task.status == TaskStatus.IN_PROGRESS
        assert task.review_state == TaskReviewState.APPROVED
        assert task.review_approved_by == manager

    def test_versions_and_annotations_attach_to_task_artifacts(self):
        manager = make_manager("manager-artifact@test.com")
        designer = make_designer("designer-artifact@test.com")
        project = Project.objects.create(
            name="Kitchen handoff",
            description="Final files",
            manager=manager,
            priority=Priority.MEDIUM,
            status=ProjectStatus.ACTIVE,
        )
        task = Task.objects.create(
            project=project,
            title="Kitchen render",
            current_assignee=designer,
            status=TaskStatus.IN_REVIEW,
            priority=Priority.HIGH,
            created_by=manager,
            updated_by=manager,
        )
        attachment = TaskAttachment.objects.create(
            task=task,
            uploaded_by=designer,
            file="design_workflow/task_attachments/kitchen.png",
            name="kitchen.png",
            mime_type="image/png",
            size=100,
        )

        client = APIClient()
        client.force_authenticate(user=designer)
        response = client.post(
            f"/api/design-workflow/tasks/{task.id}/versions/",
            {"attachment_id": attachment.id, "notes": "First render pass"},
            format="json",
        )

        assert response.status_code == 201
        version = TaskArtifactVersion.objects.get(task=task)
        assert version.version_number == 1
        assert version.approval_state == ArtifactApprovalState.PENDING

        response = client.post(
            f"/api/design-workflow/attachments/{attachment.id}/annotations/",
            {"version_id": version.id, "x_percent": "42.50", "y_percent": "30.00", "body": "Move pendant lower."},
            format="json",
        )

        assert response.status_code == 201
        annotation = AttachmentAnnotation.objects.get(attachment=attachment)
        assert annotation.version == version
        assert annotation.author == designer


class TestNotificationActions:
    def test_snooze_and_move_status_action(self):
        manager = make_manager("manager-notification@test.com")
        designer = make_designer("designer-notification@test.com")
        project = Project.objects.create(
            name="Notification project",
            description="Actions",
            manager=manager,
            priority=Priority.MEDIUM,
            status=ProjectStatus.ACTIVE,
        )
        task = Task.objects.create(
            project=project,
            title="Actionable card",
            current_assignee=designer,
            status=TaskStatus.TODO,
            priority=Priority.MEDIUM,
            created_by=manager,
            updated_by=manager,
        )
        notification = Notification.objects.create(
            recipient=designer,
            type=NotificationType.TASK_ASSIGNED,
            task=task,
            project=project,
            payload={"title": task.title},
        )
        snoozed_until = timezone.now() + timedelta(hours=2)

        client = APIClient()
        client.force_authenticate(user=designer)
        response = client.post(
            f"/api/design-workflow/notifications/{notification.id}/snooze/",
            {"snoozed_until": snoozed_until.isoformat()},
            format="json",
        )

        assert response.status_code == 200
        notification.refresh_from_db()
        assert notification.snoozed_until is not None

        response = client.post(
            f"/api/design-workflow/notifications/{notification.id}/action/",
            {"action": "move_status", "status": TaskStatus.IN_PROGRESS},
            format="json",
        )

        assert response.status_code == 200
        task.refresh_from_db()
        notification.refresh_from_db()
        assert task.status == TaskStatus.IN_PROGRESS
        assert notification.read_at is not None
        assert notification.action_taken_by == designer

    def test_daily_digest_creates_summary_notification_once_per_day(self):
        mail.outbox = []
        manager = make_manager("manager-digest@test.com")
        designer = make_designer("designer-digest@test.com")
        project = Project.objects.create(
            name="Digest project",
            description="Notification digest",
            manager=manager,
            priority=Priority.MEDIUM,
            status=ProjectStatus.ACTIVE,
        )
        task = Task.objects.create(
            project=project,
            title="Digest card",
            current_assignee=designer,
            status=TaskStatus.TODO,
            priority=Priority.MEDIUM,
            created_by=manager,
            updated_by=manager,
        )
        NotificationPreference.objects.create(
            user=designer,
            digest_frequency=NotificationDigestFrequency.DAILY,
        )
        Notification.objects.create(
            recipient=designer,
            type=NotificationType.TASK_ASSIGNED,
            task=task,
            project=project,
            payload={"title": task.title},
        )
        Notification.objects.create(
            recipient=designer,
            type=NotificationType.CHAT_MESSAGE,
            payload={"thread_id": 11, "message_id": 21},
        )

        created_count = generate_notification_digests()
        created_again = generate_notification_digests()

        assert created_count == 1
        assert created_again == 0
        digest = Notification.objects.get(recipient=designer, type=NotificationType.WORKFLOW_DIGEST)
        assert digest.payload["frequency"] == NotificationDigestFrequency.DAILY
        assert digest.payload["total_count"] == 2
        assert digest.payload["by_type"][NotificationType.TASK_ASSIGNED] == 1
        assert digest.payload["by_type"][NotificationType.CHAT_MESSAGE] == 1
        assert digest.payload["email_sent"] is True
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [designer.email]
        assert "Design Workflow digest: 2 updates" in mail.outbox[0].subject
        assert "Task assigned: 1" in mail.outbox[0].body
        assert "Chat message: 1" in mail.outbox[0].body


class TestWorkflowReports:
    def test_reports_filter_by_project_and_user(self):
        manager = make_manager("manager-report-filters@test.com")
        first_user = make_designer("first-report-user@test.com")
        second_user = make_designer("second-report-user@test.com")
        first_project = Project.objects.create(name="First report project", manager=manager)
        second_project = Project.objects.create(name="Second report project", manager=manager)
        first_task = Task.objects.create(
            project=first_project,
            title="First filtered task",
            current_assignee=first_user,
            created_by=manager,
            updated_by=manager,
        )
        second_task = Task.objects.create(
            project=second_project,
            title="Second filtered task",
            current_assignee=second_user,
            created_by=manager,
            updated_by=manager,
        )
        TimeEntry.objects.create(task=first_task, user=first_user, minutes=120)
        TimeEntry.objects.create(task=second_task, user=second_user, minutes=90)
        client = APIClient()
        client.force_authenticate(user=manager)

        time_response = client.get(
            f"/api/design-workflow/reports/time/?project={first_project.id}&user={first_user.id}"
        )
        workflow_response = client.get(
            f"/api/design-workflow/reports/workflow/?project={first_project.id}&user={first_user.id}"
        )

        assert time_response.status_code == 200
        assert [(row["project"]["id"], row["minutes"]) for row in time_response.data] == [
            (first_project.id, 120)
        ]
        assert workflow_response.status_code == 200
        assert workflow_response.data["tasks_sampled"] == 1
        assert workflow_response.data["capacity"][0]["user"]["id"] == first_user.id

    def test_workload_report_serializes_users_with_avatar_fallback(self):
        manager = make_manager("manager-workload@test.com")
        designer = make_designer("designer-workload@test.com")
        project = Project.objects.create(
            name="Workload project",
            manager=manager,
            priority=Priority.MEDIUM,
            status=ProjectStatus.ACTIVE,
        )
        Task.objects.create(
            project=project,
            title="Assigned workload",
            current_assignee=designer,
            status=TaskStatus.IN_PROGRESS,
            priority=Priority.MEDIUM,
            created_by=manager,
            updated_by=manager,
        )

        client = APIClient()
        client.force_authenticate(user=manager)
        response = client.get("/api/design-workflow/workload/")

        assert response.status_code == 200
        designer_row = next(row for row in response.data if row["user"]["id"] == designer.id)
        assert designer_row["user"]["avatar"] is None
        assert designer_row["open_tasks"] == 1

    def test_workflow_analytics_report_includes_time_review_and_capacity_metrics(self):
        manager = make_manager("manager-workflow-report@test.com")
        designer = make_designer("designer-workflow-report@test.com")
        project = Project.objects.create(
            name="Analytics project",
            description="Workflow metrics",
            manager=manager,
            priority=Priority.HIGH,
            status=ProjectStatus.ACTIVE,
        )
        now = timezone.now()
        done_task = Task.objects.create(
            project=project,
            title="Completed render",
            current_assignee=designer,
            status=TaskStatus.DONE,
            priority=Priority.HIGH,
            estimated_minutes=300,
            actual_minutes=420,
            completed_at=now,
            is_completed=True,
            created_by=manager,
            updated_by=manager,
        )
        Task.objects.filter(pk=done_task.pk).update(created_at=now - timedelta(days=5), completed_at=now)
        progress_activity = TaskActivity.objects.create(
            task=done_task,
            actor=designer,
            action_type=TaskActivityType.STATUS_CHANGED,
            metadata={"status": TaskStatus.IN_PROGRESS},
        )
        TaskActivity.objects.filter(pk=progress_activity.pk).update(created_at=now - timedelta(days=3))

        blocked_task = Task.objects.create(
            project=project,
            title="Blocked drawing",
            current_assignee=designer,
            status=TaskStatus.BLOCKED,
            priority=Priority.URGENT,
            estimated_minutes=540,
            actual_minutes=60,
            created_by=manager,
            updated_by=manager,
        )
        blocked_activity = TaskActivity.objects.create(
            task=blocked_task,
            actor=designer,
            action_type=TaskActivityType.STATUS_CHANGED,
            metadata={"status": TaskStatus.BLOCKED},
        )
        TaskActivity.objects.filter(pk=blocked_activity.pk).update(created_at=now - timedelta(hours=2))

        Task.objects.create(
            project=project,
            title="Review board",
            current_assignee=designer,
            status=TaskStatus.IN_REVIEW,
            priority=Priority.MEDIUM,
            estimated_minutes=240,
            actual_minutes=120,
            review_state=TaskReviewState.NEEDS_REVIEW,
            review_requested_at=now - timedelta(hours=3),
            review_requested_by=designer,
            created_by=manager,
            updated_by=manager,
        )

        client = APIClient()
        client.force_authenticate(user=manager)
        response = client.get("/api/design-workflow/reports/workflow/")

        assert response.status_code == 200
        assert response.data["tasks_sampled"] == 3
        assert response.data["lead_time_days"] >= 5
        assert response.data["cycle_time_days"] >= 3
        assert response.data["blocked_tasks"] == 1
        assert response.data["blocked_time_minutes"] >= 100
        assert response.data["review_bottlenecks"]["needs_review"] == 1
        assert response.data["estimate_vs_actual"]["estimated_minutes"] == 1080
        assert response.data["estimate_vs_actual"]["actual_minutes"] == 600
        assert response.data["capacity"][0]["user"]["id"] == designer.id
