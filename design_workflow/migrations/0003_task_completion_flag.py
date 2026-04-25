from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("design_workflow", "0002_cards_chat_features"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="is_completed",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="task",
            name="completed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
