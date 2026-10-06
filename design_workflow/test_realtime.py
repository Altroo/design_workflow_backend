from unittest.mock import AsyncMock, patch

import pytest
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.db import transaction
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from design_workflow_backend.asgi import application

from .models import Project, Task, TaskStatus
from .services import broadcast_task_event, broadcast_workflow_event
from .tests import make_designer


@pytest.mark.django_db
def test_events_wait_for_commit_and_rollback_discards_them(
    django_capture_on_commit_callbacks,
):
    with patch("design_workflow.services.get_channel_layer") as layer:
        layer.return_value.group_send = AsyncMock()
        with django_capture_on_commit_callbacks(execute=True), transaction.atomic():
            broadcast_workflow_event("projects")
            layer.return_value.group_send.assert_not_called()
        assert layer.return_value.group_send.call_count == 1
        layer.return_value.group_send.reset_mock()
        with (
            django_capture_on_commit_callbacks(execute=True),
            pytest.raises(ValueError),
            transaction.atomic(),
        ):
            broadcast_workflow_event("projects")
            raise ValueError("rollback")
        layer.return_value.group_send.assert_not_called()


@pytest.mark.django_db
def test_card_events_include_read_only_board_viewers(
    django_capture_on_commit_callbacks,
):
    owner = make_designer("live-owner@example.test")
    project = Project.objects.create(name="Live board", manager=owner)
    task = Task.objects.create(
        project=project, title="Card", created_by=owner, updated_by=owner
    )
    with patch("design_workflow.services.get_channel_layer") as layer:
        layer.return_value.group_send = AsyncMock()
        with django_capture_on_commit_callbacks(execute=True):
            broadcast_task_event(task, "attachment_added", recipients=[owner.pk])
        group, payload = layer.return_value.group_send.call_args.args
        assert group == "workflow"
        assert payload["message"]["task_id"] == task.pk
        assert payload["message"]["event"] == "attachment_added"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "path,data,scope",
    [
        ("projects/", {"name": "Live project"}, "projects"),
        ("labels/", {"name": "Live label", "color": "#22AA55"}, "labels"),
        ("views/", {"name": "Live view", "visibility": "private"}, "views"),
    ],
)
def test_non_card_mutations_publish_invalidation(
    path, data, scope, django_capture_on_commit_callbacks
):
    user = make_designer(f"{scope}@live.test")
    client = APIClient()
    client.force_authenticate(user)
    if scope == "projects":
        data = {**data, "manager_id": user.pk}
    with patch("design_workflow.services.get_channel_layer") as layer:
        layer.return_value.group_send = AsyncMock()
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(f"/api/design-workflow/{path}", data, format="json")
        assert response.status_code == 201
        assert any(
            call.args[1].get("message") == {"type": "WORKFLOW_EVENT", "scope": scope}
            for call in layer.return_value.group_send.call_args_list
        )


@pytest.mark.django_db(transaction=True)
def test_real_sockets_observe_card_move_and_private_chat_stays_private():
    owner = make_designer("socket-owner@example.test")
    collaborator = make_designer("socket-collaborator@example.test")
    viewer = make_designer("socket-viewer@example.test")
    project = Project.objects.create(name="Live shared project", manager=owner)
    project.collaborators.add(collaborator)
    task = Task.objects.create(
        project=project, title="Live card", created_by=owner, updated_by=owner
    )
    tokens = [str(AccessToken.for_user(user)) for user in (owner, collaborator, viewer)]

    def move():
        client = APIClient()
        client.force_authenticate(collaborator)
        return client.patch(
            "/api/design-workflow/tasks/reorder/",
            {
                "moved_task_id": task.pk,
                "tasks": [{"id": task.pk, "status": TaskStatus.TODO, "sort_order": 0}],
            },
            format="json",
        ).status_code

    def send_private():
        client = APIClient()
        client.force_authenticate(owner)
        thread = client.post(
            "/api/design-workflow/chat/threads/",
            {"kind": "private", "recipient_id": collaborator.pk},
            format="json",
        )
        assert thread.status_code == 201
        return client.post(
            f'/api/design-workflow/chat/threads/{thread.data["id"]}/messages/',
            {"body": "Private live message"},
            format="json",
        ).status_code

    async def scenario():
        sockets = [
            WebsocketCommunicator(application, f"/ws?token={token}") for token in tokens
        ]
        try:
            for socket in sockets:
                assert (await socket.connect())[0]
            # Drain presence snapshots before testing mutations.
            for socket in sockets:
                while not await socket.receive_nothing(timeout=0.05):
                    await socket.receive_json_from()
            assert await database_sync_to_async(move)() == 200
            for socket in sockets:
                event = await socket.receive_json_from()
                assert event["message"]["type"] == "TASK_EVENT"
                assert event["message"]["task_id"] == task.pk
                assert event["message"]["status"] == TaskStatus.TODO
            assert await database_sync_to_async(send_private)() == 201
            for socket in sockets[:2]:
                for _ in range(4):
                    event = await socket.receive_json_from()
                    if event.get("type") == "chat_message":
                        assert event["message"]["body"] == "Private live message"
                        break
                else:
                    pytest.fail("Private message did not reach its participant")
            assert await sockets[2].receive_nothing(timeout=0.1)
        finally:
            for socket in sockets:
                await socket.disconnect()

    async_to_sync(scenario)()
