from datetime import date, datetime, timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from tracker.exceptions import raise_tracker_error
from tracker.models import Task, TaskOccurrence
from tracker.serializers.common import TrackerValidationMixin, is_overdue, occurrence_stats
from tracker.services import (
    check_entity_schedule_conflicts,
    check_goal_completion,
    check_milestone_completion,
    generate_occurrences,
    regenerate_future_occurrences,
    reconcile_occurrences,
)


class TaskOccurrenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskOccurrence
        fields = (
            "id",
            "scheduled_date",
            "scheduled_time",
            "schedule_end_time",
            "status",
            "completed_at",
            "notes",
            "context_title",
            "context_description",
            "context_checklist",
            "is_deleted",
        )


class TaskListSerializer(serializers.ModelSerializer):
    is_overdue = serializers.SerializerMethodField()
    next_occurrence = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = (
            "id",
            "title",
            "section",
            "status",
            "frequency_type",
            "start_date",
            "end_date",
            "start_time",
            "end_time",
            "is_overdue",
            "next_occurrence",
        )

    def get_is_overdue(self, obj):
        return is_overdue(obj)

    def get_next_occurrence(self, obj):
        stats = occurrence_stats(obj)
        next_occurrence = stats["next_occurrence"]
        return next_occurrence.scheduled_date if next_occurrence else None


