from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib import admin
from django.db import connection, transaction
from django.test import RequestFactory
from django.utils import timezone
from simple_history.admin import SimpleHistoryAdmin

from .admin_realtime import WorkflowRealtimeAdmin
from .models import (
    ChatThread, ChatThreadKind, Project, ProjectStatus, Task, TaskLabel,
    TaskStatus, TimeEntry,
)
from .tests import make_designer, make_manager
from .time_tracking import reconcile_task_work_sessions

pytestmark = pytest.mark.django_db


def at(hour, minute=0):
    return timezone.make_aware(datetime(2026, 9, 14, hour, minute))


@pytest.fixture
def studio():
    owner = make_designer("admin-owner@example.test")
    member = make_designer("admin-member@example.test")
    manager = make_manager("admin-supervisor@example.test")
    project = Project.objects.create(name="Admin project", manager=owner)
    task = Task.objects.create(
        title="Admin card", project=project, current_assignee=owner,
        created_by=owner, updated_by=owner, status=TaskStatus.IN_PROGRESS,
    )
    request = RequestFactory().post("/admin/design_workflow/")
    request.user = manager
    return SimpleNamespace(owner=owner, member=member, manager=manager, project=project, task=task, request=request)


def save_admin(studio, obj, *, change=True, save_m2m=lambda: None):
    model_admin = admin.site._registry[type(obj)]
    form = SimpleNamespace(instance=obj, save_m2m=save_m2m)
    with transaction.atomic():
        model_admin.save_model(studio.request, obj, form, change)
        model_admin.save_related(studio.request, form, [], change)
    return form


def test_writable_admins_have_live_hooks_and_history_registrations_stay_readonly(studio):
    writable = []
    history = []
    for model, model_admin in admin.site._registry.items():
        if model._meta.app_label != "design_workflow":
            continue
        if model._meta.model_name.startswith("historical"):
            history.append(model)
            assert not model_admin.has_add_permission(studio.request)
            assert not model_admin.has_change_permission(studio.request)
            assert not model_admin.has_delete_permission(studio.request)
        else:
            writable.append(model)
            assert isinstance(model_admin, WorkflowRealtimeAdmin)
    assert len(writable) == len(history) == 20


def test_admin_save_broadcasts_only_after_m2m_and_commit(studio, django_capture_on_commit_callbacks):
    label = TaskLabel(name="Private label title", created_by=studio.owner)
    model_admin = admin.site._registry[TaskLabel]
    form = SimpleNamespace(instance=label, save_m2m=lambda: None)
    with patch("design_workflow.admin_realtime.broadcast_workflow_event") as broadcast:
        with django_capture_on_commit_callbacks(execute=True):
            with transaction.atomic():
                model_admin.save_model(studio.request, label, form, False)
                broadcast.assert_not_called()
                model_admin.save_related(studio.request, form, [], False)
                broadcast.assert_not_called()
        broadcast.assert_called_once_with("admin")


def test_admin_rollback_discards_changes_and_event(studio, django_capture_on_commit_callbacks):
    with patch("design_workflow.admin_realtime.broadcast_workflow_event") as broadcast:
        with django_capture_on_commit_callbacks(execute=True):
            with pytest.raises(ValueError), transaction.atomic():
                studio.project.name = "Rolled back"
                save_admin(studio, studio.project, save_m2m=lambda: studio.project.collaborators.add(studio.member))
                raise ValueError("rollback")
        broadcast.assert_not_called()
    studio.project.refresh_from_db()
    assert studio.project.name == "Admin project"
    assert not studio.project.collaborators.exists()
    assert not studio.task.work_sessions.exists()


def test_admin_m2m_membership_reconciles_person_timers_and_chat(studio, monkeypatch):
    reconcile_task_work_sessions(studio.task, now=at(9))
    thread = ChatThread.objects.create(kind=ChatThreadKind.PROJECT, project=studio.project)
    thread.participants.add(studio.owner)
    clock = [at(10)]
    monkeypatch.setattr("design_workflow.time_tracking.timezone.now", lambda: clock[0])

    save_admin(studio, studio.project, save_m2m=lambda: studio.project.collaborators.add(studio.member))
    assert studio.task.work_sessions.get(user=studio.owner).started_at == at(9)
    assert studio.task.work_sessions.get(user=studio.member).started_at == at(10)
    assert thread.participants.filter(pk=studio.member.pk).exists()
    assert not studio.task.work_sessions.filter(user=studio.manager).exists()

    clock[0] = at(10, 30)
    save_admin(studio, studio.project, save_m2m=lambda: studio.project.collaborators.remove(studio.member))
    assert not studio.task.work_sessions.filter(user=studio.member).exists()
    assert studio.task.time_entries.get(user=studio.member).minutes == 30
    assert not thread.participants.filter(pk=studio.member.pk).exists()


def test_admin_project_archive_stops_timers_and_restore_keeps_cards_archived(studio, monkeypatch):
    studio.project.collaborators.add(studio.member)
    reconcile_task_work_sessions(studio.task, now=at(9))
    monkeypatch.setattr("design_workflow.time_tracking.timezone.now", lambda: at(10))
    studio.project.archived = True
    save_admin(studio, studio.project)
    studio.task.refresh_from_db()
    studio.project.refresh_from_db()
    assert studio.project.status == ProjectStatus.ARCHIVED
    assert studio.task.archived and studio.task.archived_at == at(10)
    assert not studio.task.work_sessions.exists()
    assert studio.task.actual_minutes == 120

    studio.project.archived = False
    save_admin(studio, studio.project)
    studio.task.refresh_from_db()
    assert studio.project.archived_at is None
    assert studio.project.status == ProjectStatus.PLANNED
    assert studio.task.archived
    assert not studio.task.work_sessions.exists()


