from sadhak_base.services import is_feature_enabled
from user_management.emails import send_email
from .templates import task_reminder_template_html
from dataclasses import dataclass
from typing import Any
from sadhak_base.models import UserNotification
from firebase_admin import messaging
from sadhak_base.firebase import init_firebase
from user_management.models import UserDeviceToken

@dataclass
class NotificationResult:
    status: str
    channel: str
    provider_id: str | None = None
    meta: dict[str, Any] | None = None

def can_send_notification(user, parent, mode):
        if not is_feature_enabled("notifications.enabled", getattr(user, "id", None)):
            return False

        pref = getattr(user, "notification_pref", None)
        if not pref or not pref.notifications_enabled:
            return False

        if mode == "in-app" and not pref.in_app_enabled:
            return False
        if mode == "push" and not pref.push_enabled:
            return False
        if mode == "email" and not pref.email_enabled:
            return False

        if parent and not getattr(parent, "reminder_enabled", False):
            return False

        return True

class NotificationRetryableError(Exception):
    pass

class NotificationPermanentError(Exception):
    pass

def _send_in_app_notification(user, title, task_type, body, data) -> NotificationResult:
    try:
        notif = UserNotification.objects.create(
            user=user,
            title=title,
            message=body,
            payload=data or {},
        )
        return NotificationResult(status="sent", channel="in-app", provider_id=str(notif.id), meta={})
    except Exception as e:
        raise NotificationRetryableError(f"Failed to create in-app notification: {str(e)}")
    
def _send_email(user, title, task_type, body, data) -> NotificationResult:
    email = getattr(user, "email", None)
    if not email:
        raise NotificationPermanentError("User has no email")
    body = task_reminder_template_html(
        username=getattr(user, "first_name", "there"),
        task_title=title,
        task_type=task_type,
        scheduled_at=data.get("scheduled_date") + " " + data.get("scheduled_time") if data else None,
        scheduled_end_at=data.get("scheduled_end_time") if data else None,

    )
    title = f"Reminder: {title}"
    sent, message = send_email(title, body, email, "html")

    if sent:
        return NotificationResult(status="sent", channel="email", provider_id=None, meta={"to": email})

    # classify failures for retry policy
    msg = (message or "").lower()
    if "timeout" in msg or "connection" in msg or "temporar" in msg:
        raise NotificationRetryableError(message or "Transient email failure")

    raise NotificationPermanentError(message or "Email send failed")

def send_notification(user, mode: str, title: str, task_type: str, body: str, data: dict[str, Any] | None = None):
    """
    Replace internals with your real channel implementations.
    Raise exception on transient failures so Celery retries.
    """
    if mode == "in-app":
        return _send_in_app_notification(user, title, task_type, body, data if data else None)

    if mode == "push":
        return _send_push(user, title, body, data if data else None)
    
    if mode == "email":
        return _send_email(user, title, task_type, body, data if data else None)

    raise ValueError(f"Unsupported notification mode: {mode}")

def _send_push(user, title: str, body: str, data: dict | None = None):
    tokens = list(
        UserDeviceToken.objects.filter(user=user, is_active=True)
        .exclude(token__isnull=True)
        .exclude(token__exact="")
        .values_list("token", flat=True)
    )

    if not tokens:
        return {"status": "skipped", "reason": "no_active_tokens"}

    init_firebase()

    msg = messaging.MulticastMessage(
        notification=messaging.Notification(title=title, body=body),
        data={k: str(v) for k, v in (data or {}).items()},
        tokens=tokens,
    )

    try:
        resp = messaging.send_each_for_multicast(msg)
    except Exception as exc:
        raise NotificationRetryableError(f"FCM send failed: {exc}") from exc

    # deactivate invalid tokens
    invalid_tokens = []
    for i, r in enumerate(resp.responses):
        if r.success:
            continue
        err = str(getattr(r, "exception", "")).lower()
        if "registration-token-not-registered" in err or "invalid-argument" in err:
            invalid_tokens.append(tokens[i])

    if invalid_tokens:
        UserDeviceToken.objects.filter(token__in=invalid_tokens).update(is_active=False)

    return {
        "status": "sent",
        "success_count": resp.success_count,
        "failure_count": resp.failure_count,
    }

def _notify_google_sync_issue(user, reason: str, parent=None, occurrence=None, calendar_id: str = "primary"):

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

