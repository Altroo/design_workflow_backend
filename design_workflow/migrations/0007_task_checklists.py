from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def create_default_checklists(apps, schema_editor):
    TaskChecklist = apps.get_model("design_workflow", "TaskChecklist")
    TaskChecklistItem = apps.get_model("design_workflow", "TaskChecklistItem")

    task_ids = (
        TaskChecklistItem.objects.filter(checklist__isnull=True)
        .values_list("task_id", flat=True)
        .distinct()
    )
    for task_id in task_ids:
        first_item = (
            TaskChecklistItem.objects.filter(task_id=task_id, checklist__isnull=True)
            .order_by("sort_order", "created_at")
            .first()
        )
        if not first_item:
            continue
        checklist = TaskChecklist.objects.create(
            task_id=task_id,
            title="Checklist",
            sort_order=0,
            created_by_id=first_item.created_by_id,
        )
        TaskChecklistItem.objects.filter(task_id=task_id, checklist__isnull=True).update(checklist=checklist)


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("design_workflow", "0006_chat_workflow_actions"),
    ]

    operations = [
        migrations.CreateModel(
            name="TaskChecklist",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("title", models.CharField(max_length=255)),
                ("sort_order", models.PositiveIntegerField(default=0)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="created_task_checklists",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="checklists",
                        to="design_workflow.task",
                    ),
                ),
            ],
            options={
                "ordering": ("sort_order", "created_at"),
            },
        ),
        migrations.AddField(
            model_name="taskchecklistitem",
            name="checklist",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="items",
                to="design_workflow.taskchecklist",
            ),
        ),
        migrations.RunPython(create_default_checklists, migrations.RunPython.noop),
    ]
