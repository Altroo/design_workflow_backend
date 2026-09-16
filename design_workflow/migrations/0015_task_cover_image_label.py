from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("design_workflow", "0014_historicaltasklabel_created_by_tasklabel_created_by_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="historicaltask",
            name="cover_image_label",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="task",
            name="cover_image_label",
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
