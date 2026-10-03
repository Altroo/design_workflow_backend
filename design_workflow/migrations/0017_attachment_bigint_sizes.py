from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("design_workflow", "0016_alter_historicalnotification_type_and_more")]

    operations = [
        migrations.AlterField(model_name=name, name="size", field=models.PositiveBigIntegerField(default=0))
        for name in ("taskattachment", "historicaltaskattachment", "chatmessageattachment", "historicalchatmessageattachment")
    ]
