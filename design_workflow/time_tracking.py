"""Per-person work sessions for shared cards, using the studio work schedule."""

from datetime import date, datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from .models import Task, TaskActivityType, TaskStatus, TaskWorkSession, TimeEntry


def _daily_worked_minutes(started_at, ended_at):
    # Use the same calendar as the existing task timer, but attribute each
    # day's work to that date rather than to the day the card leaves En cours.
    from .services import count_working_minutes

    local_start = timezone.localtime(started_at)
    local_end = timezone.localtime(ended_at)
    work_date = local_start.date()
    while work_date <= local_end.date():
        day_start = datetime.combine(work_date, time.min, tzinfo=local_start.tzinfo)
        day_end = datetime.combine(work_date + timedelta(days=1), time.min, tzinfo=local_start.tzinfo)
        minutes = count_working_minutes(max(local_start, day_start), min(local_end, day_end))
        if minutes:
            yield work_date, minutes
        work_date += timedelta(days=1)


def active_session_minutes(*, task_ids=None, user_id=None, start_date=None, end_date=None, now=None):
    """Return non-persisted person-minutes keyed by (task, user, work date).

    One time snapshot keeps a response internally consistent. Callers combine
    this with already-read logged totals; no GET checkpoints a timer or loses
    fractional minutes by rewriting its start time.
    """
    now = now or timezone.now()
    if isinstance(start_date, str):
        start_date = date.fromisoformat(start_date)
    if isinstance(end_date, str):
        end_date = date.fromisoformat(end_date)
    local_now = timezone.localtime(now)
    start_bound = datetime.combine(start_date, time.min, tzinfo=local_now.tzinfo) if start_date else None
    end_bound = min(local_now, datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=local_now.tzinfo)) if end_date else local_now
    sessions = TaskWorkSession.objects.filter(
        task__status=TaskStatus.IN_PROGRESS, task__archived=False,
        task__project__archived=False, user__is_active=True,
    )
    if task_ids is not None:
        sessions = sessions.filter(task_id__in=task_ids)
    if user_id is not None:
        sessions = sessions.filter(user_id=user_id)
    minutes = {}
    for task_id, worker_id, started_at in sessions.values_list("task_id", "user_id", "started_at"):
        started_at = max(started_at, start_bound) if start_bound else started_at
        for work_date, amount in _daily_worked_minutes(started_at, end_bound):
            key = (task_id, worker_id, work_date)
            minutes[key] = minutes.get(key, 0) + amount
    return minutes


def reconcile_task_work_sessions(task, *, event="workflow", now=None):
    """Reconcile current membership without retroactively changing attribution.

    The task row serializes all timer mutations. A worker who stays on a card
    keeps their start time; a new worker starts now, and a departing worker's
    completed time is preserved. Existing legacy task timers are deliberately
    not multiplied into historical collaboration time.
    """
    from .services import broadcast_task_event, record_task_activity

    now = now or timezone.now()
    entries = []
    with transaction.atomic():
        current = Task.objects.select_for_update(of=("self",)).select_related("project").get(pk=task.pk)
        participant_ids = set()
        if current.status == TaskStatus.IN_PROGRESS and not current.archived and not current.project.archived:
            candidate_ids = {current.project.manager_id, current.current_assignee_id}
            candidate_ids.update(current.project.collaborators.values_list("pk", flat=True))
            participant_ids = set(
                get_user_model().objects.filter(pk__in=candidate_ids, is_active=True).values_list("pk", flat=True)
            )

        sessions = list(current.work_sessions.select_related("user"))
        continuing = [session for session in sessions if session.user_id in participant_ids]
        closed = [session for session in sessions if session.user_id not in participant_ids]
        for session in closed:
            for work_date, minutes in _daily_worked_minutes(session.started_at, now):
                entry = TimeEntry.objects.create(
                    task=current,
                    user=session.user,
                    minutes=minutes,
                    work_date=work_date,
                    note="Automatic shared workflow entry based on scheduled studio hours.",
                )
                entries.append(entry)
                record_task_activity(current, session.user, TaskActivityType.TIME_LOGGED, {
                    "time_entry_id": entry.pk,
                    "minutes": minutes,
                    "event": event,
                    "work_session_id": session.pk,
                })
        if closed:
            TaskWorkSession.objects.filter(pk__in=[session.pk for session in closed]).delete()

        new_ids = participant_ids - {session.user_id for session in continuing}
        created = TaskWorkSession.objects.bulk_create([
            TaskWorkSession(task=current, user_id=user_id, started_at=now)
            for user_id in sorted(new_ids)
        ])
        work_started_at = min((session.started_at for session in [*continuing, *created]), default=None)
        if current.work_started_at != work_started_at:
            Task.objects.filter(pk=current.pk).update(work_started_at=work_started_at)
            current.work_started_at = work_started_at
        if entries:
            current.recalculate_actual_minutes()
        task.work_started_at = current.work_started_at
        task.actual_minutes = current.actual_minutes

        if closed or created:
            event_type = "time_logged" if entries else "time_tracking_changed"
            transaction.on_commit(lambda: broadcast_task_event(current, event_type))
    return entries
