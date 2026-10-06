import pytest
from asgiref.sync import async_to_sync
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from .models import (
    ChatThread,
    Notification,
    NotificationType,
    Project,
    Task,
    TaskStatus,
)
from .services import related_task_user_ids
from .tests import make_designer, make_manager
from .views import linked_thread_user_ids

pytestmark = pytest.mark.django_db


@pytest.fixture(name="shared_project")
def shared_project_fixture():
    owner = make_designer("owner@collaboration.test")
    first = make_designer("first@collaboration.test")
    second = make_designer("second@collaboration.test")
    outsider = make_designer("outsider@collaboration.test")
    client = APIClient()
    client.force_authenticate(owner)
    response = client.post(
        "/api/design-workflow/projects/",
        {
            "name": "Shared studio",
            "manager_id": owner.pk,
            "collaborator_ids": [first.pk, second.pk],
        },
        format="json",
    )
    assert response.status_code == 201
    project = Project.objects.get(pk=response.data["id"])
    return client, project, owner, first, second, outsider


def test_collaborators_can_create_tasks_but_not_manage_project(shared_project):
    client, project, _, first, second, outsider = shared_project
    for member in (first, second):
        client.force_authenticate(member)
        detail = client.get(f"/api/design-workflow/projects/{project.pk}/")
        assert detail.data["can_work"] is True
        assert detail.data["can_manage"] is False
        assert {person["id"] for person in detail.data["collaborators"]} == {
            first.pk,
            second.pk,
        }
        created = client.post(
            "/api/design-workflow/tasks/",
            {
                "title": f"Work by {member.pk}",
                "project_id": project.pk,
                "current_assignee_id": member.pk,
            },
            format="json",
        )
        assert created.status_code == 201
        assert (
            client.patch(
                f"/api/design-workflow/projects/{project.pk}/",
                {"collaborator_ids": [outsider.pk]},
                format="json",
            ).status_code
            == 403
        )
        assert (
            client.patch(
                f"/api/design-workflow/projects/{project.pk}/",
                {"archived": True},
                format="json",
            ).status_code
            == 403
        )
        assert project.pk in [
            item["id"] for item in client.get("/api/design-workflow/projects/").data
        ]
    client.force_authenticate(first)
    mine = client.get("/api/design-workflow/tasks/?my_projects=true")
    assert len(mine.data) == 2  # Includes teammates' tasks, without duplicate rows.
    client.force_authenticate(outsider)
    assert (
        client.get(f"/api/design-workflow/projects/{project.pk}/").data["can_work"]
        is False
    )
    assert (
        client.post(
            "/api/design-workflow/tasks/",
            {"title": "No access", "project_id": project.pk},
            format="json",
        ).status_code
        == 403
    )


def test_all_project_members_can_edit_and_move_each_others_tasks(shared_project):
    client, project, owner, first, second, outsider = shared_project
    task = Task.objects.create(
        project=project,
        title="Assigned work",
        current_assignee=first,
        created_by=owner,
        updated_by=owner,
    )
    for member in (owner, first, second):
        client.force_authenticate(member)
        assert (
            client.get(f"/api/design-workflow/tasks/{task.pk}/").data["can_edit"]
            is True
        )
        assert client.get("/api/design-workflow/tasks/").data[0]["can_edit"] is True
        assert (
            client.get(f"/api/design-workflow/projects/{project.pk}/").data["tasks"][0][
                "can_edit"
            ]
            is True
        )
        assert (
            client.patch(
                f"/api/design-workflow/tasks/{task.pk}/",
                {"title": f"Updated by {member.pk}"},
                format="json",
            ).status_code
            == 200
        )
        response = client.patch(
            "/api/design-workflow/tasks/reorder/",
            {
                "moved_task_id": task.pk,
                "tasks": [
                    {
                        "id": task.pk,
                        "status": TaskStatus.IN_PROGRESS,
                        "sort_order": member.pk,
                    }
                ],
            },
            format="json",
        )
        assert response.status_code == 200
        task.refresh_from_db()
        assert task.status == TaskStatus.IN_PROGRESS
        assert task.sort_order == 0
        assert task.current_assignee_id == first.pk
        assert task.updated_by_id == member.pk

    client.force_authenticate(outsider)
    assert (
        client.get(f"/api/design-workflow/tasks/{task.pk}/").data["can_edit"] is False
    )
    assert (
        client.patch(
            f"/api/design-workflow/tasks/{task.pk}/",
            {"title": "Not mine"},
            format="json",
        ).status_code
        == 403
    )
    assert (
        client.patch(
            "/api/design-workflow/tasks/reorder/",
            {
                "moved_task_id": task.pk,
                "tasks": [{"id": task.pk, "status": TaskStatus.TODO, "sort_order": 0}],
            },
            format="json",
        ).status_code
        == 403
    )


