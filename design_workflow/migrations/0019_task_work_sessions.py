from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def seed_running_sessions(apps, schema_editor):
    Task = apps.get_model("design_workflow", "Task")
    Session = apps.get_model("design_workflow", "TaskWorkSession")
    User = apps.get_model(settings.AUTH_USER_MODEL)
    db = schema_editor.connection.alias
    active_ids = set(User.objects.using(db).filter(is_active=True).values_list("pk", flat=True))
    starts_now = django.utils.timezone.now()
    for task in Task.objects.using(db).filter(status="in_progress", archived=False, project__archived=False).select_related("project"):
        members = {task.project.manager_id, task.current_assignee_id}
        members.update(task.project.collaborators.using(db).values_list("pk", flat=True))
        legacy_worker = task.current_assignee_id or task.project.manager_id
        sessions = [Session(task_id=task.pk, user_id=user_id,
                            started_at=(task.work_started_at or starts_now) if user_id == legacy_worker else starts_now)
                    for user_id in sorted(members & active_ids)]
        Session.objects.using(db).bulk_create(sessions)
        Task.objects.using(db).filter(pk=task.pk).update(work_started_at=min((row.started_at for row in sessions), default=None))


class Migration(migrations.Migration):
    dependencies = [
        ("design_workflow", "0018_project_collaborators"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="TaskWorkSession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("started_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("task", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="work_sessions", to="design_workflow.task")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="design_work_sessions", to=settings.AUTH_USER_MODEL)),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("task", "user"), name="unique_task_worker_session")]},
        ),
        # Preserve the existing worker's running timer. Other collaborators
        # start at migration time; previously logged hours are untouched.
        migrations.RunPython(seed_running_sessions, migrations.RunPython.noop),
    ]
