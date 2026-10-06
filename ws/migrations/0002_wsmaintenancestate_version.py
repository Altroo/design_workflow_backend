from django.db import migrations, models
from django.core.validators import RegexValidator


class Migration(migrations.Migration):
    dependencies = [("ws", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="wsmaintenancestate",
            name="version",
            field=models.CharField(
                default="0.1.0",
                help_text="Publish only after this frontend version is deployed and healthy.",
                max_length=20,
                validators=[
                    RegexValidator(
                        r"^(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})\Z",
                        "Use a release version such as 1.2.0 (three numbers, up to six digits each).",
                    )
                ],
                verbose_name="Version",
            ),
        ),
    ]
