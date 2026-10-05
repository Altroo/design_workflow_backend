from datetime import datetime

import pytest
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Notification, NotificationType, Project, Task, TaskActivityType, TaskReviewState, TaskStatus, TaskWorkSession, TimeEntry
from .tests import make_designer, make_manager
from .time_tracking import active_session_minutes, reconcile_task_work_sessions

pytestmark = pytest.mark.django_db


def at(day, hour, minute=0):
    return timezone.make_aware(datetime(2026, 9, day, hour, minute))


@pytest.fixture
def shared_timer():
    owner = make_designer("timer-owner@example.test")
    member = make_designer("timer-member@example.test")
    outsider = make_designer("timer-outsider@example.test")
    project = Project.objects.create(name="Shared timer", manager=owner)
    project.collaborators.add(owner, member)
    task = Task.objects.create(
        project=project, title="Shared card", current_assignee=owner,
        created_by=owner, updated_by=owner, status=TaskStatus.IN_PROGRESS,
    )
    return task, project, owner, member, outsider


def close(task, now):
    Task.objects.filter(pk=task.pk).update(status=TaskStatus.IN_REVIEW)
    return reconcile_task_work_sessions(task, now=now, event="test_close")


def totals(task):
    result = {}
    for entry in task.time_entries.all():
        result[entry.user_id] = result.get(entry.user_id, 0) + entry.minutes
    return result


def test_shared_hour_credits_each_member_once_not_supervisory_actor(shared_timer):
    task, _, owner, member, _ = shared_timer
    supervisor = make_manager("timer-supervisor@example.test")
    Task.objects.filter(pk=task.pk).update(updated_by=supervisor)
    assert reconcile_task_work_sessions(task, now=at(14, 9)) == []
    assert task.work_sessions.count() == 2
    close(task, at(14, 10))
    task.refresh_from_db()
    assert totals(task) == {owner.pk: 60, member.pk: 60}
    assert task.actual_minutes == 120
    assert task.work_started_at is None
    assert not task.work_sessions.exists()
    assert not task.time_entries.filter(user=supervisor).exists()


def test_mid_session_join_and_departure_preserve_continuing_workers(shared_timer):
    task, project, owner, member, outsider = shared_timer
    reconcile_task_work_sessions(task, now=at(14, 9))
    project.collaborators.add(outsider)
    reconcile_task_work_sessions(task, now=at(14, 10))
    assert task.work_sessions.get(user=owner).started_at == at(14, 9)
    assert task.work_sessions.get(user=outsider).started_at == at(14, 10)
    project.collaborators.remove(member)
    reconcile_task_work_sessions(task, now=at(14, 11))
    assert totals(task) == {member.pk: 120}
    close(task, at(14, 12))
    assert totals(task) == {owner.pk: 180, member.pk: 120, outsider.pk: 120}


def test_reassignment_only_closes_worker_without_remaining_project_membership(shared_timer):
    task, project, owner, member, outsider = shared_timer
    project.collaborators.remove(member)
    Task.objects.filter(pk=task.pk).update(current_assignee=member)
    reconcile_task_work_sessions(task, now=at(14, 9))
    Task.objects.filter(pk=task.pk).update(current_assignee=outsider)
    reconcile_task_work_sessions(task, now=at(14, 10))
    assert totals(task) == {member.pk: 60}
    assert set(task.work_sessions.values_list("user_id", flat=True)) == {owner.pk, outsider.pk}
    close(task, at(14, 11))
    assert totals(task) == {owner.pk: 120, member.pk: 60, outsider.pk: 60}


@pytest.mark.parametrize("archive_project", [False, True])
def test_archive_stops_and_restore_starts_new_time_without_archived_gap(shared_timer, archive_project):
    task, project, owner, member, _ = shared_timer
    reconcile_task_work_sessions(task, now=at(14, 9))
    archived_object = project if archive_project else task
    type(archived_object).objects.filter(pk=archived_object.pk).update(archived=True)
    reconcile_task_work_sessions(task, now=at(14, 10))
    assert not task.work_sessions.exists()
    type(archived_object).objects.filter(pk=archived_object.pk).update(archived=False)
    reconcile_task_work_sessions(task, now=at(14, 14))
    close(task, at(14, 15))
    assert totals(task) == {owner.pk: 120, member.pk: 120}


