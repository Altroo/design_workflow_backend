from django.contrib.auth import get_user_model
from django.db.models import Q
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver


@receiver(pre_save, sender=get_user_model())
def track_active_change(sender, instance, raw=False, update_fields=None, **kwargs):
    if (
        raw
        or not instance.pk
        or (update_fields is not None and "is_active" not in update_fields)
    ):
        instance._workflow_active_changed = False
        return
    previous = (
        sender.objects.filter(pk=instance.pk)
        .values_list("is_active", flat=True)
        .first()
    )
    instance._workflow_active_changed = (
        previous is not None and previous != instance.is_active
    )


@receiver(post_save, sender=get_user_model())
@receiver(post_delete, sender=get_user_model())
def publish_user_change(sender, instance, raw=False, update_fields=None, **kwargs):
    if raw or (update_fields and set(update_fields) <= {"last_login", "password"}):
        return
    from design_workflow.services import broadcast_workflow_event

    broadcast_workflow_event("users")
    if getattr(instance, "_workflow_active_changed", False):
        from design_workflow.models import Task
        from design_workflow.time_tracking import reconcile_task_work_sessions

        for task in (
            Task.objects.filter(
                Q(work_sessions__user_id=instance.pk)
                | Q(current_assignee_id=instance.pk)
                | Q(project__manager_id=instance.pk)
                | Q(project__collaborators__id=instance.pk)
            )
            .distinct()
            .order_by("pk")
        ):
            reconcile_task_work_sessions(task, event="member_deactivated")
