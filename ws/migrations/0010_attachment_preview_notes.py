"""Document the attachment-preview follow-up; publish app version after deploy."""

from django.db import migrations

CHANGES = {
    "fr": [
        "Les pièces jointes image utilisent des aperçus légers dans les cartes et les tâches ; les fichiers d’origine restent disponibles au téléchargement.",
        "Les boutons pour renommer une pièce jointe, la mettre en couverture et la supprimer sont regroupés et mieux alignés.",
    ],
    "en": [
        "Image attachments use lightweight previews in cards and tasks; original files remain available to download.",
        "The actions to rename an attachment, set it as a cover and delete it are grouped and better aligned.",
    ],
}


def add_attachment_preview_notes(apps, schema_editor):
    entries = apps.get_model("ws", "ChangelogEntry").objects.using(
        schema_editor.connection.alias
    )
    saved = entries.filter(date="2026-10-07", version__in=("1.3.2", "1.3.3")).first()
    if saved is None:
        return
    for language, additions in CHANGES.items():
        field = f"changes_{language}"
        lines = getattr(saved, field).splitlines()
        lines.extend(line for line in additions if line not in lines)
        setattr(saved, field, "\n".join(lines))
    saved.version = "1.3.3"
    saved.save(update_fields=["version", "changes_fr", "changes_en"])


class Migration(migrations.Migration):
    dependencies = [("ws", "0009_use_patch_release_version")]
    operations = [
        migrations.RunPython(add_attachment_preview_notes, migrations.RunPython.noop)
    ]
