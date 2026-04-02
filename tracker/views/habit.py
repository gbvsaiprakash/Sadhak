from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

from tracker.models import Habit, TaskOccurrence
from tracker.serializers import HabitDetailSerializer, HabitListSerializer
from tracker.services import check_goal_completion, check_milestone_completion, generate_occurrences, mark_occurrence
from tracker.views.mixins import TrackerAPIViewMixin


class HabitBaseAPIView(TrackerAPIViewMixin):
    queryset = Habit.objects.filter(is_deleted=False).select_related("goal", "milestone", "user").prefetch_related("occurrences")
    list_serializer_class = HabitListSerializer
    detail_serializer_class = HabitDetailSerializer

    def get_queryset(self):
        if not self.request.user or self.request.user.is_anonymous:
            return self.queryset.none()
        queryset = self.queryset.filter(user=self.request.user)
        return self.filter_common(queryset)

    def get_not_found_code(self):
        return "HABIT_NOT_FOUND"

    def get_habit(self, pk):
        return self.get_object(self.get_queryset(), id=pk)

    def stop_habit(self, habit, is_deleted=False):
        habit.status = "stopped"
        habit.is_deleted = is_deleted
        habit.save(update_fields=["status", "is_deleted", "updated_at"])
        TaskOccurrence.objects.filter(habit_id=habit.id).update(status="stopped", is_deleted=is_deleted, updated_at=timezone.now())
        if habit.milestone:
            check_milestone_completion(habit.milestone)
        if habit.goal:
            check_goal_completion(habit.goal)


class HabitListAPIView(HabitBaseAPIView):
    def get_serializer_class(self):
        return self.list_serializer_class

    def get(self, request):
        serializer = self.get_serializer(self.get_queryset(), many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = self.detail_serializer_class(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        habit = serializer.save()
        return Response(self.detail_serializer_class(habit, context=self.get_serializer_context()).data, status=status.HTTP_201_CREATED)


class HabitDetailAPIView(HabitBaseAPIView):
    def get_serializer_class(self):
        return self.detail_serializer_class

    def get(self, request, pk):
        habit = self.get_habit(pk)
        return Response(self.get_serializer(habit).data, status=status.HTTP_200_OK)

    def put(self, request, pk):
        habit = self.get_habit(pk)
        serializer = self.get_serializer(habit, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request, pk):
        habit = self.get_habit(pk)
        serializer = self.get_serializer(habit, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    def delete(self, request, pk):
        habit = self.get_habit(pk)
        self.stop_habit(habit, is_deleted=True)
        habit.is_deleted = True
        habit.save(update_fields=["is_deleted", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class HabitPauseAPIView(HabitBaseAPIView):
    def patch(self, request, pk):
        habit = self.get_habit(pk)
        habit.status = "paused"
        habit.save(update_fields=["status", "updated_at"])
        return Response(self.detail_serializer_class(habit, context=self.get_serializer_context()).data, status=status.HTTP_200_OK)


class HabitResumeAPIView(HabitBaseAPIView):
    def patch(self, request, pk):
        habit = self.get_habit(pk)
        habit.status = "active"
        habit.save(update_fields=["status", "updated_at"])
        generate_occurrences(habit, from_date=habit.start_date)
        return Response(self.detail_serializer_class(habit, context=self.get_serializer_context()).data, status=status.HTTP_200_OK)


class HabitStopAPIView(HabitBaseAPIView):
    def patch(self, request, pk):
        habit = self.get_habit(pk)
        self.stop_habit(habit)
        return Response(self.detail_serializer_class(habit, context=self.get_serializer_context()).data, status=status.HTTP_200_OK)

class HabitCancelAPIView(HabitBaseAPIView):
    def put(self, request, pk):
        habit = self.get_habit(pk)
        self.stop_habit(habit)

        return Response(
            self.detail_serializer_class(habit, context=self.get_serializer_context()).data,
            status=status.HTTP_200_OK,
        )

class HabitLogAPIView(HabitBaseAPIView):
    def patch(self, request, pk):
        habit = self.get_habit(pk)
        occurrence = TaskOccurrence.objects.filter(habit=habit, id=request.data.get("occurrence_id")).first()
        if occurrence is None:
            return self.finalize_error("HABIT_NOT_FOUND", "Habit occurrence was not found.")
        status_value = request.data.get("status", "completed")
        mark_occurrence(habit, occurrence, status_value, notes=request.data.get("notes"))
        if habit.milestone:
            check_milestone_completion(habit.milestone)
        if habit.goal:
            check_goal_completion(habit.goal)
        return Response(self.detail_serializer_class(habit, context=self.get_serializer_context()).data, status=status.HTTP_200_OK)
