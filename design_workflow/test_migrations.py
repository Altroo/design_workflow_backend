import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


@pytest.mark.django_db(transaction=True)
def test_duplicate_public_threads_are_merged_before_unique_constraint():
    account_target = ("accounts", "0004_customuser_sso_subject")
    migrate_from = [
        account_target,
        ("design_workflow", "0012_historicalattachmentannotation_historicalchatmessage_and_more"),
    ]
    migrate_to = [
        account_target,
        ("design_workflow", "0013_merge_duplicate_public_chat_threads"),
    ]

    executor = MigrationExecutor(connection)
    executor.migrate(migrate_from)
    old_apps = executor.loader.project_state(migrate_from).apps

    ChatMessage = old_apps.get_model("design_workflow", "ChatMessage")
    ChatThread = old_apps.get_model("design_workflow", "ChatThread")
    User = old_apps.get_model("accounts", "CustomUser")

    sender = User.objects.create(email="migration-chat@test.com")
    empty_thread = ChatThread.objects.create(kind="public", title="Studio public")
    active_thread = ChatThread.objects.create(kind="public", title="Canal public")
    active_thread.participants.add(sender)
    message = ChatMessage.objects.create(
        thread=active_thread,
        sender=sender,
        body="Keep this public message.",
    )

    executor = MigrationExecutor(connection)
    executor.migrate(migrate_to)
    new_apps = executor.loader.project_state(migrate_to).apps

    ChatMessage = new_apps.get_model("design_workflow", "ChatMessage")
    ChatThread = new_apps.get_model("design_workflow", "ChatThread")

    public_threads = ChatThread.objects.filter(kind="public")
    assert public_threads.count() == 1
    assert public_threads.get().pk == active_thread.pk
    assert not ChatThread.objects.filter(pk=empty_thread.pk).exists()
    assert ChatMessage.objects.get(pk=message.pk).thread_id == active_thread.pk
