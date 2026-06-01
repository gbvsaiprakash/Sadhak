from django.db.models.signals import post_save
from django.dispatch import receiver
import logging

from tracker.models import TaskOccurrence, Task
from integrations.services import push_local_occurrence_change, create_recurring_google_event

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Task)
def sync_recurring_task_to_google(sender, instance: Task, created: bool, **kwargs):
    """
    Sync recurring tasks to Google Calendar.
    When a Task with recurrence_rule is created or updated, create/update recurring event in Google.
    """
    if not instance.recurrence_rule:
        return  # Not a recurring task
    
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
        result = create_recurring_google_event(user, instance, calendar_id="primary")
        logger.info(f"Recurring task sync result: {result}")
    except Exception as e:
        logger.error(f"Calendar sync error for recurring task {instance.id}: {str(e)}", exc_info=True)


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

    try:
        logger.info(f"Syncing occurrence {instance.id} for user {user.username} (action: {'delete' if instance.is_deleted else 'create' if created else 'update'})")
        
        if instance.is_deleted:
            result = push_local_occurrence_change(user, instance, action="delete")
            logger.info(f"Delete sync result: {result}")
            return

        if created:
            result = push_local_occurrence_change(user, instance, action="create")
            logger.info(f"Create sync result: {result}")
            return

        # Update on any change including status changes
        result = push_local_occurrence_change(user, instance, action="update")
        logger.info(f"Update sync result: {result}")
    except Exception as e:
        logger.error(f"Calendar sync error for occurrence {instance.id}: {str(e)}", exc_info=True)
