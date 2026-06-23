from rest_framework import status
from rest_framework.response import Response
from django.db import transaction
from datetime import timedelta
from django.utils import timezone
from tracker.models import Task, TaskOccurrence
from tracker.models.occurrence import OccurrenceReminder
from tracker.serializers import TaskDetailSerializer, TaskListSerializer
from tracker.services import check_goal_completion, check_milestone_completion, mark_occurrence, sync_task_status_from_occurrences
from tracker.views.mixins import TrackerAPIViewMixin
from tracker.services.dependency import ensure_not_depended_on, list_dependency_candidates, list_dependency_candidates_for_create, soft_delete_owned_dependencies
from integrations.services import _update_rrule_until_date
from integrations.tasks import delete_parent_from_google_task, sync_parent_action_to_google_task, sync_recurring_parent_to_google_task


class TaskBaseAPIView(TrackerAPIViewMixin):
    queryset = Task.objects.filter(is_deleted=False).select_related("goal", "milestone", "user").prefetch_related("occurrences")
    list_serializer_class = TaskListSerializer
    detail_serializer_class = TaskDetailSerializer

    def get_queryset(self):
        if not self.request.user or self.request.user.is_anonymous:
            return self.queryset.none()
        queryset = self.queryset.filter(user=self.request.user)
        return self.filter_common(queryset)

    def get_not_found_code(self):
        return "TASK_NOT_FOUND"

    def get_task(self, pk):
        return self.get_object(self.get_queryset(), id=pk)

    def cancel_task(self, task):
        ensure_not_depended_on(task)
        task.status = "cancelled"
        task.save(update_fields=["status", "updated_at"])
        TaskOccurrence.objects.filter(task=task, status="pending").update(
            status="cancelled",
            updated_at=timezone.now(),
        )
        OccurrenceReminder.objects.filter(occurrence__task=task, is_deleted=False).update(is_deleted=True)
        soft_delete_owned_dependencies(task)
        if task.milestone:
            check_milestone_completion(task.milestone)
        if task.goal:
            check_goal_completion(task.goal)
    
    def delete_task(self, task):
        ensure_not_depended_on(task)
        task.is_deleted = True
        task.save(update_fields=["is_deleted", "updated_at"])
        soft_delete_owned_dependencies(task)
        TaskOccurrence.objects.filter(task=task, is_deleted=False).update(is_deleted=True)
        OccurrenceReminder.objects.filter(occurrence__task=task, is_deleted=False).update(is_deleted=True)
        if task.milestone:
            check_milestone_completion(task.milestone)
        if task.goal:
            check_goal_completion(task.goal)
    

class TaskDependencyCandidatesAPIView(TaskBaseAPIView):
    def get(self, request, pk):
        task = self.get_task(pk)
        return Response(
            {"error": "False", "data": list_dependency_candidates(task, request.user)},
            status=status.HTTP_200_OK,
        )

class TaskDependencyCandidatesForCreateAPIView(TaskBaseAPIView):
    def get(self, request):
        return Response(
            {"error": "False", "data": list_dependency_candidates_for_create(request.user)},
            status=status.HTTP_200_OK,
        )


