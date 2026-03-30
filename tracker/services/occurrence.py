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


def _safe_time(t):
    if t is None:
        return None
    return t.replace(second=0, microsecond=0)


def _entity_duration(entity):
    """
    Returns a timedelta duration for an occurrence based on entity start/end time.
    Assumes validation ensures end_time > start_time (same-day window).
    """
    start = datetime.combine(timezone.localdate(), entity.start_time)
    end = datetime.combine(timezone.localdate(), entity.end_time or entity.start_time)
    return max(end - start, timedelta(minutes=0))


def _add_duration(start_t, duration):
    dt = datetime.combine(timezone.localdate(), start_t) + duration
    capped = min(dt, datetime.combine(timezone.localdate(), time(23, 59)))
    return capped.time().replace(second=0, microsecond=0)


def _monthly_days_for(entity, year, month):
    last_day = monthrange(year, month)[1]
    days = list(entity.frequency_days or [])
    if not days:
        days = [entity.start_date.day]
    normalized = []
    for d in days:
        try:
            d_int = int(d)
        except (TypeError, ValueError):
            continue
        if 1 <= d_int <= 31:
            normalized.append(min(d_int, last_day))
    return sorted(set(normalized))


def _generate_dates(entity, start_date, end_date):
    freq = entity.frequency_type
    interval = max(int(getattr(entity, "frequency_interval", 1) or 1), 1)

    if freq == "once":
        yield entity.start_date
        return

    if freq == "daily":
        current = start_date
        while current <= end_date:
            yield current
            current += timedelta(days=interval)
        return

    if freq == "weekly":
        anchor_weekday = entity.start_date.weekday()
        if getattr(entity, "frequency_days", None):
            try:
                wd = int((entity.frequency_days or [])[0])
                if 0 <= wd <= 6:
                    anchor_weekday = wd
            except (TypeError, ValueError, IndexError):
                pass
        # find first date >= start_date matching anchor weekday
        current = start_date
        while current.weekday() != anchor_weekday and current <= end_date:
            current += timedelta(days=1)
        while current <= end_date:
            yield current
            current += timedelta(days=7 * interval)
        return

    if freq == "monthly":
        # step months by interval and emit configured day(s) each month
        year, month = start_date.year, start_date.month
        cursor = start_date.replace(day=1)
        while cursor <= end_date:
            for day in _monthly_days_for(entity, cursor.year, cursor.month):
                candidate = cursor.replace(day=day)
                if start_date <= candidate <= end_date:
                    yield candidate
            # advance by interval months
            month = cursor.month + interval
            year = cursor.year + (month - 1) // 12
            month = ((month - 1) % 12) + 1
            cursor = cursor.replace(year=year, month=month, day=1)
        return

    if freq == "hourly":
        current = start_date
        while current <= end_date:
            yield current
            current += timedelta(days=1)
        return

    if freq == "custom":
        days = list(entity.frequency_days or [])
        if days:
            # Treat 0-6 as weekday filters
            weekday_set = {int(d) for d in days if isinstance(d, int) or (isinstance(d, str) and d.isdigit())}
            weekday_set = {d for d in weekday_set if 0 <= d <= 6}
            current = start_date
            while current <= end_date:
                if current.weekday() in weekday_set:
                    yield current
                current += timedelta(days=1)
            return

        # times_per_period driven custom schedules are handled at the (date,time) level
        current = start_date
        while current <= end_date:
            yield current
            current += timedelta(days=1)
        return


