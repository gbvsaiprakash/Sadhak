from calendar import monthrange
from datetime import datetime, time, timedelta
from sadhak.app_settings import POST_DUE_GRACE_MIN
from django.db import transaction
from django.utils import timezone
from sadhak_base.services import emit_event
from tracker.constants import RESOLVED_OCCURRENCE_STATUSES
from tracker.models import TaskOccurrence, OccurrenceReminder
from tracker.services.dependency import ensure_dependencies_completed_for_occurrence

def _occurrence_model_fields(entity):
    if entity.is_habit:
        return {"habit": entity}
    return {"task": entity}


def _safe_time(t):
    if t is None:
        return None
    return t.replace(second=0, microsecond=0)

def _duration_minutes(entity):
    cfg = getattr(entity, "duration_config", None) or {}
    value = int(cfg.get("value", 30) or 30)
    unit = (cfg.get("unit") or "minutes").lower()
    return value * 60 if unit == "hours" else value

def _compute_occurrence_end_time(start_t, duration_mins):
    dt = datetime.combine(timezone.localdate(), start_t) + timedelta(minutes=duration_mins)
    if dt.date() != timezone.localdate():
        return time(23, 59, 59)
    return dt.time().replace(second=0, microsecond=0)

def _entity_duration(entity):
    """
    Returns a timedelta duration for an occurrence based on entity start/end time.
    Assumes validation ensures end_time > start_time (same-day window).
    """
    start = datetime.combine(timezone.localdate(), entity.start_time)
    end = datetime.combine(timezone.localdate(), entity.end_time or entity.start_time)
    return max(end - start, timedelta(minutes=0), entity.duration_config and timedelta(minutes=_duration_minutes(entity)) or timedelta(minutes=0))


def _add_duration(start_t, duration):
    dt = datetime.combine(timezone.localdate(), start_t) + duration
    capped = min(dt, datetime.combine(timezone.localdate(), time(23, 59)))
    return capped.time().replace(second=0, microsecond=0)

def _occurrence_datetime(occ):
    base_time = occ.scheduled_time or time(0, 0)
    naive = datetime.combine(occ.scheduled_date, base_time)
    return timezone.make_aware(naive, timezone.get_current_timezone())

def _parent_reminders(parent):
    if not parent.reminder_enabled:
        return []
    normalized = parent.get_normalized_reminders()
    out = []
    for item in normalized:
        mins = item["value"] * 60 if item["unit"] == "hours" else item["value"]
        for mode in item.get("modes", []):
            out.append({"offset_minutes": mins, "mode": mode})
    return out

def rebuild_reminders_for_occurrences(occurrences):
    """
    Rebuild reminder rows only for the touched occurrences.
    Safe for both first-create and reschedule paths.
    """
    
    occurrences = list(occurrences)
    if not occurrences:
        return

    OccurrenceReminder.objects.filter(
        occurrence__in=occurrences,
        sent=False,
        is_deleted=False,
    ).update(is_deleted=True)

    rows = []
    now = timezone.now()
    for occ in occurrences:
        if occ.status != "pending" or occ.is_deleted:
            continue

        parent = occ.task or occ.habit
        rules = _parent_reminders(parent)

        if not rules:
            continue

        occ_dt = _occurrence_datetime(occ)
        for rule in rules:
            remind_at = occ_dt - timedelta(minutes=rule["offset_minutes"])
            if remind_at < now:
                continue
            rows.append(
                OccurrenceReminder(
                    occurrence=occ,
                    offset_minutes=rule["offset_minutes"],
                    mode=rule["mode"],
                    remind_at=remind_at,
                )
            )

    if rows:
        OccurrenceReminder.objects.bulk_create(rows, ignore_conflicts=True)

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
        # accept 0..6 (Sun..Sat)
        raw_days = list(entity.frequency_days or [])
        if raw_days:
            weekdays = set()
            for d in raw_days:
                try:
                    n = int(d)
                except (TypeError, ValueError):
                    continue
                if 0 <= n <= 6:
                    weekdays.add(n)  # python weekday: Mon=0..Sun=6
            if not weekdays:
                weekdays = {entity.start_date.weekday()}
        else:
            weekdays = {entity.start_date.weekday()}

        current = start_date
        while current <= end_date:
            weeks_since_start = (current - entity.start_date).days // 7
            if weeks_since_start >= 0 and (weeks_since_start % interval == 0) and (current.weekday() in weekdays):
                yield current
            current += timedelta(days=1)
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


