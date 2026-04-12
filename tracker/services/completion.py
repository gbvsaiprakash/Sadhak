from django.db.models import Q
from django.utils import timezone
from tracker.models.occurrence import TaskOccurrence
from tracker.constants import ACTIVE_GOAL_STATUSES, ACTIVE_HABIT_STATUSES, ACTIVE_TASK_STATUSES, RESOLVED_OCCURRENCE_STATUSES


def calculate_completion_percentage(children):
    items = list(children)
    if not items:
        return 0
    completed = sum(1 for item in items if getattr(item, "status", None) in {"completed", "stopped"})
    return int((completed / len(items)) * 100)


def task_is_complete(task):
    if task.status in {"completed", "skipped", "cancelled"}:
        return True
    if task.frequency_type == "once":
        return False
    unresolved = task.occurrences.filter(scheduled_date__gte=timezone.localdate()).exclude(status__in=RESOLVED_OCCURRENCE_STATUSES).exists()
    if unresolved:
        return False
    if task.end_date and task.end_date < timezone.localdate():
        return not task.occurrences.exclude(status__in=RESOLVED_OCCURRENCE_STATUSES).exists()
    return False


def habit_is_complete_for_parent(habit):
    return habit.status in {"completed", "stopped"}

def sync_tasks_habits_and_occurrences_for_override_completion(milestone):
    # Implementation for syncing tasks, habits, and occurrences for override completion
    # For occurrences, we might want to mark all future occurrences as skipped or cancelled, and past occurrences as completed, depending on the current date.
    milestone.tasks.filter(is_deleted=False).exclude(status="cancelled").update(status="completed", updated_at=timezone.now())
    milestone.habits.filter(is_deleted=False).exclude(status__in={"stopped", "cancelled"}).update(status="completed", updated_at=timezone.now())
    task_ids = list(
        milestone.tasks.filter(is_deleted=False)
        .exclude(status="cancelled")
        .values_list("id", flat=True)
    )

    habit_ids = list(
        milestone.habits.filter(is_deleted=False)
        .exclude(status__in=["stopped", "cancelled"])
        .values_list("id", flat=True)
    )

    TaskOccurrence.objects.filter(
        task_id__in=task_ids,
        is_deleted=False,
    ).update(status="completed", completed_at=timezone.now(), updated_at=timezone.now())

    TaskOccurrence.objects.filter(
        habit_id__in=habit_ids,
        is_deleted=False,
    ).update(status="completed", completed_at=timezone.now(), updated_at=timezone.now())

def check_goal_completion(goal):
    if goal.override_completed:
        if goal.status != "completed" or goal.achieved_date is None:
            goal.status = "completed"
            if not goal.achieved_date:
                goal.achieved_date = timezone.localdate()
            goal.save(update_fields=["status", "achieved_date", "updated_at"])
        return goal

    milestones = list(goal.milestones.filter(is_deleted=False).exclude(status="cancelled"))
    root_tasks = list(goal.tasks.filter(is_deleted=False,milestone__isnull=True).exclude(status="cancelled"))
    root_habits = list(goal.habits.filter(is_deleted=False,milestone__isnull=True).exclude(status="stopped"))

    has_children = bool(milestones or root_tasks or root_habits)
    milestones_complete = all(item.status == "completed" for item in milestones)
    tasks_complete = all(task_is_complete(item) for item in root_tasks)
    habits_complete = all(habit_is_complete_for_parent(item) for item in root_habits)

    if has_children and milestones_complete and tasks_complete and habits_complete:
        goal.status = "completed"
        goal.achieved_date = goal.achieved_date or timezone.localdate()
    elif goal.status == "completed":
        goal.status = "active"
        goal.achieved_date = None

    goal.save(update_fields=["status", "achieved_date", "updated_at"])
    return goal
    # active_milestones = list(goal.milestones.filter(status__in=ACTIVE_GOAL_STATUSES))
    # active_tasks = list(goal.tasks.filter(milestone__isnull=True, status__in=ACTIVE_TASK_STATUSES))
    # active_habits = list(goal.habits.filter(milestone__isnull=True, status__in=ACTIVE_HABIT_STATUSES))

    # has_children = bool(active_milestones or active_tasks or active_habits)
    # milestones_complete = all(item.status == "completed" for item in active_milestones)
    # tasks_complete = all(task_is_complete(item) for item in active_tasks)
    # habits_complete = all(habit_is_complete_for_parent(item) for item in active_habits)

    # if has_children and milestones_complete and tasks_complete and habits_complete:
    #     goal.status = "completed"
    #     goal.achieved_date = goal.achieved_date or timezone.localdate()
    # elif goal.status == "completed":
    #     goal.status = "active"
    #     goal.achieved_date = None
    # goal.save(update_fields=["status", "achieved_date", "updated_at"])
    # return goal


def check_milestone_completion(milestone):
    if milestone.override_completed:
        sync_tasks_habits_and_occurrences_for_override_completion(milestone)
        if milestone.status != "completed" or milestone.achieved_date is None:
            milestone.status = "completed"
            if not milestone.achieved_date:
                milestone.achieved_date = timezone.localdate()
            milestone.save(update_fields=["status", "achieved_date", "updated_at"])
        check_goal_completion(milestone.goal)
        return milestone

    tasks = list(milestone.tasks.filter(is_deleted=False).exclude(status="cancelled"))
    habits = list(milestone.habits.filter(is_deleted=False).exclude(status="stopped"))

    has_children = bool(tasks or habits)
    tasks_complete = all(task_is_complete(item) for item in tasks)
    habits_complete = all(habit_is_complete_for_parent(item) for item in habits)

    if has_children and tasks_complete and habits_complete:
        milestone.status = "completed"
        milestone.achieved_date = milestone.achieved_date or timezone.localdate()
    elif milestone.status == "completed":
        milestone.status = "active"
        milestone.achieved_date = None

    milestone.save(update_fields=["status", "achieved_date", "updated_at"])
    check_goal_completion(milestone.goal)
    return milestone


def sync_task_status_from_occurrences(task):
    if task.frequency_type == "once":
        return task
    unresolved_exists = task.occurrences.exclude(status__in=RESOLVED_OCCURRENCE_STATUSES).exists()
    if not unresolved_exists:
        task.status = "completed"
        task.save(update_fields=["status", "updated_at"])
    elif task.status == "completed":
        task.status = "pending"
        task.save(update_fields=["status", "updated_at"])
    return task
