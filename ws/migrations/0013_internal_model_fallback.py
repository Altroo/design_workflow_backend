from django.db import migrations


def record_release(apps, schema_editor):
    entry, _ = apps.get_model("ws", "ChangelogEntry").objects.using(
        schema_editor.connection.alias
    ).get_or_create(
        date="2026-11-20",
        defaults={
            "version": "1.5.4",
            "title_fr": "Traduction et correction avec les modèles internes",
            "title_en": "Internal fallback for translation and editing",
            "changes_fr": (
                "Lorsque Groq atteint sa limite, les modèles internes prennent le relais "
                "pour la traduction, la correction et la reformulation professionnelle.\n"
                "Les délais d’attente s’affichent en heures, minutes et secondes."
            ),
            "changes_en": (
                "When Groq reaches its quota, internal models handle translation, spelling "
                "and grammar correction, and professional rewriting.\n"
                "Retry delays appear in hours, minutes, and seconds."
            ),
            "is_published": False,
        },
    )
    if entry.version != "1.5.4":
        raise ValueError("Another changelog entry already exists for this release date")


class Migration(migrations.Migration):
    dependencies = [("ws", "0012_groq_writing_release")]
    operations = [migrations.RunPython(record_release, migrations.RunPython.noop)]