# def _generate_times_for_date(entity, scheduled_date):
#     freq = entity.frequency_type
#     start_time = _safe_time(entity.start_time)
#     end_time = _safe_time(entity.end_time)
#     duration = _entity_duration(entity)



#     if freq != "custom":
#         if freq != "hourly":
#             return [start_time]
#         # hourly
#         interval = max(int(getattr(entity, "frequency_interval", 1) or 1), 1)
#         current = datetime.combine(scheduled_date, start_time)
#         day_end = datetime.combine(scheduled_date, time(23, 59))
#         times = []
#         while current <= day_end:
#             times.append(current.time().replace(second=0, microsecond=0))
#             current += timedelta(hours=interval)
#         return times

#     # custom
#     if entity.frequency_times_per_period and entity.frequency_period:
#         k = int(entity.frequency_times_per_period)
#         if k <= 0:
#             return [start_time]

#         if entity.frequency_period == "day":
#             if k == 1:
#                 return [start_time]
#             # place k occurrences evenly in [start_time, end_time)
#             start_dt = datetime.combine(scheduled_date, start_time)
#             end_dt = datetime.combine(scheduled_date, end_time)
#             window = end_dt - start_dt
#             if window <= timedelta(minutes=0):
#                 return [start_time]
#             step = window / k
#             times = []
#             for i in range(k):
#                 t = (start_dt + (step * i)).time().replace(second=0, microsecond=0)
#                 # ensure the implied end doesn't overflow past 23:59
#                 if _add_duration(t, duration) <= time(23, 59):
#                     times.append(t)
#             return sorted(set(times))

#         # week/month handled at date selection level; within a selected date we use start_time
#         return [start_time]

#     return [start_time]

# def _generate_times_for_date(entity, scheduled_date):
#     freq = entity.frequency_type
#     start_time = _safe_time(entity.start_time)
#     end_time = _safe_time(entity.end_time)  # can be None

#     def slot_end(t):
#         return end_time if end_time else None

#     if freq != "custom":
#         if freq != "hourly":
#             return [(start_time, slot_end(start_time))]

#         interval = max(int(getattr(entity, "frequency_interval", 1) or 1), 1)
#         current = datetime.combine(scheduled_date, start_time)
#         day_end = datetime.combine(scheduled_date, end_time or time(23, 59))
#         slots = []
#         while current <= day_end:
#             t = current.time().replace(second=0, microsecond=0)
#             slots.append((t, slot_end(t)))
#             current += timedelta(hours=interval)
#         return slots

#     if entity.frequency_times_per_period and entity.frequency_period:
#         k = int(entity.frequency_times_per_period)
#         if k <= 0:
#             return [(start_time, slot_end(start_time))]

#         if entity.frequency_period == "day":
#             if k == 1:
#                 return [(start_time, slot_end(start_time))]
#             start_dt = datetime.combine(scheduled_date, start_time)
#             end_dt = datetime.combine(scheduled_date, end_time or time(23, 59))
#             window = end_dt - start_dt
#             if window <= timedelta(minutes=0):
#                 return [(start_time, slot_end(start_time))]
#             step = window / k
#             slots = []
#             for i in range(k):
#                 t = (start_dt + (step * i)).time().replace(second=0, microsecond=0)
#                 slots.append((t, slot_end(t)))
#             return slots

#         return [(start_time, slot_end(start_time))]

