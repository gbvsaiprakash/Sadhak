from rest_framework import status
from rest_framework.response import Response

from tracker.models import Goal
from tracker.serializers import GoalDetailSerializer, GoalListSerializer
from tracker.services import cascade_goal_cancel
from tracker.views.mixins import TrackerAPIViewMixin


class GoalBaseAPIView(TrackerAPIViewMixin):
    queryset = Goal.objects.all().select_related("user").prefetch_related("milestones", "tasks", "habits")
    list_serializer_class = GoalListSerializer
    detail_serializer_class = GoalDetailSerializer

    def get_queryset(self):
        if not self.request.user or self.request.user.is_anonymous:
            return self.queryset.none()
        queryset = self.queryset.filter(user=self.request.user)
        return self.filter_common(queryset)

    def get_not_found_code(self):
        return "GOAL_NOT_FOUND"

    def get_goal(self, pk):
        return self.get_object(self.get_queryset(), id=pk)


class GoalListAPIView(GoalBaseAPIView):
    def get_serializer_class(self):
        return self.list_serializer_class

    def get(self, request):
        serializer = self.get_serializer(self.get_queryset(), many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = self.detail_serializer_class(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        goal = serializer.save()
        return Response(self.detail_serializer_class(goal, context=self.get_serializer_context()).data, status=status.HTTP_201_CREATED)


class GoalDetailAPIView(GoalBaseAPIView):
    def get_serializer_class(self):
        return self.detail_serializer_class

    def get(self, request, pk):
        goal = self.get_goal(pk)
        return Response(self.get_serializer(goal).data, status=status.HTTP_200_OK)

    def put(self, request, pk):
        goal = self.get_goal(pk)
        serializer = self.get_serializer(goal, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request, pk):
        goal = self.get_goal(pk)
        serializer = self.get_serializer(goal, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    def delete(self, request, pk):
        goal = self.get_goal(pk)
        cascade_goal_cancel(goal)
        return Response(status=status.HTTP_204_NO_CONTENT)
