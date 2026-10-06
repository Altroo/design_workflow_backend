from datetime import timedelta
from importlib import import_module
from unittest.mock import AsyncMock, patch

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils import timezone
from rest_framework.test import APIClient

from .models import ChangelogEntry
from .serializers import ChangelogEntrySerializer

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def empty_changelog():
    ChangelogEntry.objects.all().delete()


def entry(**kwargs):
    values = dict(
        date=timezone.localdate(),
        title_fr="Travail partagé",
        title_en="Shared work",
        changes_fr="  Une nouveauté.\n\nUne amélioration. ",
        changes_en=" A new feature.\n\nAn improvement. ",
        is_published=True,
    )
    return ChangelogEntry.objects.create(**(values | kwargs))


def test_authenticated_members_get_complete_bilingual_published_history_only():
    today = timezone.localdate()
    entry(date=today - timedelta(days=3))
    newest = entry(version="1.0.0")
    entry(date=today - timedelta(days=1), is_published=False, title_fr="DRAFT SECRET")
    entry(date=today + timedelta(days=1), title_fr="FUTURE SECRET")
    client = APIClient()
    client.force_authenticate(
        get_user_model().objects.create_user(
            email="changelog@example.com", password="test-only"
        )
    )
    response = client.get("/api/ws/changelog/")
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    assert len(response.data) == 2
    assert response.data[0]["id"] == newest.pk
    assert response.data[0]["version"] == "1.0.0"
    assert response.data[1]["version"] == ""
    assert response.data[0]["changes_fr"] == ["Une nouveauté.", "Une amélioration."]
    assert response.data[0]["changes_en"] == ["A new feature.", "An improvement."]
    assert "SECRET" not in str(response.data)
    assert client.post("/api/ws/changelog/", {}).status_code == 405


def test_history_requires_authentication():
    assert APIClient().get("/api/ws/changelog/").status_code in (401, 403)


@pytest.mark.parametrize("version", ["", "1.0.0", "1.12.3"])
def test_changelog_accepts_release_versions_and_unversioned_history(version):
    entry(version=version).full_clean()


@pytest.mark.parametrize("version", ["1", "01.0.0", "1.0.0-beta"])
def test_changelog_rejects_invalid_versions(version):
    with pytest.raises(ValidationError):
        entry(version=version).full_clean()


def test_drafts_can_be_incomplete_but_publishing_requires_both_languages():
    draft = entry(title_en="", changes_en=" ", is_published=False)
    draft.full_clean()
    draft.is_published = True
    with pytest.raises(ValidationError) as error:
        draft.full_clean()
    assert set(error.value.message_dict) == {"title_en", "changes_en"}


def test_changelog_dates_are_unique_and_displayed_with_title():
    saved = entry()
    assert str(saved) == f"{saved.date} — Travail partagé"
    with pytest.raises(ValidationError):
        ChangelogEntry(date=saved.date).full_clean()


def test_serializer_exposes_plain_content_not_admin_publication_fields():
    data = ChangelogEntrySerializer(entry(changes_fr="<b>Texte</b>")).data
    assert data["changes_fr"] == ["<b>Texte</b>"]
    assert "is_published" not in data
    assert "updated_at" not in data


def test_publish_edit_and_unpublish_signal_after_commit_without_content(
    django_capture_on_commit_callbacks,
):
    with patch("ws.models.get_channel_layer") as layer:
        layer.return_value.group_send = AsyncMock()
        with django_capture_on_commit_callbacks(execute=True), transaction.atomic():
            saved = entry()
            saved.is_published = False
            saved.save()
            layer.return_value.group_send.assert_not_called()
        assert layer.return_value.group_send.await_count == 2
        layer.return_value.group_send.assert_awaited_with(
            "workflow",
            {
                "type": "receive_group_message",
                "message": {"type": "WORKFLOW_EVENT", "scope": "changelog"},
            },
        )
        layer.return_value.group_send.reset_mock()
        with django_capture_on_commit_callbacks(execute=True):
            saved.delete()
        layer.return_value.group_send.assert_awaited_once()


def test_rollback_never_announces_changelog(django_capture_on_commit_callbacks):
    with patch("ws.models.get_channel_layer") as layer:
        layer.return_value.group_send = AsyncMock()
        with (
            django_capture_on_commit_callbacks(execute=True),
            pytest.raises(ValueError),
            transaction.atomic(),
        ):
            entry()
            raise ValueError("rollback")
        layer.return_value.group_send.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_git_history_seed_is_bilingual_idempotent_and_preserves_admin_edits():
    migration = import_module("ws.migrations.0004_seed_changelog")
    with connection.schema_editor(atomic=False) as editor:
        migration.seed_changelog(apps, editor)
        assert ChangelogEntry.objects.count() == 12
        for saved in ChangelogEntry.objects.all():
            saved.full_clean()
            assert len(saved.changes_fr.splitlines()) == len(
                saved.changes_en.splitlines()
            )
        latest = ChangelogEntry.objects.first()
        latest.title_fr = "Titre modifié par l’administrateur"
        latest.save()
        migration.seed_changelog(apps, editor)
    assert ChangelogEntry.objects.count() == 12
    latest.refresh_from_db()
    assert latest.title_fr == "Titre modifié par l’administrateur"


@pytest.mark.django_db(transaction=True)
def test_stable_release_notes_preserve_edits_and_do_not_invent_older_versions():
    migration = import_module("ws.migrations.0006_release_1_0_changelog")
    older = entry(date=migration.RELEASE_DATE - timedelta(days=1))
    current = entry(date=migration.RELEASE_DATE, title_fr="Titre conservé")
    with connection.schema_editor(atomic=False) as editor:
        migration.publish_release_notes(apps, editor)
        migration.publish_release_notes(apps, editor)
    current.refresh_from_db()
    older.refresh_from_db()
    assert current.version == "1.0.0"
    assert older.version == ""
    assert current.title_fr == "Titre conservé"
    for language, additions in migration.CHANGES.items():
        lines = getattr(current, f"changes_{language}").splitlines()
        assert all(lines.count(line) == 1 for line in additions)


@pytest.mark.django_db(transaction=True)
def test_stable_release_notes_do_not_overwrite_another_numbered_release():
    migration = import_module("ws.migrations.0006_release_1_0_changelog")
    current = entry(date=migration.RELEASE_DATE, version="2.0.0")
    with connection.schema_editor(atomic=False) as editor:
        migration.publish_release_notes(apps, editor)
    current.refresh_from_db()
    assert current.version == "2.0.0"
    assert "PDF" not in current.changes_fr