#     return [(start_time, slot_end(start_time))]
def _generate_times_for_date(entity, scheduled_date):
    freq = entity.frequency_type
    start_time = _safe_time(entity.start_time)
    end_time = _safe_time(entity.end_time)  # optional window bound

    if freq != "custom":
        if freq != "hourly":
            return [start_time]

        interval = max(int(getattr(entity, "frequency_interval", 1) or 1), 1)
        current = datetime.combine(scheduled_date, start_time)
        day_end = datetime.combine(scheduled_date, end_time or time(23, 59))
        slots = []
        while current <= day_end:
            slots.append(current.time().replace(second=0, microsecond=0))
            current += timedelta(hours=interval)
        return slots

    if entity.frequency_times_per_period and entity.frequency_period:
        k = int(entity.frequency_times_per_period)
        if k <= 0:
            return [start_time]

        if entity.frequency_period == "day":
            if k == 1:
                return [start_time]
            start_dt = datetime.combine(scheduled_date, start_time)
            end_dt = datetime.combine(scheduled_date, end_time or time(23, 59))
            window = end_dt - start_dt
            if window <= timedelta(minutes=0):
                return [start_time]
            step = window / k
            slots = []
            for i in range(k):
                t = (start_dt + (step * i)).time().replace(second=0, microsecond=0)
                slots.append(t)
            return sorted(set(slots))

        return [start_time]

    return [start_time]




def _week_bounds(d):
    # Monday..Sunday
    start = d - timedelta(days=d.weekday())
    return start, start + timedelta(days=6)

def get_regeneration_window(instance, updated_entity, validated_data, schedule_fields):
    today = timezone.localdate()
    old_start_date = instance.start_date
    old_end_date = instance.end_date
    old_frequency_type = instance.frequency_type

    only_end_date_extended = (
        "end_date" in validated_data
        and old_end_date
        and updated_entity.end_date
        and updated_entity.end_date > old_end_date
        and not any(field in validated_data for field in (schedule_fields - {"end_date"}))
    )

    if only_end_date_extended:
        regen_from = max(today, old_end_date + timedelta(days=1))
        return regen_from, regen_from, updated_entity.end_date

    if (
        "start_date" in validated_data
        or "frequency_type" in validated_data
        or old_frequency_type != updated_entity.frequency_type
    ):
        regen_from = min(old_start_date, updated_entity.start_date)
        return regen_from, regen_from, None

    regen_from = min(today, updated_entity.start_date)
    return regen_from, regen_from, None


def _anchored_evenly_spaced_weekdays(start_weekday, k):
    """
    start_weekday: Python weekday (Mon=0..Sun=6), typically entity.start_date.weekday()
    k: times per week (1..7)
    Returns stable weekday indexes (0..6), anchored to start_weekday.
    """
    k = max(min(int(k), 7), 1)
    start_weekday = int(start_weekday) % 7

    # floor-based spacing gives Tue+Fri for start Tue and k=2
    picks = [(start_weekday + int((i * 7) / k)) % 7 for i in range(k)]

    unique = []
    for wd in picks:
        if wd not in unique:
            unique.append(wd)

    # safety fill if duplicates appear
    cursor = start_weekday
    while len(unique) < k:
        if cursor not in unique:
            unique.append(cursor)
        cursor = (cursor + 1) % 7

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
        anchor = entity.start_date.weekday()

        while current_week_start <= end_date:
            weekdays = _anchored_evenly_spaced_weekdays(anchor, k)
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
            month_start = cursor
            month_end = cursor.replace(day=last_day)

            # Active window for this month only
            active_start = max(month_start, start_date)
            active_end = min(month_end, end_date)

            if active_start <= active_end:
                span_days = (active_end - active_start).days + 1
                picks_count = min(k, span_days)

                if picks_count == span_days:
                    offsets = list(range(span_days))
                else:
                    # evenly spaced offsets in active window
                    offsets = []
                    if picks_count == 1:
                        offsets = [0]
                    else:
                        for i in range(picks_count):
                            off = int(round(i * (span_days - 1) / (picks_count - 1)))
                            offsets.append(off)
                    offsets = sorted(set(offsets))
                    # fill if dedupe reduced count
                    x = 0
                    while len(offsets) < picks_count and x < span_days:
                        if x not in offsets:
                            offsets.append(x)
                        x += 1
                    offsets = sorted(offsets[:picks_count])

                for off in offsets:
                    candidate = active_start + timedelta(days=off)
                    if start_date <= candidate <= end_date:
                        yield candidate

            # next month
            month = cursor.month + 1
            year = cursor.year + (month - 1) // 12
            month = ((month - 1) % 12) + 1
            cursor = cursor.replace(year=year, month=month, day=1)
        return


