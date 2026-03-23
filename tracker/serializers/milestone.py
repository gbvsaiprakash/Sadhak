from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from tracker.exceptions import raise_tracker_error
from tracker.models import Milestone
from tracker.serializers.common import is_overdue
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
        active_children = list(obj.tasks.exclude(status="cancelled")) + list(obj.habits.exclude(status="stopped"))
        if not active_children:
            return 0
        completed = sum(1 for item in active_children if item.status in {"completed", "stopped"})
        return int((completed / len(active_children)) * 100)

    def get_is_overdue(self, obj):
        return is_overdue(obj)


class MilestoneDetailSerializer(MilestoneListSerializer):
    tasks = TaskListSerializer(many=True, read_only=True)
    habits = HabitListSerializer(many=True, read_only=True)

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
            has_children = instance.tasks.exclude(status="cancelled").exists() or instance.habits.exclude(status="stopped").exists()
            unresolved_children = instance.tasks.filter(status__in={"pending", "in_progress"}).exists() or instance.habits.filter(status__in={"active", "paused"}).exists()
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
