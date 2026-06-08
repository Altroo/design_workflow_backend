import django_filters
from django.db.models import Q
from django.utils import timezone

from .models import Task, TaskStatus


def _parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    if value.lower() in {"true", "1", "yes"}:
        return True
    if value.lower() in {"false", "0", "no"}:
        return False
    return None


class TaskFilter(django_filters.FilterSet):
    q = django_filters.CharFilter(method="filter_search")
    archived = django_filters.CharFilter(method="filter_archived")
    project = django_filters.CharFilter(field_name="project_id", lookup_expr="exact")
    assignee = django_filters.CharFilter(field_name="current_assignee_id", lookup_expr="exact")
    status = django_filters.CharFilter(field_name="status", lookup_expr="exact")
    priority = django_filters.CharFilter(field_name="priority", lookup_expr="exact")
    review_state = django_filters.CharFilter(field_name="review_state", lookup_expr="exact")
    label = django_filters.CharFilter(method="filter_label")
    overdue = django_filters.CharFilter(method="filter_overdue")
    blocked = django_filters.CharFilter(method="filter_blocked")
    mine = django_filters.CharFilter(method="filter_mine")
    start_date = django_filters.CharFilter(field_name="project__start_date", lookup_expr="gte")
    end_date = django_filters.CharFilter(field_name="project__target_end_date", lookup_expr="lte")

    class Meta:
        model = Task
        fields = (
            "q",
            "archived",
            "project",
            "assignee",
            "status",
            "priority",
            "review_state",
            "label",
            "overdue",
            "blocked",
            "mine",
            "start_date",
            "end_date",
        )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def filter_queryset(self, queryset):
        if _parse_bool(self.data.get("archived")) is None:
            queryset = queryset.filter(archived=False)
        return super().filter_queryset(queryset)

    def filter_archived(self, queryset, name, value):
        archived = _parse_bool(value)
        return queryset.filter(archived=False) if archived is None else queryset.filter(archived=archived)

    def filter_search(self, queryset, name, value):
        query = (value or "").strip()
        if not query:
            return queryset
        return queryset.filter(
            Q(title__icontains=query)
            | Q(description__icontains=query)
            | Q(project__name__icontains=query)
            | Q(current_assignee__first_name__icontains=query)
            | Q(current_assignee__last_name__icontains=query)
            | Q(current_assignee__email__icontains=query)
            | Q(labels__name__icontains=query)
        ).distinct()

    def filter_label(self, queryset, name, value):
        return queryset.filter(labels__id=value)

    def filter_overdue(self, queryset, name, value):
        if _parse_bool(value) is True:
            return queryset.filter(due_date__lt=timezone.localdate()).exclude(status=TaskStatus.DONE)
        return queryset

    def filter_blocked(self, queryset, name, value):
        if _parse_bool(value) is True:
            return queryset.filter(status=TaskStatus.BLOCKED)
        return queryset

    def filter_mine(self, queryset, name, value):
        if _parse_bool(value) is True and self.user is not None:
            return queryset.filter(current_assignee=self.user)
        return queryset
