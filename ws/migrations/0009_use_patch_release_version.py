"""Number this small follow-up as a patch; 1.3.1 was already published.

0008 may already be applied, so correct its notes without rewriting history.
Maintenance.version is published separately once the frontend is healthy.
"""

from django.db import migrations


def use_patch_release_version(apps, schema_editor):
    entries = apps.get_model("ws", "ChangelogEntry").objects.using(
        schema_editor.connection.alias
    )
    entries.filter(date="2026-10-07", version="1.4.0").update(version="1.3.2")


class Migration(migrations.Migration):
    dependencies = [("ws", "0008_complete_release_notes")]
    operations = [
        migrations.RunPython(use_patch_release_version, migrations.RunPython.noop),
    ]
