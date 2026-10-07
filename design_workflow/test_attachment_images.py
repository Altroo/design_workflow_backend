import hashlib
import io
from unittest.mock import patch

import pytest
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import DatabaseError
from PIL import Image
from rest_framework.test import APIClient

from .attachment_images import create_attachment_thumbnail, save_attachment_thumbnail
from .card_images import THUMBNAIL_MAX_BYTES, make_card_thumbnail
from .models import Project, Task, TaskAttachment
from .serializers import TaskCardSerializer
from .test_card_images import image_bytes
from .tests import make_designer

pytestmark = pytest.mark.django_db


@pytest.fixture
def attachment_context(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    owner = make_designer("attachment-preview@test.com")
    project = Project.objects.create(name="Image previews", manager=owner)
    task = Task.objects.create(
        project=project, title="Card", created_by=owner, updated_by=owner
    )
    client = APIClient()
    client.force_authenticate(owner)
    return client, task, owner


def attach(task, owner, data, *, name="render.png", mime_type="image/png"):
    return TaskAttachment.objects.create(
        task=task,
        uploaded_by=owner,
        file=ContentFile(data, name=name),
        name=name,
        mime_type=mime_type,
        size=len(data),
    )


@pytest.mark.parametrize("format_", ["PNG", "JPEG"])
def test_image_upload_returns_preview_and_preserves_original(
    attachment_context, format_
):
    client, task, _ = attachment_context
    original = image_bytes((3200, 1800), format_=format_)
    response = client.post(
        f"/api/design-workflow/tasks/{task.pk}/attachments/",
        {
            "file": SimpleUploadedFile(
                f"render.{format_.lower()}",
                original,
                content_type=f"image/{format_.lower()}",
            ),
            "name": "Final render",
        },
        format="multipart",
    )
    assert response.status_code == 201
    saved = TaskAttachment.objects.get(pk=response.data["id"])
    assert response.data["thumbnail_url"] == f"http://testserver{saved.thumbnail.url}"
    assert response.data["file_url"] == f"http://testserver{saved.file.url}"
    assert saved.thumbnail.name != saved.file.name
    assert saved.size == len(original)
    assert saved.name == "Final render"
    with saved.file.open("rb") as source:
        assert (
            hashlib.file_digest(source, "sha256").digest()
            == hashlib.sha256(original).digest()
        )
    with saved.thumbnail.open("rb") as source:
        assert source.size <= THUMBNAIL_MAX_BYTES
        with Image.open(source) as preview:
            assert preview.format == "WEBP"
            assert preview.size == (960, 540)
    assert TaskCardSerializer().get_cover_image_url(task) == saved.thumbnail.url


@pytest.mark.parametrize(
    "name,mime_type",
    [
        ("broken.png", "image/png"),
        ("render.mp4", "video/mp4"),
        ("drawing.psd", "image/vnd.adobe.photoshop"),
    ],
)
def test_unpreviewable_files_download_without_original_image_fallback(
    attachment_context, name, mime_type
):
    client, task, _ = attachment_context
    original = b"original bytes preserved"
    response = client.post(
        f"/api/design-workflow/tasks/{task.pk}/attachments/",
        {
            "file": SimpleUploadedFile(name, original, content_type=mime_type),
            "name": name,
        },
        format="multipart",
    )
    assert response.status_code == 201
    assert response.data["thumbnail_url"] is None
    saved = TaskAttachment.objects.get(pk=response.data["id"])
    with saved.file.open("rb") as source:
        assert source.read() == original
    assert TaskCardSerializer().get_cover_image_url(task) is None


def test_pixel_guard_does_not_reject_or_change_original(attachment_context):
    _, task, owner = attachment_context
    saved = attach(task, owner, image_bytes((30, 20)))
    with patch("design_workflow.card_images.MAX_SOURCE_PIXELS", 100):
        create_attachment_thumbnail(saved)
    saved.refresh_from_db()
    assert not saved.thumbnail
    assert saved.file.storage.exists(saved.file.name)


def test_backfill_dry_default_then_idempotent_scoped_and_preserves_files(
    attachment_context, tmp_path
):
    _, task, owner = attachment_context
    original = image_bytes((1200, 600))
    saved = attach(task, owner, original)
    other = attach(task, owner, original, name="second.png")
    old_timestamp = saved.updated_at
    files_before = sorted(tmp_path.rglob("*.png"))
    output = io.StringIO()
    call_command(
        "generate_attachment_thumbnails", attachment_id=saved.pk, stdout=output
    )
    saved.refresh_from_db()
    assert "Dry run; no files changed" in output.getvalue()
    assert not saved.thumbnail
    assert not list(tmp_path.rglob("*.webp"))
    with patch(
        "design_workflow.management.commands.generate_attachment_thumbnails.broadcast_task_event"
    ) as broadcast:
        call_command(
            "generate_attachment_thumbnails",
            attachment_id=saved.pk,
            apply=True,
            stdout=io.StringIO(),
        )
        broadcast.assert_called_once_with(task, "attachment_preview_ready")
        call_command(
            "generate_attachment_thumbnails",
            attachment_id=saved.pk,
            apply=True,
            stdout=io.StringIO(),
        )
        assert broadcast.call_count == 1
    saved.refresh_from_db()
    other.refresh_from_db()
    assert saved.thumbnail and not other.thumbnail
    assert saved.updated_at == old_timestamp
    assert sorted(tmp_path.rglob("*.png")) == files_before
    assert len(list(tmp_path.rglob("*.webp"))) == 1
    with saved.file.open("rb") as source:
        assert source.read() == original
    thumbnail_name = saved.thumbnail.name
    create_attachment_thumbnail(saved)
    assert saved.thumbnail.name == thumbnail_name


def test_backfill_skips_non_images_and_reports_missing_originals(attachment_context):
    _, task, owner = attachment_context
    attach(task, owner, b"video", name="render.mp4", mime_type="video/mp4")
    saved = attach(task, owner, image_bytes())
    saved.file.storage.delete(saved.file.name)
    stdout, stderr = io.StringIO(), io.StringIO()
    call_command(
        "generate_attachment_thumbnails", apply=True, stdout=stdout, stderr=stderr
    )
    assert "Non-images: 1. Unavailable: 1." in stdout.getvalue()
    assert "original unchanged" in stderr.getvalue()


def test_failed_preview_save_removes_only_new_derivative(attachment_context, tmp_path):
    _, task, owner = attachment_context
    original = image_bytes()
    saved = attach(task, owner, original)
    thumbnail = make_card_thumbnail(io.BytesIO(original))
    with (
        patch.object(saved, "save", side_effect=DatabaseError("write failed")),
        pytest.raises(DatabaseError),
    ):
        save_attachment_thumbnail(saved, thumbnail)
    assert not list(tmp_path.rglob("*.webp"))
    with saved.file.open("rb") as source:
        assert source.read() == original


def test_rename_keeps_preview_and_original_paths(attachment_context):
    client, task, owner = attachment_context
    saved = attach(task, owner, image_bytes())
    create_attachment_thumbnail(saved)
    preview_name, original_name = saved.thumbnail.name, saved.file.name
    response = client.patch(
        f"/api/design-workflow/tasks/{task.pk}/attachments/{saved.pk}/",
        {"name": "Approved"},
        format="json",
    )
    assert response.status_code == 200
    saved.refresh_from_db()
    assert saved.thumbnail.name == preview_name
    assert saved.file.name == original_name


def test_thumbnail_cached_but_original_not_marked_immutable(attachment_context):
    client, task, owner = attachment_context
    saved = attach(task, owner, image_bytes())
    create_attachment_thumbnail(saved)
    response = client.get(saved.thumbnail.url)
    assert response.status_code == 200
    assert response["Content-Type"] == "image/webp"
    assert "immutable" in response["Cache-Control"]
    response.close()
    original = client.get(saved.file.url)
    assert "immutable" not in original.get("Cache-Control", "")
    original.close()


def test_image_with_generic_mime_and_renamed_label_keeps_preview_and_cover_action(
    attachment_context,
):
    client, task, owner = attachment_context
    saved = attach(task, owner, image_bytes(), mime_type="application/octet-stream")
    saved.name = "Final render without extension"
    saved.save(update_fields=["name"])
    create_attachment_thumbnail(saved)
    assert saved.thumbnail
    response = client.post(
        f"/api/design-workflow/tasks/{task.pk}/attachments/{saved.pk}/"
    )
    assert response.status_code == 200
    task.refresh_from_db()
    assert task.cover_image
    assert task.cover_image_label == saved.name
