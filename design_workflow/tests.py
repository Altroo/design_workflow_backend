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
