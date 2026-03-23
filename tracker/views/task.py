from rest_framework import status
from rest_framework.response import Response

from tracker.models import Task, TaskOccurrence
from tracker.serializers import TaskDetailSerializer, TaskListSerializer
from tracker.services import check_goal_completion, check_milestone_completion, mark_occurrence, sync_task_status_from_occurrences
from tracker.views.mixins import TrackerAPIViewMixin


class TaskBaseAPIView(TrackerAPIViewMixin):
    queryset = Task.objects.all().select_related("goal", "milestone", "user").prefetch_related("occurrences")
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
        task.status = "cancelled"
        task.save(update_fields=["status", "updated_at"])
        if task.milestone:
            check_milestone_completion(task.milestone)
        elif task.goal:
            check_goal_completion(task.goal)


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
        self.cancel_task(task)
        return Response(status=status.HTTP_204_NO_CONTENT)


class TaskCompleteAPIView(TaskBaseAPIView):
    def patch(self, request, pk):
        task = self.get_task(pk)
        occurrence_id = request.data.get("occurrence_id")
        notes = request.data.get("notes")
        if occurrence_id:
            occurrence = TaskOccurrence.objects.filter(task=task, id=occurrence_id).first()
            if occurrence is None:
                return self.finalize_error("TASK_NOT_FOUND", "Task occurrence was not found.")
            mark_occurrence(task, occurrence, "completed", notes=notes)
            sync_task_status_from_occurrences(task)
        else:
            task.status = "completed"
            task.save(update_fields=["status", "updated_at"])
        if task.milestone:
            check_milestone_completion(task.milestone)
        elif task.goal:
            check_goal_completion(task.goal)
        return Response(self.detail_serializer_class(task, context=self.get_serializer_context()).data, status=status.HTTP_200_OK)


class TaskSkipAPIView(TaskBaseAPIView):
    def patch(self, request, pk):
        task = self.get_task(pk)
        occurrence = TaskOccurrence.objects.filter(task=task, id=request.data.get("occurrence_id")).first()
        if occurrence is None:
            return self.finalize_error("TASK_NOT_FOUND", "Task occurrence was not found.")
        mark_occurrence(task, occurrence, "skipped", notes=request.data.get("notes"))
        sync_task_status_from_occurrences(task)
        if task.milestone:
            check_milestone_completion(task.milestone)
        elif task.goal:
            check_goal_completion(task.goal)
        return Response(self.detail_serializer_class(task, context=self.get_serializer_context()).data, status=status.HTTP_200_OK)
