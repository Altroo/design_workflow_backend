import io
from unittest.mock import patch

import pytest
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from PIL import Image
from rest_framework.test import APIClient

from . import card_images
from .card_images import (
    THUMBNAIL_MAX_BYTES,
    is_card_thumbnail,
    make_card_thumbnail,
    save_card_thumbnail,
)
from .models import Project, Task, TaskAttachment
from .tests import make_designer


def image_bytes(size=(2400, 1200), mode="RGB", format_="PNG", **save_options):
    image = Image.new(mode, size, (20, 140, 200, 100) if mode == "RGBA" else 100)
    output = io.BytesIO()
    image.save(output, format_, **save_options)
    return output.getvalue()


@pytest.fixture(name="cover_context")
def cover_context_fixture(settings, tmp_path, db):
    settings.MEDIA_ROOT = tmp_path
    owner = make_designer("thumbnail-owner@example.test")
    project = Project.objects.create(name="Thumbnail project", manager=owner)
    task = Task.objects.create(
        project=project, title="Card", created_by=owner, updated_by=owner
    )
    client = APIClient()
    client.force_authenticate(owner)
    return client, task, owner


@pytest.mark.parametrize(
    "mode,format_",
    [("RGB", "JPEG"), ("RGBA", "PNG"), ("P", "GIF"), ("L", "PNG"), ("CMYK", "JPEG")],
)
def test_thumbnail_dimensions_format_size_and_metadata(mode, format_):
    source = io.BytesIO(image_bytes(mode=mode, format_=format_))
    result = make_card_thumbnail(source)
    assert result.size <= THUMBNAIL_MAX_BYTES
    assert result.name.startswith("thumb_") and result.name.endswith(".webp")
    with Image.open(result) as image:
        assert image.format == "WEBP"
        assert image.size == (960, 480)
        assert not image.getexif()
        assert "icc_profile" not in image.info
        if mode == "RGBA":
            assert image.getpixel((0, 0))[3] == 100


def test_orientation_is_applied_without_upscaling_small_images():
    exif = Image.Exif()
    exif[274] = 6
    result = make_card_thumbnail(
        io.BytesIO(image_bytes((120, 60), format_="JPEG", exif=exif))
    )
    with Image.open(result) as image:
        assert image.size == (60, 120)
        assert not image.getexif()


@pytest.mark.parametrize(
    "size,expected", [((7360, 5520), (960, 720)), ((7680, 7680), (960, 960))]
)
def test_existing_large_designer_render_can_be_converted(size, expected):
    source = io.BytesIO(image_bytes(size, mode="RGBA"))
    thumbnail = make_card_thumbnail(source)
    assert thumbnail.size <= THUMBNAIL_MAX_BYTES
    with Image.open(thumbnail) as image:
        assert image.format == "WEBP"
        assert image.size == expected
        assert "A" in image.getbands()


def test_animated_image_stores_only_first_frame():
    output = io.BytesIO()
    Image.new("RGB", (40, 20), "red").save(
        output, "GIF", save_all=True, append_images=[Image.new("RGB", (40, 20), "blue")]
    )
    result = make_card_thumbnail(output)
    with Image.open(result) as image:
        assert not getattr(image, "is_animated", False)
        assert image.getpixel((0, 0))[0] > 240


def test_complex_image_respects_strict_byte_budget(monkeypatch):
    output = io.BytesIO()
    Image.effect_noise((1600, 1000), 100).convert("RGB").save(output, "PNG")
    monkeypatch.setattr(card_images, "THUMBNAIL_MAX_BYTES", 10 * 1024)
    result = make_card_thumbnail(output)
    assert result.size <= 10 * 1024
    with Image.open(result) as image:
        assert max(image.size) < 960


def test_invalid_and_unsafe_images_are_rejected(monkeypatch):
    for raw in (
        b"not an image",
        b'<svg xmlns="http://www.w3.org/2000/svg"/>',
        b"GIF89a",
    ):
        with pytest.raises(ValueError):
            make_card_thumbnail(io.BytesIO(raw))
    monkeypatch.setattr(card_images, "MAX_SOURCE_PIXELS", 100)
    with pytest.raises(ValueError, match="too many pixels"):
        make_card_thumbnail(io.BytesIO(image_bytes((11, 10))))


def upload_cover(client, task, content=None):
    return client.post(
        f"/api/design-workflow/tasks/{task.pk}/cover/",
        {
            "cover_image": SimpleUploadedFile(
                "original.png",
                content if content is not None else image_bytes(),
                content_type="image/png",
            ),
            "name": "Preview",
        },
        format="multipart",
    )


def test_upload_only_stores_thumbnail_and_replacement_deletes_old_after_commit(
    cover_context, django_capture_on_commit_callbacks, tmp_path
):
    client, task, _ = cover_context
    with django_capture_on_commit_callbacks(execute=True):
        response = upload_cover(client, task)
    assert response.status_code == 200
    task.refresh_from_db()
    assert is_card_thumbnail(task.cover_image.name)
    first_name = task.cover_image.name
    assert list(tmp_path.rglob("*original*")) == []
    assert response.data["cover_image_url"].endswith(task.cover_image.name)
    assert task.cover_image.size <= THUMBNAIL_MAX_BYTES
    with django_capture_on_commit_callbacks(execute=True):
        second = upload_cover(client, task)
        assert task.cover_image.storage.exists(first_name)
    task.refresh_from_db()
    assert second.status_code == 200
    assert task.cover_image.name != first_name
    assert not task.cover_image.storage.exists(first_name)
    assert len([path for path in tmp_path.rglob("*") if path.is_file()]) == 1
    name = task.cover_image.name
    with django_capture_on_commit_callbacks(execute=True):
        deleted = client.delete(f"/api/design-workflow/tasks/{task.pk}/cover/")
    assert deleted.status_code == 200
    assert not task.cover_image.storage.exists(name)


