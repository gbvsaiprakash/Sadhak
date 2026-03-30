from datetime import datetime, time, timedelta

from django.db.models import Q
from django.utils import timezone

from tracker.exceptions import raise_tracker_error
from tracker.models import TaskOccurrence
from tracker.services.occurrence import _add_duration, _entity_duration, _generate_custom_period_dates, _generate_dates, _generate_times_for_date


def _normalize_time(t):
    if t is None:
        return None
    return t.replace(second=0, microsecond=0)


def _overlaps(existing_start, existing_end, new_start, new_end):
    # strict overlap; boundary touch is allowed
    return existing_start < new_end and existing_end > new_start


def check_time_conflict(user, instance, scheduled_date, start_time, end_time, exclude_id=None):
    """
    Collision rules (same user, same date):
      - task-task: collision
      - task-habit: collision
      - habit-task: collision
      - habit-habit: allowed
    """
    start_time = _normalize_time(start_time)
    end_time = _normalize_time(end_time)

    if start_time is None or end_time is None:
        return

    # determine which existing occurrences to consider
    if getattr(instance, "is_habit", False):
        qs = TaskOccurrence.objects.filter(task__user=user, scheduled_date=scheduled_date).select_related("task")
        qs = qs.filter(task__is_deleted=False).exclude(task__status__in={"cancelled"})
    else:
        qs = (
            TaskOccurrence.objects.filter(
                Q(task__user=user) | Q(habit__user=user),
                scheduled_date=scheduled_date,
            )
            .select_related("task", "habit")
            .filter(Q(task__isnull=False, task__is_deleted=False) | Q(habit__isnull=False, habit__is_deleted=False))
            .exclude(Q(task__isnull=False, task__status__in={"cancelled"}) | Q(habit__isnull=False, habit__status__in={"stopped"}))
        )

    if exclude_id:
        qs = qs.exclude(id=exclude_id)

    for occ in qs:
        parent = occ.task or occ.habit
        if parent is None:
            continue
        duration = _entity_duration(parent)
        occ_start = _normalize_time(occ.scheduled_time or parent.start_time)
        occ_end = _add_duration(occ_start, duration)
        if _overlaps(occ_start, occ_end, start_time, end_time):
            conflict_type = "task" if occ.task_id else "habit"
            raise_tracker_error(
                "TIME_COLLISION",
                f"This task conflicts with an existing task or habit on {scheduled_date} from {start_time.strftime('%H:%M')} to {end_time.strftime('%H:%M')}.",
                details={
                    "conflict_date": str(scheduled_date),
                    "conflict_with": parent.title,
                    "conflict_type": conflict_type,
                    "conflict_start": occ_start.strftime("%H:%M"),
                    "conflict_end": occ_end.strftime("%H:%M"),
                },
            )


def check_entity_schedule_conflicts(user, entity, from_date=None, to_date=None):
    """
    Checks the occurrences that would be generated for entity (without creating them).
    Returns None if no conflicts; raises TIME_COLLISION otherwise (first conflict only).
    """
    start_date = max(entity.start_date, from_date or entity.start_date)
    if entity.frequency_type == "once":
        end_date = entity.start_date
    elif to_date:
        end_date = to_date
    elif entity.end_date:
        end_date = entity.end_date
    else:
        end_date = timezone.localdate() + timedelta(days=90)

    if end_date < start_date:
        return

    duration = _entity_duration(entity)

    if entity.frequency_type == "custom" and entity.frequency_times_per_period and entity.frequency_period in {"week", "month"}:
        date_iter = _generate_custom_period_dates(entity, start_date, end_date)
    else:
        date_iter = _generate_dates(entity, start_date, end_date)

    for scheduled_date in date_iter:
        for start_t in _generate_times_for_date(entity, scheduled_date):
            end_t = _add_duration(start_t, duration)
            check_time_conflict(user, entity, scheduled_date, start_t, end_t)
