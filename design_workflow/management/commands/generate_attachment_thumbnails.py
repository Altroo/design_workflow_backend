from django.core.management.base import BaseCommand
from django.db import transaction

from design_workflow.attachment_images import (
    make_attachment_thumbnail,
    save_attachment_thumbnail,
)
from design_workflow.models import TaskAttachment
from design_workflow.services import broadcast_task_event


class Command(BaseCommand):
    help = "Generate attachment previews without changing originals. Dry run unless --apply."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--attachment-id", type=int)

    def handle(self, *args, **options):
        attachments = TaskAttachment.objects.filter(thumbnail="")
        if options["attachment_id"] is not None:
            attachments = attachments.filter(pk=options["attachment_id"])
        generated = skipped = failed = 0
        for pk in attachments.values_list("pk", flat=True).iterator():
            try:
                with transaction.atomic():
                    attachment = (
                        TaskAttachment.objects.select_for_update().filter(pk=pk).first()
                    )
                    if attachment is None or attachment.thumbnail:
                        continue
                    thumbnail = make_attachment_thumbnail(attachment)
                    if thumbnail is None:
                        skipped += 1
                        continue
                    if options["apply"]:
                        save_attachment_thumbnail(attachment, thumbnail)
                        broadcast_task_event(
                            attachment.task, "attachment_preview_ready"
                        )
                    generated += 1
                    self.stdout.write(
                        f"Attachment {pk}: preview {thumbnail.size:,} bytes; original unchanged"
                    )
            except (ValueError, OSError) as exc:
                failed += 1
                self.stderr.write(
                    f"Attachment {pk}: preview unavailable ({exc}); original unchanged"
                )
        mode = "Applied" if options["apply"] else "Dry run; no files changed"
        self.stdout.write(
            f"{mode}. Previews: {generated}. Non-images: {skipped}. Unavailable: {failed}."
        )
