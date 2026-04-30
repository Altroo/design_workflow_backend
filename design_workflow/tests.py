import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from design_workflow.models import (
    Priority,
    Project,
    ProjectStatus,
    Task,
    TaskActivity,
    TaskActivityType,
    TaskComment,
    TaskStatus,
    TimeEntry,
)

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


class TestTaskCreation:
    def test_designer_can_create_task_assigned_to_self(self):
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
        assert task.current_assignee == designer
        assert task.estimated_minutes == 0
        assert response.data["current_assignee"]["id"] == designer.id


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

    def test_leaving_in_progress_logs_one_work_day(self):
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

        response = client.patch(
            f"/api/design-workflow/tasks/{task.id}/status/",
            {"status": TaskStatus.IN_REVIEW},
            format="json",
        )

        assert response.status_code == 200
        task.refresh_from_db()
        assert task.work_started_at is None
        assert task.actual_minutes == 540
        assert task.time_entries.count() == 1
        assert task.time_entries.first().minutes == 540

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