def test_repeated_reconciliation_and_close_are_idempotent(shared_timer):
    task, _, owner, member, _ = shared_timer
    reconcile_task_work_sessions(task, now=at(14, 9))
    original_ids = set(task.work_sessions.values_list("pk", flat=True))
    reconcile_task_work_sessions(task, now=at(14, 9, 30))
    assert set(task.work_sessions.values_list("pk", flat=True)) == original_ids
    assert task.work_started_at == at(14, 9)
    assert len(close(task, at(14, 10))) == 2
    assert reconcile_task_work_sessions(task, now=at(14, 11)) == []
    assert totals(task) == {owner.pk: 60, member.pk: 60}
    assert task.activities.filter(action_type=TaskActivityType.TIME_LOGGED).count() == 2


def test_daily_entries_respect_lunch_weekend_and_actual_work_dates(shared_timer):
    task, _, owner, member, _ = shared_timer
    reconcile_task_work_sessions(task, now=at(18, 12))  # Friday.
    close(task, at(21, 15))  # Monday.
    expected = [(at(18, 0).date(), 300), (at(19, 0).date(), 240), (at(21, 0).date(), 300)]
    for user in (owner, member):
        assert list(task.time_entries.filter(user=user).order_by("work_date").values_list("work_date", "minutes")) == expected
    assert task.actual_minutes == 1680


def test_inactive_user_stops_and_cannot_start_session(shared_timer, monkeypatch):
    task, _, owner, member, outsider = shared_timer
    clock = [at(14, 9)]
    monkeypatch.setattr("design_workflow.time_tracking.timezone.now", lambda: clock[0])
    outsider.is_active = False
    outsider.save(update_fields=["is_active"])
    Task.objects.filter(pk=task.pk).update(current_assignee=outsider)
    reconcile_task_work_sessions(task, now=at(14, 9))
    assert not task.work_sessions.filter(user=outsider).exists()
    clock[0] = at(14, 10)
    member.is_active = False
    member.save(update_fields=["is_active"])
    reconcile_task_work_sessions(task, now=at(14, 10))
    close(task, at(14, 11))
    assert totals(task) == {owner.pk: 120, member.pk: 60}


def test_existing_entries_and_legacy_start_are_not_retroactively_multiplied(shared_timer):
    task, _, owner, member, _ = shared_timer
    old = TimeEntry.objects.create(task=task, user=owner, minutes=45, work_date=at(11, 0).date(), note="Existing entry")
    Task.objects.filter(pk=task.pk).update(work_started_at=at(11, 9))
    reconcile_task_work_sessions(task, now=at(14, 9))
    assert set(task.work_sessions.values_list("started_at", flat=True)) == {at(14, 9)}
    close(task, at(14, 10))
    old.refresh_from_db()
    assert (old.minutes, old.work_date, old.note) == (45, at(11, 0).date(), "Existing entry")
    assert totals(task) == {owner.pk: 105, member.pk: 60}


def test_moving_project_preserves_only_members_shared_with_new_project(shared_timer):
    task, _, owner, member, outsider = shared_timer
    destination = Project.objects.create(name="Destination", manager=outsider)
    reconcile_task_work_sessions(task, now=at(14, 9))
    Task.objects.filter(pk=task.pk).update(project=destination)
    reconcile_task_work_sessions(task, now=at(14, 10))
    # The former owner is still the individually assigned worker.
    assert task.work_sessions.get(user=owner).started_at == at(14, 9)
    close(task, at(14, 11))
    assert totals(task) == {owner.pk: 120, member.pk: 60, outsider.pk: 60}


def test_zero_working_minutes_closes_without_zero_entries(shared_timer):
    task, _, _, _, _ = shared_timer
    reconcile_task_work_sessions(task, now=at(20, 9))  # Sunday.
    assert close(task, at(20, 18)) == []
    assert not task.work_sessions.exists()
    assert not task.time_entries.exists()
    assert task.work_started_at is None