def test_invalid_upload_keeps_existing_image(cover_context):
    client, task, _ = cover_context
    assert upload_cover(client, task).status_code == 200
    task.refresh_from_db()
    name = task.cover_image.name
    assert upload_cover(client, task, b"invalid").status_code == 400
    task.refresh_from_db()
    assert task.cover_image.name == name
    assert task.cover_image.storage.exists(name)


def test_attachment_as_cover_keeps_original_attachment_bytes(
    cover_context, django_capture_on_commit_callbacks
):
    client, task, owner = cover_context
    original = image_bytes()
    attachment = TaskAttachment.objects.create(
        task=task,
        uploaded_by=owner,
        name="Original plan.png",
        file=ContentFile(original, name="plan.png"),
        size=len(original),
        mime_type="image/png",
    )
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            f"/api/design-workflow/tasks/{task.pk}/attachments/{attachment.pk}/"
        )
    assert response.status_code == 200
    task.refresh_from_db()
    assert is_card_thumbnail(task.cover_image.name)
    assert task.cover_image.name != attachment.file.name
    with attachment.file.open("rb") as source:
        assert source.read() == original


def test_failed_save_and_transaction_rollback_preserve_previous_file(
    cover_context, django_capture_on_commit_callbacks
):
    _, task, _ = cover_context
    task.cover_image.save("old.png", ContentFile(image_bytes()))
    previous = task.cover_image.name
    with patch.object(task, "save", side_effect=RuntimeError("DB error")):
        with pytest.raises(RuntimeError):
            save_card_thumbnail(
                task,
                make_card_thumbnail(io.BytesIO(image_bytes())),
                update_fields=["cover_image"],
            )
    assert task.cover_image.storage.exists(previous)
    task.refresh_from_db()
    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(RuntimeError):
            with transaction.atomic():
                save_card_thumbnail(
                    task,
                    make_card_thumbnail(io.BytesIO(image_bytes())),
                    update_fields=["cover_image"],
                )
                raise RuntimeError("Rollback")
    task.refresh_from_db()
    assert task.cover_image.name == previous
    assert task.cover_image.storage.exists(previous)


def test_thumbnail_cache_headers_and_conditional_get(cover_context):
    client, task, _ = cover_context
    assert upload_cover(client, task).status_code == 200
    task.refresh_from_db()
    response = client.get(task.cover_image.url)
    assert response.status_code == 200
    assert response["Content-Type"] == "image/webp"
    assert "private" in response["Cache-Control"]
    assert "max-age=31536000" in response["Cache-Control"]
    assert "immutable" in response["Cache-Control"]
    assert int(response["Content-Length"]) <= THUMBNAIL_MAX_BYTES
    modified = response["Last-Modified"]
    response.close()
    cached = client.get(task.cover_image.url, HTTP_IF_MODIFIED_SINCE=modified)
    assert cached.status_code == 304
    assert "immutable" in cached["Cache-Control"]


def test_existing_cover_conversion_is_dry_run_first_idempotent_and_preserves_shared_files(
    cover_context, django_capture_on_commit_callbacks
):
    _, task, owner = cover_context
    task.cover_image.save("old.png", ContentFile(image_bytes()))
    previous = task.cover_image.name
    original_updated = task.updated_at
    shared = Task.objects.create(
        project=task.project,
        title="Shared",
        created_by=owner,
        updated_by=owner,
        cover_image=previous,
    )
    output = io.StringIO()
    call_command("optimize_card_images", task_id=task.pk, stdout=output)
    task.refresh_from_db()
    assert task.cover_image.name == previous
    assert "Dry run" in output.getvalue()
    with django_capture_on_commit_callbacks(execute=True):
        call_command(
            "optimize_card_images", task_id=task.pk, apply=True, stdout=io.StringIO()
        )
    task.refresh_from_db()
    assert is_card_thumbnail(task.cover_image.name)
    assert task.updated_at == original_updated
    assert task.cover_image.storage.exists(previous)
    name = task.cover_image.name
    with django_capture_on_commit_callbacks(execute=True):
        call_command("optimize_card_images", apply=True, stdout=io.StringIO())
    task.refresh_from_db()
    shared.refresh_from_db()
    assert task.cover_image.name == name
    assert is_card_thumbnail(shared.cover_image.name)
    assert not task.cover_image.storage.exists(previous)


def test_command_preserves_unreadable_originals(cover_context):
    _, task, _ = cover_context
    task.cover_image.save("invalid.png", ContentFile(b"bad image"))
    name = task.cover_image.name
    with pytest.raises(CommandError):
        call_command(
            "optimize_card_images",
            apply=True,
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )
    task.refresh_from_db()
    assert task.cover_image.name == name
    assert task.cover_image.storage.exists(name)
