from rest_framework import status
from rest_framework.response import Response

from tracker.models import Goal, Milestone
from tracker.serializers import MilestoneDetailSerializer, MilestoneListSerializer
from tracker.services import cascade_milestone_cancel, cascade_milestone_delete
from tracker.views.mixins import TrackerAPIViewMixin


class MilestoneBaseAPIView(TrackerAPIViewMixin):
    queryset = Milestone.objects.filter(is_deleted=False).select_related("goal", "goal__user").prefetch_related("tasks", "habits")
    list_serializer_class = MilestoneListSerializer
    detail_serializer_class = MilestoneDetailSerializer

    def get_queryset(self):
        if not self.request.user or self.request.user.is_anonymous:
            return self.queryset.none()
        queryset = self.queryset.filter(goal__user=self.request.user)
        goal_id = self.kwargs.get("goal_id")
        if goal_id:
            queryset = queryset.filter(goal_id=goal_id)
        return queryset

    def get_not_found_code(self):
        return "MILESTONE_NOT_FOUND"

    def get_goal(self, goal_id, check_status=False):
        goal = Goal.objects.filter(id=self.kwargs["goal_id"], user=self.request.user).first()
        if check_status and (goal.status == "cancelled" or goal.override_completed):
            return None
        if goal is None:
            return None
        return goal

    def get_milestone(self, goal_id, pk):
        return self.get_object(self.get_queryset(), id=pk, goal_id=goal_id)


class MilestoneListAPIView(MilestoneBaseAPIView):
    def get_serializer_class(self):
        return self.list_serializer_class

    def get(self, request, goal_id):
        if self.get_goal(goal_id) is None:
            return self.finalize_error("GOAL_NOT_FOUND", "Goal was not found.")
        serializer = self.get_serializer(self.get_queryset(), many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request, goal_id):
        goal = self.get_goal(goal_id,check_status=True)
        if goal is None:
            return self.finalize_error("GOAL_NOT_FOUND", "Goal was not found.")
        serializer = self.detail_serializer_class(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        milestone = serializer.save(goal=goal)
        return Response(self.detail_serializer_class(milestone, context=self.get_serializer_context()).data, status=status.HTTP_201_CREATED)


class MilestoneDetailAPIView(MilestoneBaseAPIView):
    def get_serializer_class(self):
        return self.detail_serializer_class

    def get(self, request, goal_id, pk):
        milestone = self.get_milestone(goal_id, pk)
        return Response(self.get_serializer(milestone).data, status=status.HTTP_200_OK)

    def put(self, request, goal_id, pk):
        milestone = self.get_milestone(goal_id, pk)
        serializer = self.get_serializer(milestone, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request, goal_id, pk):
        milestone = self.get_milestone(goal_id, pk)
        serializer = self.get_serializer(milestone, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    def delete(self, request, goal_id, pk):
        milestone = self.get_milestone(goal_id, pk)
        cascade_milestone_delete(milestone)
        return Response(status=status.HTTP_204_NO_CONTENT)

class MilestoneCancelAPIView(MilestoneBaseAPIView):
    def put(self, request, goal_id, pk):
        milestone = self.get_milestone(goal_id, pk)
        cascade_milestone_cancel(milestone)
        return Response(
            self.detail_serializer_class(milestone, context=self.get_serializer_context()).data,
            status=status.HTTP_200_OK,
        )

