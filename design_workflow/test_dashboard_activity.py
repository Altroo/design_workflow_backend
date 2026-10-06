from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Project, Task, TaskStatus, TimeEntry
from .tests import make_designer, make_manager

pytestmark = pytest.mark.django_db


@pytest.fixture(name="dashboard")
def dashboard_fixture(monkeypatch):
    now = datetime(2026, 10, 6, 15, tzinfo=ZoneInfo("Africa/Casablanca"))
    monkeypatch.setattr("design_workflow.views.timezone.now", lambda: now)
    manager = make_manager()
    client = APIClient()
    client.force_authenticate(manager)
    project = Project.objects.create(name="Dashboard activity", manager=manager)
    with timezone.override("Africa/Casablanca"):
        yield client, project, manager, now


def make_task(project, manager, created, **changes):
    task = Task.objects.create(
        project=project, title="Tracked task", created_by=manager, updated_by=manager
    )
    Task.objects.filter(pk=task.pk).update(created_at=created, **changes)
    return task


def test_dashboard_fills_fourteen_days_and_includes_unplanned_tasks(dashboard):
    client, project, manager, now = dashboard
    make_task(project, manager, now - timedelta(days=13), status=TaskStatus.BACKLOG)
    make_task(
        project,
        manager,
        now - timedelta(days=3),
        status=TaskStatus.DONE,
        completed_at=now,
    )
    result = client.get("/api/design-workflow/dashboard/summary/")
    assert result.status_code == 200
    assert result.data["backlog_tasks"] == 1
    days = result.data["daily_activity"]
    assert len(days) == 14
    assert days[0] == {"date": "2026-09-23", "created": 1, "completed": 0}
    assert days[1] == {"date": "2026-09-24", "created": 0, "completed": 0}
    assert days[-1] == {"date": "2026-10-06", "created": 0, "completed": 1}
    assert sum(day["created"] for day in days) == 2


def test_activity_excludes_archives_future_dates_and_reopened_tasks(dashboard):
    client, project, manager, now = dashboard
    make_task(
        project,
        manager,
        now - timedelta(days=14),
        status=TaskStatus.DONE,
        completed_at=now - timedelta(days=14),
    )
    make_task(
        project, manager, now, archived=True, status=TaskStatus.DONE, completed_at=now
    )
    make_task(
        project,
        manager,
        now - timedelta(days=20),
        status=TaskStatus.IN_PROGRESS,
        completed_at=now,
    )
    make_task(
        project, manager, now - timedelta(days=20), status=TaskStatus.DONE
    )  # No known completion date.
    make_task(
        project,
        manager,
        now + timedelta(days=1),
        status=TaskStatus.DONE,
        completed_at=now + timedelta(days=1),
    )
    archived_project = Project.objects.create(
        name="Archived", manager=manager, archived=True
    )
    make_task(archived_project, manager, now, status=TaskStatus.DONE, completed_at=now)
    days = client.get("/api/design-workflow/dashboard/summary/").data["daily_activity"]
    assert sum(day["created"] + day["completed"] for day in days) == 0


def test_activity_groups_dates_in_the_active_business_timezone(dashboard):
    client, project, manager, _ = dashboard
    instant = datetime(2026, 10, 5, 23, 30, tzinfo=ZoneInfo("UTC"))
    make_task(project, manager, instant, status=TaskStatus.DONE, completed_at=instant)
    # Use a fixed offset so the regression does not depend on future DST rules.
    with timezone.override(ZoneInfo("Etc/GMT-1")):
        days = client.get("/api/design-workflow/dashboard/summary/").data[
            "daily_activity"
        ]
    assert days[-1] == {"date": "2026-10-06", "created": 1, "completed": 1}


def test_dashboard_week_excludes_future_time_entries_and_is_manager_only(dashboard):
    client, project, manager, now = dashboard
    task = make_task(project, manager, now)
    for offset, minutes in ((-1, 30), (0, 60), (1, 120)):
        TimeEntry.objects.create(
            task=task,
            user=manager,
            work_date=timezone.localdate(now) + timedelta(days=offset),
            minutes=minutes,
        )
    assert (
        client.get("/api/design-workflow/dashboard/summary/").data[
            "week_logged_minutes"
        ]
        == 90
    )
    client.force_authenticate(make_designer())
    assert client.get("/api/design-workflow/dashboard/summary/").status_code == 403
