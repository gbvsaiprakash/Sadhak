from django.db import transaction
from rest_framework import serializers

from tracker.exceptions import raise_tracker_error
from tracker.models import Task, TaskOccurrence
from tracker.serializers.common import TrackerValidationMixin, is_overdue, occurrence_stats
from tracker.services import check_goal_completion, check_milestone_completion, check_time_conflict, generate_occurrences, regenerate_future_occurrences


class TaskOccurrenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskOccurrence
        fields = ("id", "scheduled_date", "scheduled_time", "status", "completed_at", "notes")


class TaskListSerializer(serializers.ModelSerializer):
    is_overdue = serializers.SerializerMethodField()
    next_occurrence = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = ("id", "title", "section", "status", "frequency_type", "start_date", "end_date", "is_overdue", "next_occurrence")

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
            "start_date",
            "end_date",
            "time_of_day",
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
        self.validate_frequency(attrs, require_end_date=True)
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
        task = super().create(validated_data)
        check_time_conflict(task.user, task)
        generate_occurrences(task)
        if task.milestone:
            check_milestone_completion(task.milestone)
        elif task.goal:
            check_goal_completion(task.goal)
        return task

    @transaction.atomic
    def update(self, instance, validated_data):
        if instance.status == "cancelled":
            raise_tracker_error("CANNOT_MODIFY_CANCELLED", "Cancelled tasks cannot be modified.")
        task = super().update(instance, validated_data)
        check_time_conflict(task.user, task)
        regenerate_future_occurrences(task)
        if task.milestone:
            check_milestone_completion(task.milestone)
        elif task.goal:
            check_goal_completion(task.goal)
        return task
