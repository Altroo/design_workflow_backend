import asyncio
from datetime import timedelta
import threading
from types import SimpleNamespace
from unittest.mock import patch
import uuid

import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient
from chat_ai_assistant.contracts import ChatAIError
from account.models import CustomUser
from design_workflow.models import Project, Task, ChatThread, ChatMessage, TimeEntry
from .actions import prepare, confirm
from .models import Conversation, Message, PendingAction, AuditEvent, KnowledgeDocument
from .navigation import ChatAINavigationResolver
from .security import authorization_stamp, validate_text
from .services import ChatAIConversationService, get_conversation, replay_message
from .tools import ChatAIToolExecutor, registry

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def env(settings, tmp_path):
    settings.CHAT_AI_ASSISTANT_ENABLED = True
    settings.MEDIA_ROOT = str(tmp_path)
    cache.clear()
    owner = CustomUser.objects.create_user(
        email="owner@assistant.test", password=None, first_name="Sara", role="designer"
    )
    collab = CustomUser.objects.create_user(
        email="collab@assistant.test",
        password=None,
        first_name="Amine",
        role="designer",
    )
    outsider = CustomUser.objects.create_user(
        email="other@assistant.test", password=None, role="designer"
    )
    manager = CustomUser.objects.create_user(
        email="manager@assistant.test", password=None, role="manager"
    )
    project = Project.objects.create(name="Atlas", manager=owner, status="active")
    project.collaborators.add(collab)
    task = Task.objects.create(
        project=project,
        title="Moodboard",
        created_by=owner,
        updated_by=owner,
        current_assignee=collab,
        status="in_progress",
    )
    return SimpleNamespace(
        owner=owner,
        collab=collab,
        outsider=outsider,
        manager=manager,
        project=project,
        task=task,
    )


def executor(user, state=None, context=None):
    return ChatAIToolExecutor(user.pk, 1, uuid.uuid4(), state=state, context=context)


def conversation(user):
    return Conversation.objects.create(
        user=user,
        authorization_stamp=authorization_stamp(user.pk),
        expires_at=timezone.now() + timedelta(days=30),
    )


def client(user):
    api = APIClient()
    api.force_authenticate(user)
    return api


def proposal(env, user=None, resource="task", operation="update", changes=None):
    agent = executor(user or env.collab)
    identifier = env.task.pk if resource == "task" else env.project.pk
    agent.get_record(resource, identifier)
    return prepare(
        agent,
        resource,
        identifier,
        operation,
        (
            changes
            if changes is not None
            else ({"title": "Revised"} if operation == "update" else {})
        ),
    )


def test_feature_off_and_authentication(env, settings):
    assert APIClient().get("/api/chat-ai/capabilities/").status_code == 401
    settings.CHAT_AI_ASSISTANT_ENABLED = False
    assert client(env.owner).get("/api/chat-ai/capabilities/").status_code == 503


def test_capabilities_and_strict_workspace(env):
    for user, report in [(env.owner, False), (env.manager, True)]:
        api = client(user)
        data = api.get("/api/chat-ai/capabilities/?language=en").data
        assert data["can_report"] is report
        assert any("working time" in s for s in data["suggestions"]) is report
        assert (
            api.post(
                "/api/chat-ai/conversations/", {"company_id": 2}, format="json"
            ).status_code
            == 403
        )
        assert (
            api.post(
                "/api/chat-ai/conversations/",
                {"company_id": 1, "user_id": env.manager.pk},
                format="json",
            ).status_code
            == 400
        )


@pytest.mark.parametrize("resource", ["task", "project"])
def test_cross_project_read_only_matches_native_app(env, resource):
    result = executor(env.outsider).execute("search_records", {"resource": resource})
    assert result["items"][0]["can_edit"] is False
    assert result["items"][0]["name"] in ("Moodboard", "Atlas")


def test_collaborator_and_assigned_only_access(env):
    assert executor(env.collab).get_record("task", env.task.pk)["items"][0]["can_edit"]
    env.project.collaborators.clear()
    assert executor(env.collab).get_record("task", env.task.pk)["items"][0]["can_edit"]
    assert not executor(env.collab).get_record("project", env.project.pk)["items"][0][
        "can_edit"
    ]


@pytest.mark.parametrize("resource", ["reports", "team", "overview"])
def test_manager_only_navigation(env, resource):
    with pytest.raises(ChatAIError, match="PERMISSION_DENIED"):
        executor(env.owner).navigate(resource)
    assert (
        executor(env.manager)
        .navigate(resource)["target"]["href"]
        .startswith("/dashboard/")
    )


