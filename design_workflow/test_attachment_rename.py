from unittest.mock import patch

import pytest
from django.core.files.base import ContentFile
from rest_framework.test import APIClient

from .models import Project, Task, TaskAttachment
from .tests import make_designer, make_manager

pytestmark = pytest.mark.django_db


@pytest.fixture
def rename_context(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    owner = make_designer("attachment-owner@test.com")
    assignee = make_designer("attachment-assignee@test.com")
    collaborator = make_designer("attachment-collaborator@test.com")
    viewer = make_designer("attachment-viewer@test.com")
    manager = make_manager()
    project = Project.objects.create(name="Shared project", manager=owner)
    project.collaborators.add(collaborator)
    task = Task.objects.create(
        project=project,
        title="Card",
        current_assignee=assignee,
        created_by=owner,
        updated_by=owner,
    )
    attachment = TaskAttachment.objects.create(
        task=task,
        uploaded_by=assignee,
        name="Original brief.pdf",
        file=ContentFile(b"unchanged original bytes", name="original.pdf"),
        mime_type="application/pdf",
        size=24,
    )
    users = dict(
        owner=owner,
        assignee=assignee,
        collaborator=collaborator,
        viewer=viewer,
        manager=manager,
    )
    return task, attachment, users


def rename(client, task, attachment, data):
    return client.patch(
        f"/api/design-workflow/tasks/{task.pk}/attachments/{attachment.pk}/",
        data,
        format="json",
    )


@pytest.mark.parametrize("role", ["owner", "assignee", "collaborator", "manager"])
def test_members_rename_without_changing_file_and_broadcast(rename_context, role):
    task, attachment, users = rename_context
    client = APIClient()
    client.force_authenticate(users[role])
    original_file = attachment.file.name
    original_uploader = attachment.uploaded_by_id
    with patch("design_workflow.views.broadcast_task_event") as broadcast:
        response = rename(client, task, attachment, {"name": "  Final client brief  "})
    assert response.status_code == 200
    assert response.data["name"] == "Final client brief"
    attachment.refresh_from_db()
    assert attachment.file.name == original_file
    assert attachment.uploaded_by_id == original_uploader
    assert attachment.mime_type == "application/pdf"
    with attachment.file.open("rb") as source:
        assert source.read() == b"unchanged original bytes"
    activity = task.activities.get()
    assert activity.actor_id == users[role].pk
    assert activity.metadata["previous_name"] == "Original brief.pdf"
    assert activity.metadata["attachment_renamed"] == "Final client brief"
    broadcast.assert_called_once_with(task, "attachment_renamed")


def test_read_only_and_anonymous_users_cannot_rename(rename_context):
    task, attachment, users = rename_context
    client = APIClient()
    assert rename(client, task, attachment, {"name": "Changed"}).status_code in (
        401,
        403,
    )
    client.force_authenticate(users["viewer"])
    assert rename(client, task, attachment, {"name": "Changed"}).status_code == 404
    attachment.refresh_from_db()
    assert attachment.name == "Original brief.pdf"


@pytest.mark.parametrize(
    "data", [{}, {"name": ""}, {"name": "   "}, {"name": None}, {"name": "a" * 256}]
)
def test_invalid_names_leave_attachment_unchanged(rename_context, data):
    task, attachment, users = rename_context
    client = APIClient()
    client.force_authenticate(users["owner"])
    with patch("design_workflow.views.broadcast_task_event") as broadcast:
        assert rename(client, task, attachment, data).status_code == 400
    attachment.refresh_from_db()
    assert attachment.name == "Original brief.pdf"
    assert not task.activities.exists()
    broadcast.assert_not_called()


def test_rename_is_scoped_to_task_and_only_updates_name(rename_context):
    task, attachment, users = rename_context
    client = APIClient()
    client.force_authenticate(users["owner"])
    other_task = Task.objects.create(
        project=task.project,
        title="Other",
        created_by=users["owner"],
        updated_by=users["owner"],
    )
    assert (
        rename(client, other_task, attachment, {"name": "Wrong card"}).status_code
        == 404
    )
    response = rename(
        client,
        task,
        attachment,
        {
            "name": "a" * 255,
            "file": "replacement.pdf",
            "size": 1,
            "mime_type": "text/plain",
            "uploaded_by": users["viewer"].pk,
        },
    )
    assert response.status_code == 200
    attachment.refresh_from_db()
    assert attachment.name == "a" * 255
    assert attachment.file.name.endswith("original.pdf")
    assert attachment.mime_type == "application/pdf"
    assert attachment.size == 24
    assert attachment.uploaded_by_id == users["assignee"].pk


def test_unchanged_name_is_a_noop(rename_context):
    task, attachment, users = rename_context
    client = APIClient()
    client.force_authenticate(users["owner"])
    with patch("design_workflow.views.broadcast_task_event") as broadcast:
        response = rename(client, task, attachment, {"name": attachment.name})
    assert response.status_code == 200
    assert not task.activities.exists()
    broadcast.assert_not_called()