class TaskListAPIView(TaskBaseAPIView):
    def get_serializer_class(self):
        return self.list_serializer_class

    def get(self, request):
        serializer = self.get_serializer(self.get_queryset(), many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = self.detail_serializer_class(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        task = serializer.save()
        return Response(self.detail_serializer_class(task, context=self.get_serializer_context()).data, status=status.HTTP_201_CREATED)


class TaskDetailAPIView(TaskBaseAPIView):
    def get_serializer_class(self):
        return self.detail_serializer_class

    def get(self, request, pk):
        task = self.get_task(pk)
        return Response(self.get_serializer(task).data, status=status.HTTP_200_OK)

    def put(self, request, pk):
        task = self.get_task(pk)
        serializer = self.get_serializer(task, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request, pk):
        task = self.get_task(pk)
        serializer = self.get_serializer(task, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    def delete(self, request, pk):
        task = self.get_task(pk)
        self.delete_task(task)
        transaction.on_commit(lambda: delete_parent_from_google_task.delay("task", str(task.id), str(task.user.user_id), "primary"))
        return Response(status=status.HTTP_204_NO_CONTENT)

class TaskCancelAPIView(TaskBaseAPIView):
    def put(self, request, pk):
        task = self.get_task(pk)
        self.cancel_task(task)

        if task.milestone:
            check_milestone_completion(task.milestone)
        if task.goal:
            check_goal_completion(task.goal)

        return Response(
            self.detail_serializer_class(task, context=self.get_serializer_context()).data,
            status=status.HTTP_200_OK,
        )


class TaskCompleteAPIView(TaskBaseAPIView):
    def patch(self, request, pk):
        task = self.get_task(pk)
        occurrence_id = request.data.get("occurrence_id")
        notes = request.data.get("notes")
        if occurrence_id:
            occurrence = TaskOccurrence.objects.filter(task=task, id=occurrence_id).first()
            if occurrence is None:
                return self.finalize_error("TASK_NOT_FOUND", "Task occurrence was not found.")
            override_dependency = bool(request.data.get("override_dependency", False))
            override_reason = request.data.get("override_reason")
            mark_occurrence(task, occurrence, "completed", notes=notes, override_dependency=override_dependency, override_reason=override_reason)
            sync_task_status_from_occurrences(task)
            transaction.on_commit(
                lambda: sync_parent_action_to_google_task.delay(
                    "task",
                    str(task.id),
                    str(task.user.user_id),
                    "update",
                    "primary",
                    str(occurrence.id),
                )
            )
        else:
            task.status = "completed"
            task.save(update_fields=["status", "updated_at"])
        if task.milestone:
            check_milestone_completion(task.milestone)
        if task.goal:
            check_goal_completion(task.goal)
        return Response(self.detail_serializer_class(task, context=self.get_serializer_context()).data, status=status.HTTP_200_OK)


class TaskSkipAPIView(TaskBaseAPIView):
    def patch(self, request, pk):
        task = self.get_task(pk)
        occurrence = TaskOccurrence.objects.filter(task=task, id=request.data.get("occurrence_id")).first()
        if occurrence is None:
            return self.finalize_error("TASK_NOT_FOUND", "Task occurrence was not found.")
        recurrence_rule = task.recurrence_rule or task.build_recurrence_rule()
        if recurrence_rule and not task.recurrence_rule:
            task.recurrence_rule = recurrence_rule
            task._skip_google_calendar_sync = True
            try:
                task.save(update_fields=["recurrence_rule", "updated_at"])
            finally:
                task._skip_google_calendar_sync = False
        if recurrence_rule and request.data.get("delete_all_future") is not None:
            delete_all_future = bool(request.data.get("delete_all_future"))
            if delete_all_future:
                task.recurrence_rule = _update_rrule_until_date(
                    recurrence_rule,
                    occurrence.scheduled_date - timedelta(days=1),
                )
                if task.end_date is None or task.end_date >= occurrence.scheduled_date:
                    task.end_date = occurrence.scheduled_date - timedelta(days=1)
                task.save(update_fields=["recurrence_rule", "end_date", "updated_at"])
                task.occurrences.filter(
                    scheduled_date__gte=occurrence.scheduled_date,
                    is_deleted=False,
                ).update(status="skipped", updated_at=timezone.now())
                OccurrenceReminder.objects.filter(
                    occurrence__task=task,
                    occurrence__scheduled_date__gte=occurrence.scheduled_date,
                    is_deleted=False,
                ).update(is_deleted=True, updated_at=timezone.now())
                transaction.on_commit(lambda: sync_recurring_parent_to_google_task.delay("task", str(task.id), str(task.user.user_id), "primary"))
            else:
                mark_occurrence(task, occurrence, "skipped", notes=request.data.get("notes"))
                transaction.on_commit(
                    lambda: sync_parent_action_to_google_task.delay(
                        "task",
                        str(task.id),
                        str(task.user.user_id),
                        "update",
                        "primary",
                        str(occurrence.id),
                    )
                )
            sync_task_status_from_occurrences(task)
            if task.milestone:
                check_milestone_completion(task.milestone)
            if task.goal:
                check_goal_completion(task.goal)
            return Response(self.detail_serializer_class(task, context=self.get_serializer_context()).data, status=status.HTTP_200_OK)
        mark_occurrence(task, occurrence, "skipped", notes=request.data.get("notes"))
        sync_task_status_from_occurrences(task)
        transaction.on_commit(
            lambda: sync_parent_action_to_google_task.delay(
                "task",
                str(task.id),
                str(task.user.user_id),
                "update",
                "primary",
                str(occurrence.id),
            )
        )
        if task.milestone:
            check_milestone_completion(task.milestone)
        if task.goal:
            check_goal_completion(task.goal)
        return Response(self.detail_serializer_class(task, context=self.get_serializer_context()).data, status=status.HTTP_200_OK)
