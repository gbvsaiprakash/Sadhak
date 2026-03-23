from django.utils import timezone

from tracker.services.completion import check_goal_completion


def cascade_goal_cancel(goal):
    now = timezone.localdate()
    goal.milestones.exclude(status="cancelled").update(status="cancelled", achieved_date=None, updated_at=timezone.now())
    goal.tasks.exclude(status="cancelled").update(status="cancelled", updated_at=timezone.now())
    goal.habits.exclude(status="stopped").update(status="stopped", updated_at=timezone.now())
    goal.status = "cancelled"
    goal.achieved_date = None
    goal.save(update_fields=["status", "achieved_date", "updated_at"])
    return now


def cascade_milestone_cancel(milestone):
    milestone.tasks.exclude(status="cancelled").update(status="cancelled", updated_at=timezone.now())
    milestone.habits.exclude(status="stopped").update(status="stopped", updated_at=timezone.now())
    milestone.status = "cancelled"
    milestone.achieved_date = None
    milestone.save(update_fields=["status", "achieved_date", "updated_at"])
    check_goal_completion(milestone.goal)
