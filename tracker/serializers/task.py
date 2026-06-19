from datetime import date, datetime, timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from tracker.constants import REMINDER_MODE_SET
from tracker.exceptions import raise_tracker_error
from tracker.models import Task, TaskOccurrence
from tracker.serializers.common import TrackerValidationMixin, is_overdue, occurrence_stats, DependencyItemSerializer
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
from integrations.tasks import sync_parent_occurrences_to_google_task, sync_parent_reminders_to_google_task


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
    dependencies = DependencyItemSerializer(many=True, write_only=True, required=False)
    dependency_items = serializers.SerializerMethodField(read_only=True)

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
        "duration_config",
        "day_of_week",
        "day_of_month",
        "interval_hours",
    }
    REMINDER_FIELDS = {"reminder_enabled", "reminder_mode_all", "reminder_offset"}
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
            "duration_config",
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
            "reminder_enabled",
            "reminder_mode_all",
            "reminder_offset",
            "occurrences",
            "dependencies",
            "dependency_items",
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
    
    def get_duration(self, obj):
        cfg = obj.duration_config or {"value": 30, "unit": "minutes"}
        unit = str(cfg.get("unit", "minutes")).lower()
        if unit not in {"minutes", "hours"}:
            unit = "minutes"
        try:
            value = int(cfg.get("value", 30) or 30)
        except (TypeError, ValueError):
            value = 30
        return {"value": value, "unit": unit}

    def _normalize_duration_config(self, config=None):
        cfg = config or {"value": 30, "unit": "minutes"}
        try:
            value = int(cfg.get("value", 30) or 30)
        except (TypeError, ValueError):
            value = 30
        unit = str(cfg.get("unit", "minutes")).lower()
        if unit not in {"minutes", "hours"}:
            unit = "minutes"
        return {"value": value, "unit": unit}

    def _effective_duration_config(self, attrs):
        v = attrs.pop("duration_value", None)
        u = attrs.pop("duration_unit", None)

        if v is not None and u is None:
            raise_tracker_error("INVALID_DURATION", "duration_unit is required when duration_value is provided.")
        if u is not None and v is None:
            raise_tracker_error("INVALID_DURATION", "duration_value is required when duration_unit is provided.")

        if v is not None and u is not None:
            attrs["duration_config"] = self._normalize_duration_config({"value": v, "unit": u})
            return
        
        if attrs.get("duration_config") is not None:
            attrs["duration_config"] = self._normalize_duration_config(attrs.get("duration_config"))
            return

        if self.instance is not None and getattr(self.instance, "duration_config", None):
            attrs["duration_config"] = self._normalize_duration_config(self.instance.duration_config)
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
        data["duration_config"] = self._normalize_duration_config(data.get("duration_config"))
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
        self.validate_frequency(attrs, require_end_date=True)
        self._effective_duration_config(attrs)
        self.validate_time_window(attrs)
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
            raise_tracker_error("MILESTONE_CANCELLED", "Cannot assign a cancelled milestone to a task.")
        goal = attrs.get("goal")
        if goal and goal.status == "cancelled":
            raise_tracker_error("GOAL_CANCELLED", "Cannot assign a cancelled goal to a task.")

    def default_duration_config():
        return {"value": 30, "unit": "minutes"}

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
    
    def get_skipped_occurrences(self, obj):
        return occurrence_stats(obj)["skipped"]
    
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
        validated_data["user"] = self.context["request"].user
        validated_data["is_habit"] = False
        deps = validated_data.pop("dependencies", None)
        draft = Task(**validated_data)
        check_entity_schedule_conflicts(draft.user, draft)
        task = super().create(validated_data)
        if deps is not None:
            set_dependencies(task, deps, self.context["request"].user)
        generate_occurrences(task)
        transaction.on_commit(lambda: sync_parent_occurrences_to_google_task.delay("task", str(task.id), str(task.user.user_id), "primary"))
        if task.milestone:
            check_milestone_completion(task.milestone)
        if task.goal:
            check_goal_completion(task.goal)
        return task

    @transaction.atomic
    def update(self, instance, validated_data):
        if instance.status == "cancelled":
            raise_tracker_error("CANNOT_MODIFY_CANCELLED", "Cancelled tasks cannot be modified.")
        deps = validated_data.pop("dependencies", None)
        schedule_changed = self._changed(instance, validated_data, self.SCHEDULE_FIELDS)
        reminder_changed = self._changed(instance, validated_data, self.REMINDER_FIELDS)
        old_instance = Task.objects.get(pk=instance.pk)
        task = super().update(instance, validated_data)
        self._schedule_changed = schedule_changed
        self._reminder_changed = reminder_changed and not schedule_changed
        if deps is not None:
            set_dependencies(task, deps, self.context["request"].user)

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
            transaction.on_commit(lambda: sync_parent_occurrences_to_google_task.delay("task", str(task.id), str(task.user.user_id), "primary"))
        elif reminder_changed:
            sync_occurrence_reminders_for_parent(task)
            transaction.on_commit(lambda: sync_parent_reminders_to_google_task.delay("task", str(task.id), str(task.user.user_id), "primary"))
        if task.milestone:
            check_milestone_completion(task.milestone)
        if task.goal:
            check_goal_completion(task.goal)
        return task
