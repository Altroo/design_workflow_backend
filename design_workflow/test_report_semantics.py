from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    Project,
    Task,
    TaskActivity,
    TaskActivityType,
    TaskReviewState,
    TaskStatus,
    TimeEntry,
)
from .tests import make_designer, make_manager

pytestmark = pytest.mark.django_db


@pytest.fixture(name="report_context")
def report_context_fixture(monkeypatch):
    now = timezone.now()
    monkeypatch.setattr("design_workflow.views.timezone.now", lambda: now)
    manager = make_manager()
    worker = make_designer()
    project = Project.objects.create(name="Report semantics", manager=worker)
    client = APIClient()
    client.force_authenticate(manager)
    return client, project, worker, now


def task_at(project, worker, created, **values):
    task = Task.objects.create(
        project=project,
        title="Report task",
        current_assignee=worker,
        created_by=worker,
        updated_by=worker,
        **values,
    )
    Task.objects.filter(pk=task.pk).update(created_at=created)
    return task


def status_at(task, actor, timestamp, metadata):
    event = TaskActivity.objects.create(
        task=task,
        actor=actor,
        action_type=TaskActivityType.STATUS_CHANGED,
        metadata=metadata,
    )
    TaskActivity.objects.filter(pk=event.pk).update(created_at=timestamp)


def test_completion_averages_use_recorded_dates_without_rounding_days_first(
    report_context,
):
    client, project, worker, now = report_context
    for minutes in (37, 77):
        created = now - timedelta(hours=4)
        task = task_at(project, worker, created, status=TaskStatus.DONE)
        status_at(
            task,
            worker,
            created + timedelta(minutes=10),
            {"status": "todo", "next": "in_progress"},
        )
        status_at(
            task,
            worker,
            created + timedelta(minutes=minutes),
            {"status": "in_progress", "next": "done"},
        )
    data = client.get("/api/design-workflow/reports/workflow/").data
    assert data["lead_time_days"] * 1440 == pytest.approx(57)
    assert data["cycle_time_days"] * 1440 == pytest.approx(47)
    assert data["lead_time_sample_size"] == data["cycle_time_sample_size"] == 2


def test_unknown_completion_is_not_inferred_from_last_edit_or_task_creation(
    report_context,
):
    client, project, worker, now = report_context
    task_at(project, worker, now - timedelta(days=2), status=TaskStatus.DONE)
    data = client.get("/api/design-workflow/reports/workflow/").data
    assert data["status_counts"]["done"] == 1
    assert data["lead_time_days"] is None
    assert data["cycle_time_days"] is None
    assert data["lead_time_sample_size"] == data["cycle_time_sample_size"] == 0


def test_completed_date_without_start_does_not_invent_a_cycle_time(report_context):
    client, project, worker, now = report_context
    task_at(
        project,
        worker,
        now - timedelta(minutes=30),
        status=TaskStatus.DONE,
        completed_at=now,
    )
    data = client.get("/api/design-workflow/reports/workflow/").data
    assert data["lead_time_days"] * 1440 == pytest.approx(30)
    assert data["cycle_time_days"] is None
    assert data["cycle_time_sample_size"] == 0


def test_reopened_future_and_invalid_completion_dates_do_not_enter_averages(
    report_context,
):
    client, project, worker, now = report_context
    created = now - timedelta(days=2)
    task_at(project, worker, created, status=TaskStatus.TODO, completed_at=now)
    task_at(
        project,
        worker,
        created,
        status=TaskStatus.DONE,
        completed_at=now + timedelta(days=1),
    )
    task_at(
        project,
        worker,
        created,
        status=TaskStatus.DONE,
        completed_at=created - timedelta(minutes=1),
    )
    data = client.get("/api/design-workflow/reports/workflow/").data
    assert data["lead_time_sample_size"] == 0
    assert data["lead_time_days"] is None


def test_formal_reviews_are_distinguished_from_manual_review_column_moves(
    report_context,
):
    client, project, worker, now = report_context
    for _ in range(3):
        task_at(project, worker, now - timedelta(days=1), status=TaskStatus.IN_REVIEW)
    task_at(
        project,
        worker,
        now - timedelta(days=1),
        status=TaskStatus.IN_REVIEW,
        review_state=TaskReviewState.NEEDS_REVIEW,
        review_requested_at=now - timedelta(minutes=30),
    )
    task_at(
        project,
        worker,
        now - timedelta(days=1),
        status=TaskStatus.IN_REVIEW,
        review_state=TaskReviewState.CHANGES_REQUESTED,
        review_requested_at=now - timedelta(hours=8),
    )
    data = client.get("/api/design-workflow/reports/workflow/").data
    review = data["review_bottlenecks"]
    assert data["status_counts"]["in_review"] == 5
    assert review["needs_review"] == 1
    assert review["in_review_without_request"] == 3
    assert review["changes_requested"] == 1
    assert (
        review["pending_review_minutes"]
        == review["average_pending_review_minutes"]
        == 30
    )
    assert review["wait_sample_size"] == 1