class TaskDetailSerializer(TaskListSerializer, TrackerValidationMixin):
    occurrences = TaskOccurrenceSerializer(many=True, read_only=True)
    total_occurrences = serializers.SerializerMethodField()
    completed_occurrences = serializers.SerializerMethodField()
    missed_occurrences = serializers.SerializerMethodField()
    skipped_occurrences = serializers.SerializerMethodField()

    SCHEDULE_FIELDS = {
        "frequency_type",
        "frequency_interval",
        "frequency_days",
        "frequency_times_per_period",
        "frequency_period",
        "start_date",
        "end_date",
        "start_time",
        "end_time",
        "day_of_week",
        "day_of_month",
        "interval_hours",
    }

    VALID_WEEKDAYS = {1,2,3,4,5,6,0} #{"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
    VALID_MONTHDAYS = set(range(1, 32))
    
    class Meta(TaskListSerializer.Meta):
        fields = (
            "id",
            "user",
            "goal",
            "milestone",
            "section",
            "title",
            "description",
            "status",
            "frequency_type",
            "frequency_interval",
            "frequency_days",
            "frequency_times_per_period",
            "frequency_period",
            "start_date",
            "end_date",
            "start_time",
            "end_time",
            "day_of_week",
            "day_of_month",
            "interval_hours",
            "is_habit",
            "is_overdue",
            "next_occurrence",
            "total_occurrences",
            "completed_occurrences",
            "missed_occurrences",
            "skipped_occurrences",
            "occurrences",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("user", "is_habit", "created_at", "updated_at")

    def _effective(self, attrs, key, default=None):
        if key in attrs:
            return attrs.get(key)
        if self.instance is not None:
            return getattr(self.instance, key, default)
        return default

    def _normalize_days(self, raw_days, frequency_type):
        if not raw_days:
            return []
        valid_values = {}   
        if frequency_type == "weekly":
            valid_values = self.VALID_WEEKDAYS
        elif frequency_type == "monthly":
            valid_values = self.VALID_MONTHDAYS
        days = [d for d in raw_days if isinstance(d, (int, str))]
        invalid = [str(d) for d in days if d not in valid_values]
        if invalid:
            # raise_tracker_error("INVALID_FREQUENCY_CONFIG", f"Invalid { 'weekday(s)' if frequency_type == "weekly" else 'monthday(s)' }: {', '.join(invalid)}")
            raise_tracker_error(
                "INVALID_FREQUENCY_CONFIG",
                f"Invalid {'weekday(s)' if frequency_type == 'weekly' else 'monthday(s)'}: {', '.join(invalid)}",
            )

        return list(dict.fromkeys(days))

    def _normalize_frequency_payload(self, attrs):
        frequency_type = self._effective(attrs, "frequency_type")
        if not frequency_type:
            return

        interval = self._effective(attrs, "frequency_interval")
        raw_days = self._effective(attrs, "frequency_days", [])
        days = self._normalize_days(raw_days, frequency_type)
        times_per_period = self._effective(attrs, "frequency_times_per_period")
        period = self._effective(attrs, "frequency_period")
        day_of_month = days[0] if days and frequency_type == "monthly" else self._effective(attrs, "day_of_month")
        interval_hours = self._effective(attrs, "interval_hours") or self._effective(attrs, "frequency_interval") if frequency_type == "hourly" else None
        attrs["frequency_days"] = days

        if frequency_type == "once":
            attrs["frequency_interval"] = int(interval or 1)
            attrs["frequency_days"] = []
            attrs["frequency_times_per_period"] = None
            attrs["frequency_period"] = None
            attrs["day_of_week"] = None
            attrs["day_of_month"] = None
            attrs["interval_hours"] = None
            return

        if frequency_type == "daily":
            if not interval or int(interval) < 1:
                raise_tracker_error("INVALID_FREQUENCY_CONFIG", "Daily frequency requires frequency_interval >= 1.")
            attrs["frequency_days"] = days
            attrs["frequency_times_per_period"] = None
            attrs["frequency_period"] = None
            attrs["day_of_week"] = None
            attrs["day_of_month"] = None
            attrs["interval_hours"] = None
            return

        if frequency_type == "weekly":
            if not interval or int(interval) < 1:
                raise_tracker_error("INVALID_FREQUENCY_CONFIG", "Weekly frequency requires frequency_interval >= 1.")
            if not days:
                raise_tracker_error("INVALID_FREQUENCY_CONFIG", "Weekly frequency requires at least one day in frequency_days.")
            attrs["day_of_week"] = days[0] if len(days) == 1 else None
            attrs["frequency_days"] = days
            attrs["frequency_times_per_period"] = None
            attrs["frequency_period"] = None
            attrs["day_of_month"] = None
            attrs["interval_hours"] = None
            return

        if frequency_type == "monthly":
            if not interval or int(interval) < 1:
                raise_tracker_error("INVALID_FREQUENCY_CONFIG", "Monthly frequency requires frequency_interval >= 1.")
            if not day_of_month or int(day_of_month) < 1 or int(day_of_month) > 31:
                raise_tracker_error("INVALID_FREQUENCY_CONFIG", "Monthly frequency requires day_of_month between 1 and 31.")
            attrs["frequency_days"] = days 
            attrs["frequency_times_per_period"] = None
            attrs["frequency_period"] = None
            attrs["day_of_week"] = None
            attrs["interval_hours"] = None
            return

        if frequency_type == "hourly":
            if not interval_hours or int(interval_hours) < 1:
                raise_tracker_error("INVALID_FREQUENCY_CONFIG", "Hourly frequency requires interval_hours >= 1.")
            attrs["frequency_interval"] = int(interval_hours or 1)
            attrs["frequency_days"] = days
            attrs["frequency_times_per_period"] = None
            attrs["frequency_period"] = None
            attrs["day_of_week"] = None
            attrs["day_of_month"] = None
            return

        if frequency_type == "custom":
            if days:
                raise_tracker_error(
                    "INVALID_FREQUENCY_CONFIG",
                    "Custom does not support weekday selection. Use weekly frequency for selected weekdays.",
                )

            times_mode = times_per_period is not None

            enabled_modes = int(times_mode)
            if enabled_modes != 1:
                raise_tracker_error(
                    "INVALID_FREQUENCY_CONFIG",
                    "Custom must use exactly one mode: N times per period OR every N days.",
                )

            # common cleanup
            attrs["frequency_days"] = []
            attrs["day_of_week"] = None
            attrs["day_of_month"] = None
            attrs["interval_hours"] = None

            if times_mode:
                if int(times_per_period) < 1:
                    raise_tracker_error("INVALID_FREQUENCY_CONFIG", "frequency_times_per_period must be >= 1.")
                if period not in {"day", "week", "month"}:
                    raise_tracker_error("INVALID_FREQUENCY_CONFIG", "frequency_period must be one of: day, week, month.")
                attrs["frequency_interval"] = int(interval or 1)  # keep NOT NULL DB column safe
                return

            return

    def to_representation(self, instance):
        data = super().to_representation(instance)
        start = instance.start_date.isoformat() if instance.start_date else None
        end = instance.end_date.isoformat() if instance.end_date else None

        if "occurrences" in data and start:
            data["occurrences"] = [
                o for o in data["occurrences"]
                if o.get("scheduled_date")
                and not o.get("is_deleted", False)
                and o["scheduled_date"] >= start
                and (end is None or o["scheduled_date"] <= end)
            ]
        return data

    def validate(self, attrs):
        self.validate_parent_assignment(attrs)
        self.validate_active_parents(attrs)
        self._normalize_frequency_payload(attrs)
        self.validate_frequency(attrs, require_end_date=True)
        self.validate_time_window(attrs)
        return attrs

    def validate_active_parents(self, attrs):
        milestone = attrs.get("milestone")
        if milestone and milestone.status == "cancelled":
            raise_tracker_error("MILESTONE_CANCELLED", "Cannot assign a cancelled milestone to a task.")
        goal = attrs.get("goal")
        if goal and goal.status == "cancelled":
            raise_tracker_error("GOAL_CANCELLED", "Cannot assign a cancelled goal to a task.")

    def _default_end_time(self, start_time):
        dt = datetime.combine(date.today(), start_time) + timedelta(hours=1)
        if dt.date() != date.today():
            return datetime.combine(date.today(), datetime.max.time().replace(hour=23, minute=59, second=0, microsecond=0)).time()
        return dt.time().replace(second=0, microsecond=0)

    def validate_time_window(self, attrs):
        start_time = attrs.get("start_time", getattr(self.instance, "start_time", None))
        end_time = attrs.get("end_time", getattr(self.instance, "end_time", None))
        if start_time is None:
            raise_tracker_error("START_TIME_REQUIRED", "start_time is required.")
        if not end_time:
            end_time = self._default_end_time(start_time)
            attrs["end_time"] = end_time
        if end_time < start_time:
            # If client sent +1h and wrapped past midnight (e.g., 23:30 -> 00:30),
            # cap to end-of-day for same-day schedule semantics.
            attrs["end_time"] = datetime.max.time().replace(hour=23, minute=59, second=59, microsecond=0)
            end_time = attrs["end_time"]
        if start_time == end_time:
            raise_tracker_error("INVALID_TIME_WINDOW", "start_time and end_time cannot be the same.")
        if end_time < start_time:
            raise_tracker_error("INVALID_TIME_WINDOW", "end_time must be after start_time.")
        return attrs

    def get_total_occurrences(self, obj):
        return occurrence_stats(obj)["total"]

    def get_completed_occurrences(self, obj):
        return occurrence_stats(obj)["completed"]

    def get_missed_occurrences(self, obj):
        return occurrence_stats(obj)["missed"]
    
    def get_skipped_occurrences(self, obj):
        return occurrence_stats(obj)["skipped"]
    
    def _get_schedule_window(self, old_instance, new_instance, validated_data):
        horizon = timezone.localdate() + timedelta(days=90)
        old_end = old_instance.end_date or horizon
        new_end = new_instance.end_date or horizon
        from_date = min(old_instance.start_date, new_instance.start_date)
        to_date = max(old_end, new_end)
        return from_date, to_date

    @transaction.atomic
    def create(self, validated_data):
        validated_data["user"] = self.context["request"].user
        validated_data["is_habit"] = False
        draft = Task(**validated_data)
        check_entity_schedule_conflicts(draft.user, draft)
        task = super().create(validated_data)
        generate_occurrences(task)
        if task.milestone:
            check_milestone_completion(task.milestone)
        if task.goal:
            check_goal_completion(task.goal)
        return task

    @transaction.atomic
    def update(self, instance, validated_data):
        if instance.status == "cancelled":
            raise_tracker_error("CANNOT_MODIFY_CANCELLED", "Cancelled tasks cannot be modified.")

        schedule_changed = any(f in validated_data for f in self.SCHEDULE_FIELDS)
        old_instance = Task.objects.get(pk=instance.pk)
        task = super().update(instance, validated_data)

        if schedule_changed:
            from_date, to_date = self._get_schedule_window(old_instance, task, validated_data)
            today = timezone.localdate()
            effective_from = max(today, from_date)
            check_entity_schedule_conflicts(
                task.user,
                task,
                from_date=effective_from,
                to_date=to_date,
                exclude_id=task.id,
            )
            try:
                reconcile_occurrences(task, window_from=effective_from, window_to=to_date)
            except TypeError:
                # fallback for any unforeseen issues in reconciliation logic
                generate_occurrences(task, from_date=effective_from, to_date=to_date)
        if task.milestone:
            check_milestone_completion(task.milestone)
        if task.goal:
            check_goal_completion(task.goal)
        return task