def test_database_prevents_duplicate_live_worker_session(shared_timer):
    task, _, owner, _, _ = shared_timer
    reconcile_task_work_sessions(task, now=at(14, 9))
    with pytest.raises(IntegrityError), transaction.atomic():
        TaskWorkSession.objects.create(task=task, user=owner, started_at=at(14, 10))


def test_time_broadcast_once_after_commit_not_per_worker_or_day(shared_timer, monkeypatch, django_capture_on_commit_callbacks):
    task, _, _, _, _ = shared_timer
    calls = []
    monkeypatch.setattr("design_workflow.services.broadcast_task_event", lambda *args, **kwargs: calls.append((args, kwargs)))
    with django_capture_on_commit_callbacks(execute=True):
        reconcile_task_work_sessions(task, now=at(14, 9))
        assert calls == []
    assert len(calls) == 1
    calls.clear()
    with django_capture_on_commit_callbacks(execute=True):
        close(task, at(15, 10))
        assert calls == []
    assert len(calls) == 1
    assert calls[0][0][1] == "time_logged"


def test_rollback_does_not_log_time_or_broadcast(shared_timer, monkeypatch, django_capture_on_commit_callbacks):
    task, _, _, _, _ = shared_timer
    reconcile_task_work_sessions(task, now=at(14, 9))
    calls = []
    monkeypatch.setattr("design_workflow.services.broadcast_task_event", lambda *args, **kwargs: calls.append((args, kwargs)))
    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(ValueError), transaction.atomic():
            close(task, at(14, 10))
            raise ValueError("Simulated failed mutation")
    task.refresh_from_db()
    assert task.status == TaskStatus.IN_PROGRESS
    assert task.work_sessions.count() == 2
    assert not task.time_entries.exists()
    assert calls == []


def test_api_create_in_progress_starts_all_members(shared_timer, monkeypatch):
    _, project, owner, member, _ = shared_timer
    monkeypatch.setattr("design_workflow.time_tracking.timezone.now", lambda: at(14, 9))
    client = APIClient()
    client.force_authenticate(owner)
    response = client.post("/api/design-workflow/tasks/", {
        "project_id": project.pk, "title": "Started on creation", "status": TaskStatus.IN_PROGRESS,
        "current_assignee_id": owner.pk,
    }, format="json")
    assert response.status_code == 201
    created = Task.objects.get(pk=response.data["id"])
    assert set(created.work_sessions.values_list("user_id", flat=True)) == {owner.pk, member.pk}
    assert created.work_started_at == at(14, 9)
    assert not created.time_entries.exists()


def test_api_project_membership_changes_checkpoint_only_changed_workers(shared_timer, monkeypatch):
    task, project, owner, member, outsider = shared_timer
    clock = [at(14, 9)]
    monkeypatch.setattr("design_workflow.time_tracking.timezone.now", lambda: clock[0])
    reconcile_task_work_sessions(task)
    client = APIClient()
    client.force_authenticate(owner)
    clock[0] = at(14, 10)
    response = client.patch(f"/api/design-workflow/projects/{project.pk}/", {
        "collaborator_ids": [member.pk, outsider.pk],
    }, format="json")
    assert response.status_code == 200
    assert task.work_sessions.get(user=outsider).started_at == clock[0]
    clock[0] = at(14, 11)
    response = client.patch(f"/api/design-workflow/projects/{project.pk}/", {
        "collaborator_ids": [outsider.pk],
    }, format="json")
    assert response.status_code == 200
    assert totals(task) == {member.pk: 120}
    clock[0] = at(14, 12)
    assert client.patch(f"/api/design-workflow/tasks/{task.pk}/status/", {
        "status": TaskStatus.IN_REVIEW,
    }, format="json").status_code == 200
    assert totals(task) == {owner.pk: 180, member.pk: 120, outsider.pk: 120}


