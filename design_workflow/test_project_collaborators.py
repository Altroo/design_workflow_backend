import pytest
from rest_framework.test import APIClient

from .models import ChatThread, Project, Task
from .tests import make_designer, make_manager
from .views import linked_thread_user_ids

pytestmark = pytest.mark.django_db


@pytest.fixture
def shared_project():
    owner = make_designer("owner@collaboration.test")
    first = make_designer("first@collaboration.test")
    second = make_designer("second@collaboration.test")
    outsider = make_designer("outsider@collaboration.test")
    client = APIClient()
    client.force_authenticate(owner)
    response = client.post("/api/design-workflow/projects/", {
        "name": "Shared studio", "manager_id": owner.pk,
        "collaborator_ids": [first.pk, second.pk],
    }, format="json")
    assert response.status_code == 201
    project = Project.objects.get(pk=response.data["id"])
    return client, project, owner, first, second, outsider


def test_collaborators_can_create_tasks_but_not_manage_project(shared_project):
    client, project, owner, first, second, outsider = shared_project
    for member in (first, second):
        client.force_authenticate(member)
        detail = client.get(f"/api/design-workflow/projects/{project.pk}/")
        assert detail.data["can_work"] is True
        assert detail.data["can_manage"] is False
        assert {person["id"] for person in detail.data["collaborators"]} == {first.pk, second.pk}
        created = client.post("/api/design-workflow/tasks/", {
            "title": f"Work by {member.pk}", "project_id": project.pk, "current_assignee_id": member.pk,
        }, format="json")
        assert created.status_code == 201
        assert client.patch(f"/api/design-workflow/projects/{project.pk}/", {"collaborator_ids": [outsider.pk]}, format="json").status_code == 403
        assert client.patch(f"/api/design-workflow/projects/{project.pk}/", {"archived": True}, format="json").status_code == 403
        assert project.pk in [item["id"] for item in client.get("/api/design-workflow/projects/").data]
    client.force_authenticate(first)
    mine = client.get("/api/design-workflow/tasks/?my_projects=true")
    assert len(mine.data) == 2  # Includes teammates' tasks, without duplicate rows.
    client.force_authenticate(outsider)
    assert client.get(f"/api/design-workflow/projects/{project.pk}/").data["can_work"] is False
    assert client.post("/api/design-workflow/tasks/", {"title": "No access", "project_id": project.pk}, format="json").status_code == 403


def test_task_permissions_stay_with_assignee(shared_project):
    client, project, owner, first, second, _ = shared_project
    task = Task.objects.create(project=project, title="Assigned work", current_assignee=first, created_by=owner, updated_by=owner)
    client.force_authenticate(second)
    assert client.patch(f"/api/design-workflow/tasks/{task.pk}/", {"title": "Not mine"}, format="json").status_code == 403
    client.force_authenticate(first)
    assert client.patch(f"/api/design-workflow/tasks/{task.pk}/", {"title": "My update"}, format="json").status_code == 200
    project.collaborators.remove(first)
    assert client.post("/api/design-workflow/tasks/", {"title": "New", "project_id": project.pk}, format="json").status_code == 403
    assert client.patch(f"/api/design-workflow/tasks/{task.pk}/", {"title": "Still assigned"}, format="json").status_code == 200


def test_project_chat_membership_and_removal(shared_project):
    client, project, owner, first, second, _ = shared_project
    client.force_authenticate(first)
    response = client.post("/api/design-workflow/chat/threads/", {"kind": "project", "project_id": project.pk}, format="json")
    assert response.status_code == 201
    thread = ChatThread.objects.get(pk=response.data["id"])
    assert {owner.pk, first.pk, second.pk}.issubset(linked_thread_user_ids(thread))
    assert client.post(f"/api/design-workflow/chat/threads/{thread.pk}/messages/", {"body": "Let's work together"}, format="json").status_code == 201
    client.force_authenticate(owner)
    assert client.patch(f"/api/design-workflow/projects/{project.pk}/", {"collaborator_ids": [second.pk]}, format="json").status_code == 200
    assert first.pk not in linked_thread_user_ids(thread)
    assert not thread.participants.filter(pk=first.pk).exists()
    client.force_authenticate(first)
    assert client.get(f"/api/design-workflow/chat/threads/{thread.pk}/messages/").status_code == 404
    assert thread.pk not in [item["id"] for item in client.get("/api/design-workflow/chat/threads/").data]


def test_manager_can_update_membership_and_inactive_members_rejected(shared_project):
    client, project, _, first, second, outsider = shared_project
    client.force_authenticate(make_manager("manager@collaboration.test"))
    response = client.patch(f"/api/design-workflow/projects/{project.pk}/", {"collaborator_ids": [outsider.pk]}, format="json")
    assert response.status_code == 200
    assert response.data["can_manage"] is True
    assert list(project.collaborators.all()) == [outsider]
    first.is_active = False
    first.save(update_fields=["is_active"])
    assert client.patch(f"/api/design-workflow/projects/{project.pk}/", {"collaborator_ids": [first.pk]}, format="json").status_code == 400
    assert list(project.collaborators.all()) == [outsider]
