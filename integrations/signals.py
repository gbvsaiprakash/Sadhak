from django.db.models.signals import post_save
from django.dispatch import receiver
import logging
from django.db import transaction

from tracker.models import TaskOccurrence, Task, Habit
from integrations.tasks import (
    delete_parent_from_google_task,
    sync_parent_action_to_google_task,
    sync_recurring_parent_to_google_task,
)

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Task)
def sync_recurring_task_to_google(sender, instance: Task, created: bool, **kwargs):
    """
    Sync recurring tasks to Google Calendar.
    When a Task with recurrence_rule is created or updated, create/update recurring event in Google.
    """
    if not instance.recurrence_rule:
        return  # Not a recurring task
    if getattr(instance, "_skip_google_calendar_sync", False):
        return
    if getattr(instance, "external_google_id", False):
        return
    
    user = getattr(instance, "user", None)
    if not user:
        logger.warning(f"Sync skipped: No user found for task {instance.id}")
        return
    
    try:
        logger.info(f"Syncing recurring task {instance.id} for user {user.username}")
        
        # Check if Google Calendar is connected
        from integrations.models import GoogleCalendarConnection
        connection = GoogleCalendarConnection.objects.filter(user=user, is_active=True).first()
        if not connection:
            logger.info(f"Google Calendar not connected for user {user.username} - skipping recurring task sync")
            return
        
        # Create recurring event in Google Calendar
        transaction.on_commit(lambda: sync_recurring_parent_to_google_task.delay("task", str(instance.id), str(user.user_id), "primary"))
        logger.info(f"Queued recurring task sync for task {instance.id}")
    except Exception as e:
        logger.error(f"Calendar sync error for recurring task {instance.id}: {str(e)}", exc_info=True)


@receiver(post_save, sender=Habit)
def sync_recurring_habit_to_google(sender, instance: Habit, created: bool, **kwargs):
    if not instance.recurrence_rule:
        return
    if getattr(instance, "_skip_google_calendar_sync", False):
        return
    if getattr(instance, "external_google_id", False):
        return

    user = getattr(instance, "user", None)
    if not user:
        logger.warning(f"Sync skipped: No user found for habit {instance.id}")
        return

    try:
        logger.info(f"Syncing recurring habit {instance.id} for user {user.username}")
        from integrations.models import GoogleCalendarConnection
        connection = GoogleCalendarConnection.objects.filter(user=user, is_active=True).first()
        if not connection:
            logger.info(f"Google Calendar not connected for user {user.username} - skipping recurring habit sync")
            return
        transaction.on_commit(lambda: sync_recurring_parent_to_google_task.delay("habit", str(instance.id), str(user.user_id), "primary"))
        logger.info(f"Queued recurring habit sync for habit {instance.id}")
    except Exception as e:
        logger.error(f"Calendar sync error for recurring habit {instance.id}: {str(e)}", exc_info=True)


@receiver(post_save, sender=TaskOccurrence)
def sync_occurrence_to_google(sender, instance: TaskOccurrence, created: bool, **kwargs):
    """
    Bidirectional sync: when app occurrence changes, push to Google Calendar.
    Includes status changes (pending, completed, skipped, missed).
    """
    parent = instance.task or instance.habit
    user = getattr(parent, "user", None)
    if not user:
        logger.warning(f"Sync skipped: No user found for occurrence {instance.id}")
        return
    if getattr(instance, "synced_from_google", False):
        return

    try:
        logger.info(f"Syncing occurrence {instance.id} for user {user.username} (action: {'delete' if instance.is_deleted else 'create' if created else 'update'})")
        
        parent_type = "task" if instance.task_id else "habit"
        if instance.is_deleted:
            transaction.on_commit(
                lambda: sync_parent_action_to_google_task.delay(
                    parent_type,
                    str(parent.id),
                    str(user.user_id),
                    "delete",
                    "primary",
                    str(instance.id),
                )
            )
            logger.info(f"Queued delete sync for occurrence {instance.id}")
            return

        if created:
            transaction.on_commit(
                lambda: sync_parent_action_to_google_task.delay(
                    parent_type,
                    str(parent.id),
                    str(user.user_id),
                    "create",
                    "primary",
                    str(instance.id),
                )
            )
            logger.info(f"Queued create sync for occurrence {instance.id}")
            return

        # Update on any change including status changes
        transaction.on_commit(
            lambda: sync_parent_action_to_google_task.delay(
                parent_type,
                str(parent.id),
                str(user.user_id),
                "update",
                "primary",
                str(instance.id),
            )
        )
        logger.info(f"Queued update sync for occurrence {instance.id}")
    except Exception as e:
        logger.error(f"Calendar sync error for occurrence {instance.id}: {str(e)}", exc_info=True)