def test_multiple_project_creation_uses_explicit_destination(shared_project):
    client, shared, _, first, _, outsider = shared_project
    own = Project.objects.create(name="Second writable project", manager=first)
    read_only = Project.objects.create(name="Read-only project", manager=outsider)
    client.force_authenticate(first)
    assert (
        client.post(
            "/api/design-workflow/tasks/",
            {"title": "Missing destination"},
            format="json",
        ).status_code
        == 400
    )
    response = client.post(
        "/api/design-workflow/tasks/",
        {"title": "Chosen destination", "project_id": own.pk},
        format="json",
    )
    assert response.status_code == 201
    assert Task.objects.get(pk=response.data["id"]).project_id == own.pk
    assert not shared.tasks.exists()
    assert (
        client.post(
            "/api/design-workflow/tasks/",
            {"title": "Forbidden destination", "project_id": read_only.pk},
            format="json",
        ).status_code
        == 403
    )
    own.archived = True
    own.save(update_fields=["archived"])
    assert (
        client.post(
            "/api/design-workflow/tasks/",
            {"title": "Archived destination", "project_id": own.pk},
            format="json",
        ).status_code
        == 400
    )
    assert Task.objects.count() == 1


def test_removed_collaborator_retains_only_individually_assigned_tasks(shared_project):
    client, project, owner, first, second, _ = shared_project
    own = Task.objects.create(
        project=project,
        title="Assigned work",
        current_assignee=first,
        created_by=owner,
        updated_by=owner,
    )
    teammates = Task.objects.create(
        project=project,
        title="Teammate work",
        current_assignee=second,
        created_by=owner,
        updated_by=owner,
    )
    project.collaborators.remove(first)
    client.force_authenticate(first)
    assert (
        client.post(
            "/api/design-workflow/tasks/",
            {"title": "New", "project_id": project.pk},
            format="json",
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/api/design-workflow/tasks/{own.pk}/",
            {"title": "Still assigned"},
            format="json",
        ).status_code
        == 200
    )
    assert (
        client.get(f"/api/design-workflow/tasks/{teammates.pk}/").data["can_edit"]
        is False
    )
    assert (
        client.patch(
            f"/api/design-workflow/tasks/{teammates.pk}/",
            {"title": "Not mine"},
            format="json",
        ).status_code
        == 403
    )


def test_owner_can_rename_created_card_without_changing_other_fields(shared_project):
    client, project, owner, first, _, _ = shared_project
    task = Task.objects.create(
        project=project,
        title="Original title",
        description="Keep this description",
        status=TaskStatus.TODO,
        current_assignee=first,
        created_by=owner,
        updated_by=owner,
    )
    response = client.patch(
        f"/api/design-workflow/tasks/{task.pk}/",
        {"title": "Renamed card"},
        format="json",
    )
    assert response.status_code == 200
    task.refresh_from_db()
    assert task.title == "Renamed card"
    assert task.description == "Keep this description"
    assert task.status == TaskStatus.TODO
    assert task.current_assignee_id == first.pk
    assert task.created_by_id == owner.pk
    for invalid_title in ("   ", "x" * 256):
        assert (
            client.patch(
                f"/api/design-workflow/tasks/{task.pk}/",
                {"title": invalid_title},
                format="json",
            ).status_code
            == 400
        )
    task.refresh_from_db()
    assert task.title == "Renamed card"


