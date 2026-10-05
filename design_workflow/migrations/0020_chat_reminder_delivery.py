from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("design_workflow", "0019_task_work_sessions")]

    operations = [
        migrations.AddField(model_name="chatmessagereminder", name="delivered_at", field=models.DateTimeField(blank=True, db_index=True, null=True)),
        migrations.AddField(model_name="historicalchatmessagereminder", name="delivered_at", field=models.DateTimeField(blank=True, db_index=True, null=True)),
    ]
