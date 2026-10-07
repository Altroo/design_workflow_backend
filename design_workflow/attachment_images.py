"""Small attachment previews. Original files are never rewritten or removed."""

import logging
import re

from .card_images import make_card_thumbnail

logger = logging.getLogger(__name__)

ATTACHMENT_THUMBNAIL_PATH = (
    r"design_workflow/attachment_thumbnails/\d{4}/\d{2}/thumb_[0-9a-f]{32}\.webp"
)
IMAGE_EXTENSION = re.compile(r"\.(avif|bmp|gif|jpe?g|png|tiff?|webp)$", re.IGNORECASE)


def make_attachment_thumbnail(attachment):
    if not attachment.mime_type.startswith("image/") and not IMAGE_EXTENSION.search(
        attachment.file.name
    ):
        return None
    with attachment.file.open("rb") as source:
        return make_card_thumbnail(source)


def save_attachment_thumbnail(attachment, thumbnail):
    """Save only the derived field; don't change the file, label or timestamps."""
    attachment.thumbnail.save(thumbnail.name, thumbnail, save=False)
    try:
        attachment.save(update_fields=["thumbnail"])
    except Exception:
        attachment.thumbnail.storage.delete(attachment.thumbnail.name)
        raise


def create_attachment_thumbnail(attachment):
    # A valid original remains downloadable even when its image format cannot
    # be previewed. Never fall back to fetching the full original in a list.
    if attachment.thumbnail:
        return
    try:
        thumbnail = make_attachment_thumbnail(attachment)
        if thumbnail is not None:
            save_attachment_thumbnail(attachment, thumbnail)
    except (ValueError, OSError) as exc:
        attachment.thumbnail = ""
        logger.warning("Attachment %s preview unavailable: %s", attachment.pk, exc)
