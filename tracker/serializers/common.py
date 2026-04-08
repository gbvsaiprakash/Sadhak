from django.utils import timezone
from rest_framework import serializers
from datetime import datetime
from tracker.constants import ACTIVE_HABIT_STATUSES, ACTIVE_TASK_STATUSES
from tracker.exceptions import raise_tracker_error


class EmptySerializer(serializers.Serializer):
    pass

def _get_occurrence_units(tasks, habits):
    """
    Given a queryset of tasks and habits (already prefetched with occurrences),
    returns (total_units, completed_units) based on occurrence-level granularity.

    Note: completion calculations depend only on the `occurrences` queryset, so
    expanding frequency pattern support does not change this logic as long as
    occurrences continue to be generated and stored per schedule unit.
    """
    total_units = 0
    completed_units = 0

    for task in tasks:
        occurrences = task.occurrences.all()
        if task.frequency_type == "once":
            total_units += 1
            if task.status in {"completed", "skipped"}:
                completed_units += 1
        elif occurrences:
            total_units += len(occurrences)
            completed_units += sum(
                1 for o in occurrences
                if o.status in {"completed", "skipped"}
            )
        else:
            # recurring task with no occurrences yet — 1 pending unit
            total_units += 1

    for habit in habits:
        occurrences = habit.occurrences.all()
        if occurrences:
            total_units += len(occurrences)
            completed_units += sum(
                1 for o in occurrences
                if o.status in {"completed", "skipped"}
            )
        else:
            # habit exists but no occurrences yet — 1 pending unit
            total_units += 1
            if habit.status == "completed":
                completed_units += 1

    return total_units, completed_units

class TrackerValidationMixin:
    def validate_parent_assignment(self, attrs):
        goal = attrs.get("goal", getattr(self.instance, "goal", None))
        milestone = attrs.get("milestone", getattr(self.instance, "milestone", None))
        section = attrs.get("section", getattr(self.instance, "section", None))
        if milestone and goal and milestone.goal_id != goal.id:
            raise_tracker_error(
                "INVALID_PARENT",
                "Milestone does not belong to the selected goal.",
                details={"goal_id": str(goal.id), "milestone_goal_id": str(milestone.goal_id)},
            )
        if milestone and not goal:
            goal = milestone.goal
            attrs["goal"] = goal
        
        parent_goal = milestone.goal if milestone else goal
        if parent_goal and section and parent_goal.section != section:
            raise_tracker_error(
                "INVALID_PARENT",
                "Section must match the parent goal section.",
                details={"section": section, "parent_section": parent_goal.section},
            )

    def validate_frequency(self, attrs, require_end_date):
        frequency = attrs.get("frequency_type", getattr(self.instance, "frequency_type", None))
        end_date = attrs.get("end_date", getattr(self.instance, "end_date", None))

        interval = attrs.get("frequency_interval", getattr(self.instance, "frequency_interval", 1))
        days = attrs.get("frequency_days", getattr(self.instance, "frequency_days", []))
        times_per_period = attrs.get("frequency_times_per_period", getattr(self.instance, "frequency_times_per_period", None))
        period = attrs.get("frequency_period", getattr(self.instance, "frequency_period", None))

        # Back-compat: accept deprecated hourly interval field, map to frequency_interval
        interval_hours = attrs.get("interval_hours", getattr(self.instance, "interval_hours", None))
        if frequency == "hourly" and (not interval or int(interval) == 1) and interval_hours:
            attrs["frequency_interval"] = interval_hours
            interval = interval_hours

        if frequency == "once":
            return

        if require_end_date and not end_date:
            raise_tracker_error("END_DATE_REQUIRED", "Recurring items require an end date.")

        try:
            interval_int = int(interval or 1)
        except (TypeError, ValueError):
            raise_tracker_error("INVALID_FREQUENCY_CONFIG", "frequency_interval must be an integer.")
        if interval_int < 1:
            raise_tracker_error("INVALID_FREQUENCY_CONFIG", "frequency_interval must be >= 1.")
        attrs["frequency_interval"] = interval_int

        if frequency in {"daily", "weekly", "monthly", "hourly"}:
            return

        if frequency != "custom":
            raise_tracker_error("INVALID_FREQUENCY_CONFIG", "Invalid frequency_type.")

        has_days = bool(days)
        has_times = times_per_period is not None

        if has_days and has_times:
            raise_tracker_error("INVALID_FREQUENCY_CONFIG", "frequency_days and frequency_times_per_period are mutually exclusive.")

        if has_days:
            if not isinstance(days, list):
                raise_tracker_error("INVALID_FREQUENCY_CONFIG", "frequency_days must be a list.")
            normalized = []
            for d in days:
                try:
                    d_int = int(d)
                except (TypeError, ValueError):
                    raise_tracker_error("INVALID_FREQUENCY_CONFIG", "frequency_days must contain integers.")
                if d_int not in range(0, 7) and d_int not in range(1, 32):
                    raise_tracker_error("INVALID_FREQUENCY_CONFIG", "frequency_days items must be 0–6 (weekday) or 1–31 (day of month).")
                normalized.append(d_int)
            attrs["frequency_days"] = normalized
            return

        if has_times:
            try:
                tpp = int(times_per_period)
            except (TypeError, ValueError):
                raise_tracker_error("INVALID_FREQUENCY_CONFIG", "frequency_times_per_period must be an integer.")
            if tpp < 1:
                raise_tracker_error("INVALID_FREQUENCY_CONFIG", "frequency_times_per_period must be >= 1.")
            if not period:
                raise_tracker_error("INVALID_FREQUENCY_CONFIG", "frequency_period is required when using frequency_times_per_period.")
            if period not in {"day", "week", "month"}:
                raise_tracker_error("INVALID_FREQUENCY_CONFIG", "frequency_period must be one of: day, week, month.")
            attrs["frequency_times_per_period"] = tpp
            return

        raise_tracker_error("INVALID_FREQUENCY_CONFIG", "Custom frequency requires either frequency_days or frequency_times_per_period.")


def is_overdue(instance):
    today = timezone.localdate()
    expected = getattr(instance, "expected_end_date", None) or getattr(instance, "expected_achieved_date", None) or getattr(instance, "end_date", None)
    if not expected:
        return False
    return instance.status not in {"completed", "cancelled", "stopped"} and expected < today


def occurrence_stats(instance):
    if not hasattr(instance, "occurrences"):
        return {"total": 0, "completed": 0, "missed": 0, "skipped": 0, "next_occurrence": None}
    qs = instance.occurrences.all()
    next_occurrence = qs.filter(scheduled_date__gte=timezone.localdate(), status="pending").order_by("scheduled_date", "scheduled_time").first()
    missed_count = qs.filter(status="missed").count()
    now = timezone.localtime()
    pending_qs = qs.filter(status="pending")
    computed_missed = 0
    for o in pending_qs:
        end_t = o.schedule_end_time or o.scheduled_time
        if end_t is None:
            continue
        deadline = timezone.make_aware(
            datetime.combine(o.scheduled_date, end_t),
            timezone.get_current_timezone(),
        )
        if deadline < now:
            computed_missed += 1

    # avoid double-count (persisted + computed pending overdue)
    final_missed_count = missed_count + computed_missed

    return {
        "total": qs.count(),
        "completed": qs.filter(status="completed").count(),
        "missed": final_missed_count,
        "skipped": qs.filter(status="skipped").count(),
        "next_occurrence": next_occurrence,
    }
