from celery import shared_task
from django.contrib.auth import get_user_model

from integrations.services import (
    create_recurring_google_event,
    ensure_valid_access_token,
    delete_parent_from_google,
    google_list_events,
    pull_google_delta_for_watch,
    push_local_occurrence_change,
    sync_google_recurring_change,
    sync_external_google_event_to_app,
    sync_google_status_to_app_occurrences,
    upsert_mirror_events,
    sync_parent_occurrences_to_google,
    sync_parent_reminders_to_google,
    _coerce_access_token_result,
)
from integrations.models import GoogleCalendarConnection, GoogleCalendarWatch
from tracker.models import Task, Habit, TaskOccurrence
from tracker.services.occurrence import sync_occurrence_reminders_for_parent

User = get_user_model()


def _notify_google_sync_issue(user, reason: str, parent=None, occurrence=None, calendar_id: str = "primary"):
    from sadhak_base.notifications import send_notification

    payload = {
        "error_code": reason,
        "calendar_id": calendar_id,
    }
    if parent is not None:
        payload["parent_id"] = str(parent.id)
        payload["parent_type"] = "habit" if getattr(parent, "is_habit", False) else "task"
    if occurrence is not None:
        payload["occurrence_id"] = str(occurrence.id)

    for mode in ("in-app", "push"):
        try:
            send_notification(
                user,
                mode,
                "Google Calendar sync paused",
                "google_sync",
                "We couldn't finish syncing with Google Calendar.",
                payload,
            )
        except Exception:
            continue


@shared_task
def sync_recurring_parent_to_google_task(parent_type: str, parent_id: str, user_id: str, calendar_id: str = "primary"):
    parent_model = Task if parent_type == "task" else Habit
    user = User.objects.filter(pk=user_id).first()
    parent = parent_model.objects.filter(id=parent_id).first()
    if not user or not parent:
        return {"synced": False, "reason": "missing_parent_or_user"}
    result = create_recurring_google_event(user, parent, calendar_id=calendar_id)
    if not result.get("created") and result.get("error"):
        _notify_google_sync_issue(user, result.get("error", "unknown"), parent=parent, calendar_id=calendar_id)
    return result


@shared_task
def sync_parent_occurrences_to_google_task(parent_type: str, parent_id: str, user_id: str, calendar_id: str = "primary"):
    parent_model = Task if parent_type == "task" else Habit
    user = User.objects.filter(pk=user_id).first()
    parent = parent_model.objects.filter(id=parent_id).first()
    if not user or not parent:
        return {"synced": False, "reason": "missing_parent_or_user"}

    result = sync_parent_occurrences_to_google(user, parent, calendar_id=calendar_id)
    print(f"Sync parent occurrences result: {result}")
    if not result.get("synced") and result.get("reason") in {"access_token_error", "not_connected"}:
        _notify_google_sync_issue(user, result.get("reason", "unknown"), parent=parent, calendar_id=calendar_id)
    return result


@shared_task
def sync_parent_reminders_to_google_task(parent_type: str, parent_id: str, user_id: str, calendar_id: str = "primary"):
    parent_model = Task if parent_type == "task" else Habit
    user = User.objects.filter(pk=user_id).first()
    parent = parent_model.objects.filter(id=parent_id).first()
    if not user or not parent:
        return {"synced": False, "reason": "missing_parent_or_user"}
    result = sync_parent_reminders_to_google(user, parent, calendar_id=calendar_id)
    if not result.get("synced") and result.get("reason") in {"access_token_error", "not_connected"}:
        _notify_google_sync_issue(user, result.get("reason", "unknown"), parent=parent, calendar_id=calendar_id)
    return result


@shared_task
def delete_parent_from_google_task(parent_type: str, parent_id: str, user_id: str, calendar_id: str = "primary"):
    parent_model = Task if parent_type == "task" else Habit
    user = User.objects.filter(pk=user_id).first()
    parent = parent_model.objects.filter(id=parent_id).first()
    if not user or not parent:
        return {"deleted": False, "reason": "missing_parent_or_user"}
    result = delete_parent_from_google(user, parent, calendar_id=calendar_id)
    if not result.get("deleted") and result.get("reason") in {"access_token_error", "not_connected"}:
        _notify_google_sync_issue(user, result.get("reason", "unknown"), parent=parent, calendar_id=calendar_id)
    return result


@shared_task
def push_occurrence_to_google_task(occurrence_id: str, action: str, user_id: str, calendar_id: str = "primary"):
    user = User.objects.filter(pk=user_id).first()
    occurrence = TaskOccurrence.objects.filter(id=occurrence_id).first()
    if not user or not occurrence:
        return {"pushed": False, "reason": "missing_occurrence_or_user"}
    result = push_local_occurrence_change(user, occurrence, action=action, calendar_id=calendar_id)
    if not result.get("pushed") and result.get("reason") in {"access_token_error", "not_connected"}:
        parent = occurrence.task or occurrence.habit
        _notify_google_sync_issue(user, result.get("reason", "unknown"), parent=parent, occurrence=occurrence, calendar_id=calendar_id)
    return result


@shared_task
def sync_google_watch_task(watch_id: str):
    watch = GoogleCalendarWatch.objects.filter(id=watch_id, is_active=True).select_related("user").first()
    if not watch:
        return {"synced": False, "reason": "missing_watch"}
    return pull_google_delta_for_watch(watch)


@shared_task
def sync_google_full_calendar_task(user_id: str, calendar_id: str = "primary"):
    user = User.objects.filter(pk=user_id).first()
    if not user:
        return {"synced": False, "reason": "missing_user"}
    connection = GoogleCalendarConnection.objects.filter(user=user, is_active=True).first()
    if not connection:
        return {"synced": False, "reason": "not_connected"}
    access_token, error = _coerce_access_token_result(ensure_valid_access_token(connection))
    if error:
        _notify_google_sync_issue(user, error, calendar_id=calendar_id)
        return {"synced": False, "reason": error}
    data = google_list_events(access_token, calendar_id=calendar_id, user=user)
    items = data.get("items", [])
    changed = upsert_mirror_events(user, calendar_id, items)
    external_synced = 0
    for item in items:
        if item.get("recurringEventId") or item.get("recurrence"):
            result = sync_google_recurring_change(user, item, calendar_id=calendar_id)
        else:
            result = sync_external_google_event_to_app(user, item, calendar_id=calendar_id)
        if result.get("synced"):
            external_synced += 1
    status_synced = sync_google_status_to_app_occurrences(user, calendar_id, items)
    next_sync_token = data.get("nextSyncToken")
    watch_obj = GoogleCalendarWatch.objects.filter(id=watch.id).first()
    if next_sync_token and watch_obj:
        watch_obj.sync_token = next_sync_token
        watch_obj.save(update_fields=["sync_token", "updated_at"])
    return {
        "synced": True,
        "fetched_events": len(items),
        "upserted_events": changed,
        "external_synced": external_synced,
        "status_synced": status_synced,
        "next_sync_token": next_sync_token,
    }
