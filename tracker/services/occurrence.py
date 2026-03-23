from calendar import monthrange
from datetime import datetime, time, timedelta

from django.db import transaction
from django.utils import timezone

from tracker.constants import RESOLVED_OCCURRENCE_STATUSES
from tracker.models import TaskOccurrence


def _occurrence_model_fields(entity):
    if entity.is_habit:
        return {"habit": entity}
    return {"task": entity}


def _generate_dates(entity, start_date, end_date):
    current = start_date
    if entity.frequency_type == "once":
        return [entity.start_date]
    if entity.frequency_type == "daily":
        while current <= end_date:
            yield current
            current += timedelta(days=1)
        return
    if entity.frequency_type == "weekly":
        while current <= end_date:
            if current.weekday() == entity.day_of_week:
                yield current
            current += timedelta(days=1)
        return
    if entity.frequency_type == "monthly":
        while current <= end_date:
            if current.day == min(entity.day_of_month, monthrange(current.year, current.month)[1]):
                yield current
            current += timedelta(days=1)
        return
    if entity.frequency_type == "hourly":
        while current <= end_date:
            yield current
            current += timedelta(days=1)


def _generate_times(entity):
    anchor = entity.time_of_day or time(hour=0, minute=0)
    if entity.frequency_type != "hourly":
        return [anchor]

    interval = entity.interval_hours or 24
    current = datetime.combine(timezone.localdate(), anchor)
    day_end = datetime.combine(timezone.localdate(), time(hour=23, minute=59))
    times = []
    while current <= day_end:
        times.append(current.time().replace(second=0, microsecond=0))
        current += timedelta(hours=interval)
    return times


def generate_occurrences(entity, from_date=None):
    start_date = max(entity.start_date, from_date or entity.start_date)
    if entity.frequency_type == "once":
        end_date = entity.start_date
    elif entity.end_date:
        end_date = entity.end_date
    else:
        end_date = timezone.localdate() + timedelta(days=90)

    if end_date < start_date:
        return []

    payloads = []
    for scheduled_date in _generate_dates(entity, start_date, end_date):
        for scheduled_time in _generate_times(entity):
            payloads.append(
                TaskOccurrence(
                    scheduled_date=scheduled_date,
                    scheduled_time=scheduled_time,
                    **_occurrence_model_fields(entity),
                )
            )
    TaskOccurrence.objects.bulk_create(payloads, ignore_conflicts=True)
    return payloads


@transaction.atomic
def regenerate_future_occurrences(entity):
    today = timezone.localdate()
    occurrence_filters = _occurrence_model_fields(entity)
    TaskOccurrence.objects.filter(**occurrence_filters, scheduled_date__gte=today).exclude(
        status__in=RESOLVED_OCCURRENCE_STATUSES
    ).delete()
    return generate_occurrences(entity, from_date=today)


def mark_occurrence(entity, occurrence, status_value, notes=None):
    if occurrence is None:
        return None
    occurrence.status = status_value
    occurrence.notes = notes or occurrence.notes
    occurrence.completed_at = timezone.now() if status_value == "completed" else None
    occurrence.save(update_fields=["status", "notes", "completed_at", "updated_at"])
    return occurrence