def _generate_times_for_date(entity, scheduled_date):
    freq = entity.frequency_type
    start_time = _safe_time(entity.start_time)
    end_time = _safe_time(entity.end_time)
    duration = _entity_duration(entity)

    if freq != "custom":
        if freq != "hourly":
            return [start_time]
        # hourly
        interval = max(int(getattr(entity, "frequency_interval", 1) or 1), 1)
        current = datetime.combine(scheduled_date, start_time)
        day_end = datetime.combine(scheduled_date, time(23, 59))
        times = []
        while current <= day_end:
            times.append(current.time().replace(second=0, microsecond=0))
            current += timedelta(hours=interval)
        return times

    # custom
    if entity.frequency_times_per_period and entity.frequency_period:
        k = int(entity.frequency_times_per_period)
        if k <= 0:
            return [start_time]

        if entity.frequency_period == "day":
            if k == 1:
                return [start_time]
            # place k occurrences evenly in [start_time, end_time)
            start_dt = datetime.combine(scheduled_date, start_time)
            end_dt = datetime.combine(scheduled_date, end_time)
            window = end_dt - start_dt
            if window <= timedelta(minutes=0):
                return [start_time]
            step = window / k
            times = []
            for i in range(k):
                t = (start_dt + (step * i)).time().replace(second=0, microsecond=0)
                # ensure the implied end doesn't overflow past 23:59
                if _add_duration(t, duration) <= time(23, 59):
                    times.append(t)
            return sorted(set(times))

        # week/month handled at date selection level; within a selected date we use start_time
        return [start_time]

    return [start_time]


def _week_bounds(d):
    # Monday..Sunday
    start = d - timedelta(days=d.weekday())
    return start, start + timedelta(days=6)


def _evenly_spaced_weekdays(k):
    # choose k unique weekdays spread across 0..6
    k = max(min(int(k), 7), 1)
    if k == 1:
        return [0]
    step = 7 / k
    picks = []
    for i in range(k):
        wd = int(round(i * step))
        picks.append(min(wd, 6))
    # de-dupe while preserving order, then fill remaining from left to right
    unique = []
    for wd in picks:
        if wd not in unique:
            unique.append(wd)
    for wd in range(7):
        if len(unique) >= k:
            break
        if wd not in unique:
            unique.append(wd)
    return sorted(unique)


def _generate_custom_period_dates(entity, start_date, end_date):
    """
    For custom+times_per_period with period week/month:
    - week: pick k evenly spaced weekdays each week
    - month: pick k evenly spaced days each month
    """
    k = int(entity.frequency_times_per_period or 0)
    period = entity.frequency_period
    if k <= 0 or period not in {"week", "month"}:
        return

    if period == "week":
        week_start, week_end = _week_bounds(start_date)
        current_week_start = week_start
        while current_week_start <= end_date:
            weekdays = _evenly_spaced_weekdays(k)
            for wd in weekdays:
                candidate = current_week_start + timedelta(days=wd)
                if start_date <= candidate <= end_date:
                    yield candidate
            current_week_start += timedelta(days=7)
        return

    if period == "month":
        cursor = start_date.replace(day=1)
        while cursor <= end_date:
            last_day = monthrange(cursor.year, cursor.month)[1]
            if k >= last_day:
                days = list(range(1, last_day + 1))
            else:
                step = last_day / k
                days = sorted({max(1, min(last_day, int(round(1 + i * step)))) for i in range(k)})
                while len(days) < k:
                    for d in range(1, last_day + 1):
                        if d not in days:
                            days.append(d)
                            if len(days) >= k:
                                break
                days = sorted(days[:k])
            for d in days:
                candidate = cursor.replace(day=d)
                if start_date <= candidate <= end_date:
                    yield candidate
            # next month
            month = cursor.month + 1
            year = cursor.year + (month - 1) // 12
            month = ((month - 1) % 12) + 1
            cursor = cursor.replace(year=year, month=month, day=1)
        return


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
    if entity.frequency_type == "custom" and entity.frequency_times_per_period and entity.frequency_period in {"week", "month"}:
        date_iter = _generate_custom_period_dates(entity, start_date, end_date)
    else:
        date_iter = _generate_dates(entity, start_date, end_date)

    for scheduled_date in date_iter:
        for scheduled_time in _generate_times_for_date(entity, scheduled_date):
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
