import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


@pytest.mark.django_db(transaction=True)
def test_duplicate_public_threads_are_merged_before_unique_constraint():
    account_target = ("accounts", "0004_customuser_sso_subject")
    migrate_from = [
        account_target,
        (
            "design_workflow",
            "0012_historicalattachmentannotation_historicalchatmessage_and_more",
        ),
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


@pytest.mark.django_db(transaction=True)
def test_running_shared_sessions_migration_preserves_legacy_worker_and_logged_time():
    from datetime import timedelta

    from django.utils import timezone

    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    previous = [
        ("accounts", "0004_customuser_sso_subject"),
        ("design_workflow", "0018_project_collaborators"),
    ]
    target = [
        ("accounts", "0004_customuser_sso_subject"),
        ("design_workflow", "0019_task_work_sessions"),
    ]
    try:
        executor.migrate(previous)
        apps = executor.loader.project_state(previous).apps
        User = apps.get_model("accounts", "CustomUser")
        Project = apps.get_model("design_workflow", "Project")
        Task = apps.get_model("design_workflow", "Task")
        TimeEntry = apps.get_model("design_workflow", "TimeEntry")
        owner = User.objects.create(
            email="session-migration-owner@test.com", is_active=True
        )
        member = User.objects.create(
            email="session-migration-member@test.com", is_active=True
        )
        inactive = User.objects.create(
            email="session-migration-inactive@test.com", is_active=False
        )
        project = Project.objects.create(name="Running migration", manager=owner)
        project.collaborators.add(owner, member, inactive)
        old_start = timezone.now() - timedelta(hours=2)
        task = Task.objects.create(
            project=project,
            title="Running",
            current_assignee=owner,
            created_by=owner,
            updated_by=owner,
            status="in_progress",
            work_started_at=old_start,
        )
        entry = TimeEntry.objects.create(
            task=task, user=member, minutes=50, work_date=timezone.localdate()
        )
        earliest_new_start = timezone.now()
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        apps = executor.loader.project_state(target).apps
        Session = apps.get_model("design_workflow", "TaskWorkSession")
        assert Session.objects.filter(task_id=task.pk).count() == 2
        assert (
            Session.objects.get(task_id=task.pk, user_id=owner.pk).started_at
            == old_start
        )
        assert (
            Session.objects.get(task_id=task.pk, user_id=member.pk).started_at
            >= earliest_new_start
        )
        assert (
            apps.get_model("design_workflow", "TimeEntry")
            .objects.get(pk=entry.pk)
            .minutes
            == 50
        )
    finally:
        MigrationExecutor(connection).migrate(latest)
