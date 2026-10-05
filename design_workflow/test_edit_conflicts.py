from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Notification, NotificationType, Project, Task, TaskLabel, TaskStatus
from .serializers import ProjectSummarySerializer, TaskDetailSerializer
from .tests import make_designer
from .time_tracking import reconcile_task_work_sessions
from .test_time_tracking import at

pytestmark = pytest.mark.django_db


@pytest.fixture
def editable():
    owner = make_designer("conflict-owner@example.test")
    project = Project.objects.create(name="Original project", manager=owner)
    task = Task.objects.create(project=project, title="Original card", created_by=owner, updated_by=owner)
    client = APIClient()
    client.force_authenticate(owner)
    return client, project, task, owner


@pytest.mark.parametrize("resource,field", [("tasks", "title"), ("projects", "name")])
def test_same_field_conflict_is_rejected_without_overwriting(editable, resource, field):
    client, project, task, _ = editable
    obj = task if resource == "tasks" else project
    old = getattr(obj, field)
    type(obj).objects.filter(pk=obj.pk).update(**{field: "Changed by collaborator"})
    response = client.patch(f"/api/design-workflow/{resource}/{obj.pk}/", {
        field: "My pending edit", "expected_values": {field: old},
    }, format="json")
    assert response.status_code == 409
    obj.refresh_from_db()
    assert getattr(obj, field) == "Changed by collaborator"


def test_unrelated_remote_field_does_not_block_save(editable):
    client, _, task, _ = editable
    Task.objects.filter(pk=task.pk).update(description="Their new description")
    response = client.patch(f"/api/design-workflow/tasks/{task.pk}/", {
        "title": "My title", "expected_values": {"title": task.title},
    }, format="json")
    assert response.status_code == 200
    task.refresh_from_db()
    assert task.title == "My title"
    assert task.description == "Their new description"


@pytest.mark.parametrize("value", [[1, {}], [1, None], [True], "bad", 2])
def test_malformed_baselines_never_cause_server_error(editable, value):
    client, project, _, _ = editable
    response = client.patch(f"/api/design-workflow/projects/{project.pk}/", {
        "collaborator_ids": [], "expected_values": {"collaborator_ids": value},
    }, format="json")
    assert response.status_code == 409


def test_conflict_guard_does_not_grant_edit_permission(editable):
    client, _, task, _ = editable
    client.force_authenticate(make_designer("conflict-outsider@example.test"))
    response = client.patch(f"/api/design-workflow/tasks/{task.pk}/", {
        "title": "Denied", "expected_values": {"title": task.title},
    }, format="json")
    assert response.status_code == 403


def test_label_baseline_is_private_to_editor_and_preserves_others(editable):
    client, _, task, owner = editable
    colleague = make_designer("private-label-colleague@example.test")
    private = TaskLabel.objects.create(name="Other person's label", color="#118844", created_by=colleague)
    mine = TaskLabel.objects.create(name="My label", color="#2244aa", created_by=owner)
    task.labels.add(private)
    response = client.patch(f"/api/design-workflow/tasks/{task.pk}/", {
        "label_ids": [mine.pk], "expected_values": {"label_ids": []},
    }, format="json")
    assert response.status_code == 200
    assert set(task.labels.values_list("pk", flat=True)) == {mine.pk, private.pk}


def test_archive_committed_during_validation_rechecked_before_card_create(editable):
    client, _, task, owner = editable
    destination = Project.objects.create(name="Archiving destination", manager=owner)
    from .serializers import TaskWriteSerializer
    validate = TaskWriteSerializer.validate
    changed = False

    def archive_after_validation(serializer, attrs):
        nonlocal changed
        result = validate(serializer, attrs)
        if not changed:
            Project.objects.filter(pk=destination.pk).update(archived=True)
            changed = True
        return result

    with patch.object(TaskWriteSerializer, "validate", archive_after_validation):
        response = client.post("/api/design-workflow/tasks/", {"project_id": destination.pk, "title": "Incoming"}, format="json")
    assert response.status_code == 400
    assert not Task.objects.filter(project=destination).exists()


def test_project_serialization_does_not_double_count_session_closed_between_items(editable):
    _, first, _, owner = editable
    second = Project.objects.create(name="Second", manager=owner)
    task = Task.objects.create(project=second, title="Running", status=TaskStatus.IN_PROGRESS,
                               created_by=owner, updated_by=owner)
    reconcile_task_work_sessions(task, now=at(14, 9))
    context = {}
    with patch("design_workflow.time_tracking.timezone.now", return_value=at(14, 10)):
        ProjectSummarySerializer(first, context=context).data
        Task.objects.filter(pk=task.pk).update(status=TaskStatus.TODO)
        reconcile_task_work_sessions(task, now=at(14, 10))
        data = ProjectSummarySerializer(second, context=context).data
        task.refresh_from_db()
        card_data = TaskDetailSerializer(task, context=context).data
    assert data["total_logged_minutes"] == 60
    assert card_data["actual_minutes"] == 60
    assert card_data["total_logged_minutes"] == 60


def test_future_snoozed_notifications_excluded_from_unread_only(editable):
    client, project, task, owner = editable
    notice = Notification.objects.create(recipient=owner, type=NotificationType.TASK_ASSIGNED,
        task=task, project=project, snoozed_until=timezone.now() + timedelta(hours=1))
    assert client.get("/api/design-workflow/notifications/?unread=true").data == []
    assert client.get("/api/design-workflow/notifications/").data[0]["id"] == notice.pk
