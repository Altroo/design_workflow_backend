"""Keep back-office edits consistent with the workflow and its live clients."""

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from simple_history.admin import SimpleHistoryAdmin

from .models import (
    ChatThreadKind,
    Project,
    ProjectStatus,
    Task,
    TaskActivityType,
    TimeEntry,
)
from .services import broadcast_workflow_event, record_task_activity
from .time_tracking import reconcile_task_work_sessions


def _lock_tasks(task_ids):
    return list(
        Task.objects.select_for_update(of=("self",))
        .filter(
            pk__in=task_ids,
        )
        .order_by("pk")
    )


def _publish_admin_change():
    # No model names, IDs, private messages or field values leave the admin.
    # Permission-checked queries supply the new data after the transaction commits.
    transaction.on_commit(lambda: broadcast_workflow_event("admin"), robust=True)


def _sync_project_threads(project_ids):
    # Reuse normal API membership rules, including managers and task assignees.
    from .views import get_chat_thread_base_queryset, sync_linked_thread_participants

    threads = get_chat_thread_base_queryset().filter(
        Q(kind=ChatThreadKind.PROJECT, project_id__in=project_ids)
        | Q(kind=ChatThreadKind.TASK, task__project_id__in=project_ids)
    )
    for thread in threads:
        sync_linked_thread_participants(thread)


class WorkflowRealtimeAdmin(SimpleHistoryAdmin):
    """Admin save/delete hooks, not model signals that duplicate normal API events."""

    def save_model(self, request, obj, form, change):
        # Django wraps changeform_view in an outer atomic transaction, keeping
        # these locks through save_related and its post-M2M reconciliation.
        with transaction.atomic():
            if isinstance(obj, Project):
                previous = (
                    Project.objects.select_for_update().filter(pk=obj.pk).first()
                    if change
                    else None
                )
                form._workflow_was_archived = previous.archived if previous else False
                _lock_tasks(Task.objects.filter(project_id=obj.pk).values("pk"))
            elif isinstance(obj, Task):
                project = Project.objects.select_for_update().get(pk=obj.project_id)
                previous = _lock_tasks([obj.pk]) if change else []
                form._workflow_old_project_id = (
                    previous[0].project_id if previous else obj.project_id
                )
                # Tasks cannot be restored/created active inside an archived project.
                if project.archived:
                    obj.archived = True
                if obj.archived:
                    obj.archived_at = obj.archived_at or timezone.now()
                else:
                    obj.archived_at = None
            elif isinstance(obj, TimeEntry):
                old_task_id = (
                    TimeEntry.objects.filter(pk=obj.pk)
                    .values_list("task_id", flat=True)
                    .first()
                    if change
                    else None
                )
                form._workflow_time_task_ids = {obj.task_id, old_task_id} - {None}
                _lock_tasks(form._workflow_time_task_ids)
            super().save_model(request, obj, form, change)
            if getattr(request, "_workflow_history_revert", False):
                # SimpleHistoryAdmin bypasses save_related on its revert route.
                save_m2m = form.save_m2m

                def save_history_relations():
                    save_m2m()
                    self._reconcile_saved_object(request, form)

                form.save_m2m = save_history_relations

    def history_form_view(self, request, object_id, version_id, extra_context=None):
        with transaction.atomic():
            request._workflow_history_revert = True
            try:
                return super().history_form_view(
                    request, object_id, version_id, extra_context
                )
            finally:
                del request._workflow_history_revert

    def save_related(self, request, form, formsets, change):
        with transaction.atomic():
            super().save_related(request, form, formsets, change)
            self._reconcile_saved_object(request, form)

    def _reconcile_saved_object(self, request, form):
        obj = form.instance
        if isinstance(obj, Project):
            tasks = _lock_tasks(Task.objects.filter(project=obj).values("pk"))
            was_archived = getattr(form, "_workflow_was_archived", False)
            if obj.archived:
                archived_at = obj.archived_at or timezone.now()
                obj.archived_at = archived_at
                obj.status = ProjectStatus.ARCHIVED
                obj.save(update_fields=["archived_at", "status", "updated_at"])
                for task in tasks:
                    if task.archived:
                        continue
                    task.archived = True
                    task.archived_at = archived_at
                    task.updated_by = request.user
                    task.save(
                        update_fields=[
                            "archived",
                            "archived_at",
                            "updated_by",
                            "updated_at",
                        ]
                    )
                    record_task_activity(
                        task,
                        request.user,
                        TaskActivityType.PROJECT_ARCHIVED,
                        {
                            "archived": True,
                            "project_id": obj.pk,
                        },
                    )
            elif was_archived:
                # Match the API: restoring a project never restores its cards.
                obj.archived_at = None
                if obj.status == ProjectStatus.ARCHIVED:
                    obj.status = ProjectStatus.PLANNED
                obj.save(update_fields=["archived_at", "status", "updated_at"])
            for task in tasks:
                reconcile_task_work_sessions(task, event="admin_project_updated")
            _sync_project_threads([obj.pk])
        elif isinstance(obj, Task):
            # A history revert must not restore an obsolete cached total.
            obj.recalculate_actual_minutes()
            reconcile_task_work_sessions(obj, event="admin_task_updated")
            _sync_project_threads(
                {
                    obj.project_id,
                    getattr(form, "_workflow_old_project_id", obj.project_id),
                }
            )
        elif isinstance(obj, TimeEntry):
            for task in _lock_tasks(
                getattr(form, "_workflow_time_task_ids", {obj.task_id})
            ):
                task.recalculate_actual_minutes()
        _publish_admin_change()

    def delete_model(self, request, obj):
        with transaction.atomic():
            tasks = _lock_tasks([obj.task_id]) if isinstance(obj, TimeEntry) else []
            super().delete_model(request, obj)
            for task in tasks:
                task.recalculate_actual_minutes()
            _publish_admin_change()

    def delete_queryset(self, request, queryset):
        # QuerySet.delete bypasses TimeEntry.delete(), so its cached task totals
        # need explicit correction. Lock all affected tasks in the same order.
        with transaction.atomic():
            tasks = (
                _lock_tasks(queryset.values_list("task_id", flat=True))
                if queryset.model is TimeEntry
                else []
            )
            super().delete_queryset(request, queryset)
            for task in tasks:
                task.recalculate_actual_minutes()
            _publish_admin_change()