@pytest.mark.parametrize("use_reassign_endpoint", [False, True])
def test_api_assignment_without_status_change_closes_only_previous_assignee(shared_timer, monkeypatch, use_reassign_endpoint):
    task, project, owner, member, outsider = shared_timer
    project.collaborators.remove(member)
    Task.objects.filter(pk=task.pk).update(current_assignee=member)
    clock = [at(14, 9)]
    monkeypatch.setattr("design_workflow.time_tracking.timezone.now", lambda: clock[0])
    reconcile_task_work_sessions(task)
    client = APIClient()
    client.force_authenticate(make_manager("timer-reassign-manager@example.test") if use_reassign_endpoint else owner)
    clock[0] = at(14, 10)
    if use_reassign_endpoint:
        response = client.post(f"/api/design-workflow/tasks/{task.pk}/reassign/", {
            "assignee_id": outsider.pk, "reason": "Designer changes",
        }, format="json")
    else:
        response = client.patch(f"/api/design-workflow/tasks/{task.pk}/", {
            "current_assignee_id": outsider.pk,
        }, format="json")
    assert response.status_code == 200
    assert totals(task) == {member.pk: 60}
    assert task.work_sessions.get(user=owner).started_at == at(14, 9)
    assert task.work_sessions.get(user=outsider).started_at == at(14, 10)
    close(task, at(14, 11))
    assert totals(task) == {owner.pk: 120, member.pk: 60, outsider.pk: 60}


@pytest.mark.parametrize("archive_project", [False, True])
def test_api_archive_restore_does_not_count_the_archived_interval(shared_timer, monkeypatch, archive_project):
    task, project, owner, member, _ = shared_timer
    clock = [at(14, 9)]
    monkeypatch.setattr("design_workflow.time_tracking.timezone.now", lambda: clock[0])
    reconcile_task_work_sessions(task)
    client = APIClient()
    client.force_authenticate(owner)
    clock[0] = at(14, 10)
    if archive_project:
        response = client.patch(f"/api/design-workflow/projects/{project.pk}/", {"archived": True}, format="json")
    else:
        response = client.post(f"/api/design-workflow/tasks/{task.pk}/archive/", {"archived": True}, format="json")
    assert response.status_code == 200
    assert not task.work_sessions.exists()
    clock[0] = at(14, 14)
    if archive_project:
        assert client.patch(f"/api/design-workflow/projects/{project.pk}/", {"archived": False}, format="json").status_code == 200
        assert not task.work_sessions.exists()  # Restoring project leaves its cards archived.
    assert client.post(f"/api/design-workflow/tasks/{task.pk}/archive/", {"archived": False}, format="json").status_code == 200
    assert task.work_sessions.count() == 2
    close(task, at(14, 15))
    assert totals(task) == {owner.pk: 120, member.pk: 120}


@pytest.mark.parametrize("endpoint", ["status", "detail", "reorder", "review", "notification"])
def test_api_every_status_exit_closes_shared_sessions(shared_timer, monkeypatch, endpoint):
    task, project, owner, member, _ = shared_timer
    reconcile_task_work_sessions(task, now=at(14, 9))
    monkeypatch.setattr("design_workflow.time_tracking.timezone.now", lambda: at(14, 10))
    client = APIClient()
    client.force_authenticate(owner)
    if endpoint == "status":
        response = client.patch(f"/api/design-workflow/tasks/{task.pk}/status/", {"status": TaskStatus.IN_REVIEW}, format="json")
    elif endpoint == "detail":
        response = client.patch(f"/api/design-workflow/tasks/{task.pk}/", {"status": TaskStatus.IN_REVIEW}, format="json")
    elif endpoint == "reorder":
        response = client.patch("/api/design-workflow/tasks/reorder/", {
            "moved_task_id": task.pk,
            "tasks": [{"id": task.pk, "status": TaskStatus.IN_REVIEW, "sort_order": 0}],
        }, format="json")
    elif endpoint == "review":
        response = client.post(f"/api/design-workflow/tasks/{task.pk}/review/", {"review_state": TaskReviewState.NEEDS_REVIEW}, format="json")
    else:
        notification = Notification.objects.create(recipient=owner, task=task, project=project, type=NotificationType.TASK_STATUS)
        response = client.post(f"/api/design-workflow/notifications/{notification.pk}/action/", {
            "action": "move_status", "status": TaskStatus.IN_REVIEW,
        }, format="json")
    assert response.status_code == 200
    assert totals(task) == {owner.pk: 60, member.pk: 60}
    assert not task.work_sessions.exists()


