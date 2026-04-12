from django.utils import timezone
from tracker.models import TaskOccurrence, Task, Habit
from tracker.services.completion import check_goal_completion


def cascade_goal_cancel(goal, goal_data):
    now = timezone.localdate()
    # get tasks and habits ids of tasks and habits associated with milestones of the goal to be cancelled, to cancel their occurrences as well
    tasks = [task["id"] for task in goal_data["all_tasks"] if task["status"] != "cancelled"]
    habits = [habit["id"] for habit in goal_data["all_habits"] if habit["status"] != "stopped"]
    goal.milestones.exclude(status="cancelled").update(status="cancelled", achieved_date=None, updated_at=timezone.now())
    goal.tasks.exclude(status="cancelled").update(status="cancelled", updated_at=timezone.now())
    goal.habits.exclude(status="stopped").update(status="stopped", updated_at=timezone.now())
    Task.objects.filter(id__in=tasks).update(status="cancelled", updated_at=timezone.now())
    Habit.objects.filter(id__in=habits).update(status="stopped", updated_at=timezone.now())
    TaskOccurrence.objects.filter(task_id__in=tasks, scheduled_date__gte=timezone.localdate()).update(status="cancelled", updated_at=timezone.now())
    TaskOccurrence.objects.filter(habit_id__in=habits, scheduled_date__gte=timezone.localdate()).update(status="stopped", updated_at=timezone.now())
    goal.status = "cancelled"
    goal.achieved_date = None
    goal.save(update_fields=["status", "achieved_date", "updated_at"])
    return now


def cascade_milestone_cancel(milestone):
    tasks = list(milestone.tasks.exclude(status="cancelled").values_list("id", flat=True))
    habits = list(milestone.habits.exclude(status="stopped").values_list("id", flat=True))
    milestone.tasks.exclude(status="cancelled").update(status="cancelled", updated_at=timezone.now())
    milestone.habits.exclude(status="stopped").update(status="stopped", updated_at=timezone.now())
    TaskOccurrence.objects.filter(task_id__in=tasks, scheduled_date__gte=timezone.localdate()).update(status="cancelled", updated_at=timezone.now())
    TaskOccurrence.objects.filter(habit_id__in=habits, scheduled_date__gte=timezone.localdate()).update(status="stopped", updated_at=timezone.now())
    milestone.status = "cancelled"
    milestone.achieved_date = None
    milestone.save(update_fields=["status", "achieved_date", "updated_at"])
    check_goal_completion(milestone.goal)

def cascade_goal_delete(goal, goal_data):
    now = timezone.localdate()
    # get tasks and habits ids of tasks and habits associated with milestones of the goal to be deleted, to delete their occurrences as well
    tasks = [task["id"] for task in goal_data["all_tasks"] if task["status"] != "cancelled"]
    habits = [habit["id"] for habit in goal_data["all_habits"] if habit["status"] != "stopped"]
    goal.milestones.exclude(status="cancelled").update(is_deleted=True, achieved_date=None, updated_at=timezone.now())
    goal.tasks.exclude(status="cancelled").update(is_deleted=True, updated_at=timezone.now())
    goal.habits.exclude(status="stopped").update(is_deleted=True, updated_at=timezone.now())
    Task.objects.filter(id__in=tasks).update(is_deleted=True, updated_at=timezone.now())
    Habit.objects.filter(id__in=habits).update(is_deleted=True, updated_at=timezone.now())
    TaskOccurrence.objects.filter(task_id__in=tasks).update(is_deleted=True, updated_at=timezone.now())
    TaskOccurrence.objects.filter(habit_id__in=habits).update(is_deleted=True, updated_at=timezone.now())
    goal.is_deleted = True
    goal.achieved_date = None
    goal.save(update_fields=["is_deleted", "achieved_date", "updated_at"])
    return now


def cascade_milestone_delete(milestone):
    milestone.tasks.exclude(status="cancelled").update(is_deleted=True, updated_at=timezone.now())
    milestone.habits.exclude(status="stopped").update(is_deleted=True, updated_at=timezone.now())
    milestone.is_deleted = True
    milestone.achieved_date = None
    milestone.save(update_fields=["is_deleted", "achieved_date", "updated_at"])
    check_goal_completion(milestone.goal)