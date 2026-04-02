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
)


class TaskOccurrenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskOccurrence
        fields = (
            "id",
            "scheduled_date",
            "scheduled_time",
            "status",
            "completed_at",
            "notes",
            "context_title",
            "context_description",
            "context_checklist",
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
            "occurrences",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("user", "is_habit", "created_at", "updated_at")

    def validate(self, attrs):
        self.validate_parent_assignment(attrs)
        self.validate_active_parents(attrs)
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

    @transaction.atomic
    def create(self, validated_data):
        validated_data["user"] = self.context["request"].user
        validated_data["is_habit"] = False
        # Collision check before writing anything
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
        schedule_fields = {
            "frequency_type",
            "frequency_interval",
            "frequency_days",
            "frequency_times_per_period",
            "frequency_period",
            "start_date",
            "end_date",
            "start_time",
            "end_time",
        }
        schedule_changed = any(f in validated_data for f in schedule_fields)

        old_end_date = instance.end_date
        task = super().update(instance, validated_data)

        if schedule_changed:
            today = timezone.localdate()
            from_date = today
            to_date = None
            if "end_date" in validated_data and old_end_date and task.end_date and task.end_date > old_end_date and not any(
                f in validated_data for f in (schedule_fields - {"end_date"})
            ):
                from_date = max(today, old_end_date + timedelta(days=1))
                to_date = task.end_date
            check_entity_schedule_conflicts(task.user, task, from_date=from_date, to_date=to_date, exclude_id=task.id)
            regenerate_future_occurrences(task)
        if task.milestone:
            check_milestone_completion(task.milestone)
        if task.goal:
            check_goal_completion(task.goal)
        return task