def test_search_combines_filters_and_bounds(env):
    agent = executor(env.owner)
    assert agent.execute(
        "search_records",
        {
            "resource": "task",
            "query": "Mood",
            "project_name": "Atlas",
            "assignee_name": "Amine",
            "status": "in_progress",
        },
    )["items"]
    assert not agent.execute(
        "search_records", {"resource": "task", "query": "Mood", "project_name": "Other"}
    )["items"]
    with pytest.raises(ChatAIError):
        agent.execute("search_records", {"resource": "task", "limit": 1000})
    with pytest.raises(ChatAIError):
        agent.execute("search_records", {"resource": "task", "status": "invented"})
    env.task.due_date = timezone.localdate() - timedelta(days=1)
    env.task.save()
    assert agent.search_records("task", overdue=True)["items"]
    env.task.status = "done"
    env.task.save()
    assert not agent.search_records("task", overdue=True)["items"]


def test_chat_privacy_even_for_manager_and_deleted_messages(env):
    thread = ChatThread.objects.create(kind="private", title="Private")
    thread.participants.add(env.owner, env.collab)
    message = ChatMessage.objects.create(
        thread=thread, sender=env.owner, body="Private text"
    )
    assert (
        executor(env.owner).search_records("message")["items"][0]["description"]
        == "Private text"
    )
    assert not executor(env.manager).search_records("message")["items"]
    assert not executor(env.outsider).search_records("message")["items"]
    thread.participants.remove(env.collab)
    with pytest.raises(ChatAIError):
        executor(env.collab).get_record("message", message.pk)
    message.deleted_at = timezone.now()
    message.save()
    assert not executor(env.owner).search_records("message")["items"]


def test_time_report_uses_native_minutes_and_manager_gate(env):
    TimeEntry.objects.create(task=env.task, user=env.owner, minutes=480)
    TimeEntry.objects.create(task=env.task, user=env.collab, minutes=480)
    assert (
        executor(env.manager).execute("time_report", {"project_name": "Atlas"})[
            "minutes"
        ]
        == 960
    )
    with pytest.raises(ChatAIError, match="PERMISSION_DENIED"):
        executor(env.owner).execute("time_report", {})
    with pytest.raises(ChatAIError):
        executor(env.manager).time_report(date_from="2026-10-10", date_to="2026-10-01")
    with pytest.raises(ChatAIError):
        executor(env.manager).time_report(date_from="2026-10-01")


def test_workload_counts_no_archives(env):
    agent = executor(env.collab)
    assert agent.workload_summary(mine=True)["counts"] == {"in_progress": 1}
    env.project.archived = True
    env.project.save()
    assert not agent.workload_summary(mine=True)["counts"]


@pytest.mark.parametrize(
    "operation,changes",
    [
        ("delete", {}),
        ("update", {"status": "done"}),
        ("update", {"current_assignee_id": "1"}),
        ("update", {"title": ""}),
    ],
)
def test_unsupported_changes_are_rejected(env, operation, changes):
    with pytest.raises(ChatAIError):
        proposal(env, operation=operation, changes=changes)


def test_confirmation_not_prompt_executes_native_update_once(env):
    card = proposal(env)
    env.task.refresh_from_db()
    assert env.task.title == "Moodboard"
    with patch("design_workflow.views.broadcast_task_event") as broadcast:
        result = confirm(SimpleNamespace(user=env.collab), card["action_id"])
        assert result["success"]
        broadcast.assert_called_once()
    env.task.refresh_from_db()
    assert env.task.title == "Revised"
    assert env.task.updated_by_id == env.collab.pk
    audit = AuditEvent.objects.get(tool="confirmed_update")
    assert audit.actor_id == env.collab.pk
    with pytest.raises(ChatAIError, match="CONTEXT_EXPIRED"):
        confirm(SimpleNamespace(user=env.collab), card["action_id"])


@pytest.mark.parametrize(
    "scenario", ["changed", "revoked", "expired", "wrong_user", "archived"]
)
def test_confirmation_revalidates_every_boundary(env, scenario):
    card = proposal(env)
    actor = env.collab
    if scenario == "changed":
        env.task.title = "Concurrent edit"
        env.task.save()
    elif scenario == "revoked":
        env.task.current_assignee = env.owner
        env.task.save()
        env.project.collaborators.clear()
    elif scenario == "expired":
        PendingAction.objects.filter(pk=card["action_id"]).update(
            expires_at=timezone.now()
        )
    elif scenario == "wrong_user":
        actor = env.owner
    else:
        env.project.archived = True
        env.project.save()
    with pytest.raises(ChatAIError):
        confirm(SimpleNamespace(user=actor), card["action_id"])
    env.task.refresh_from_db()
    assert env.task.title != "Revised"


