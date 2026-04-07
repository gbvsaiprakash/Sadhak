from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import serializers

from tracker.models import Habit, Task, TaskOccurrence, task
from tracker.serializers.task import TaskOccurrenceSerializer
from tracker.serializers.habit import HabitOccurrenceSerializer
from tracker.views.mixins import TrackerAPIViewMixin


class _OccurrenceContextSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskOccurrence
        fields = ("context_title", "context_description", "context_checklist")


class TaskOccurrenceListAPIView(TrackerAPIViewMixin):
    serializer_class = TaskOccurrenceSerializer
    def get(self, request, task_id):
        task = Task.objects.filter(user=request.user, is_deleted=False, id=task_id).first()
        if task is None:
            return self.finalize_error("TASK_NOT_FOUND", "Task was not found.")
        # qs = TaskOccurrence.objects.filter(task=task).order_by("scheduled_date", "scheduled_time", "created_at")
        qs = TaskOccurrence.objects.filter(task=task, scheduled_date__gte=task.start_date)
        if task.end_date:
            qs = qs.filter(scheduled_date__lte=task.end_date)
        qs = qs.order_by("scheduled_date", "scheduled_time", "created_at")
        return Response(TaskOccurrenceSerializer(qs, many=True).data, status=status.HTTP_200_OK)


class HabitOccurrenceListAPIView(TrackerAPIViewMixin):
    serializer_class = HabitOccurrenceSerializer
    def get(self, request, habit_id):
        habit = Habit.objects.filter(user=request.user, is_deleted=False, id=habit_id).first()
        if habit is None:
            return self.finalize_error("HABIT_NOT_FOUND", "Habit was not found.")
        # qs = TaskOccurrence.objects.filter(habit=habit).order_by("scheduled_date", "scheduled_time", "created_at")
        qs = TaskOccurrence.objects.filter(habit=habit, scheduled_date__gte=habit.start_date)
        if habit.end_date:
            qs = qs.filter(scheduled_date__lte=habit.end_date)
        qs = qs.order_by("scheduled_date", "scheduled_time", "created_at")
        return Response(HabitOccurrenceSerializer(qs, many=True).data, status=status.HTTP_200_OK)


class TaskOccurrenceContextAPIView(TrackerAPIViewMixin):
    serializer_class = _OccurrenceContextSerializer
    def patch(self, request, task_id, pk):
        task = Task.objects.filter(user=request.user, is_deleted=False, id=task_id).first()
        if task is None:
            return self.finalize_error("TASK_NOT_FOUND", "Task was not found.")
        occurrence = TaskOccurrence.objects.filter(task=task, id=pk).first()
        if occurrence is None:
            return self.finalize_error("TASK_NOT_FOUND", "Task occurrence was not found.")
        serializer = _OccurrenceContextSerializer(occurrence, data=request.data, partial=True, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(TaskOccurrenceSerializer(occurrence).data, status=status.HTTP_200_OK)


class HabitOccurrenceContextAPIView(TrackerAPIViewMixin):
    serializer_class = _OccurrenceContextSerializer
    def patch(self, request, habit_id, pk):
        habit = Habit.objects.filter(user=request.user, is_deleted=False, id=habit_id).first()
        if habit is None:
            return self.finalize_error("HABIT_NOT_FOUND", "Habit was not found.")
        occurrence = TaskOccurrence.objects.filter(habit=habit, id=pk).first()
        if occurrence is None:
            return self.finalize_error("HABIT_NOT_FOUND", "Habit occurrence was not found.")
        serializer = _OccurrenceContextSerializer(occurrence, data=request.data, partial=True, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(HabitOccurrenceSerializer(occurrence).data, status=status.HTTP_200_OK)

