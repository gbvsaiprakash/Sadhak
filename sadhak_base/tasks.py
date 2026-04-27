from celery import shared_task
from django.db import transaction
from django.utils import timezone
from datetime import datetime, timedelta
from .models import DomainEvent  # adjust if path differs
from tracker.models.occurrence import OccurrenceReminder
from sadhak_base.notifications import can_send_notification, send_notification, NotificationRetryableError
from tracker.services.occurrence import emit_due_reminder_events, _occurrence_due_dt
from sadhak.app_settings import POST_DUE_GRACE_MIN


REMINDER_EVENTS = {"task.reminder_due", "habit.reminder_due"}

@shared_task
def emit_due_reminder_events_task():
    return emit_due_reminder_events()

@shared_task(bind=True, autoretry_for=(NotificationRetryableError, ), retry_backoff=True, retry_jitter=True, max_retries=5)
def process_domain_event(self, event_id: str):
    event = DomainEvent.objects.get(id=event_id)

    if getattr(event, "processed", False):
        return "already_processed"

    if event.event_type not in REMINDER_EVENTS:
        event.processed = True
        event.processed_at = timezone.now()
        event.save(update_fields=["processed", "processed_at", "updated_at"])
        return "ignored_non_reminder_event"

    reminder_id = (event.payload or {}).get("reminder_id")
    if not reminder_id:
        event.processed = True
        event.processed_at = timezone.now()
        event.save(update_fields=["processed", "processed_at", "updated_at"])
        return "missing_reminder_id"

    with transaction.atomic():
        reminder = OccurrenceReminder.objects.select_for_update().get(id=reminder_id)
        reminder = (
            OccurrenceReminder.objects
            .select_related("occurrence", "occurrence__task", "occurrence__habit")
            .get(id=reminder_id)
        )

        if reminder.sent:
            event.processed = True
            event.processed_at = timezone.now()
            event.save(update_fields=["processed", "processed_at", "updated_at"])
            return "already_sent"

        occurrence = reminder.occurrence
        parent = occurrence.task or occurrence.habit
        user = getattr(parent, "user", None)
        now = timezone.now()

        latest_due = (
            OccurrenceReminder.objects.filter(
                occurrence_id=reminder.occurrence_id,
                mode=reminder.mode,
                is_deleted=False,
                sent=False,
                remind_at__lte=now,
            ).order_by("-remind_at").first()
        )

        if not latest_due or latest_due.id != reminder.id:
            reminder.is_deleted = True
            reminder.save(update_fields=["is_deleted", "updated_at"])
            event.processed = True
            event.processed_at = now
            event.save(update_fields=["processed", "processed_at", "updated_at"])
            return "superseded"

        due_dt = _occurrence_due_dt(occurrence)
        if now > due_dt + timedelta(minutes=POST_DUE_GRACE_MIN):
            reminder.is_deleted = True
            reminder.save(update_fields=["is_deleted", "updated_at"])
            event.processed = True
            event.processed_at = now
            event.save(update_fields=["processed", "processed_at", "updated_at"])
            return "stale"

        # Hard stops (non-retry conditions)
        if occurrence.status != "pending" or occurrence.is_deleted:
            event.processed = True
            event.processed_at = timezone.now()
            event.save(update_fields=["processed", "processed_at", "updated_at"])
            return "occurrence_not_pending"


        if not can_send_notification(user=user, parent=parent, mode=reminder.mode):
            event.processed = True
            event.processed_at = timezone.now()
            event.save(update_fields=["processed", "processed_at", "updated_at"])
            return "notification_not_allowed"
        
        send_notification(
            user=user,
            mode=reminder.mode,
            title=getattr(parent, "title", "Reminder") if event.event_type.__contains__("reminder_due") else "Notification",
            task_type="habit" if getattr(parent, "is_habit", False) else "task" if parent else "item",
            body=f"Reminder for {getattr(parent, 'title', 'your item')}" if event.event_type.__contains__("reminder_due") else "",
            data={
                f"{parent.__class__.__name__.lower()}_id": str(getattr(parent, "id", "")),
                "occurrence_id": str(occurrence.id),
                "reminder_id": str(reminder.id),
                "event_type": event.event_type,
                "scheduled_date": (datetime.strptime(str(occurrence.scheduled_date), "%Y-%m-%d").strftime("%d %b %Y") if occurrence.scheduled_date else None),
                "scheduled_time": (datetime.strptime(str(occurrence.scheduled_time), "%H:%M:%S").strftime("%I:%M %p") if occurrence.scheduled_time else None),
                "scheduled_end_time": (datetime.strptime(str(getattr(occurrence, "schedule_end_time", None)), "%H:%M:%S").strftime("%I:%M %p") if getattr(occurrence, "schedule_end_time", None) else None),
            },
        )

        now = timezone.now()
        reminder.sent = True
        reminder.sent_at = now
        reminder.save(update_fields=["sent", "sent_at", "updated_at"])

        event.processed = True
        event.processed_at = now
        event.save(update_fields=["processed", "processed_at", "updated_at"])

    return "processed"


@shared_task
def process_pending_domain_events(batch_size: int = 200):
    pending_ids = list(
        DomainEvent.objects.filter(
            processed=False,
            event_type__in=REMINDER_EVENTS,
        ).order_by("created_at").values_list("id", flat=True)[:batch_size]
    )
    for event_id in pending_ids:
        process_domain_event.delay(str(event_id))
    return len(pending_ids)