def test_archive_project_is_native_and_warns_running_tasks(env):
    card = proposal(env, env.owner, "project", "archive")
    assert card["running_tasks"] == 1
    confirm(SimpleNamespace(user=env.owner), card["action_id"])
    env.project.refresh_from_db()
    env.task.refresh_from_db()
    assert env.project.archived and env.task.archived


def test_outsider_and_unknown_targets_cannot_propose(env):
    with pytest.raises(ChatAIError, match="PERMISSION_DENIED"):
        proposal(env, env.outsider)
    with pytest.raises(ChatAIError, match="CONTEXT_EXPIRED"):
        prepare(executor(env.owner), "task", env.task.pk, "archive", {})


def test_knowledge_permissions_sync_and_replay_expiry(env):
    call_command("sync_ai_knowledge", verbosity=0)
    assert KnowledgeDocument.objects.count() == 5
    from .knowledge import ChatAIKnowledgeService

    docs = ChatAIKnowledgeService().retrieve("time rapport", 1, {"read"})
    assert all(d["document_id"] != "workflow-time" for d in docs)
    conv = conversation(env.manager)
    result = ChatAIConversationService().run(
        env.manager.pk,
        conv.pk,
        "Comment créer une tâche ?",
        uuid.uuid4(),
        {},
        lambda *_: None,
        threading.Event(),
    )
    assert "Tableau de tâches" in result["text"]
    stored = Message.objects.get(pk=result["id"])
    assert "documents" not in stored.action
    KnowledgeDocument.objects.filter(document_id="workflow-tasks").update(
        document_version="revoked"
    )
    assert replay_message(executor(env.manager), stored)["text"] == ""


def test_conversation_ownership_idempotency_and_history_refresh(env):
    conv = conversation(env.owner)
    request_id = uuid.uuid4()
    service = ChatAIConversationService()
    result = service.run(
        env.owner.pk,
        conv.pk,
        "/taches",
        request_id,
        {},
        lambda *_: None,
        threading.Event(),
    )
    assert result["cards"][0]["items"][0]["name"] == "Moodboard"
    env.task.title = "Live title"
    env.task.save()
    replay = service.run(
        env.owner.pk,
        conv.pk,
        "/taches",
        request_id,
        {},
        lambda *_: None,
        threading.Event(),
    )
    assert replay["cards"][0]["items"][0]["name"] == "Live title"
    assert Message.objects.filter(conversation=conv).count() == 2
    with pytest.raises(ChatAIError):
        get_conversation(env.outsider.pk, conv.pk)
    with pytest.raises(ChatAIError):
        service.run(
            env.owner.pk,
            conv.pk,
            "/projets",
            request_id,
            {},
            lambda *_: None,
            threading.Event(),
        )


def test_api_json_streaming_feedback_and_owned_delete(env):
    api = client(env.owner)
    conv = api.post(
        "/api/chat-ai/conversations/", {"company_id": 1}, format="json"
    ).data["id"]
    payload = {
        "text": "/projets",
        "request_id": str(uuid.uuid4()),
        "context": {"interface_language": "fr"},
    }
    response = api.post(
        f"/api/chat-ai/conversations/{conv}/messages/",
        payload,
        format="json",
        HTTP_ACCEPT="text/event-stream",
    )

    async def read():
        return b"".join([chunk async for chunk in response.streaming_content]).decode()

    body = asyncio.run(read())
    assert "event: message.completed" in body and "Atlas" in body
    assert "_knowledge.sources" not in body
    history = api.get(f"/api/chat-ai/conversations/{conv}/").data
    message = history["messages"][-1]["id"]
    assert (
        api.post(
            "/api/chat-ai/feedback/",
            {"message_id": message, "helpful": True},
            format="json",
        ).status_code
        == 200
    )
    assert (
        client(env.outsider).delete(f"/api/chat-ai/conversations/{conv}/").status_code
        == 404
    )
    assert api.delete(f"/api/chat-ai/conversations/{conv}/").status_code == 204


def test_retention_preserves_confirmed_actor_audit(env):
    card = proposal(env)
    confirm(SimpleNamespace(user=env.collab), card["action_id"])
    conv = conversation(env.collab)
    Conversation.objects.filter(pk=conv.pk).update(expires_at=timezone.now())
    call_command("purge_ai_history")
    assert not Conversation.objects.filter(pk=conv.pk).exists()
    assert AuditEvent.objects.filter(
        tool="confirmed_update", actor_id=env.collab.pk
    ).exists()