def test_collaborator_can_use_unassigned_card_tools_but_cannot_approve(
    shared_project, settings, tmp_path
):
    settings.MEDIA_ROOT = tmp_path
    client, project, owner, first, second, outsider = shared_project
    task = Task.objects.create(
        project=project, title="Shared tools", created_by=owner, updated_by=owner
    )
    base = f"/api/design-workflow/tasks/{task.pk}"
    client.force_authenticate(second)
    group = client.post(f"{base}/checklists/", {"title": "Delivery"}, format="json")
    assert group.status_code == 201
    item = client.post(
        f"{base}/checklist/",
        {"title": "Check colors", "checklist_id": group.data["id"]},
        format="json",
    )
    assert item.status_code == 201
    checked = client.patch(
        f"{base}/checklist/{item.data['id']}/", {"done": True}, format="json"
    )
    assert checked.status_code == 200
    assert checked.data["done"] is True
    assert (
        client.post(
            f"{base}/comments/", {"body": "Checked by collaborator"}, format="json"
        ).status_code
        == 201
    )
    attachment = client.post(
        f"{base}/attachments/",
        {
            "file": SimpleUploadedFile(
                "brief.txt", b"shared brief", content_type="text/plain"
            ),
            "name": "Shared brief",
        },
        format="multipart",
    )
    assert attachment.status_code == 201
    cover = client.post(
        f"{base}/cover/",
        {
            "cover_image": SimpleUploadedFile(
                "preview.gif",
                b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;",
                content_type="image/gif",
            ),
            "name": "Shared preview",
        },
        format="multipart",
    )
    assert cover.status_code == 200

    client.force_authenticate(outsider)
    assert (
        client.delete(f"{base}/attachments/{attachment.data['id']}/").status_code == 404
    )
    assert client.delete(f"{base}/cover/").status_code == 404
    assert (
        client.post(
            f"{base}/comments/", {"body": "Not allowed"}, format="json"
        ).status_code
        == 404
    )

    # A different collaborator can manage tools created by their teammate.
    client.force_authenticate(first)
    assert client.delete(f"{base}/checklists/{group.data['id']}/").status_code == 204
    assert (
        client.delete(f"{base}/attachments/{attachment.data['id']}/").status_code == 204
    )
    assert client.delete(f"{base}/cover/").status_code == 200
    assert (
        client.post(f"{base}/archive/", {"archived": True}, format="json").status_code
        == 200
    )
    assert (
        client.post(f"{base}/archive/", {"archived": False}, format="json").status_code
        == 200
    )
    assert (
        client.post(
            f"{base}/review/", {"review_state": "needs_review"}, format="json"
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"{base}/review/", {"review_state": "approved"}, format="json"
        ).status_code
        == 403
    )
    task.refresh_from_db()
    assert task.status == TaskStatus.IN_REVIEW
    assert task.review_requested_by_id == first.pk
    assert {owner.pk, first.pk, second.pk}.issubset(related_task_user_ids(task))


def test_collaborator_cannot_move_tasks_into_other_projects_or_reorder_readonly_cards(
    shared_project,
):
    client, project, owner, first, _, outsider = shared_project
    other_project = Project.objects.create(name="Other team", manager=outsider)
    writable = Task.objects.create(
        project=project, title="Shared card", created_by=owner, updated_by=owner
    )
    readonly = Task.objects.create(
        project=other_project,
        title="Read only",
        status=TaskStatus.TODO,
        sort_order=9,
        created_by=outsider,
        updated_by=outsider,
    )
    client.force_authenticate(first)
    assert (
        client.patch(
            f"/api/design-workflow/tasks/{writable.pk}/",
            {"project_id": other_project.pk},
            format="json",
        ).status_code
        == 403
    )
    response = client.patch(
        "/api/design-workflow/tasks/reorder/",
        {
            "moved_task_id": writable.pk,
            "tasks": [
                {"id": writable.pk, "status": TaskStatus.IN_PROGRESS, "sort_order": 1},
                {"id": readonly.pk, "status": TaskStatus.IN_PROGRESS, "sort_order": 0},
            ],
        },
        format="json",
    )
    assert response.status_code == 200
    writable.refresh_from_db()
    readonly.refresh_from_db()
    assert writable.project_id == project.pk
    assert writable.status == TaskStatus.IN_PROGRESS
    assert readonly.sort_order == 9
    assert readonly.status == TaskStatus.TODO


def test_mixed_board_reorder_preserves_placement_and_other_cards_relative_order(
    shared_project,
):
    client, project, owner, first, _, outsider = shared_project
    other_project = Project.objects.create(name="Read only project", manager=outsider)
    before = Task.objects.create(
        project=other_project,
        title="First read only",
        sort_order=0,
        created_by=outsider,
        updated_by=outsider,
    )
    moving = Task.objects.create(
        project=project,
        title="Shared card",
        sort_order=1,
        created_by=owner,
        updated_by=owner,
    )
    after = Task.objects.create(
        project=other_project,
        title="Second read only",
        sort_order=2,
        created_by=outsider,
        updated_by=outsider,
    )
    client.force_authenticate(first)
    response = client.patch(
        "/api/design-workflow/tasks/reorder/",
        {
            "moved_task_id": moving.pk,
            "tasks": [
                {"id": moving.pk, "status": TaskStatus.BACKLOG, "sort_order": 0},
                {"id": before.pk, "status": TaskStatus.BACKLOG, "sort_order": 1},
                {"id": after.pk, "status": TaskStatus.BACKLOG, "sort_order": 2},
            ],
        },
        format="json",
    )
    assert response.status_code == 200
    assert list(
        Task.objects.order_by("sort_order", "id").values_list("id", flat=True)
    ) == [moving.pk, before.pk, after.pk]
    before.refresh_from_db()
    assert before.updated_by_id == outsider.pk
    # Hidden cards are kept; forged indices cannot reverse read-only neighbors.
    assert (
        client.patch(
            "/api/design-workflow/tasks/reorder/",
            {
                "moved_task_id": moving.pk,
                "tasks": [
                    {"id": after.pk, "status": TaskStatus.BACKLOG, "sort_order": 0},
                    {"id": moving.pk, "status": TaskStatus.BACKLOG, "sort_order": 1},
                ],
            },
            format="json",
        ).status_code
        == 200
    )
    assert list(
        Task.objects.order_by("sort_order", "id").values_list("id", flat=True)
    ) == [before.pk, after.pk, moving.pk]