def test_api_notification_accept_assignment_adds_actual_assignee(shared_timer, monkeypatch):
    task, project, owner, member, _ = shared_timer
    supervisor = make_manager("timer-accept-manager@example.test")
    Task.objects.filter(pk=task.pk).update(current_assignee=None)
    reconcile_task_work_sessions(task, now=at(14, 9))
    notification = Notification.objects.create(recipient=supervisor, task=task, project=project, type=NotificationType.TASK_ASSIGNED)
    monkeypatch.setattr("design_workflow.time_tracking.timezone.now", lambda: at(14, 10))
    client = APIClient()
    client.force_authenticate(supervisor)
    response = client.post(f"/api/design-workflow/notifications/{notification.pk}/action/", {"action": "accept_assignment"}, format="json")
    assert response.status_code == 200
    assert task.work_sessions.get(user=supervisor).started_at == at(14, 10)
    close(task, at(14, 11))
    assert totals(task) == {owner.pk: 120, member.pk: 120, supervisor.pk: 60}


def test_reactivation_resumes_member_without_counting_inactive_interval(shared_timer, monkeypatch):
    task, _, owner, member, _ = shared_timer
    clock = [at(14, 9)]
    monkeypatch.setattr("design_workflow.time_tracking.timezone.now", lambda: clock[0])
    reconcile_task_work_sessions(task)
    clock[0] = at(14, 10)
    member.is_active = False
    member.save(update_fields=["is_active"])
    assert totals(task) == {member.pk: 60}
    assert not task.work_sessions.filter(user=member).exists()
    clock[0] = at(14, 11)
    member.is_active = True
    member.save(update_fields=["is_active"])
    assert task.work_sessions.get(user=member).started_at == at(14, 11)
    assert task.work_sessions.get(user=owner).started_at == at(14, 9)
    clock[0] = at(14, 11, 30)
    member.first_name = "Updated name"
    member.save(update_fields=["first_name"])
    assert task.work_sessions.get(user=member).started_at == at(14, 11)
    close(task, at(14, 12))
    assert totals(task) == {owner.pk: 180, member.pk: 120}


def test_shared_workload_and_analytics_use_person_hours_without_duplicate_owner(shared_timer):
    task, project, owner, member, outsider = shared_timer
    Task.objects.filter(pk=task.pk).update(estimated_minutes=60)
    reconcile_task_work_sessions(task, now=at(14, 9))
    close(task, at(14, 10))
    manager = make_manager("timer-report-manager@example.test")
    client = APIClient()
    client.force_authenticate(manager)
    workload = client.get("/api/design-workflow/workload/")
    assert workload.status_code == 200
    rows = {row["user"]["id"]: row for row in workload.data}
    for user in (owner, member):
        assert rows[user.pk]["open_tasks"] == 1
        assert rows[user.pk]["estimated_minutes"] == 60
        assert rows[user.pk]["actual_minutes"] == 60
    assert rows[outsider.pk]["open_tasks"] == 0
    assert rows[manager.pk]["actual_minutes"] == 0
    team = client.get(f"/api/design-workflow/reports/workflow/?project={project.pk}")
    assert team.status_code == 200
    assert team.data["estimate_vs_actual"] == {
        "estimated_minutes": 120, "actual_minutes": 120,
        "variance_minutes": 0, "actual_to_estimate_ratio": 1.0,
    }
    assert {row["user"]["id"] for row in team.data["capacity"]} == {owner.pk, member.pk}
    for user in (owner, member):
        personal = client.get(f"/api/design-workflow/reports/workflow/?project={project.pk}&user={user.pk}")
        assert personal.status_code == 200
        assert personal.data["estimate_vs_actual"] == {
            "estimated_minutes": 60, "actual_minutes": 60,
            "variance_minutes": 0, "actual_to_estimate_ratio": 1.0,
        }
        assert [row["user"]["id"] for row in personal.data["capacity"]] == [user.pk]
        assert personal.data["capacity"][0]["remaining_minutes"] == 0
        logged = client.get(f"/api/design-workflow/reports/time/?project={project.pk}&user={user.pk}&start_date=2026-09-14&end_date=2026-09-14")
        assert logged.status_code == 200
        assert [(row["project"]["id"], row["minutes"]) for row in logged.data] == [(project.pk, 60)]


