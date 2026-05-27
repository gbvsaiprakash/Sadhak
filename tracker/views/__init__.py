from tracker.views.goal import GoalDetailAPIView, GoalListAPIView, GoalCancelAPIView
from tracker.views.habit import HabitDetailAPIView, HabitListAPIView, HabitLogAPIView, HabitPauseAPIView, HabitResumeAPIView, HabitStopAPIView, HabitCancelAPIView, HabitDependencyCandidatesAPIView, HabitDependencyCandidatesForCreateAPIView
from tracker.views.milestone import MilestoneDetailAPIView, MilestoneListAPIView, MilestoneCancelAPIView
from tracker.views.task import TaskCompleteAPIView, TaskDetailAPIView, TaskListAPIView, TaskSkipAPIView, TaskCancelAPIView, TaskDependencyCandidatesAPIView, TaskDependencyCandidatesForCreateAPIView
from tracker.views.occurrence import (
    HabitOccurrenceContextAPIView,
    HabitOccurrenceListAPIView,
    TaskOccurrenceContextAPIView,
    TaskOccurrenceListAPIView,
)
from tracker.views.calendar import CalendarCombinedAPIView, CalendarHabitAPIView, CalendarTaskAPIView
from tracker.views.notes_and_diary import (
    NotesAPIView,
    NotesDetailAPIView,
    NoteContentAPIView,
    DiaryPageAPIView,
)

__all__ = [
    "GoalListAPIView",
    "GoalDetailAPIView",
    "MilestoneListAPIView",
    "MilestoneDetailAPIView",
    "TaskListAPIView",
    "TaskDetailAPIView",
    "TaskCompleteAPIView",
    "TaskSkipAPIView",
    "HabitListAPIView",
    "HabitDetailAPIView",
    "HabitPauseAPIView",
    "HabitResumeAPIView",
    "HabitStopAPIView",
    "HabitLogAPIView",
    "GoalCancelAPIView",
    "MilestoneCancelAPIView",
    "TaskCancelAPIView",
    "HabitCancelAPIView",
    "TaskOccurrenceListAPIView",
    "TaskDependencyCandidatesAPIView",
    "TaskDependencyCandidatesForCreateAPIView",
    "TaskOccurrenceContextAPIView",
    "HabitOccurrenceListAPIView",
    "HabitOccurrenceContextAPIView",
    "HabitDependencyCandidatesAPIView",
    "HabitDependencyCandidatesForCreateAPIView",
    "CalendarTaskAPIView",
    "CalendarHabitAPIView",
    "CalendarCombinedAPIView",
    "NotesAPIView",
    "NotesDetailAPIView",
    "NoteContentAPIView",
    "DiaryPageAPIView",
]
