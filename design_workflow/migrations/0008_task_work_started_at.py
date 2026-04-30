from django.db import migrations, models


def seed_active_work_sessions(apps, schema_editor):
    task_model = apps.get_model("design_workflow", "Task")
    task_model.objects.filter(status="in_progress", work_started_at__isnull=True).update(work_started_at=models.F("updated_at"))


class Migration(migrations.Migration):

    dependencies = [
        ("design_workflow", "0007_task_checklists"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="work_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(seed_active_work_sessions, migrations.RunPython.noop),
    ]