@pytest.mark.parametrize("deactivate", [False, True])
def test_reports_preserve_departed_contributors_history_and_team_estimate(shared_timer, deactivate):
    task, project, owner, member, _ = shared_timer
    Task.objects.filter(pk=task.pk).update(estimated_minutes=60)
    reconcile_task_work_sessions(task, now=at(14, 9))
    close(task, at(14, 10))
    if deactivate:
        member.is_active = False
        member.save(update_fields=["is_active"])
    else:
        project.collaborators.remove(member)
    manager = make_manager("timer-history-manager@example.test")
    client = APIClient()
    client.force_authenticate(manager)
    team = client.get(f"/api/design-workflow/reports/workflow/?project={project.pk}")
    assert team.status_code == 200
    assert team.data["estimate_vs_actual"]["estimated_minutes"] == 120
    assert team.data["estimate_vs_actual"]["actual_minutes"] == 120
    assert team.data["estimate_vs_actual"]["actual_to_estimate_ratio"] == 1.0
    assert [row["user"]["id"] for row in team.data["capacity"]] == [owner.pk]
    historical = client.get(f"/api/design-workflow/reports/workflow/?project={project.pk}&user={member.pk}")
    assert historical.status_code == 200
    assert historical.data["tasks_sampled"] == 1
    assert historical.data["estimate_vs_actual"]["estimated_minutes"] == 60
    assert historical.data["estimate_vs_actual"]["actual_minutes"] == 60
    assert historical.data["capacity"] == []


def test_active_hour_counts_each_worker_without_creating_entries(shared_timer):
    task, _, owner, member, _ = shared_timer
    reconcile_task_work_sessions(task, now=at(14, 9))
    assert active_session_minutes(task_ids=[task.pk], now=at(14, 10)) == {
        (task.pk, owner.pk, at(14, 0).date()): 60,
        (task.pk, member.pk, at(14, 0).date()): 60,
    }
    assert sum(active_session_minutes(user_id=member.pk, now=at(14, 10)).values()) == 60
    assert active_session_minutes(task_ids=[], now=at(14, 10)) == {}
    task.refresh_from_db()
    assert task.actual_minutes == 0
    assert not task.time_entries.exists()


@pytest.mark.parametrize("start,end,personal_minutes", [
    (at(14, 12, 30), at(14, 14, 30), 60),  # Weekday lunch is excluded.
    (at(19, 9), at(19, 15), 240),  # Saturday ends at 13:00 without lunch.
    (at(20, 9), at(20, 18), 0),  # Sunday is off.
    (at(18, 17), at(21, 10), 360),  # Friday, Saturday and Monday only.
])
def test_active_minutes_follow_work_schedule(shared_timer, start, end, personal_minutes):
    task, _, owner, member, _ = shared_timer
    reconcile_task_work_sessions(task, now=start)
    result = active_session_minutes(now=end)
    for worker in (owner, member):
        assert sum(minutes for (_, user_id, _), minutes in result.items() if user_id == worker.pk) == personal_minutes


def test_active_minutes_respect_inclusive_dates_without_rounding_checkpoints(shared_timer):
    task, _, owner, _, _ = shared_timer
    reconcile_task_work_sessions(task, now=at(18, 17))
    bounded = active_session_minutes(
        task_ids=[task.pk], user_id=owner.pk,
        start_date="2026-09-19", end_date=at(20, 0).date(), now=at(21, 10),
    )
    assert bounded == {(task.pk, owner.pk, at(19, 0).date()): 240}
    assert active_session_minutes(start_date="2026-09-22", now=at(21, 10)) == {}
    assert active_session_minutes(start_date="2026-09-21", end_date="2026-09-19", now=at(21, 10)) == {}
    assert set(task.work_sessions.values_list("started_at", flat=True)) == {at(18, 17)}
    assert not task.time_entries.exists()


