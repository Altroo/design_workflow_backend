from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("design_workflow", "0004_chatmessage_deleted_at_chatmessage_deleted_by_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="cover_image",
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to="design_workflow/task_covers/%Y/%m/",
            ),
        ),
    ]
