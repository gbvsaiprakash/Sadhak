from datetime import date, datetime, timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from tracker.constants import REMINDER_MODE_SET
from tracker.exceptions import raise_tracker_error
from tracker.models import Habit, TaskOccurrence
from tracker.serializers.common import TrackerValidationMixin, occurrence_stats, DependencyItemSerializer
from tracker.services import (
    check_entity_schedule_conflicts,
    check_goal_completion,
    check_milestone_completion,
    generate_occurrences,
    regenerate_future_occurrences,
    reconcile_occurrences,
    sync_occurrence_reminders_for_parent,
)
from tracker.services.dependency import get_dependencies, set_dependencies


class HabitOccurrenceSerializer(serializers.ModelSerializer):
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


class HabitListSerializer(serializers.ModelSerializer):
    next_occurrence = serializers.SerializerMethodField()

    class Meta:
        model = Habit
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
            "next_occurrence",
        )

    def get_next_occurrence(self, obj):
        stats = occurrence_stats(obj)
        next_occurrence = stats["next_occurrence"]
        return next_occurrence.scheduled_date if next_occurrence else None


class HabitDetailSerializer(HabitListSerializer, TrackerValidationMixin):
    occurrences = HabitOccurrenceSerializer(many=True, read_only=True)
    total_occurrences = serializers.SerializerMethodField()
    completed_occurrences = serializers.SerializerMethodField()
    missed_occurrences = serializers.SerializerMethodField()
    conflict_override = serializers.BooleanField(write_only=True, required=False, default=False)
    conflict_override_reason = serializers.CharField(write_only=True, required=False, allow_blank=False)
    dependencies = DependencyItemSerializer(many=True, write_only=True, required=False)
    dependency_items = serializers.SerializerMethodField(read_only=True)
    duration_value = serializers.IntegerField(write_only=True, required=False, min_value=1)
    duration_unit = serializers.ChoiceField(write_only=True, required=False, choices=("minutes", "hours"))
    duration = serializers.SerializerMethodField(read_only=True)



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
        "duration_value",
        "duration_unit",
        "duration",
        "day_of_week",
        "day_of_month",
        "interval_hours",
    }
    REMINDER_FIELDS = {"reminder_enabled", "reminder_mode_all", "reminder_offset"}
    VALID_WEEKDAYS = {1,2,3,4,5,6,0} #{"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
    VALID_MONTHDAYS = set(range(1, 32))

    class Meta(HabitListSerializer.Meta):
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
            "start_time",
            "end_time",
            "duration_value",
            "duration_unit",
            "duration",
            "day_of_week",
            "day_of_month",
            "interval_hours",
            "start_date",
            "end_date",
            "is_habit",
            "next_occurrence",
            "total_occurrences",
            "completed_occurrences",
            "missed_occurrences",
            "reminder_enabled",
            "reminder_mode_all",
            "reminder_offset",
            "occurrences",
            "dependencies",
            "dependency_items",
            "created_at",
            "updated_at",
            "conflict_override",
            "conflict_override_reason",

        )
        read_only_fields = ("user", "is_habit", "created_at", "updated_at")

    def _effective(self, attrs, key, default=None):
        if key in attrs:
            return attrs.get(key)
        if self.instance is not None:
            return getattr(self.instance, key, default)
        return default
    
    def get_duration(self, obj):
        cfg = obj.duration_config or {"value": 30, "unit": "minutes"}
        return {"value": int(cfg.get("value", 30)), "unit": cfg.get("unit", "minutes")}

    def _effective_duration_config(self, attrs):
        v = attrs.pop("duration_value", None)
        u = attrs.pop("duration_unit", None)

        if v is not None and u is None:
            raise_tracker_error("INVALID_DURATION", "duration_unit is required when duration_value is provided.")
        if u is not None and v is None:
            raise_tracker_error("INVALID_DURATION", "duration_value is required when duration_unit is provided.")

        if v is not None and u is not None:
            attrs["duration_config"] = {"value": int(v), "unit": u}
            return

        if self.instance is not None and getattr(self.instance, "duration_config", None):
            attrs["duration_config"] = self.instance.duration_config
        else:
            attrs["duration_config"] = {"value": 30, "unit": "minutes"}

    def normalize_reminder_payload(self, reminder_enabled, reminder_mode_all, reminder_offset):
        if not reminder_enabled:
            return []

        items = reminder_offset or [{"value": 30, "unit": "minutes", "modes": ["in-app"]}]
        if not isinstance(items, list):
            raise serializers.ValidationError({"reminder_offset": "Must be a list."})

        normalized = []
        seen = set()
        shared_modes = None

        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                raise serializers.ValidationError({"reminder_offset": f"Item {idx} must be object."})

            value = item.get("value")
            unit = item.get("unit")
            modes = item.get("modes")

            # backward compatibility: old payload with single mode
            if not modes and item.get("mode"):
                modes = [item.get("mode")]

            if not isinstance(value, int) or value <= 0:
                raise serializers.ValidationError({"reminder_offset": f"Item {idx}: value must be positive integer."})
            if unit not in ("minutes", "hours"):
                raise serializers.ValidationError({"reminder_offset": f"Item {idx}: unit must be minutes or hours."})
            if not isinstance(modes, list) or not modes:
                raise serializers.ValidationError({"reminder_offset": f"Item {idx}: modes must be non-empty list."})

            # validate + normalize modes
            clean_modes = sorted(set(modes))
            if any(m not in REMINDER_MODE_SET for m in clean_modes):
                raise serializers.ValidationError({"reminder_offset": f"Item {idx}: invalid mode in modes."})

            if reminder_mode_all:
                shared_modes = shared_modes or clean_modes
                clean_modes = shared_modes

            mins = value * 60 if unit == "hours" else value
            for m in clean_modes:
                dedupe_key = (mins, m)
                if dedupe_key in seen:
                    raise serializers.ValidationError({"reminder_offset": "Duplicate reminder entries (time + mode)."})
                seen.add(dedupe_key)

            normalized.append({"value": value, "unit": unit, "modes": clean_modes})

        return normalized
    
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
                    "Custom must use exactly one mode: N times per period.",
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
        normalized = []
        for item in (data.get("reminder_offset") or []):
            # backward compatibility for old stored records
            if "modes" not in item:
                if item.get("mode"):
                    item["modes"] = [item["mode"]]
                else:
                    item["modes"] = ["in-app"]
            item.pop("mode", None)  # enforce new API contract
            normalized.append(item)

        data["reminder_offset"] = normalized
        return data

    def validate(self, attrs):
        if attrs["section"] == "personal":
            attrs["goal"] = None
            attrs["milestone"] = None
        else:
            self.validate_parent_assignment(attrs)
            self.validate_active_parents(attrs)
        self._normalize_frequency_payload(attrs)
        self.validate_frequency(attrs, require_end_date=False)
        self._effective_duration_config(attrs)
        self.validate_time_window(attrs)
        if self._effective(attrs, "frequency_type") == "once":
            raise_tracker_error("INVALID_FREQUENCY_CONFIG", "Habits must be recurring and cannot use once frequency.")
        reminder_enabled = attrs.get("reminder_enabled", getattr(self.instance, "reminder_enabled", False))
        reminder_mode_all = attrs.get("reminder_mode_all", getattr(self.instance, "reminder_mode_all", True))
        reminder_offset = attrs.get("reminder_offset", getattr(self.instance, "reminder_offset", []))

        attrs["reminder_offset"] = self.normalize_reminder_payload(
            reminder_enabled=reminder_enabled,
            reminder_mode_all=reminder_mode_all,
            reminder_offset=reminder_offset,
        )
        return attrs

    def validate_active_parents(self, attrs):
        milestone = attrs.get("milestone")
        if milestone and milestone.status == "cancelled":
            raise_tracker_error("MILESTONE_CANCELLED", "Cannot assign a cancelled milestone to a habit.")
        goal = attrs.get("goal")
        if goal and goal.status == "cancelled":
            raise_tracker_error("GOAL_CANCELLED", "Cannot assign a cancelled goal to a habit.")

    # def _default_end_time(self, start_time):
    #     dt = datetime.combine(date.today(), start_time) + timedelta(hours=1)
    #     if dt.date() != date.today():
    #         return datetime.combine(date.today(), datetime.max.time().replace(hour=23, minute=59, second=0, microsecond=0)).time()
    #     return dt.time().replace(second=0, microsecond=0)

    def validate_time_window(self, attrs):
        start_time = attrs.get("start_time", getattr(self.instance, "start_time", None))
        end_time = attrs.get("end_time", getattr(self.instance, "end_time", None))

        if start_time is None:
            raise_tracker_error("START_TIME_REQUIRED", "start_time is required.")

        cfg = attrs.get("duration_config") or getattr(self.instance, "duration_config", None) or {"value": 30, "unit": "minutes"}
        try:
            value = int(cfg.get("value", 30) or 30)
        except (TypeError, ValueError):
            raise_tracker_error("INVALID_DURATION", "duration value must be a positive integer.")
        unit = str(cfg.get("unit", "minutes")).lower()

        if value < 1 or unit not in {"minutes", "hours"}:
            raise_tracker_error("INVALID_DURATION", "duration must be valid (minutes/hours) and >= 1.")

        mins = value * 60 if unit == "hours" else value
        start_dt = datetime.combine(timezone.localdate(), start_time)
        end_dt = start_dt + timedelta(minutes=mins)
        if end_dt.date() != start_dt.date():
            raise_tracker_error(
                "INVALID_DURATION",
                "This start_time and duration crosses midnight. Please reduce duration or change start_time.",
            )
        
        if end_time is None:
            return attrs
        
        start_date = attrs.get("start_date", getattr(self.instance, "start_date", None))
        end_date = attrs.get("end_date", getattr(self.instance, "end_date", None))

        # only validate absolute schedule window if end_date exists
        if start_date and end_date:
            start_dt_abs = datetime.combine(start_date, start_time)
            end_dt_abs = datetime.combine(end_date, end_time)
            if end_dt_abs <= start_dt_abs:
                raise_tracker_error("INVALID_TIME_WINDOW", "Task end boundary must be after task start.")

        return attrs

    def get_dependency_items(self, obj):
        return get_dependencies(obj)
    
    def get_total_occurrences(self, obj):
        return occurrence_stats(obj)["total"]

    def get_completed_occurrences(self, obj):
        return occurrence_stats(obj)["completed"]

    def get_missed_occurrences(self, obj):
        return occurrence_stats(obj)["missed"]

    def _get_schedule_window(self, old_instance, new_instance, validated_data):
        horizon = timezone.localdate() + timedelta(days=90)
        old_end = old_instance.end_date or horizon
        new_end = new_instance.end_date or horizon
        from_date = min(old_instance.start_date, new_instance.start_date)
        to_date = max(old_end, new_end)
        return from_date, to_date
    
    def _changed(self, instance, attrs, fields):
        for f in fields:
            if f in attrs and attrs.get(f) != getattr(instance, f):
                return True
        return False

    @transaction.atomic
    def create(self, validated_data):
        deps = validated_data.pop("dependencies", None)
        validated_data["user"] = self.context["request"].user
        validated_data["is_habit"] = True
        draft = Habit(**validated_data)
        override = validated_data.pop("conflict_override", False)
        reason = validated_data.pop("conflict_override_reason", None)

        # check_entity_schedule_conflicts(draft.user, draft)
        try:
            check_entity_schedule_conflicts(draft.user, draft, allow_habit_habit_override=override)
        except Exception as exc:
            code = getattr(exc, "code", None)
            if code != "HABIT_CONFLICT_OVERRIDE_REQUIRED":
                raise
            if not override:
                raise
            if not reason:
                raise_tracker_error("CONFLICT_OVERRIDE_REASON_REQUIRED", "Please provide conflict_override_reason.")
            check_entity_schedule_conflicts(draft.user, draft, allow_habit_habit_override=True)

        habit = super().create(validated_data)
        if deps is not None:
            set_dependencies(habit, deps, self.context["request"].user)
        habit.conflict_override = bool(override)
        habit.conflict_override_reason = reason if override else None
        habit.conflict_overridden_at = timezone.now() if override else None
        habit.save(update_fields=["conflict_override", "conflict_override_reason", "conflict_overridden_at", "updated_at"])

        generate_occurrences(habit)
        if habit.milestone:
            check_milestone_completion(habit.milestone)
        if habit.goal:
            check_goal_completion(habit.goal)
        return habit

    @transaction.atomic
    def update(self, instance, validated_data):
        if instance.status == "stopped":
            raise_tracker_error("CANNOT_MODIFY_CANCELLED", "Stopped habits cannot be modified.")

        override = validated_data.pop("conflict_override", False)
        reason = validated_data.pop("conflict_override_reason", None)
        deps = validated_data.pop("dependencies", None)
        schedule_changed = self._changed(instance, validated_data, self.SCHEDULE_FIELDS)
        reminder_changed = self._changed(instance, validated_data, self.REMINDER_FIELDS)
        old_instance = Habit.objects.get(pk=instance.pk)
        habit = super().update(instance, validated_data)
        if deps is not None:
            set_dependencies(habit, deps, self.context["request"].user)

        if schedule_changed:
            from_date, to_date = self._get_schedule_window(old_instance, habit, validated_data)
            effective_from = max(timezone.localdate(), from_date)
            # check_entity_schedule_conflicts(habit.user, habit, from_date=from_date, to_date=to_date)
            try:
                check_entity_schedule_conflicts(
                    habit.user,
                    habit,
                    from_date=effective_from,
                    to_date=to_date,
                    exclude_id=habit.id,
                    allow_habit_habit_override=False,
                )
            except Exception as exc:
                code = getattr(exc, "code", None)
                if code != "HABIT_CONFLICT_OVERRIDE_REQUIRED":
                    raise
                if not override:
                    raise
                if not reason:
                    raise_tracker_error(
                        "CONFLICT_OVERRIDE_REASON_REQUIRED",
                        "Please provide conflict_override_reason to proceed with overlap override.",
                    )

                check_entity_schedule_conflicts(
                    habit.user,
                    habit,
                    from_date=effective_from,
                    to_date=to_date,
                    exclude_id=habit.id,
                    allow_habit_habit_override=True,
                )
            try:
                # regenerate_future_occurrences(habit, from_date=from_date)
                reconcile_occurrences(habit, window_from=effective_from, window_to=to_date)
            except TypeError:
                # regenerate_future_occurrences(habit)
                generate_occurrences(habit, from_date=effective_from, to_date=to_date)
        elif reminder_changed:
            sync_occurrence_reminders_for_parent(habit)
        
        habit.conflict_override = bool(override)
        habit.conflict_override_reason = reason if override else None
        habit.conflict_overridden_at = timezone.now() if override else None
        habit.save(
            update_fields=[
                "conflict_override",
                "conflict_override_reason",
                "conflict_overridden_at",
                "updated_at",
            ]
        )
        if habit.milestone:
            check_milestone_completion(habit.milestone)
        if habit.goal:
            check_goal_completion(habit.goal)
        return habit