def test_admin_task_assignment_and_status_keep_shared_time_consistent(studio, monkeypatch):
    reconcile_task_work_sessions(studio.task, now=at(9))
    clock = [at(10)]
    monkeypatch.setattr("design_workflow.time_tracking.timezone.now", lambda: clock[0])
    studio.task.current_assignee = studio.member
    save_admin(studio, studio.task)
    assert studio.task.work_sessions.get(user=studio.owner).started_at == at(9)
    assert studio.task.work_sessions.get(user=studio.member).started_at == at(10)
    clock[0] = at(11)
    studio.task.status = TaskStatus.IN_REVIEW
    save_admin(studio, studio.task)
    assert not studio.task.work_sessions.exists()
    assert studio.task.time_entries.get(user=studio.owner).minutes == 120
    assert studio.task.time_entries.get(user=studio.member).minutes == 60


def test_admin_cannot_restore_task_inside_archived_project(studio, monkeypatch):
    Project.objects.filter(pk=studio.project.pk).update(archived=True)
    # The task still has an old cached project; the hook must read current DB state.
    assert not studio.task.project.archived
    monkeypatch.setattr("design_workflow.time_tracking.timezone.now", lambda: at(10))
    studio.task.archived = False
    save_admin(studio, studio.task)
    studio.task.refresh_from_db()
    assert studio.task.archived
    assert studio.task.archived_at == at(10)
    assert not studio.task.work_sessions.exists()


def test_admin_time_entry_move_recalculates_both_tasks(studio):
    destination = Task.objects.create(title="Destination", project=studio.project, created_by=studio.owner, updated_by=studio.owner)
    entry = TimeEntry.objects.create(task=studio.task, user=studio.owner, minutes=60)
    entry.task = destination
    entry.minutes = 90
    save_admin(studio, entry)
    studio.task.refresh_from_db()
    destination.refresh_from_db()
    assert studio.task.actual_minutes == 0
    assert destination.actual_minutes == 90


@pytest.mark.parametrize("bulk", [False, True])
def test_admin_time_entry_deletion_recalculates_totals_and_waits_for_commit(studio, bulk, django_capture_on_commit_callbacks):
    entry = TimeEntry.objects.create(task=studio.task, user=studio.owner, minutes=60)
    other_task = None
    if bulk:
        other_task = Task.objects.create(title="Other timed card", project=studio.project, created_by=studio.owner, updated_by=studio.owner)
        TimeEntry.objects.create(task=other_task, user=studio.owner, minutes=30)
    model_admin = admin.site._registry[TimeEntry]
    with patch("design_workflow.admin_realtime.broadcast_workflow_event") as broadcast:
        with django_capture_on_commit_callbacks(execute=True):
            if bulk:
                model_admin.delete_queryset(studio.request, TimeEntry.objects.filter(task__project=studio.project))
            else:
                model_admin.delete_model(studio.request, entry)
            broadcast.assert_not_called()
        broadcast.assert_called_once_with("admin")
    studio.task.refresh_from_db()
    assert studio.task.actual_minutes == 0
    assert not studio.task.time_entries.exists()
    if other_task:
        other_task.refresh_from_db()
        assert other_task.actual_minutes == 0
        assert not other_task.time_entries.exists()


@pytest.mark.parametrize("rollback", [False, True])
def test_history_revert_path_reconciles_after_m2m_and_is_atomic(studio, monkeypatch, rollback, django_capture_on_commit_callbacks):
    model_admin = admin.site._registry[Task]
    TimeEntry.objects.create(task=studio.task, user=studio.owner, minutes=45)
    reconcile_task_work_sessions(studio.task, now=at(9))
    monkeypatch.setattr("design_workflow.time_tracking.timezone.now", lambda: at(10))
    form = SimpleNamespace(instance=studio.task, save_m2m=lambda: None)
    outer_depth = len(connection.savepoint_ids)

    def library_revert(admin_instance, request, object_id, version_id, extra_context):
        assert len(connection.savepoint_ids) > outer_depth
        studio.task.status = TaskStatus.IN_REVIEW
        studio.task.actual_minutes = 0  # Old historical cached value must not survive.
        admin_instance.save_model(request, studio.task, form, True)
        assert studio.task.work_sessions.exists()
        form.save_m2m()  # The actual library bypasses save_related in precisely this way.
        if rollback:
            raise ValueError("rollback history revert")
        return "reverted"

    with patch.object(SimpleHistoryAdmin, "history_form_view", library_revert):
        with patch("design_workflow.admin_realtime.broadcast_workflow_event") as broadcast:
            with django_capture_on_commit_callbacks(execute=True):
                if rollback:
                    with pytest.raises(ValueError):
                        model_admin.history_form_view(studio.request, str(studio.task.pk), "1")
                else:
                    assert model_admin.history_form_view(studio.request, str(studio.task.pk), "1") == "reverted"
                broadcast.assert_not_called()
            if rollback:
                broadcast.assert_not_called()
            else:
                broadcast.assert_called_once_with("admin")
    assert not hasattr(studio.request, "_workflow_history_revert")
    studio.task.refresh_from_db()
    assert studio.task.actual_minutes == (45 if rollback else 105)
    assert studio.task.work_sessions.exists() is rollback
