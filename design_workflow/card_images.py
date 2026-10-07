"""Store one small, immutable card preview, never the uploaded full-size image."""

import io
import re
import warnings
from uuid import uuid4

from django.core.files.base import ContentFile
from django.db import transaction
from PIL import Image, ImageOps, UnidentifiedImageError

THUMBNAIL_MAX_EDGE = 960
THUMBNAIL_MAX_BYTES = 160 * 1024
# Existing designer renders include 7360 x 5520 images (40.6 MP). Keep a
# bounded decode budget while allowing those originals to become thumbnails.
MAX_SOURCE_PIXELS = 50_000_000
THUMBNAIL_PATH = r"design_workflow/task_covers/\d{4}/\d{2}/thumb_[0-9a-f]{32}\.webp"


def is_card_thumbnail(name):
    return re.fullmatch(THUMBNAIL_PATH, name) is not None


def make_card_thumbnail(source):
    """Validate and reduce frame one; preserve aspect ratio, orientation and alpha."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            source.seek(0)
            with Image.open(source) as original:
                if original.width * original.height > MAX_SOURCE_PIXELS:
                    raise ValueError("This image has too many pixels. Reduce it first.")
                original.thumbnail(
                    (THUMBNAIL_MAX_EDGE, THUMBNAIL_MAX_EDGE), Image.Resampling.LANCZOS
                )
                image = ImageOps.exif_transpose(original)
                image = image.convert(
                    "RGBA"
                    if "A" in image.getbands() or "transparency" in image.info
                    else "RGB"
                )
                image.info.clear()
                while True:
                    for quality in (80, 68, 56):
                        output = io.BytesIO()
                        image.save(output, "WEBP", quality=quality, method=4)
                        if output.tell() <= THUMBNAIL_MAX_BYTES:
                            return ContentFile(
                                output.getvalue(), name=f"thumb_{uuid4().hex}.webp"
                            )
                    image.thumbnail(
                        (max(1, image.width * 3 // 4), max(1, image.height * 3 // 4)),
                        Image.Resampling.LANCZOS,
                    )
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValueError(
            "Choose a valid, supported image with no more than 50 million pixels."
        ) from exc


def delete_replaced_cover_after_commit(storage, name):
    """Do not destroy a cover until its database replacement/removal is committed."""
    if not name:
        return

    def remove_unused_cover():
        from .models import Task, TaskAttachment

        # Restrict deletion to card covers, including legacy copies/shared paths.
        if not name.startswith("design_workflow/task_covers/"):
            return
        if (
            Task.objects.filter(cover_image=name).exists()
            or TaskAttachment.objects.filter(file=name).exists()
        ):
            return
        storage.delete(name)

    transaction.on_commit(remove_unused_cover, robust=True)


def save_card_thumbnail(task, thumbnail, *, update_fields):
    previous_name = task.cover_image.name
    storage = task.cover_image.storage
    task.cover_image.save(thumbnail.name, thumbnail, save=False)
    try:
        task.save(update_fields=update_fields)
    except Exception:
        # A failed save must not leave a new orphan file or delete the old cover.
        storage.delete(task.cover_image.name)
        raise
    delete_replaced_cover_after_commit(storage, previous_name)
