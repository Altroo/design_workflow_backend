from datetime import UTC, date, datetime, time, timedelta

import pytest
from django.utils import timezone

from account.filters import UsersFilter
from account.models import CustomUser

pytestmark = pytest.mark.django_db

CALENDAR_DAYS = [
    ["UTC", "2026-10-21", 24],
    ["Africa/Casablanca", "2026-10-21", 24],
    ["Europe/Paris", "2026-03-29", 23],
    ["Europe/Paris", "2026-10-25", 25],
    ["Africa/Casablanca", "2026-02-15", 25],
    ["Africa/Casablanca", "2026-03-22", 23],
]


@pytest.mark.parametrize("zone,selected_day,hours", CALENDAR_DAYS)
@pytest.mark.parametrize("field", ["date_joined", "last_login"])
@pytest.mark.parametrize("bounds", ["after", "before", "range"])
def test_user_calendar_bounds(zone, selected_day, hours, field, bounds):
    with timezone.override(zone):
        day = date.fromisoformat(selected_day)
        start = timezone.make_aware(datetime.combine(day, time.min))
        end = timezone.make_aware(datetime.combine(day + timedelta(days=1), time.min))
        assert end.astimezone(UTC) - start.astimezone(UTC) == timedelta(hours=hours)
        moments = [
            start.astimezone(UTC) - timedelta(microseconds=1),
            start,
            timezone.make_aware(datetime.combine(day, time(12))),
            end.astimezone(UTC) - timedelta(microseconds=1),
            end,
        ]
        records = CustomUser.objects.bulk_create(
            [
                CustomUser(email=f"boundary-{index}@example.test", **{field: moment})
                for index, moment in enumerate(moments)
            ]
        )
        queryset = CustomUser.objects.filter(pk__in=[record.pk for record in records])
        params = {}
        if bounds in ("after", "range"):
            params[f"{field}_after"] = selected_day
        if bounds in ("before", "range"):
            params[f"{field}_before"] = selected_day
        filters = UsersFilter(params, queryset=queryset)
        assert filters.is_valid(), filters.errors
        included = {"after": {1, 2, 3, 4}, "before": {0, 1, 2, 3}, "range": {1, 2, 3}}[
            bounds
        ]
        assert set(filters.qs.values_list("pk", flat=True)) == {
            records[index].pk for index in included
        }


@pytest.mark.parametrize("field", ["date_joined", "last_login"])
def test_empty_calendar_bounds_preserve_unfiltered_records(field):
    record = CustomUser.objects.create(email="unfiltered@example.test", last_login=None)
    queryset = CustomUser.objects.filter(pk=record.pk)
    filters = UsersFilter(
        {f"{field}_after": "", f"{field}_before": ""}, queryset=queryset
    )
    assert filters.is_valid(), filters.errors
    assert list(filters.qs) == [record]


@pytest.mark.parametrize("field", ["date_joined", "last_login"])
def test_invalid_calendar_bound_is_rejected(field):
    filters = UsersFilter(
        {f"{field}_before": "invalid"}, queryset=CustomUser.objects.all()
    )
    assert not filters.is_valid()
    assert f"{field}_before" in filters.errors