# def generate_occurrences(entity, from_date=None, to_date=None):
#     start_date = max(entity.start_date, from_date or entity.start_date)
#     if entity.frequency_type == "once":
#         end_date = entity.start_date
#     elif entity.end_date:
#         end_date = entity.end_date
#     else:
#         end_date = timezone.localdate() + timedelta(days=90)

#     if to_date is not None:
#         end_date = min(end_date, to_date)

#     if end_date < start_date:
#         return []

#     payloads = []
#     if entity.frequency_type == "custom" and entity.frequency_times_per_period and entity.frequency_period in {"week", "month"}:
#         date_iter = _generate_custom_period_dates(entity, start_date, end_date)
#     else:
#         date_iter = _generate_dates(entity, start_date, end_date)
#     for scheduled_date in date_iter:
#         for scheduled_time, schedule_end_time in _generate_times_for_date(entity, scheduled_date):
#             payloads.append(
#                 TaskOccurrence(
#                     scheduled_date=scheduled_date,
#                     scheduled_time=scheduled_time,
#                     schedule_end_time=schedule_end_time,
#                     duration_mins = _duration_minutes(entity)
#                     occ_end = _compute_occurrence_end_time(scheduled_time, duration_mins)
#                     schedule_end_time=occ_end

#                     **_occurrence_model_fields(entity),
#                 )
#             )
#     TaskOccurrence.objects.bulk_create(payloads, ignore_conflicts=True)
#     return payloads

def generate_occurrences(entity, from_date=None, to_date=None):
    start_date = max(entity.start_date, from_date or entity.start_date)
    if entity.frequency_type == "once":
        end_date = entity.start_date
    elif entity.end_date:
        end_date = entity.end_date
    else:
        end_date = timezone.localdate() + timedelta(days=90)

    if to_date is not None:
        end_date = min(end_date, to_date)

    task_end_dt = None
    if end_date:
        task_end_dt = datetime.combine(end_date, _safe_time(entity.end_time) or time(23, 59))

    if end_date < start_date:
        return []

    payloads = []
    duration_mins = _duration_minutes(entity)
    if entity.frequency_type == "custom" and entity.frequency_times_per_period and entity.frequency_period in {"week", "month"}:
        date_iter = _generate_custom_period_dates(entity, start_date, end_date)
    else:
        date_iter = _generate_dates(entity, start_date, end_date)
    dates_list = list(date_iter)
    for scheduled_date in dates_list:
        for scheduled_time in _generate_times_for_date(entity, scheduled_date):
            occ_end = _compute_occurrence_end_time(scheduled_time, duration_mins)
            slot_start_dt = datetime.combine(scheduled_date, scheduled_time)
            slot_end_dt = datetime.combine(scheduled_date, occ_end)
            if task_end_dt and slot_end_dt > task_end_dt:
                continue
            payloads.append(
                TaskOccurrence(
                    scheduled_date=scheduled_date,
                    scheduled_time=scheduled_time,
                    schedule_end_time=occ_end,
                    **_occurrence_model_fields(entity),
                )
            )

    TaskOccurrence.objects.bulk_create(payloads, ignore_conflicts=True)
    
    created_qs = TaskOccurrence.objects.filter(
        is_deleted=False,
        scheduled_date__in=dates_list,
        **_occurrence_model_fields(entity),
    ).exclude(status__in=["completed", "skipped", "missed"])

    rebuild_reminders_for_occurrences(created_qs)
    return payloads



