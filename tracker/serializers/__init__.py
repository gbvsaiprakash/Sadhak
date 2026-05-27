from tracker.serializers.goal import GoalDetailSerializer, GoalListSerializer
from tracker.serializers.habit import HabitDetailSerializer, HabitListSerializer
from tracker.serializers.milestone import MilestoneDetailSerializer, MilestoneListSerializer
from tracker.serializers.task import TaskDetailSerializer, TaskListSerializer
from tracker.serializers.notes_and_diary import  NoteContentSerializer, DiaryPageSerializer, NotesSerializer

__all__ = [
    "GoalListSerializer",
    "GoalDetailSerializer",
    "MilestoneListSerializer",
    "MilestoneDetailSerializer",
    "TaskListSerializer",
    "TaskDetailSerializer",
    "HabitListSerializer",
    "HabitDetailSerializer",
    "NotesSerializer",
    "NoteContentSerializer",
    "DiaryPageSerializer",
]
