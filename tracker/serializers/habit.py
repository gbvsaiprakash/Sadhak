from django.db import transaction
from rest_framework import serializers

from tracker.exceptions import raise_tracker_error
from tracker.models import Habit, TaskOccurrence
from tracker.serializers.common import TrackerValidationMixin, is_overdue, occurrence_stats
from tracker.services import check_goal_completion, check_milestone_completion, check_time_conflict, generate_occurrences, regenerate_future_occurrences


class HabitOccurrenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskOccurrence
        fields = ("id", "scheduled_date", "scheduled_time", "status", "completed_at", "notes")


class HabitListSerializer(serializers.ModelSerializer):
    next_occurrence = serializers.SerializerMethodField()

    class Meta:
        model = Habit
        fields = ("id", "title", "section", "status", "frequency_type", "start_date", "end_date", "next_occurrence")

    def get_next_occurrence(self, obj):
        stats = occurrence_stats(obj)
        next_occurrence = stats["next_occurrence"]
        return next_occurrence.scheduled_date if next_occurrence else None


class HabitDetailSerializer(HabitListSerializer, TrackerValidationMixin):
    occurrences = HabitOccurrenceSerializer(many=True, read_only=True)
    total_occurrences = serializers.SerializerMethodField()
    completed_occurrences = serializers.SerializerMethodField()
    missed_occurrences = serializers.SerializerMethodField()

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
            "time_of_day",
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
            "occurrences",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("user", "is_habit", "created_at", "updated_at")

    def validate(self, attrs):
        self.validate_parent_assignment(attrs)
        self.validate_frequency(attrs, require_end_date=False)
        if attrs.get("frequency_type") == "once":
            raise_tracker_error("INVALID_FREQUENCY_CONFIG", "Habits must be recurring and cannot use once frequency.")
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
        validated_data["is_habit"] = True
        habit = super().create(validated_data)
        check_time_conflict(habit.user, habit)
        generate_occurrences(habit)
        if habit.milestone:
            check_milestone_completion(habit.milestone)
        elif habit.goal:
            check_goal_completion(habit.goal)
        return habit

    @transaction.atomic
    def update(self, instance, validated_data):
        if instance.status == "stopped":
            raise_tracker_error("CANNOT_MODIFY_CANCELLED", "Stopped habits cannot be modified.")
        habit = super().update(instance, validated_data)
        check_time_conflict(habit.user, habit)
        regenerate_future_occurrences(habit)
        if habit.milestone:
            check_milestone_completion(habit.milestone)
        elif habit.goal:
            check_goal_completion(habit.goal)
        return habit
