from django.utils import timezone
from rest_framework import serializers

from tracker.constants import ACTIVE_HABIT_STATUSES, ACTIVE_TASK_STATUSES
from tracker.exceptions import raise_tracker_error


def _get_occurrence_units(tasks, habits):
    """
    Given a queryset of tasks and habits (already prefetched with occurrences),
    returns (total_units, completed_units) based on occurrence-level granularity.
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
        day_of_week = attrs.get("day_of_week", getattr(self.instance, "day_of_week", None))
        day_of_month = attrs.get("day_of_month", getattr(self.instance, "day_of_month", None))
        interval_hours = attrs.get("interval_hours", getattr(self.instance, "interval_hours", None))

        if frequency == "once":
            return
        if require_end_date and not end_date:
            raise_tracker_error("END_DATE_REQUIRED", "Recurring items require an end date.")
        if frequency == "weekly" and day_of_week is None:
            raise_tracker_error("INVALID_FREQUENCY_CONFIG", "Weekly frequency requires day_of_week.")
        if frequency == "monthly" and day_of_month is None:
            raise_tracker_error("INVALID_FREQUENCY_CONFIG", "Monthly frequency requires day_of_month.")
        if frequency == "hourly" and not interval_hours:
            raise_tracker_error("INVALID_FREQUENCY_CONFIG", "Hourly frequency requires interval_hours.")
        if day_of_week is not None and day_of_week not in range(0, 7):
            raise_tracker_error("INVALID_FREQUENCY_CONFIG", "day_of_week must be between 0 and 6.")
        if day_of_month is not None and day_of_month not in range(1, 32):
            raise_tracker_error("INVALID_FREQUENCY_CONFIG", "day_of_month must be between 1 and 31.")


def is_overdue(instance):
    today = timezone.localdate()
    expected = getattr(instance, "expected_end_date", None) or getattr(instance, "expected_achieved_date", None) or getattr(instance, "end_date", None)
    if not expected:
        return False
    return instance.status not in {"completed", "cancelled", "stopped"} and expected < today


def occurrence_stats(instance):
    if not hasattr(instance, "occurrences"):
        return {"total": 0, "completed": 0, "missed": 0, "next_occurrence": None}
    qs = instance.occurrences.all()
    next_occurrence = qs.filter(scheduled_date__gte=timezone.localdate(), status="pending").order_by("scheduled_date", "scheduled_time").first()
    return {
        "total": qs.count(),
        "completed": qs.filter(status="completed").count(),
        "missed": qs.filter(status="missed").count(),
        "next_occurrence": next_occurrence,
    }