@transaction.atomic
def regenerate_future_occurrences(entity, from_date=None):
    effective_from = from_date or timezone.localdate()
    occurrence_filters = _occurrence_model_fields(entity)

    TaskOccurrence.objects.filter(
        **occurrence_filters,
        scheduled_date__gte=effective_from,
    ).exclude(
        status__in=RESOLVED_OCCURRENCE_STATUSES
    ).delete()

    return generate_occurrences(entity, from_date=effective_from)

@transaction.atomic
def reconcile_occurrences(entity, window_from, window_to=None):
    """
    Reconcile occurrences after schedule edits:
    - delete unresolved occurrences in impacted window
    - keep resolved only if they still match the new schedule slot
    - soft-delete resolved that no longer match
    - restore matching soft-deleted rows if no active row exists for that slot
    """
    occurrence_filters = _occurrence_model_fields(entity)

    # Window queryset
    q = TaskOccurrence.objects.filter(**occurrence_filters, scheduled_date__gte=window_from)
    if window_to is not None:
        q = q.filter(scheduled_date__lte=window_to)

    # 1) remove unresolved slots (will be regenerated)
    q.exclude(status__in=RESOLVED_OCCURRENCE_STATUSES).delete()

    # 2) generate under new schedule (ignore_conflicts handles duplicates)
    generated = generate_occurrences(entity, from_date=window_from, to_date=window_to)
    expected_slots = {(o.scheduled_date, o.scheduled_time) for o in generated}

    # 3) prune resolved slots that no longer belong
    resolved_qs = TaskOccurrence.objects.filter(
        **occurrence_filters,
        status__in=RESOLVED_OCCURRENCE_STATUSES,
        scheduled_date__gte=window_from,
    )
    if window_to is not None:
        resolved_qs = resolved_qs.filter(scheduled_date__lte=window_to)

    to_soft_delete_ids = []
    for occ in resolved_qs.only("id", "scheduled_date", "scheduled_time"):
        if (occ.scheduled_date, occ.scheduled_time) not in expected_slots:
            to_soft_delete_ids.append(occ.id)

    if to_soft_delete_ids:
        TaskOccurrence.objects.filter(id__in=to_soft_delete_ids).update(
            is_deleted=True,
            updated_at=timezone.now(),
        )

    # 4) restore matching soft-deleted rows only when slot has no active row
    active_qs = TaskOccurrence.objects.filter(
        **occurrence_filters,
        is_deleted=False,
        scheduled_date__gte=window_from,
    )
    if window_to is not None:
        active_qs = active_qs.filter(scheduled_date__lte=window_to)

    active_slots = set(active_qs.values_list("scheduled_date", "scheduled_time"))

    soft_qs = TaskOccurrence.objects.filter(
        **occurrence_filters,
        is_deleted=True,
        scheduled_date__gte=window_from,
    )
    if window_to is not None:
        soft_qs = soft_qs.filter(scheduled_date__lte=window_to)

    restore_ids = []
    for occ in soft_qs.only("id", "scheduled_date", "scheduled_time"):
        slot = (occ.scheduled_date, occ.scheduled_time)
        if slot in expected_slots and slot not in active_slots:
            restore_ids.append(occ.id)
            active_slots.add(slot)

    if restore_ids:
        TaskOccurrence.objects.filter(id__in=restore_ids).update(
            is_deleted=False,
            updated_at=timezone.now(),
        )

    return generated



