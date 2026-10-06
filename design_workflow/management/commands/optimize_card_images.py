from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from design_workflow.card_images import (
    is_card_thumbnail,
    make_card_thumbnail,
    save_card_thumbnail,
)
from design_workflow.models import Task
from design_workflow.services import broadcast_task_event, related_task_user_ids


class Command(BaseCommand):
    help = "Convert existing card covers to thumbnails. Dry run unless --apply is passed. Attachments are untouched."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Save thumbnails and permanently remove replaced cover files after commit.",
        )
        parser.add_argument("--task-id", type=int, help="Limit conversion to one task.")

    def handle(self, *args, **options):
        tasks = Task.objects.exclude(cover_image="").exclude(cover_image__isnull=True)
        if options["task_id"] is not None:
            tasks = tasks.filter(pk=options["task_id"])
        converted = failed = saved_bytes = 0
        for pk in tasks.values_list("pk", flat=True).iterator():
            try:
                with transaction.atomic():
                    task = Task.objects.select_for_update().get(pk=pk)
                    if not task.cover_image or is_card_thumbnail(task.cover_image.name):
                        continue
                    with task.cover_image.open("rb") as source:
                        original_size = source.size
                        thumbnail = make_card_thumbnail(source)
                    if options["apply"]:
                        save_card_thumbnail(
                            task, thumbnail, update_fields=["cover_image"]
                        )
                        broadcast_task_event(
                            task,
                            "cover_updated",
                            recipients=related_task_user_ids(task),
                        )
                    converted += 1
                    saved_bytes += original_size - thumbnail.size
                    self.stdout.write(
                        f"Task {pk}: {original_size:,} -> {thumbnail.size:,} bytes"
                    )
            except (ValueError, OSError) as exc:
                failed += 1
                self.stderr.write(f"Task {pk}: unchanged ({exc})")
        mode = "Applied" if options["apply"] else "Dry run; no files changed"
        self.stdout.write(
            f"{mode}. Covers: {converted}. Bytes saved: {saved_bytes:,}. Failed: {failed}."
        )
        if failed:
            raise CommandError(
                "Some covers could not be converted; their original files were kept."
            )
