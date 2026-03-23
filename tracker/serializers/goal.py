from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from tracker.exceptions import raise_tracker_error
from tracker.models import Goal
from tracker.serializers.common import is_overdue
from tracker.serializers.habit import HabitListSerializer
from tracker.serializers.milestone import MilestoneListSerializer
from tracker.serializers.task import TaskListSerializer
from tracker.services import check_goal_completion


class GoalListSerializer(serializers.ModelSerializer):
    completion_percentage = serializers.SerializerMethodField()
    is_overdue = serializers.SerializerMethodField()

    class Meta:
        model = Goal
        fields = ("id", "section", "title", "status", "start_date", "expected_end_date", "completion_percentage", "is_overdue")

    def get_completion_percentage(self, obj):
        active_children = (
            list(obj.milestones.exclude(status="cancelled"))
            + list(obj.tasks.filter(milestone__isnull=True).exclude(status="cancelled"))
            + list(obj.habits.filter(milestone__isnull=True).exclude(status="stopped"))
        )
        if not active_children:
            return 0
        completed = sum(1 for item in active_children if item.status in {"completed", "stopped"})
        return int((completed / len(active_children)) * 100)

    def get_is_overdue(self, obj):
        return is_overdue(obj)


class GoalDetailSerializer(GoalListSerializer):
    milestones = MilestoneListSerializer(many=True, read_only=True)
    tasks = serializers.SerializerMethodField()
    habits = serializers.SerializerMethodField()

    class Meta(GoalListSerializer.Meta):
        fields = (
            "id",
            "user",
            "section",
            "title",
            "description",
            "status",
            "override_completed",
            "start_date",
            "expected_end_date",
            "achieved_date",
            "completion_percentage",
            "is_overdue",
            "milestones",
            "tasks",
            "habits",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("user", "achieved_date", "created_at", "updated_at")

    def get_tasks(self, obj):
        queryset = obj.tasks.filter(milestone__isnull=True)
        return TaskListSerializer(queryset, many=True, context=self.context).data

    def get_habits(self, obj):
        queryset = obj.habits.filter(milestone__isnull=True)
        return HabitListSerializer(queryset, many=True, context=self.context).data

    @transaction.atomic
    def create(self, validated_data):
        validated_data["user"] = self.context["request"].user
        return super().create(validated_data)

    @transaction.atomic
    def update(self, instance, validated_data):
        if instance.status == "cancelled":
            raise_tracker_error("CANNOT_MODIFY_CANCELLED", "Cancelled goals cannot be modified.")
        if validated_data.get("override_completed") and instance.status in {"completed", "cancelled"}:
            raise_tracker_error("OVERRIDE_CONFLICT", "Override cannot be applied to this goal.")
        if validated_data.get("status") == "completed" and not validated_data.get("override_completed", instance.override_completed):
            has_children = (
                instance.milestones.exclude(status="cancelled").exists()
                or instance.tasks.filter(milestone__isnull=True).exclude(status="cancelled").exists()
                or instance.habits.filter(milestone__isnull=True).exclude(status="stopped").exists()
            )
            unresolved_children = (
                instance.milestones.filter(status="active").exists()
                or instance.tasks.filter(milestone__isnull=True, status__in={"pending", "in_progress"}).exists()
                or instance.habits.filter(milestone__isnull=True, status__in={"active", "paused"}).exists()
            )
            if (not has_children) or unresolved_children:
                raise_tracker_error(
                    "COMPLETION_BLOCKED",
                    "Goal cannot be marked completed while active child items remain.",
                )
        goal = super().update(instance, validated_data)
        if goal.override_completed:
            goal.status = "completed"
            goal.achieved_date = timezone.localdate()
            goal.save(update_fields=["status", "achieved_date", "updated_at"])
        check_goal_completion(goal)
        return goal
