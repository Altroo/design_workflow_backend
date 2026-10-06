import pytest
from rest_framework.test import APIClient

from design_workflow.models import Project, Task, TimeEntry
from design_workflow.tests import make_designer, make_manager

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("staff_only", [False, True])
def test_time_history_pages_include_entries_older_than_fifty(staff_only):
    manager = make_manager()
    if staff_only:
        manager.role = "designer"
        manager.save(update_fields=["role"])
    project = Project.objects.create(name="History", manager=manager)
    task = Task.objects.create(
        project=project, title="History", created_by=manager, updated_by=manager
    )
    TimeEntry.objects.bulk_create(
        [TimeEntry(task=task, user=manager, minutes=index + 1) for index in range(56)]
    )
    client = APIClient()
    client.force_authenticate(manager)
    seen = []
    for page in range(1, 13):
        response = client.get(
            f"/api/design-workflow/tasks/{task.pk}/time-entries/", {"page": page}
        )
        assert response.status_code == 200
        assert response.data["count"] == 56
        seen.extend(entry["id"] for entry in response.data["results"])
    assert len(set(seen)) == 56
    assert len(seen) == 56
    legacy = client.get(f"/api/design-workflow/tasks/{task.pk}/time-entries/")
    assert len(legacy.data) == 56


def test_designer_cannot_read_manager_time_history():
    designer = make_designer()
    project = Project.objects.create(name="History", manager=designer)
    task = Task.objects.create(
        project=project, title="History", created_by=designer, updated_by=designer
    )
    client = APIClient()
    client.force_authenticate(designer)
    assert (
        client.get(
            f"/api/design-workflow/tasks/{task.pk}/time-entries/", {"page": 1}
        ).status_code
        == 403
    )
