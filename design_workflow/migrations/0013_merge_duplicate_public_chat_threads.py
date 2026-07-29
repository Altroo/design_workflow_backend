from django.db import migrations, models
from django.db.models import Count, Q


def merge_duplicate_public_chat_threads(apps, schema_editor):
    ChatMessage = apps.get_model("design_workflow", "ChatMessage")
    ChatThread = apps.get_model("design_workflow", "ChatThread")

    public_threads = list(
        ChatThread.objects.filter(kind="public")
        .annotate(message_count=Count("messages", distinct=True))
        .order_by("-message_count", "-updated_at", "id")
    )
    if len(public_threads) < 2:
        return

    canonical = public_threads[0]
    for duplicate in public_threads[1:]:
        canonical.participants.add(
            *duplicate.participants.values_list("id", flat=True)
        )
        ChatMessage.objects.filter(thread_id=duplicate.id).update(
            thread_id=canonical.id
        )
        duplicate.delete()


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("design_workflow", "0012_historicalattachmentannotation_historicalchatmessage_and_more"),
    ]

    operations = [
        migrations.RunPython(
            merge_duplicate_public_chat_threads,
            migrations.RunPython.noop,
            atomic=True,
        ),
        migrations.AddConstraint(
            model_name="chatthread",
            constraint=models.UniqueConstraint(
                condition=Q(kind="public"),
                fields=("kind",),
                name="unique_design_public_chat_thread",
            ),
        ),
    ]
