from datetime import date, datetime, time, timedelta

from tracker.constants import ACTIVE_HABIT_STATUSES, ACTIVE_TASK_STATUSES
from tracker.exceptions import raise_tracker_error


def _date_range(entity):
    start = entity.start_date
    if entity.frequency_type == "once":
        return start, start
    return start, entity.end_date or (start + timedelta(days=90))


def _time_anchor(entity):
    return entity.time_of_day or time(hour=0, minute=0)


def _hourly_slots(entity):
    interval = entity.interval_hours or 24
    current = datetime.combine(date.today(), _time_anchor(entity))
    end = datetime.combine(date.today(), time(23, 59))
    slots = set()
    while current <= end:
        slots.add(current.time().replace(second=0, microsecond=0))
        current += timedelta(hours=interval)
    return slots


def _patterns_overlap(left, right):
    if left.frequency_type == "hourly" or right.frequency_type == "hourly":
        if left.frequency_type == right.frequency_type == "hourly":
            return bool(_hourly_slots(left) & _hourly_slots(right))
        hourly = left if left.frequency_type == "hourly" else right
        other = right if hourly is left else left
        return _time_anchor(other) in _hourly_slots(hourly)
    if left.frequency_type == "weekly" and right.frequency_type == "weekly":
        return left.day_of_week == right.day_of_week and _time_anchor(left) == _time_anchor(right)
    if left.frequency_type == "monthly" and right.frequency_type == "monthly":
        return left.day_of_month == right.day_of_month and _time_anchor(left) == _time_anchor(right)
    if left.frequency_type == "once" and right.frequency_type == "once":
        return left.start_date == right.start_date and _time_anchor(left) == _time_anchor(right)
    return _time_anchor(left) == _time_anchor(right)


def _date_windows_overlap(left, right):
    left_start, left_end = _date_range(left)
    right_start, right_end = _date_range(right)
    return left_start <= right_end and right_start <= left_end


def check_time_conflict(user, entity):
    task_qs = user.tracker_tasks.exclude(id=getattr(entity, "id", None)).exclude(status__in={"cancelled", "completed", "skipped"})
    habit_qs = user.tracker_habits.exclude(id=getattr(entity, "id", None)).exclude(status__in={"stopped", "completed"})

    candidates = list(task_qs) + ([] if entity.is_habit is False else [])
    if entity.is_habit:
        candidates = list(task_qs)
    else:
        candidates += list(habit_qs.filter(status__in=ACTIVE_HABIT_STATUSES))

    for candidate in candidates:
        if not _date_windows_overlap(entity, candidate):
            continue
        if not _patterns_overlap(entity, candidate):
            continue
        raise_tracker_error(
            "CONFLICT_TIME_SLOT",
            "A task already exists at this time for the given day pattern.",
            details={"conflicting_id": str(candidate.id), "conflicting_title": candidate.title},
        )