@pytest.mark.parametrize(
    "value", ["", "password=secret", "Bearer " + "a" * 30, "x" * 4001, "\x00"]
)
def test_sensitive_or_invalid_prompts(value):
    with pytest.raises(ChatAIError):
        validate_text(value)


def test_typed_tools_and_navigation_do_not_accept_arbitrary_execution(env):
    for name, args in [
        ("shell", {"cmd": "ls"}),
        ("navigate", {"resource": "https://example.com"}),
        ("search_records", {"resource": "task", "sql": "select"}),
    ]:
        with pytest.raises(ChatAIError):
            registry().validate(name, args)
    with pytest.raises(ChatAIError):
        ChatAINavigationResolver.resolve("task", 1, True)


def test_english_help_remains_english_in_stream_and_history(env):
    call_command("sync_ai_knowledge", verbosity=0)
    conv = conversation(env.owner)
    result = ChatAIConversationService().run(
        env.owner.pk,
        conv.pk,
        "How do I create a task?",
        uuid.uuid4(),
        {"interface_language": "en"},
        lambda *_: None,
        threading.Event(),
    )
    assert "create cards" in result["text"]
    assert "project" in result["text"] and "task" in result["text"]
    assert "Tâche" not in result["text"] and "Projet" not in result["text"]
    stored = Message.objects.get(pk=result["id"])
    assert replay_message(executor(env.owner), stored)["text"] == result["text"]


def test_real_planner_contract_can_propose_known_followup_but_never_execute(env):
    conv = conversation(env.owner)
    service = ChatAIConversationService()
    service.run(
        env.owner.pk,
        conv.pk,
        "/taches",
        uuid.uuid4(),
        {},
        lambda *_: None,
        threading.Event(),
    )

    class Model:
        def choose(self, messages, tools, cancel=None):
            assert "previous_result_identifiers" in messages[0]["content"]
            return {
                "tool": "prepare_change",
                "arguments": {
                    "resource": "task",
                    "identifier": env.task.pk,
                    "operation": "update",
                    "changes": {"title": "Requested title"},
                },
            }, {}

    with patch("chat_ai.services.get_model", return_value=Model()):
        result = service.run(
            env.owner.pk,
            conv.pk,
            "Rename the first task to Requested title",
            uuid.uuid4(),
            {"interface_language": "en"},
            lambda *_: None,
            threading.Event(),
        )
    assert result["cards"][0]["type"] == "confirmation"
    env.task.refresh_from_db()
    assert env.task.title == "Moodboard"


def test_cancel_does_not_persist_assistant_result_and_releases_lease(env):
    from .models import InferenceLease

    conv = conversation(env.owner)
    cancel = threading.Event()

    def emit(*_):
        cancel.set()

    with pytest.raises(ChatAIError, match="CANCELLED"):
        ChatAIConversationService().run(
            env.owner.pk, conv.pk, "/projets", uuid.uuid4(), {}, emit, cancel
        )
    assert not Message.objects.filter(conversation=conv, role="assistant").exists()
    assert InferenceLease.objects.get(name="model").owner is None


def test_planner_shortlist_does_not_answer_known_change_with_a_list(env):
    from .planner import shortlist

    tools = registry().permitted(executor(env.owner).capabilities())
    context = {"previous_result_count": 1}
    for text in [
        "Renomme la première tâche en Atlas",
        "Please rename this task to Atlas",
    ]:
        assert {tool.name for tool in shortlist(text, tools, context)} == {
            "prepare_change",
            "navigate",
        }
    for text in ["How do I rename a task?", "Find tasks named Change the design"]:
        assert "search_records" in {
            tool.name for tool in shortlist(text, tools, context)
        }
    assert "search_records" in {
        tool.name for tool in shortlist("Rename Atlas", tools, {})
    }


def test_shortcut_never_silently_truncates_search(env):
    from .shortcuts import shortcut_action

    result = shortcut_action("/tasks " + "a" * 121, executor(env.owner), "en")
    assert result["tool"] == "clarify"
    assert "120" in result["message"]
    result = shortcut_action("/taches Atlas", executor(env.owner))
    assert result["arguments"]["query"] == "Atlas"


def test_selection_obeys_conversation_limit(env):
    conv = conversation(env.owner)
    Message.objects.bulk_create(
        [
            Message(
                conversation=conv, role="user", text="Message", request_id=uuid.uuid4()
            )
            for _ in range(60)
        ]
    )
    result = client(env.owner).post(
        f"/api/chat-ai/conversations/{conv.pk}/selection/",
        {"resource": "task", "identifier": env.task.pk, "operation": "archive"},
        format="json",
    )
    assert result.data["error"]["code"] == "CONTEXT_LIMIT"
    assert conv.messages.count() == 60