def test_old_notification_cannot_restore_removed_collaborator_access(shared_project):
    client, project, owner, first, _, _ = shared_project
    task = Task.objects.create(
        project=project, title="Unassigned card", created_by=owner, updated_by=owner
    )
    notification = Notification.objects.create(
        recipient=first, task=task, project=project, type=NotificationType.TASK_ASSIGNED
    )
    project.collaborators.remove(first)
    client.force_authenticate(first)
    assert (
        client.post(
            f"/api/design-workflow/notifications/{notification.pk}/action/",
            {"action": "accept_assignment"},
            format="json",
        ).status_code
        == 403
    )
    task.refresh_from_db()
    assert task.current_assignee_id is None


def test_archived_card_reorder_does_not_change_active_board(shared_project):
    client, project, owner, first, _, _ = shared_project
    active = Task.objects.create(
        project=project,
        title="Active",
        sort_order=8,
        created_by=owner,
        updated_by=owner,
    )
    before = Task.objects.create(
        project=project,
        title="Archived first",
        archived=True,
        sort_order=0,
        created_by=owner,
        updated_by=owner,
    )
    moving = Task.objects.create(
        project=project,
        title="Archived moving",
        archived=True,
        sort_order=1,
        created_by=owner,
        updated_by=owner,
    )
    client.force_authenticate(first)
    assert (
        client.patch(
            "/api/design-workflow/tasks/reorder/",
            {
                "moved_task_id": moving.pk,
                "tasks": [
                    {"id": moving.pk, "status": TaskStatus.BACKLOG, "sort_order": 0},
                    {"id": before.pk, "status": TaskStatus.BACKLOG, "sort_order": 1},
                ],
            },
            format="json",
        ).status_code
        == 200
    )
    assert list(
        Task.objects.filter(archived=True)
        .order_by("sort_order", "id")
        .values_list("id", flat=True)
    ) == [moving.pk, before.pk]
    active.refresh_from_db()
    assert active.sort_order == 8


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", ["project", "task"])
def test_websocket_chat_respects_current_membership(shared_project, kind, monkeypatch):
    # Channels closes old DB connections around each async bridge. Real
    # transactions avoid closing pytest's wrapping TestCase transaction.
    from ws.consumers import ChatConsumer

    _, project, owner, first, second, outsider = shared_project
    sent_messages = []

    class FakeChannelLayer:
        @staticmethod
        async def group_send(group, event):
            sent_messages.append((group, event))

    monkeypatch.setattr(
        "design_workflow.views.get_channel_layer", lambda: FakeChannelLayer()
    )
    task = Task.objects.create(
        project=project,
        title="Socket card",
        current_assignee=owner,
        created_by=owner,
        updated_by=owner,
    )
    thread = ChatThread.objects.create(
        kind=kind, **({"project": project} if kind == "project" else {"task": task})
    )
    consumer = ChatConsumer()
    consumer.user = first
    payload = {"thread_id": thread.pk, "body": "Collaborating", "is_typing": True}
    create_message = async_to_sync(consumer._create_message)
    typing_payload = async_to_sync(consumer._typing_payload)
    message_id = create_message(payload)
    assert thread.messages.filter(pk=message_id, sender=first).exists()
    assert {f"user_{user.pk}" for user in (owner, first, second)}.issubset(
        {group for group, _ in sent_messages}
    )
    assert f"user_{outsider.pk}" not in {group for group, _ in sent_messages}
    assert all(
        event["type"] == "chat.message" and event["message"]["id"] == message_id
        for _, event in sent_messages
    )
    assert typing_payload(payload) is not None
    sent_messages.clear()
    project.collaborators.remove(first)
    assert create_message(payload) is None
    assert typing_payload(payload) is None
    consumer.user = outsider
    assert create_message(payload) is None
    assert typing_payload(payload) is None
    assert not sent_messages
    assert thread.messages.count() == 1
    consumer.user = second
    assert first.pk not in typing_payload(payload)["participant_ids"]
    sent_messages.clear()
    message_id = create_message(payload)
    assert thread.messages.filter(pk=message_id, sender=second).exists()
    assert {f"user_{owner.pk}", f"user_{second.pk}"}.issubset(
        {group for group, _ in sent_messages}
    )
    assert f"user_{first.pk}" not in {group for group, _ in sent_messages}