def test_active_reads_preserve_subminute_start_time(shared_timer):
    task, _, _, _, _ = shared_timer
    started_at = at(14, 9).replace(second=30)
    reconcile_task_work_sessions(task, now=started_at)
    assert active_session_minutes(now=at(14, 9, 1)) == {}
    assert sum(active_session_minutes(now=at(14, 9, 2)).values()) == 2
    assert set(task.work_sessions.values_list("started_at", flat=True)) == {started_at}
    assert not task.time_entries.exists()


@pytest.mark.parametrize("ignored", ["task_archive", "project_archive", "not_in_progress", "inactive"])
def test_active_helper_excludes_ineligible_stale_sessions(shared_timer, ignored):
    task, project, owner, member, _ = shared_timer
    reconcile_task_work_sessions(task, now=at(14, 9))
    if ignored == "task_archive":
        Task.objects.filter(pk=task.pk).update(archived=True)
    elif ignored == "project_archive":
        Project.objects.filter(pk=project.pk).update(archived=True)
    elif ignored == "not_in_progress":
        Task.objects.filter(pk=task.pk).update(status=TaskStatus.IN_REVIEW)
    else:
        type(owner).objects.filter(pk=member.pk).update(is_active=False)
    live = active_session_minutes(now=at(14, 10))
    assert sum(live.values()) == (60 if ignored == "inactive" else 0)
    assert not task.time_entries.exists()


def test_active_reports_show_person_hours_before_any_session_closes(shared_timer, monkeypatch):
    task, project, owner, member, outsider = shared_timer
    Task.objects.filter(pk=task.pk).update(estimated_minutes=240)
    reconcile_task_work_sessions(task, now=at(14, 9))
    monkeypatch.setattr("design_workflow.time_tracking.timezone.now", lambda: at(14, 10))
    manager = make_manager("live-report-manager@example.test")
    client = APIClient()
    client.force_authenticate(manager)

    summary = client.get("/api/design-workflow/dashboard/summary/")
    assert summary.status_code == 200
    assert summary.data["week_logged_minutes"] == 120
    workload = client.get("/api/design-workflow/workload/")
    assert workload.status_code == 200
    rows = {row["user"]["id"]: row for row in workload.data}
    assert rows[owner.pk]["actual_minutes"] == rows[member.pk]["actual_minutes"] == 60
    assert rows[outsider.pk]["actual_minutes"] == rows[manager.pk]["actual_minutes"] == 0

    team = client.get(f"/api/design-workflow/reports/workflow/?project={project.pk}")
    assert team.status_code == 200
    assert team.data["estimate_vs_actual"] == {
        "estimated_minutes": 480, "actual_minutes": 120,
        "variance_minutes": -360, "actual_to_estimate_ratio": 0.25,
    }
    for worker in (owner, member):
        personal = client.get(f"/api/design-workflow/reports/workflow/?project={project.pk}&user={worker.pk}")
        assert personal.status_code == 200
        assert personal.data["estimate_vs_actual"]["actual_minutes"] == 60
        assert personal.data["capacity"][0]["user"]["id"] == worker.pk
        assert personal.data["capacity"][0]["remaining_minutes"] == 180
    assert not task.time_entries.exists()


