"""Number Git milestones around the confirmed 16 September stable release.

Historical 0.x/1.x labels are assigned retrospectively to the curated entries.
Feature releases advance the minor version; fixes advance the patch version.
Do not announce Maintenance.version until the matching frontend is healthy.
"""

from datetime import date

from django.db import migrations

RELEASE_VERSIONS = {
    "2026-04-30": "0.1.0",
    "2026-05-04": "0.2.0",
    "2026-05-13": "0.3.0",
    "2026-06-09": "0.4.0",
    "2026-07-01": "0.4.1",
    "2026-07-29": "0.4.2",
    "2026-09-14": "0.5.0",
    "2026-09-15": "0.6.0",
    "2026-09-16": "1.0.0",
    "2026-10-03": "1.1.0",
    "2026-10-05": "1.2.0",
    "2026-10-06": "1.3.0",
}
RELEASE_DATE = date(2026, 10, 7)
CURRENT_VERSION = "1.3.1"


def correct_release_history(apps, schema_editor):
    entries = apps.get_model("ws", "ChangelogEntry").objects.using(
        schema_editor.connection.alias
    )
    for release_date, version in RELEASE_VERSIONS.items():
        # Correct the previous migration's mistaken 1.0.0 date, while retaining
        # any separately numbered entries and all administrator-edited text.
        prior_versions = [""]
        if release_date == "2026-10-06":
            prior_versions.append("1.0.0")
        entries.filter(date=release_date, version__in=prior_versions).update(
            version=version
        )

    entries.get_or_create(
        date=RELEASE_DATE,
        defaults={
            "version": CURRENT_VERSION,
            "title_fr": "Un historique des nouveautés plus lisible",
            "title_en": "An easier-to-read release history",
            "changes_fr": (
                "Retrouvez les dates et les versions à gauche de chaque mise à jour.\n"
                "Le changelog est plus lisible sur ordinateur et sur mobile."
            ),
            "changes_en": (
                "Find dates and versions to the left of each update.\n"
                "The changelog is easier to read on desktop and mobile."
            ),
            "is_published": True,
        },
    )


class Migration(migrations.Migration):
    dependencies = [("ws", "0006_release_1_0_changelog")]
    operations = [
        migrations.RunPython(correct_release_history, migrations.RunPython.noop),
    ]
