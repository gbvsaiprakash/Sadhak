from django.urls import path

from tracker.views import (
    GoalDetailAPIView,
    GoalListAPIView,
    HabitDetailAPIView,
    HabitListAPIView,
    HabitLogAPIView,
    HabitPauseAPIView,
    HabitResumeAPIView,
    HabitStopAPIView,
    MilestoneDetailAPIView,
    MilestoneListAPIView,
    TaskCompleteAPIView,
    TaskDetailAPIView,
    TaskListAPIView,
    TaskSkipAPIView,
)

urlpatterns = [
    path("goals/", GoalListAPIView.as_view(), name="tracker-goal-list"),
    path("goals/<uuid:pk>/", GoalDetailAPIView.as_view(), name="tracker-goal-detail"),
    path("goals/<uuid:goal_id>/milestones/", MilestoneListAPIView.as_view(), name="tracker-milestone-list"),
    path("goals/<uuid:goal_id>/milestones/<uuid:pk>/", MilestoneDetailAPIView.as_view(), name="tracker-milestone-detail"),
    path("tasks/", TaskListAPIView.as_view(), name="tracker-task-list"),
    path("tasks/<uuid:pk>/", TaskDetailAPIView.as_view(), name="tracker-task-detail"),
    path("tasks/<uuid:pk>/complete/", TaskCompleteAPIView.as_view(), name="tracker-task-complete"),
    path("tasks/<uuid:pk>/skip/", TaskSkipAPIView.as_view(), name="tracker-task-skip"),
    path("habits/", HabitListAPIView.as_view(), name="tracker-habit-list"),
    path("habits/<uuid:pk>/", HabitDetailAPIView.as_view(), name="tracker-habit-detail"),
    path("habits/<uuid:pk>/pause/", HabitPauseAPIView.as_view(), name="tracker-habit-pause"),
    path("habits/<uuid:pk>/resume/", HabitResumeAPIView.as_view(), name="tracker-habit-resume"),
    path("habits/<uuid:pk>/stop/", HabitStopAPIView.as_view(), name="tracker-habit-stop"),
    path("habits/<uuid:pk>/log/", HabitLogAPIView.as_view(), name="tracker-habit-log"),
]
