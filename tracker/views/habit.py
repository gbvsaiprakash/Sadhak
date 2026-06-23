from django.utils import timezone
from rest_framework import request, status
from rest_framework.response import Response
from django.db import transaction
from datetime import timedelta
from tracker.models import Habit, TaskOccurrence, OccurrenceReminder
from tracker.serializers import HabitDetailSerializer, HabitListSerializer
from tracker.services import check_goal_completion, check_milestone_completion, generate_occurrences, mark_occurrence
from tracker.views.mixins import TrackerAPIViewMixin
from tracker.services.dependency import ensure_not_depended_on, list_dependency_candidates, list_dependency_candidates_for_create, soft_delete_owned_dependencies
from integrations.services import _update_rrule_until_date
from integrations.tasks import delete_parent_from_google_task, sync_parent_action_to_google_task, sync_recurring_parent_to_google_task


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
        ensure_not_depended_on(habit)
        habit.status = "stopped"
        habit.is_deleted = is_deleted
        habit.save(update_fields=["status", "is_deleted", "updated_at"])
        TaskOccurrence.objects.filter(habit_id=habit.id, status="pending").update(
            status="stopped",
            is_deleted=is_deleted,
            updated_at=timezone.now(),
        )
        OccurrenceReminder.objects.filter(occurrence__habit_id=habit.id, is_deleted=False).update(is_deleted=True)
        soft_delete_owned_dependencies(habit)
        if habit.milestone:
            check_milestone_completion(habit.milestone)
        if habit.goal:
            check_goal_completion(habit.goal)
    
    # def _ensure_future_occurrences(self, habit):
    #     if habit.is_deleted or habit.status != "active" or habit.end_date is not None:
    #         return

    #     today = timezone.localdate()
    #     horizon_end = today + timedelta(days=90)

    #     has_upcoming_pending = TaskOccurrence.objects.filter(
    #         habit_id=habit.id,
    #         is_deleted=False,
    #         status="pending",
    #         scheduled_date__gte=today,
    #     )

    #     last_scheduled = has_upcoming_pending.order_by("-scheduled_date").values_list("scheduled_date", flat=True).first()
        
    #     if not last_scheduled:
    #         generate_occurrences(
    #             habit,
    #             from_date=today,
    #             to_date=horizon_end,
    #         )
        
    #     if last_scheduled and last_scheduled < horizon_end:
    #         generate_occurrences(
    #             habit,
    #             from_date=last_scheduled + timedelta(days=1),
    #             to_date=horizon_end,
    #         )

class HabitDependencyCandidatesAPIView(HabitBaseAPIView):
    def get(self, request, pk):
        habit = self.get_habit(pk)
        return Response(
            {"error": "False", "data": list_dependency_candidates(habit, request.user)},
            status=status.HTTP_200_OK,
        )

class HabitDependencyCandidatesForCreateAPIView(HabitBaseAPIView):
    def get(self, request):
        return Response(
            {"error": "False", "data": list_dependency_candidates_for_create(request.user)},
            status=status.HTTP_200_OK,
        )

class HabitListAPIView(HabitBaseAPIView):
    def get_serializer_class(self):
        return self.list_serializer_class

    def get(self, request):
        # habits = list(self.get_queryset())
        # for h in habits:
        #     self._ensure_future_occurrences(h)
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
        self._ensure_future_occurrences(habit)
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
        transaction.on_commit(lambda: delete_parent_from_google_task.delay("habit", str(habit.id), str(habit.user.user_id), "primary"))
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
        recurrence_rule = habit.recurrence_rule or habit.build_recurrence_rule()
        if recurrence_rule and not habit.recurrence_rule:
            habit.recurrence_rule = recurrence_rule
            habit._skip_google_calendar_sync = True
            try:
                habit.save(update_fields=["recurrence_rule", "updated_at"])
            finally:
                habit._skip_google_calendar_sync = False
        if recurrence_rule and request.data.get("delete_all_future") is not None:
            delete_all_future = bool(request.data.get("delete_all_future"))
            if delete_all_future:
                habit.recurrence_rule = _update_rrule_until_date(
                    recurrence_rule,
                    occurrence.scheduled_date - timedelta(days=1),
                )
                if habit.end_date is None or habit.end_date >= occurrence.scheduled_date:
                    habit.end_date = occurrence.scheduled_date - timedelta(days=1)
                habit.save(update_fields=["recurrence_rule", "end_date", "updated_at"])
                habit.occurrences.filter(
                    scheduled_date__gte=occurrence.scheduled_date,
                    is_deleted=False,
                ).update(status="skipped", updated_at=timezone.now())
                OccurrenceReminder.objects.filter(
                    occurrence__habit=habit,
                    occurrence__scheduled_date__gte=occurrence.scheduled_date,
                    is_deleted=False,
                ).update(is_deleted=True, updated_at=timezone.now())
                transaction.on_commit(lambda: sync_recurring_parent_to_google_task.delay("habit", str(habit.id), str(habit.user.user_id), "primary"))
            else:
                mark_occurrence(habit, occurrence, "skipped", notes=request.data.get("notes"))
                transaction.on_commit(
                    lambda: sync_parent_action_to_google_task.delay(
                        "habit",
                        str(habit.id),
                        str(habit.user.user_id),
                        "update",
                        "primary",
                        str(occurrence.id),
                    )
                )
            if habit.milestone:
                check_milestone_completion(habit.milestone)
            if habit.goal:
                check_goal_completion(habit.goal)
            return Response(self.detail_serializer_class(habit, context=self.get_serializer_context()).data, status=status.HTTP_200_OK)
        status_value = request.data.get("status", "completed")
        override_dependency = bool(request.data.get("override_dependency", False))
        override_reason = request.data.get("override_reason")
        mark_occurrence(habit, occurrence, status_value, notes=request.data.get("notes"), override_dependency=override_dependency, override_reason=override_reason)
        transaction.on_commit(
            lambda: sync_parent_action_to_google_task.delay(
                "habit",
                str(habit.id),
                str(habit.user.user_id),
                "update",
                "primary",
                str(occurrence.id),
            )
        )
        if habit.milestone:
            check_milestone_completion(habit.milestone)
        if habit.goal:
            check_goal_completion(habit.goal)
        return Response(self.detail_serializer_class(habit, context=self.get_serializer_context()).data, status=status.HTTP_200_OK)