def test_time_report_filters_project_user_and_active_work_dates(shared_timer, monkeypatch):
    task, project, owner, member, outsider = shared_timer
    reconcile_task_work_sessions(task, now=at(18, 17))
    monkeypatch.setattr("design_workflow.time_tracking.timezone.now", lambda: at(21, 10))
    other_project = Project.objects.create(name="Other selected project", manager=outsider)
    client = APIClient()
    client.force_authenticate(make_manager("live-filter-manager@example.test"))
    base = f"/api/design-workflow/reports/time/?project={project.pk}"

    whole = client.get(base)
    assert whole.status_code == 200
    assert [(row["project"]["id"], row["minutes"]) for row in whole.data] == [(project.pk, 720)]
    for worker in (owner, member):
        response = client.get(base + f"&user={worker.pk}&start_date=2026-09-19&end_date=2026-09-20")
        assert response.status_code == 200
        assert [(row["project"]["id"], row["minutes"]) for row in response.data] == [(project.pk, 240)]
    assert client.get(base + "&start_date=2026-09-20&end_date=2026-09-20").data == []
    assert client.get(base + f"&user={outsider.pk}").data == []
    assert client.get(f"/api/design-workflow/reports/time/?project={other_project.pk}").data == []
    assert not TimeEntry.objects.exists()


def test_forecast_uses_each_workers_logged_plus_live_minutes(shared_timer, monkeypatch):
    task, project, owner, member, _ = shared_timer
    Task.objects.filter(pk=task.pk).update(estimated_minutes=240)
    TimeEntry.objects.create(task=task, user=owner, minutes=30, work_date=at(11, 0).date())
    reconcile_task_work_sessions(task, now=at(14, 9))
    monkeypatch.setattr("design_workflow.time_tracking.timezone.now", lambda: at(14, 10))
    client = APIClient()
    client.force_authenticate(make_manager("live-forecast-manager@example.test"))
    response = client.get(f"/api/design-workflow/reports/workflow/?project={project.pk}")
    assert response.status_code == 200
    assert response.data["estimate_vs_actual"]["actual_minutes"] == 150
    forecasts = {row["user"]["id"]: row for row in response.data["designer_forecast"]}
    assert forecasts[owner.pk]["remaining_minutes"] == 150
    assert forecasts[member.pk]["remaining_minutes"] == 180
    assert task.time_entries.count() == 1


def test_closing_a_session_replaces_live_minutes_without_double_counting(shared_timer, monkeypatch):
    task, project, _, _, _ = shared_timer
    reconcile_task_work_sessions(task, now=at(14, 9))
    monkeypatch.setattr("design_workflow.time_tracking.timezone.now", lambda: at(14, 10))
    client = APIClient()
    client.force_authenticate(make_manager("live-close-manager@example.test"))
    report_url = f"/api/design-workflow/reports/time/?project={project.pk}"
    assert client.get(report_url).data[0]["minutes"] == 120
    close(task, at(14, 10))
    assert active_session_minutes(now=at(14, 11)) == {}
    assert client.get(report_url).data[0]["minutes"] == 120
    assert client.get("/api/design-workflow/dashboard/summary/").data["week_logged_minutes"] == 120
    assert client.get(f"/api/design-workflow/reports/workflow/?project={project.pk}").data["estimate_vs_actual"]["actual_minutes"] == 120
    assert task.time_entries.count() == 2


def test_live_report_and_card_gets_do_not_persist_timer_progress(shared_timer, monkeypatch):
    task, project, _, _, _ = shared_timer
    reconcile_task_work_sessions(task, now=at(14, 9).replace(second=30))
    monkeypatch.setattr("design_workflow.time_tracking.timezone.now", lambda: at(14, 10))
    client = APIClient()
    client.force_authenticate(make_manager("live-readonly-manager@example.test"))
    original_sessions = list(task.work_sessions.order_by("pk").values_list("pk", "user_id", "started_at"))
    with CaptureQueriesContext(connection) as captured:
        for path in (
            "dashboard/summary/", "workload/", "reports/time/", "reports/workflow/",
            f"tasks/{task.pk}/", f"projects/{project.pk}/",
        ):
            response = client.get("/api/design-workflow/" + path)
            assert response.status_code == 200
    assert not [query["sql"] for query in captured.captured_queries if query["sql"].lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE "))]
    assert list(task.work_sessions.order_by("pk").values_list("pk", "user_id", "started_at")) == original_sessions
    assert not task.time_entries.exists()
    task.refresh_from_db()
    assert task.actual_minutes == 0
