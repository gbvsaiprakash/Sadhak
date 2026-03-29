from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from tracker.exceptions import raise_tracker_error
from tracker.models import Milestone
from tracker.serializers.common import is_overdue, _get_occurrence_units
from tracker.serializers.task import TaskListSerializer
from tracker.serializers.habit import HabitListSerializer
from tracker.services import check_goal_completion, check_milestone_completion


class MilestoneListSerializer(serializers.ModelSerializer):
    completion_percentage = serializers.SerializerMethodField()
    is_overdue = serializers.SerializerMethodField()

    class Meta:
        model = Milestone
        fields = ("id", "title", "status", "start_date", "expected_achieved_date", "completion_percentage", "is_overdue")

    def get_completion_percentage(self, obj):
        tasks = obj.tasks.filter(
            is_deleted=False
        ).exclude(
            status="cancelled"
        ).prefetch_related('occurrences')

        habits = obj.habits.filter(
            is_deleted=False
        ).exclude(
            status__in={"stopped", "cancelled"}
        ).prefetch_related('occurrences')

        total_units, completed_units = _get_occurrence_units(tasks, habits)

        if total_units == 0:
            return 0

        return int((completed_units / total_units) * 100)

    def get_is_overdue(self, obj):
        return is_overdue(obj)


class MilestoneDetailSerializer(MilestoneListSerializer):
    tasks = serializers.SerializerMethodField()
    habits = serializers.SerializerMethodField()

    class Meta(MilestoneListSerializer.Meta):
        fields = (
            "id",
            "goal",
            "title",
            "description",
            "status",
            "override_completed",
            "start_date",
            "expected_achieved_date",
            "achieved_date",
            "completion_percentage",
            "is_overdue",
            "tasks",
            "habits",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("goal", "achieved_date", "created_at", "updated_at")

    @transaction.atomic
    def create(self, validated_data):
        milestone = super().create(validated_data)
        check_goal_completion(milestone.goal)
        return milestone

    @transaction.atomic
    def update(self, instance, validated_data):
        if instance.status == "cancelled":
            raise_tracker_error("CANNOT_MODIFY_CANCELLED", "Cancelled milestones cannot be modified.")
        if validated_data.get("override_completed") and instance.status in {"completed", "cancelled"}:
            raise_tracker_error("OVERRIDE_CONFLICT", "Override cannot be applied to this milestone.")
        if validated_data.get("status") == "completed" and not validated_data.get("override_completed", instance.override_completed):
            has_children = instance.tasks.filter(is_deleted=False).exclude(status="cancelled").exists() or instance.habits.exclude(status="stopped").exists()
            unresolved_children = instance.tasks.filter(is_deleted=False,status__in={"pending", "in_progress"}).exists() or instance.habits.filter(status__in={"active", "paused"}).exists()
            if (not has_children) or unresolved_children:
                raise_tracker_error(
                    "COMPLETION_BLOCKED",
                    "Milestone cannot be marked completed while active child items remain.",
                )
        milestone = super().update(instance, validated_data)
        if milestone.override_completed:
            milestone.status = "completed"
            milestone.achieved_date = timezone.localdate()
            milestone.save(update_fields=["status", "achieved_date", "updated_at"])
        check_milestone_completion(milestone)
        return milestone
    
    def get_tasks(self, obj):
        queryset = obj.tasks.filter(is_deleted=False)
        return TaskListSerializer(queryset, many=True, context=self.context).data

    def get_habits(self, obj):
        queryset = obj.habits.filter(is_deleted=False)
        return HabitListSerializer(queryset, many=True, context=self.context).data
