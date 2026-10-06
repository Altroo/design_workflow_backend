"""Document the first numbered stable release without changing older history."""

from datetime import date

from django.db import migrations

RELEASE_DATE = date(2026, 10, 6)
CHANGES = {
    "fr": [
        "Ouvrez les rapports directement en PDF pour les consulter ou les enregistrer.",
        "Activez les notifications de l’ordinateur pour suivre les messages et les nouveautés qui vous concernent.",
        "Les images des cartes se chargent plus rapidement. Les pièces jointes gardent leur qualité d’origine.",
        "Mettez l’application à jour depuis une fenêtre de confirmation, sans la réinstaller.",
        "Consultez le changelog en français ou en anglais pour découvrir les nouveautés et leur version.",
    ],
    "en": [
        "Open reports directly as PDFs to read or save them.",
        "Enable desktop notifications to follow messages and updates that concern you.",
        "Card images load faster. Attachments retain their original quality.",
        "Update the app from a confirmation dialog without reinstalling it.",
        "Browse the changelog in French or English to discover new features and their release version.",
    ],
}


def publish_release_notes(apps, schema_editor):
    entry, _ = (
        apps.get_model("ws", "ChangelogEntry")
        .objects.using(schema_editor.connection.alias)
        .get_or_create(
            date=RELEASE_DATE,
            defaults={
                "title_fr": "Un suivi plus clair pour toute l’équipe",
                "title_en": "A clearer picture of the team's work",
                "is_published": True,
            },
        )
    )
    # Preserve any separately numbered release entered by an administrator.
    if entry.version not in ("", "1.0.0"):
        return
    entry.version = "1.0.0"
    for language, additions in CHANGES.items():
        field = f"changes_{language}"
        lines = getattr(entry, field).splitlines()
        lines.extend(line for line in additions if line not in lines)
        setattr(entry, field, "\n".join(lines))
    entry.save(update_fields=["version", "changes_fr", "changes_en"])


class Migration(migrations.Migration):
    dependencies = [("ws", "0005_changelogentry_version")]
    operations = [
        migrations.RunPython(publish_release_notes, migrations.RunPython.noop),
    ]
