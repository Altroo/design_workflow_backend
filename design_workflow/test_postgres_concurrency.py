"""Real row-lock regressions; skipped by the ordinary SQLite test suite."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.db import close_old_connections, connection, connections
from rest_framework.test import APIClient

from .models import Project, Task
from .serializers import ProjectWriteSerializer, TaskWriteSerializer
from .tests import make_designer

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql", reason="Requires real PostgreSQL row locks"
    ),
]


@pytest.fixture(name="shared_projects")
def shared_projects_fixture():
    owner = make_designer("pg-owner@example.test")
    collaborator = make_designer("pg-collaborator@example.test")
    source = Project.objects.create(name="Source", manager=owner)
    destination = Project.objects.create(name="Destination", manager=owner)
    source.collaborators.add(collaborator)
    destination.collaborators.add(collaborator)
    task = Task.objects.create(
        project=source, title="Original", created_by=owner, updated_by=owner
    )
    return owner, collaborator, source, destination, task


def _request(user, method, path, payload, application_name):
    close_old_connections()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('application_name', %s, false)", [application_name]
            )
            cursor.execute("SET lock_timeout = '5s'")
            cursor.execute("SET statement_timeout = '15s'")
        client = APIClient()
        client.force_authenticate(user)
        return getattr(client, method)(path, payload, format="json")
    finally:
        connections.close_all()


def _wait_for_database_lock(application_name):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT wait_event_type FROM pg_stat_activity WHERE application_name = %s",
                [application_name],
            )
            if any(row[0] == "Lock" for row in cursor.fetchall()):
                return
        sleep(0.01)
    pytest.fail("Concurrent request never waited for the intended PostgreSQL row lock")


@pytest.mark.parametrize("create", [True, False])
@pytest.mark.parametrize("incoming_first", [True, False])
def test_archive_serializes_with_incoming_create_and_move(
    shared_projects, create, incoming_first
):
    owner, collaborator, source, destination, task = shared_projects
    acquired = Event()
    release = Event()
    held_serializer = TaskWriteSerializer if incoming_first else ProjectWriteSerializer
    held_method = "create" if incoming_first and create else "update"
    original = getattr(held_serializer, held_method)

    def hold_after_row_lock(serializer, *args, **kwargs):
        # Both views have taken their row lock before serializer.save(). The
        # other connection is then observed in pg_stat_activity waiting on it.
        acquired.set()
        assert release.wait(8), "Test did not release the first row-lock holder"
        return original(serializer, *args, **kwargs)

    incoming_name = f"dw-qa-incoming-{uuid4().hex}"
    archive_name = f"dw-qa-archive-{uuid4().hex}"
    incoming_args = (
        collaborator,
        "post" if create else "patch",
        (
            "/api/design-workflow/tasks/"
            if create
            else f"/api/design-workflow/tasks/{task.pk}/"
        ),
        {"project_id": destination.pk, "title": "Incoming"},
        incoming_name,
    )
    archive_args = (
        owner,
        "patch",
        f"/api/design-workflow/projects/{destination.pk}/",
        {"archived": True},
        archive_name,
    )
    first_args, second_args = (
        (incoming_args, archive_args)
        if incoming_first
        else (archive_args, incoming_args)
    )

    with (
        patch.object(held_serializer, held_method, hold_after_row_lock),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        first = pool.submit(_request, *first_args)
        try:
            assert acquired.wait(5), "First request did not reach its locked write"
            second = pool.submit(_request, *second_args)
            _wait_for_database_lock(second_args[-1])
        finally:
            release.set()
        first_response, second_response = first.result(timeout=10), second.result(
            timeout=10
        )

    incoming, archive = (
        (first_response, second_response)
        if incoming_first
        else (second_response, first_response)
    )
    assert archive.status_code == 200
    destination.refresh_from_db()
    assert destination.archived
    if incoming_first:
        assert incoming.status_code == (201 if create else 200)
        incoming_task = Task.objects.get(pk=incoming.data["id"])
        assert incoming_task.project_id == destination.pk
        assert incoming_task.archived
    else:
        assert incoming.status_code == 400
        assert not Task.objects.filter(project=destination).exists()
        task.refresh_from_db()
        assert task.project_id == source.pk


@pytest.mark.parametrize("resource,field", [("tasks", "title"), ("projects", "name")])
def test_simultaneous_same_field_edits_are_serialized_then_conflict(
    shared_projects, resource, field
):
    owner, collaborator, source, _, task = shared_projects
    instance = task if resource == "tasks" else source
    serializer_class = (
        TaskWriteSerializer if resource == "tasks" else ProjectWriteSerializer
    )
    acquired = Event()
    release = Event()
    original = serializer_class.update

    def hold_first_save(serializer, *args, **kwargs):
        acquired.set()
        assert release.wait(8)
        return original(serializer, *args, **kwargs)

    path = f"/api/design-workflow/{resource}/{instance.pk}/"
    baseline = {field: getattr(instance, field)}
    second_name = f"dw-qa-conflict-{uuid4().hex}"
    # Project editing belongs to the owner; card editing also permits the peer.
    second_user = collaborator if resource == "tasks" else owner
    with (
        patch.object(serializer_class, "update", hold_first_save),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        first = pool.submit(
            _request,
            owner,
            "patch",
            path,
            {field: "First edit", "expected_values": baseline},
            f"dw-qa-first-{uuid4().hex}",
        )
        try:
            assert acquired.wait(5)
            second = pool.submit(
                _request,
                second_user,
                "patch",
                path,
                {field: "Second edit", "expected_values": baseline},
                second_name,
            )
            _wait_for_database_lock(second_name)
        finally:
            release.set()
        assert first.result(timeout=10).status_code == 200
        assert second.result(timeout=10).status_code == 409
    instance.refresh_from_db()
    assert getattr(instance, field) == "First edit"


@pytest.mark.parametrize("baseline", [[1, {}], [1, None], [True]])
def test_malformed_list_baselines_are_controlled_on_postgres(shared_projects, baseline):
    owner, _, source, _, _ = shared_projects
    client = APIClient()
    client.force_authenticate(owner)
    response = client.patch(
        f"/api/design-workflow/projects/{source.pk}/",
        {
            "collaborator_ids": [],
            "expected_values": {"collaborator_ids": baseline},
        },
        format="json",
    )
    assert response.status_code == 409