@pytest.mark.parametrize(
    "estimate,logged,risk,missing,exhausted",
    [(0, 0, "unknown", 1, 0), (60, 60, "high", 0, 1), (60, 90, "high", 0, 1)],
)
def test_unknown_remaining_work_is_never_reported_as_zero_percent_normal(
    report_context, estimate, logged, risk, missing, exhausted
):
    client, project, worker, now = report_context
    task = task_at(project, worker, now - timedelta(days=1), estimated_minutes=estimate)
    if logged:
        TimeEntry.objects.create(
            task=task, user=worker, minutes=logged, work_date=now.date()
        )
    row = client.get("/api/design-workflow/reports/workflow/").data["capacity"][0]
    assert row["load_percent"] is None
    assert row["forecast_days"] is None
    assert row["risk"] == risk
    assert row["unestimated_tasks"] == missing
    assert row["exhausted_estimate_tasks"] == exhausted


def test_capacity_keeps_known_remainder_but_flags_partial_estimates(report_context):
    client, project, worker, now = report_context
    task_at(project, worker, now, estimated_minutes=480)
    task_at(project, worker, now, estimated_minutes=0)
    row = client.get("/api/design-workflow/reports/workflow/").data["capacity"][0]
    assert row["remaining_minutes"] == 480
    assert row["load_percent"] is None
    assert row["open_tasks"] == 2


def test_known_work_above_weekly_capacity_is_high_risk_even_with_missing_estimates(
    report_context,
):
    client, project, worker, now = report_context
    task_at(project, worker, now, estimated_minutes=3000)
    task_at(project, worker, now, estimated_minutes=0)
    row = client.get("/api/design-workflow/reports/workflow/").data["capacity"][0]
    assert row["load_percent"] is None
    assert row["risk"] == "high"
    assert row["remaining_minutes"] == 3000


def test_missing_or_invalid_review_dates_do_not_look_like_zero_wait(report_context):
    client, project, worker, now = report_context
    for requested in (None, now + timedelta(hours=1), now - timedelta(days=3)):
        task_at(
            project,
            worker,
            now - timedelta(days=1),
            status=TaskStatus.IN_REVIEW,
            review_state=TaskReviewState.NEEDS_REVIEW,
            review_requested_at=requested,
        )
    task_at(
        project,
        worker,
        now - timedelta(days=1),
        status=TaskStatus.DONE,
        review_state=TaskReviewState.NEEDS_REVIEW,
        review_requested_at=now - timedelta(minutes=15),
    )
    review = client.get("/api/design-workflow/reports/workflow/").data[
        "review_bottlenecks"
    ]
    assert review["needs_review"] == 3
    assert review["wait_sample_size"] == 0
    assert review["pending_review_minutes"] is None
    assert review["average_pending_review_minutes"] is None


def test_recompleted_task_uses_latest_completion_and_first_recorded_start(
    report_context,
):
    client, project, worker, now = report_context
    created = now - timedelta(hours=4)
    task = task_at(
        project,
        worker,
        created,
        status=TaskStatus.DONE,
        completed_at=created + timedelta(minutes=60),
    )
    for offset, status_value in (
        (10, "in_progress"),
        (60, "done"),
        (90, "in_progress"),
        (180, "done"),
    ):
        status_at(
            task, worker, created + timedelta(minutes=offset), {"status": status_value}
        )
    data = client.get("/api/design-workflow/reports/workflow/").data
    assert data["lead_time_days"] * 1440 == pytest.approx(180)
    assert data["cycle_time_days"] * 1440 == pytest.approx(170)


def test_archived_tasks_and_projects_are_excluded_from_both_report_totals(
    report_context,
):
    client, project, worker, now = report_context
    active = task_at(project, worker, now)
    archived_task = task_at(project, worker, now, archived=True)
    archived_project = Project.objects.create(
        name="Archived", manager=worker, archived=True
    )
    archived_project_task = task_at(archived_project, worker, now)
    for task in (active, archived_task, archived_project_task):
        TimeEntry.objects.create(
            task=task, user=worker, minutes=60, work_date=now.date()
        )
    assert (
        sum(
            row["minutes"]
            for row in client.get("/api/design-workflow/reports/time/").data
        )
        == 60
    )
    report = client.get("/api/design-workflow/reports/workflow/").data
    assert report["tasks_sampled"] == 1
    assert report["estimate_vs_actual"]["actual_minutes"] == 60
