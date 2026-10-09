"""Prepare bilingual release notes; publish only after deployment is verified."""

from django.db import migrations


def prepare_writing_assistant_release(apps, schema_editor):
    entries = apps.get_model("ws", "ChangelogEntry").objects.using(
        schema_editor.connection.alias
    )
    entries.get_or_create(
        date="2026-10-09",
        defaults={
            "version": "1.4.0",
            "title_fr": "Un coup de main pour vos textes",
            "title_en": "Writing assistance",
            "changes_fr": "\n".join(
                [
                    "Traduisez vos textes en français ou en anglais.",
                    "Corrigez l’orthographe et la grammaire, ou reformulez un texte dans un ton plus professionnel.",
                    "Retrouvez ces aides dans les projets, les tâches, le chat, les commentaires, les checklists et les notes.",
                    "Comparez le texte original et la suggestion avant de choisir de l’utiliser. Rien n’est enregistré ni envoyé automatiquement.",
                    "Les mentions de vos collègues et les références aux projets et aux tâches sont conservées.",
                ]
            ),
            "changes_en": "\n".join(
                [
                    "Translate your text into French or English.",
                    "Correct spelling and grammar, or rewrite text in a more professional tone.",
                    "Use these tools in projects, tasks, chat, comments, checklists and notes.",
                    "Compare the original text with the suggestion before choosing to apply it. Nothing is saved or sent automatically.",
                    "Colleague mentions and project and task references are preserved.",
                ]
            ),
            "is_published": False,
        },
    )


class Migration(migrations.Migration):
    dependencies = [("ws", "0010_attachment_preview_notes")]
    operations = [
        migrations.RunPython(
            prepare_writing_assistant_release, migrations.RunPython.noop
        )
    ]
