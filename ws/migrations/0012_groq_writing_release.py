from django.db import migrations


def record_release(apps, schema_editor):
    apps.get_model("ws", "ChangelogEntry").objects.using(
        schema_editor.connection.alias
    ).update_or_create(
        date="2026-11-17",
        defaults={
            "version": "1.5.3",
            "title_fr": "Des textes plus naturels et professionnels",
            "title_en": "More natural and professional writing",
            "changes_fr": "Traduction tenant compte du sens et du vocabulaire métier.\nCorrection grammaticale et reformulation professionnelle améliorées.\nRetours à la ligne, listes, mentions de collègues, références et valeurs conservés.\nLes suggestions restent à vérifier et à appliquer : aucun texte n’est enregistré ni envoyé automatiquement.",
            "changes_en": "Meaning-aware translation using business terminology.\nImproved grammar correction and professional rewriting.\nPreserve line breaks, lists, colleague mentions, references and values.\nSuggestions remain available for review and application; no text is saved or sent automatically.",
            "is_published": False,
        },
    )


class Migration(migrations.Migration):
    dependencies = [("ws", "0011_writing_assistant_release")]
    operations = [migrations.RunPython(record_release, migrations.RunPython.noop)]