def test_task_chat_includes_collaborators_and_revokes_removed_members(shared_project):
    client, project, owner, first, second, _ = shared_project
    task = Task.objects.create(
        project=project,
        title="Shared chat",
        current_assignee=owner,
        created_by=owner,
        updated_by=owner,
    )
    client.force_authenticate(first)
    response = client.post(
        "/api/design-workflow/chat/threads/",
        {"kind": "task", "task_id": task.pk},
        format="json",
    )
    assert response.status_code == 201
    thread = ChatThread.objects.get(pk=response.data["id"])
    assert {owner.pk, first.pk, second.pk}.issubset(linked_thread_user_ids(thread))
    task.comments.create(author=first, body="Past collaboration")
    task.time_entries.create(user=first, minutes=30)
    for member in (first, second):
        client.force_authenticate(member)
        assert thread.pk in [
            item["id"] for item in client.get("/api/design-workflow/chat/threads/").data
        ]
        assert (
            client.post(
                f"/api/design-workflow/chat/threads/{thread.pk}/messages/",
                {"body": "Shared task message"},
                format="json",
            ).status_code
            == 201
        )
    client.force_authenticate(owner)
    assert (
        client.patch(
            f"/api/design-workflow/projects/{project.pk}/",
            {"collaborator_ids": [second.pk]},
            format="json",
        ).status_code
        == 200
    )
    assert first.pk not in linked_thread_user_ids(thread)
    assert not thread.participants.filter(pk=first.pk).exists()
    assert first.pk not in related_task_user_ids(task)
    client.force_authenticate(first)
    assert (
        client.get(
            f"/api/design-workflow/chat/threads/{thread.pk}/messages/"
        ).status_code
        == 404
    )
    assert thread.pk not in [
        item["id"] for item in client.get("/api/design-workflow/chat/threads/").data
    ]


def test_project_chat_membership_and_removal(shared_project):
    client, project, owner, first, second, _ = shared_project
    client.force_authenticate(first)
    response = client.post(
        "/api/design-workflow/chat/threads/",
        {"kind": "project", "project_id": project.pk},
        format="json",
    )
    assert response.status_code == 201
    thread = ChatThread.objects.get(pk=response.data["id"])
    assert {owner.pk, first.pk, second.pk}.issubset(linked_thread_user_ids(thread))
    assert (
        client.post(
            f"/api/design-workflow/chat/threads/{thread.pk}/messages/",
            {"body": "Let's work together"},
            format="json",
        ).status_code
        == 201
    )
    client.force_authenticate(owner)
    assert (
        client.patch(
            f"/api/design-workflow/projects/{project.pk}/",
            {"collaborator_ids": [second.pk]},
            format="json",
        ).status_code
        == 200
    )
    assert first.pk not in linked_thread_user_ids(thread)
    assert not thread.participants.filter(pk=first.pk).exists()
    client.force_authenticate(first)
    assert (
        client.get(
            f"/api/design-workflow/chat/threads/{thread.pk}/messages/"
        ).status_code
        == 404
    )
    assert thread.pk not in [
        item["id"] for item in client.get("/api/design-workflow/chat/threads/").data
    ]


def test_manager_can_update_membership_and_inactive_members_rejected(shared_project):
    client, project, _, first, _, outsider = shared_project
    client.force_authenticate(make_manager("manager@collaboration.test"))
    response = client.patch(
        f"/api/design-workflow/projects/{project.pk}/",
        {"collaborator_ids": [outsider.pk]},
        format="json",
    )
    assert response.status_code == 200
    assert response.data["can_manage"] is True
    assert list(project.collaborators.all()) == [outsider]
    first.is_active = False
    first.save(update_fields=["is_active"])
    assert (
        client.patch(
            f"/api/design-workflow/projects/{project.pk}/",
            {"collaborator_ids": [first.pk]},
            format="json",
        ).status_code
        == 400
    )
    assert list(project.collaborators.all()) == [outsider]
