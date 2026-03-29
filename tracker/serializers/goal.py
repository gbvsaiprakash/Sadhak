from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from tracker.exceptions import raise_tracker_error
from tracker.models import Goal
from tracker.serializers.common import is_overdue, _get_occurrence_units
from tracker.serializers.habit import HabitListSerializer
from tracker.serializers.milestone import MilestoneListSerializer
from tracker.serializers.task import TaskListSerializer
from tracker.services import check_goal_completion


class GoalListSerializer(serializers.ModelSerializer):
    completion_percentage = serializers.SerializerMethodField()
    is_overdue = serializers.SerializerMethodField()

    class Meta:
        model = Goal
        fields = ("id", "section", "title", "status", "start_date", "expected_end_date", "completion_percentage", "is_overdue", "override_completed")

    def get_completion_percentage(self, obj):
        total_units = 0
        completed_units = 0

        # --- Milestones ---
        milestones = obj.milestones.filter(
            is_deleted=False
        ).exclude(
            status__in={"cancelled"}
        ).prefetch_related(
            'tasks__occurrences',
            'habits__occurrences'
        )

        for milestone in milestones:
            m_total, m_completed = _get_occurrence_units(
                milestone.tasks.filter(is_deleted=False).exclude(status="cancelled").prefetch_related('occurrences'),
                milestone.habits.filter(is_deleted=False).exclude(status__in={"stopped", "cancelled"}).prefetch_related('occurrences')
            )
            if m_total == 0:
                # milestone has no children — counts as 1 unit,
                # completed only if overridden or manually completed
                total_units += 1
                if milestone.status in {"completed", "overridden"}:
                    completed_units += 1
            else:
                total_units += m_total
                completed_units += m_completed

        # --- Root-level Tasks (not under any milestone) ---
        root_tasks = obj.tasks.filter(
            is_deleted=False,
            milestone__isnull=True
        ).exclude(
            status="cancelled"
        ).prefetch_related('occurrences')

        # --- Root-level Habits (not under any milestone) ---
        root_habits = obj.habits.filter(
            is_deleted=False,
            milestone__isnull=True
        ).exclude(
            status__in={"stopped", "cancelled"}
        ).prefetch_related('occurrences')

        r_total, r_completed = _get_occurrence_units(root_tasks, root_habits)
        total_units += r_total
        completed_units += r_completed

        if total_units == 0:
            return 0

        return int((completed_units / total_units) * 100)

    def get_is_overdue(self, obj):
        return is_overdue(obj)


class GoalDetailSerializer(GoalListSerializer):
    milestones = MilestoneListSerializer(many=True, read_only=True)
    tasks = serializers.SerializerMethodField()
    habits = serializers.SerializerMethodField()
    all_tasks = serializers.SerializerMethodField()
    all_habits = serializers.SerializerMethodField()

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
            "all_tasks",
            "all_habits",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("user", "achieved_date", "created_at", "updated_at")

    def get_tasks(self, obj):
        queryset = obj.tasks.filter(is_deleted=False,milestone__isnull=True)
        return TaskListSerializer(queryset, many=True, context=self.context).data

    def get_habits(self, obj):
        queryset = obj.habits.filter(is_deleted=False,milestone__isnull=True)
        return HabitListSerializer(queryset, many=True, context=self.context).data

    def get_all_tasks(self, obj):
        queryset = obj.tasks.filter(is_deleted=False)
        return TaskListSerializer(queryset, many=True, context=self.context).data

    def get_all_habits(self, obj):
        queryset = obj.habits.filter(is_deleted=False)
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
                instance.milestones.filter(is_deleted=False).exclude(status="cancelled").exists()
                or instance.tasks.filter(is_deleted=False,milestone__isnull=True).exclude(status="cancelled").exists()
                or instance.habits.filter(is_deleted=False,milestone__isnull=True).exclude(status="stopped").exists()
            )
            unresolved_children = (
                instance.milestones.filter(is_deleted=False,status="active").exists()
                or instance.tasks.filter(is_deleted=False,milestone__isnull=True, status__in={"pending", "in_progress"}).exists()
                or instance.habits.filter(is_deleted=False,milestone__isnull=True, status__in={"active", "paused"}).exists()
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