def mark_occurrence(entity, occurrence, status_value, notes=None, override_dependency=False, override_reason=None):
    if occurrence is None:
        return None
    if status_value.lower() == "completed":
        # ensure_dependencies_completed_for_occurrence(entity, occurrence.scheduled_date)
        ensure_dependencies_completed_for_occurrence(entity, occurrence, override_dependency=override_dependency, override_reason="Not Provided" if override_reason is None else override_reason)
    occurrence.status = status_value
    occurrence.notes = notes or occurrence.notes
    occurrence.completed_at = timezone.now() if status_value == "completed" else None
    occurrence.save(update_fields=["status", "notes", "completed_at", "updated_at"])
    OccurrenceReminder.objects.filter(
        occurrence=occurrence,
        sent=False,
        is_deleted=False,
    ).update(is_deleted=True)
    return occurrence


@transaction.atomic
def sync_occurrence_reminders_for_parent(parent):
    is_task = parent.__class__.__name__.lower() == "task"
    occ_qs = TaskOccurrence.objects.filter(
        task=parent if is_task else None,
        habit=None if is_task else parent,
        status="pending",
        is_deleted=False,
    )

    rules = _parent_reminders(parent)
    OccurrenceReminder.objects.filter(occurrence__in=occ_qs, sent=False, is_deleted=False).update(is_deleted=True)
    if not rules:
        return

    new_rows = []
    now = timezone.now()
    for occ in occ_qs:
        occ_dt = _occurrence_datetime(occ)
        for rule in rules:
            remind_at = occ_dt - timedelta(minutes=rule["offset_minutes"])
            if remind_at < now:
                continue
            new_rows.append(
                OccurrenceReminder(
                    occurrence=occ,
                    offset_minutes=rule["offset_minutes"],
                    mode=rule["mode"],
                    remind_at=remind_at,
                )
            )

    OccurrenceReminder.objects.bulk_create(new_rows, ignore_conflicts=True)

def _occurrence_due_dt(occ):
    t = occ.scheduled_time or time(0, 0)
    return timezone.make_aware(datetime.combine(occ.scheduled_date, t), timezone.get_current_timezone())

def emit_due_reminder_events(now=None, batch_size=500):
    now = now or timezone.now()
    due = (
        OccurrenceReminder.objects
        .select_related("occurrence", "occurrence__task", "occurrence__habit")
        .filter(
            remind_at__lte=now,
            event_emitted=False,
            sent=False,
            is_deleted=False,
            occurrence__status="pending",
            occurrence__is_deleted=False,
        )
        .order_by("occurrence_id", "mode", "-remind_at")[:batch_size]
    )

    seen_occurrence = set()
    updated_ids = []
    print(f"Emitting events for {len(due)} due reminders")
    for r in due:
        key = (r.occurrence_id, r.mode)
        if key in seen_occurrence:
            # older due reminder for same occurrence -> suppress:
            r.event_emitted = True
            r.event_emitted_at = now
            r.is_deleted = True  # soft-skip as superseded
            r.save(update_fields=["event_emitted", "event_emitted_at", "is_deleted", "updated_at"])
            continue

        seen_occurrence.add(key)
        due_dt = _occurrence_due_dt(r.occurrence)
        if now > due_dt + timedelta(minutes=POST_DUE_GRACE_MIN):
            # too stale
            r.event_emitted = True
            r.event_emitted_at = now
            r.is_deleted = True
            r.save(update_fields=["event_emitted", "event_emitted_at", "is_deleted", "updated_at"])
            continue

        parent = r.occurrence.task or r.occurrence.habit
        event_type = "task.reminder_due" if r.occurrence.task_id else "habit.reminder_due"

        emit_event(
            event_type=event_type,
            actor=getattr(parent, "user", None),
            object_type="TaskOccurrence",
            object_id=str(r.occurrence_id),
            payload={
                "occurrence_id": str(r.occurrence_id),
                "reminder_id": str(r.id),
                "mode": r.mode,
                "remind_at": timezone.localtime(r.remind_at).isoformat(),
            },
        )
        updated_ids.append(r.id)

    if updated_ids:
        OccurrenceReminder.objects.filter(id__in=updated_ids).update(
            event_emitted=True, event_emitted_at=now
        )

    return len(updated_ids)
