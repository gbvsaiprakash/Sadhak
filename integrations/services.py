import json
import os
import secrets
import urllib.parse
import urllib.request
import urllib.error
import logging
import time as pytime
from datetime import datetime, timezone, timedelta, time
from sadhak_base.notifications import send_notification
from django.core.cache import cache
from django.core import signing
from django.utils import timezone as dj_timezone
from integrations.crypto import encrypt_token, decrypt_token
from integrations.models import GoogleCalendarConnection, GoogleCalendarMirrorEvent, GoogleCalendarWatch, EventSyncMap
from integrations.rrule_handler import RRuleHandler, build_recurrence_rule_for_entity

logger = logging.getLogger(__name__)


GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
GOOGLE_CALENDAR_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
GOOGLE_CALENDAR_WATCH_URL = "https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/watch"


def _env(name: str, default: str | None = None) -> str | None:
    return os.getenv(name, default)

class GoogleTokenExpiredException(Exception):
    pass

def build_google_oauth_url(mode: str, redirect_uri: str) -> tuple[str, str]:
    client_id = _env("GOOGLE_OAUTH_CLIENT_ID", "")
    # state = secrets.token_urlsafe(32)
    state = signing.dumps(
        {
            "mode": mode,
            "redirect_uri": redirect_uri,
            "nonce": secrets.token_urlsafe(16),
        },
        salt="google-oauth-state",
    )
    cache.set(
        f"google_oauth_state:{state}",
        {"mode": mode, "redirect_uri": redirect_uri},
        timeout=900,
    )

    if mode == "calendar":
        scope = "openid email profile https://www.googleapis.com/auth/calendar"
        prompt = "consent"
        access_type = "offline"
    else:
        scope = "openid email profile"
        prompt = "select_account"
        access_type = "online"

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,
        "state": state,
        "include_granted_scopes": "true",
        "access_type": access_type,
        "prompt": prompt,
    }
    return f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}", state


def validate_state_and_get_mode(state: str) -> dict | None:
    state_data = cache.get(f"google_oauth_state:{state}")
    if state_data:
        cache.delete(f"google_oauth_state:{state}")
        return state_data
    
    try:
        payload = signing.loads(state, salt="google-oauth-state", max_age=900)
    except signing.BadSignature:
        return None
    except signing.SignatureExpired:
        return None

    if not isinstance(payload, dict):
        return None

    mode = payload.get("mode")
    redirect_uri = payload.get("redirect_uri")
    if not mode or not redirect_uri:
        return None
    return {"mode": mode, "redirect_uri": redirect_uri}


def exchange_code_for_token(code: str, redirect_uri: str) -> dict:
    payload = {
        "code": code,
        "client_id": _env("GOOGLE_OAUTH_CLIENT_ID", ""),
        "client_secret": _env("GOOGLE_OAUTH_CLIENT_SECRET", ""),
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    body = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(
        GOOGLE_TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def refresh_access_token(refresh_token: str) -> dict:
    payload = {
        "client_id": _env("GOOGLE_OAUTH_CLIENT_ID", ""),
        "client_secret": _env("GOOGLE_OAUTH_CLIENT_SECRET", ""),
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    body = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(
        GOOGLE_TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        # Read and print the actual reason from Google
        error_body = e.read().decode('utf-8')
        return json.loads(error_body)
        # Prevent a generic 500 crash by raising an explicit exception
        # raise ValueError(f"Google Token Refresh Failed: {error_body}")


def fetch_google_user_info(access_token: str) -> dict:
    req = urllib.request.Request(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def compute_expiry(expires_in_seconds: int | None) -> datetime | None:
    if not expires_in_seconds:
        return None
    return datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=int(expires_in_seconds))


def ensure_valid_access_token(connection: GoogleCalendarConnection) -> str:
    if connection.access_token and connection.token_expiry and connection.token_expiry > dj_timezone.now() + timedelta(seconds=30):
        return decrypt_token(connection.access_token), ""

    token_data = refresh_access_token(decrypt_token(connection.refresh_token))
    access_token = token_data.get("access_token", "")
    if not access_token:
        return "", token_data.get("error_description", "Unknown error during token refresh")
        # raise ValueError("Unable to refresh Google access token")
    connection.access_token = encrypt_token(access_token)
    expires_in = token_data.get("expires_in")
    connection.token_expiry = compute_expiry(expires_in)
    if token_data.get("scope"):
        connection.scope = token_data.get("scope")
    connection.save(update_fields=["access_token", "token_expiry", "scope", "updated_at"])
    return access_token, token_data.get("error_description", "")

def _coerce_access_token_result(result) -> tuple[str, str]:
    if isinstance(result, tuple):
        if len(result) >= 2:
            return result[0], result[1] or ""
        if len(result) == 1:
            return result[0], ""
    return result or "", ""

def _google_request_json(method: str, url: str, access_token: str, data: dict | None = None) -> dict:
    return _google_request_json_with_retry(method, url, access_token, data)


def _google_request_json_with_retry(
    method: str,
    url: str,
    access_token: str,
    data: dict | None = None,
    extra_headers: dict | None = None,
    retries: int = 3,
) -> dict:
    body = json.dumps(data).encode() if data is not None else None
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update({k: v for k, v in extra_headers.items() if v is not None})

    transient_statuses = {429, 500, 502, 503, 504}
    last_error = None
    for attempt in range(retries):
        print(url)
        req = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers=headers,
        )
        print(req.get_full_url)
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                raw = r.read().decode() or "{}"
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            detail = e.read().decode() if hasattr(e, "read") else str(e)
            if e.code == 412:
                raise ValueError(f"Google API conflict: {detail}")
            if e.code in transient_statuses and attempt < retries - 1:
                last_error = e
                pytime.sleep(0.2 * (attempt + 1))
                continue
            raise ValueError(f"Google API error {e.code}: {detail}")
        except urllib.error.URLError as e:
            last_error = e
            if attempt < retries - 1:
                pytime.sleep(0.2 * (attempt + 1))
                continue
            raise ValueError(f"Google API network error: {e}")
    if last_error:
        raise ValueError(f"Google API error: {last_error}")
    raise ValueError("Google API request failed")


def _google_request_no_content(method: str, url: str, access_token: str, extra_headers: dict | None = None) -> None:
    transient_statuses = {429, 500, 502, 503, 504}
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        **({k: v for k, v in (extra_headers or {}).items() if v is not None}),
    }
    for attempt in range(3):
        req = urllib.request.Request(
            url,
            method=method,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=25):
                return
        except urllib.error.HTTPError as e:
            if e.code == 204:
                return
            detail = e.read().decode() if hasattr(e, "read") else str(e)
            if e.code == 412:
                raise ValueError(f"Google API conflict: {detail}")
            if e.code in transient_statuses and attempt < 2:
                pytime.sleep(0.2 * (attempt + 1))
                continue
            raise ValueError(f"Google API error {e.code}: {detail}")
        except urllib.error.URLError as e:
            if attempt < 2:
                pytime.sleep(0.2 * (attempt + 1))
                continue
            raise ValueError(f"Google API network error: {e}")


def google_list_events(access_token: str, calendar_id: str = "primary", sync_token: str | None = None, user=None) -> dict:
    params = {"singleEvents": "true", "maxResults": "2500"}
    if sync_token:
        params["syncToken"] = sync_token
    else:
        if user and getattr(user, "verified_at", None):
            # Safe conversion to UTC string regardless of DB timezone settings
            verified_dt = user.verified_at
            if isinstance(verified_dt, str):
                verified_dt = datetime.fromisoformat(verified_dt)
            params["timeMin"] = verified_dt.astimezone(timezone.utc).isoformat()
        else:
            params["timeMin"] = datetime.now(timezone.utc).isoformat()
    url = GOOGLE_CALENDAR_EVENTS_URL.format(
        calendar_id=urllib.parse.quote(calendar_id, safe="")
    ) + "?" + urllib.parse.urlencode(params)

    return _google_request_json("GET", url, access_token)


def google_list_event_instances(
    access_token: str,
    event_id: str,
    calendar_id: str = "primary",
    time_min: datetime | None = None,
    time_max: datetime | None = None,
) -> dict:
    params = {}
    if time_min:
        params["timeMin"] = time_min.astimezone(timezone.utc).isoformat()
    if time_max:
        params["timeMax"] = time_max.astimezone(timezone.utc).isoformat()
    encoded_calendar = urllib.parse.quote(calendar_id, safe="")
    encoded_event_id = urllib.parse.quote(event_id, safe="")
    url = (
        f"https://www.googleapis.com/calendar/v3/calendars/{encoded_calendar}/events/"
        f"{encoded_event_id}/instances"
    )
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return _google_request_json("GET", url, access_token)


def ensure_watch(user, calendar_id: str = "primary") -> GoogleCalendarWatch:
    connection = GoogleCalendarConnection.objects.filter(user=user, is_active=True).first()
    if not connection:
        raise ValueError("Google Calendar not connected")
    access_token, error = _coerce_access_token_result(ensure_valid_access_token(connection))
    if error:
        raise ValueError(f"Access token error: {error}")
    channel_id = secrets.token_urlsafe(24)
    webhook_url = _env("GOOGLE_CALENDAR_WEBHOOK_URL", "")
    if not webhook_url:
        raise ValueError("GOOGLE_CALENDAR_WEBHOOK_URL is required")

    payload = {
        "id": channel_id,
        "type": "web_hook",
        "address": webhook_url,
    }
    url = GOOGLE_CALENDAR_WATCH_URL.format(calendar_id=urllib.parse.quote(calendar_id, safe=""))
    res = _google_request_json("POST", url, access_token, payload)
    expiration = None
    if res.get("expiration"):
        try:
            expiration = datetime.fromtimestamp(int(res["expiration"]) / 1000, tz=timezone.utc)
        except Exception:
            expiration = None

    watch, _ = GoogleCalendarWatch.objects.update_or_create(
        user=user,
        calendar_id=calendar_id,
        defaults={
            "channel_id": res.get("id", channel_id),
            "resource_id": res.get("resourceId", ""),
            "expiration": expiration,
            "is_active": True,
        },
    )
    return watch


def _parse_google_event_datetime(payload: dict) -> tuple[datetime | None, datetime | None, str | None]:
    start = payload.get("start", {}) or {}
    end = payload.get("end", {}) or {}
    tz = start.get("timeZone") or end.get("timeZone")
    start_raw = start.get("dateTime") or start.get("date")
    end_raw = end.get("dateTime") or end.get("date")
    if not start_raw:
        return None, None, tz
    if "T" not in start_raw:
        start_raw = f"{start_raw}T00:00:00+00:00"
    if end_raw and "T" not in end_raw:
        end_raw = f"{end_raw}T00:00:00+00:00"
    start_dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
    end_dt = datetime.fromisoformat(end_raw.replace("Z", "+00:00")) if end_raw else None
    return start_dt, end_dt, tz


def upsert_mirror_events(user, calendar_id: str, items: list[dict]) -> int:
    changed = 0
    for item in items:
        google_event_id = item.get("id")
        if not google_event_id:
            continue
        status = item.get("status")
        start_at, end_at, tz = _parse_google_event_datetime(item)
        if not start_at:
            continue
        defaults = {
            "title": item.get("summary"),
            "description": item.get("description"),
            "start_at": start_at,
            "end_at": end_at,
            "timezone": tz,
            "is_cancelled": status == "cancelled",
            "etag": item.get("etag"),
        }
        GoogleCalendarMirrorEvent.objects.update_or_create(
            user=user,
            calendar_id=calendar_id,
            google_event_id=google_event_id,
            defaults=defaults,
        )
        changed += 1
    return changed


def _map_app_status_to_google(app_status: str) -> tuple[str, dict]:
    """
    Map app occurrence status to Google Calendar event status.
    Returns (google_status, extended_properties)
    
    App statuses: pending, completed, skipped, missed
    Google statuses: confirmed, cancelled
    """
    status_map = {
        "pending": ("confirmed", {}),
        "completed": ("confirmed", {"app_status": "completed"}),
        "skipped": ("cancelled", {}),
        "missed": ("cancelled", {"app_status": "missed"}),
    }
    return status_map.get(app_status, ("confirmed", {}))


def _minutes_value_to_unit(minutes: int) -> dict:
    if minutes % 60 == 0 and minutes >= 60:
        return {"value": minutes // 60, "unit": "hours"}
    return {"value": minutes, "unit": "minutes"}


def _google_reminders_to_app_offsets(google_event: dict) -> list[dict]:
    reminders = (google_event.get("reminders") or {}).get("overrides") or []
    normalized = []
    for reminder in reminders:
        method = reminder.get("method")
        minutes = reminder.get("minutes")
        if method not in {"popup", "email"}:
            continue
        if not isinstance(minutes, int) or minutes <= 0:
            continue
        app_mode = "push" if method == "popup" else "email"
        unit_payload = _minutes_value_to_unit(minutes)
        normalized.append(
            {
                "value": unit_payload["value"],
                "unit": unit_payload["unit"],
                "modes": [app_mode],
            }
        )
    return normalized


def _app_reminders_to_google_overrides(parent_or_occurrence) -> list[dict]:
    overrides = []
    if hasattr(parent_or_occurrence, "reminders"):
        reminders = parent_or_occurrence.reminders.filter(is_deleted=False)
        for reminder in reminders:
            method = "popup" if reminder.mode == "push" else "email" if reminder.mode == "email" else None
            if not method:
                continue
            overrides.append(
                {
                    "method": method,
                    "minutes": int(reminder.offset_minutes),
                }
            )
        return overrides

    normalized = getattr(parent_or_occurrence, "get_normalized_reminders", None)
    if callable(normalized):
        for reminder in normalized() or []:
            modes = reminder.get("modes") or []
            minutes = reminder["value"] * 60 if reminder["unit"] == "hours" else reminder["value"]
            for mode in modes:
                method = "popup" if mode == "push" else "email" if mode == "email" else None
                if not method:
                    continue
                overrides.append({"method": method, "minutes": int(minutes)})
    return overrides


def _build_google_reminders_payload(parent_or_occurrence) -> dict:
    overrides = _app_reminders_to_google_overrides(parent_or_occurrence)
    if not overrides:
        return {"useDefault": True}
    return {"useDefault": False, "overrides": overrides}


def _google_time_zone_name() -> str:
    return dj_timezone.get_current_timezone_name() or "Asia/Kolkata"


def _google_date_time_payload(date_value, time_value) -> tuple[str, str]:
    local_time = time_value or time(0, 0)
    aware_dt = dj_timezone.make_aware(
        datetime.combine(date_value, local_time),
        dj_timezone.get_current_timezone(),
    )
    return aware_dt.isoformat(), _google_time_zone_name()

def _get_task_duration_config(task):
    duration_config = getattr(task, "duration_config", None) or {"value": 30, "unit": "minutes"}
    dummy_date = datetime.combine(task.start_date, task.start_time)
    if duration_config["unit"] == "minutes":
        return (dummy_date + timedelta(minutes=duration_config["value"])).time()
    return (dummy_date + timedelta(hours=duration_config["value"])).time()

def _occurrence_due_dt(occurrence):
    from datetime import datetime, time as dt_time

    scheduled_time = occurrence.scheduled_time or dt_time(0, 0)
    return dj_timezone.make_aware(
        datetime.combine(occurrence.scheduled_date, scheduled_time),
        dj_timezone.get_current_timezone(),
    )


def sync_parent_reminders_to_google(user, parent, calendar_id: str = "primary") -> dict:
    connection = GoogleCalendarConnection.objects.filter(user=user, is_active=True).first()
    if not connection:
        return {"synced": False, "reason": "not_connected"}
    if not getattr(parent, "google_event_id", None):
        return {"synced": False, "reason": "missing_google_event_id"}

    access_token, error = _coerce_access_token_result(ensure_valid_access_token(connection))
    if error:
        return {"synced": False, "reason": f"access_token_error: {error}"}
    encoded_calendar = urllib.parse.quote(calendar_id, safe="")
    encoded_event = urllib.parse.quote(parent.google_event_id, safe="")
    event_url = f"https://www.googleapis.com/calendar/v3/calendars/{encoded_calendar}/events/{encoded_event}"
    payload = {"reminders": _build_google_reminders_payload(parent)}
    mapping = EventSyncMap.objects.filter(user=user, calendar_id=calendar_id, google_event_id=parent.google_event_id).first()
    extra_headers = {"If-Match": mapping.google_etag} if mapping and mapping.google_etag else None

    res = _google_request_json_with_retry("PATCH", event_url, access_token, payload, extra_headers=extra_headers)
    if mapping:
        mapping.etag = res.get("etag")
        mapping.google_etag = res.get("etag")
        mapping.last_google_updated_at = dj_timezone.now()
        mapping.save(update_fields=["etag", "google_etag", "last_google_updated_at", "updated_at"])

    return {
        "synced": True,
        "google_event_id": parent.google_event_id,
        "etag": res.get("etag"),
    }



def delete_google_event_from_calendar(user, google_event_id: str, calendar_id: str = "primary", google_etag: str | None = None) -> dict:
    connection = GoogleCalendarConnection.objects.filter(user=user, is_active=True).first()
    if not connection:
        return {"deleted": False, "reason": "not_connected"}

    access_token, error = _coerce_access_token_result(ensure_valid_access_token(connection))
    if error:
        return {"deleted": False, "reason": f"access_token_error: {error}"}

    encoded_calendar = urllib.parse.quote(calendar_id, safe="")
    encoded_event = urllib.parse.quote(google_event_id, safe="")
    delete_url = f"https://www.googleapis.com/calendar/v3/calendars/{encoded_calendar}/events/{encoded_event}"
    extra_headers = {"If-Match": google_etag} if google_etag else None
    try:
        _google_request_no_content("DELETE", delete_url, access_token, extra_headers=extra_headers)
    except Exception as exc:
        return {"deleted": False, "reason": str(exc), "google_event_id": google_event_id}

    mapping = EventSyncMap.objects.filter(user=user, calendar_id=calendar_id, google_event_id=google_event_id).first()
    if mapping:
        mapping.is_deleted = True
        mapping.last_google_updated_at = dj_timezone.now()
        mapping.save(update_fields=["is_deleted", "last_google_updated_at", "updated_at"])

    return {"deleted": True, "google_event_id": google_event_id}


def delete_google_events_from_calendar(user, google_event_ids: list[str], calendar_id: str = "primary") -> dict:
    deleted_count = 0
    failed_google_event_ids: list[str] = []
    for google_event_id in google_event_ids:
        if not google_event_id:
            continue
        result = delete_google_event_from_calendar(user, google_event_id, calendar_id=calendar_id)
        if result.get("deleted"):
            deleted_count += 1
        else:
            failed_google_event_ids.append(google_event_id)
    return {
        "deleted": True,
        "deleted_count": deleted_count,
        "failed_google_event_ids": failed_google_event_ids,
    }


def delete_parent_from_google(user, parent, calendar_id: str = "primary") -> dict:
    """
    Remove a task/habit from Google Calendar when the parent is deleted in-app.
    Recurring parents delete their root event; non-recurring parents delete
    each mirrored occurrence.
    """
    if not parent:
        return {"deleted": False, "reason": "missing_parent"}

    parent_event_id = getattr(parent, "google_event_id", None)
    if getattr(parent, "recurrence_rule", None) and parent_event_id:
        mapping = EventSyncMap.objects.filter(user=user, calendar_id=calendar_id, google_event_id=parent_event_id).first()
        result = delete_google_event_from_calendar(
            user,
            parent_event_id,
            calendar_id=calendar_id,
            google_etag=getattr(mapping, "google_etag", None),
        )
        if result.get("deleted"):
            EventSyncMap.objects.filter(user=user, calendar_id=calendar_id, google_event_id=parent_event_id).update(
                is_deleted=True,
                last_google_updated_at=dj_timezone.now(),
            )
        return result

    from tracker.models import TaskOccurrence

    occurrence_filters = {"task": parent} if getattr(parent, "is_habit", False) is False else {"habit": parent}
    deleted_count = 0
    for occurrence in TaskOccurrence.objects.filter(**occurrence_filters).order_by("scheduled_date", "scheduled_time", "created_at"):
        mapping = EventSyncMap.objects.filter(
            user=user,
            calendar_id=calendar_id,
            local_occurrence_id=occurrence.id,
        ).first()
        google_event_id = getattr(mapping, "google_event_id", None) or getattr(occurrence, "google_event_id", None)
        google_etag = getattr(mapping, "google_etag", None)
        if not google_event_id:
            continue
        if mapping and mapping.is_deleted:
            continue
        result = delete_google_event_from_calendar(
            user,
            google_event_id,
            calendar_id=calendar_id,
            google_etag=google_etag,
        )
        if result.get("deleted"):
            deleted_count += 1

    return {"deleted": True, "deleted_occurrences": deleted_count, "parent_type": "habit" if getattr(parent, "is_habit", False) else "task"}


def _delete_soft_deleted_parent_occurrences_from_google(user, parent, calendar_id: str = "primary") -> int:
    """
    Remove Google events that belong to soft-deleted local occurrences.

    This prevents schedule edits that regenerate occurrences from leaving the
    old Google events behind as duplicates.
    """
    if not parent:
        return 0

    from tracker.models import TaskOccurrence
    occurrence_filters = {"task": parent} if getattr(parent, "is_habit", False) is False else {"habit": parent}
    occurrences = TaskOccurrence.objects.filter(**occurrence_filters).order_by("scheduled_date", "scheduled_time", "created_at")
    deleted_count = 0
    for occurrence in occurrences:
        if occurrence.is_deleted is False:
            continue
        mapping = EventSyncMap.objects.filter(
            user=user,
            calendar_id=calendar_id,
            local_occurrence_id=occurrence.id,
        ).first()
        google_event_id = getattr(mapping, "google_event_id", None) or getattr(occurrence, "google_event_id", None)
        google_etag = getattr(mapping, "google_etag", None)
        if not google_event_id:
            continue
        if mapping and mapping.is_deleted:
            continue
        result = delete_google_event_from_calendar(
            user,
            google_event_id,
            calendar_id=calendar_id,
            google_etag=google_etag,
        )
        if result.get("deleted"):
            deleted_count += 1

    return deleted_count

def sync_parent_occurrences_to_google(
    user,
    parent,
    calendar_id: str = "primary",
    stale_google_event_ids: list[str] | None = None,
) -> dict:
    """
    Push one-off app-generated occurrences to Google after they are generated.
    Recurring parents are skipped because their Google root event is the source
    of truth.
    """
    if not parent:
        return {"synced": False, "reason": "missing_parent"}
    deleted_stale = 0
    if stale_google_event_ids:
        deleted_stale = delete_google_events_from_calendar(
            user,
            stale_google_event_ids,
            calendar_id=calendar_id,
        ).get("deleted_count", 0)
    recurrence_rule = _ensure_parent_recurrence_rule(parent)
    if recurrence_rule:
        print(f"Recurring parent detected for sync_parent_occurrences_to_google: {parent} with recurrence_rule: {recurrence_rule}")
        return {"synced": False, "reason": "recurring_parent", "deleted_stale_occurrences": deleted_stale}
    if getattr(parent, "external_google_id", False) or getattr(parent, "synced_from_google", False):
        return {"synced": False, "reason": "google_source_of_truth", "deleted_stale_occurrences": deleted_stale}

    from tracker.models import TaskOccurrence

    occurrence_filters = {"task": parent} if getattr(parent, "is_habit", False) is False else {"habit": parent}
    synced = 0
    deleted_count = _delete_soft_deleted_parent_occurrences_from_google(user, parent, calendar_id=calendar_id)
    for occurrence in TaskOccurrence.objects.filter(**occurrence_filters, is_deleted=False).order_by("scheduled_date", "scheduled_time"):
        result = push_local_occurrence_change(user, occurrence, action="create", calendar_id=calendar_id)
        if result.get("pushed"):
            synced += 1
            if result.get("google_event_id") and result.get("action")!="delete":
                occurrence.google_event_id = result["google_event_id"]
                occurrence.save(update_fields=["google_event_id", "updated_at"])
        if not result.get("pushed") and result.get("reason") in {"access_token_error", "not_connected"}:
            return {
                "synced": False,
                "reason": result.get("reason"),
                "deleted_stale_occurrences": deleted_stale,
                "deleted_occurrences": deleted_count,
                "pushed_occurrences": synced,
            }

    return {
        "synced": True,
        "deleted_stale_occurrences": deleted_stale,
        "deleted_occurrences": deleted_count,
        "pushed_occurrences": synced,
    }


def sync_parent_action_to_google(
    user,
    parent,
    action: str,
    occurrence=None,
    calendar_id: str = "primary",
    stale_google_event_ids: list[str] | None = None,
) -> dict:
    if not parent:
        return {"synced": False, "reason": "missing_parent"}
    if action == "delete" or getattr(parent, "is_deleted", False):
        return delete_parent_from_google(user, parent, calendar_id=calendar_id)
    recurrence_rule = _ensure_parent_recurrence_rule(parent)
    if recurrence_rule:
        print(f"Recurring parent detected for sync_parent_action_to_google: {parent} with recurrence_rule: {recurrence_rule}")
        if occurrence is not None:
            print(f"Syncing recurring occurrence change for occurrence: {occurrence} with action: {action}")
            return handle_recurring_occurrence_change(user, occurrence, action=action, calendar_id=calendar_id)
        return create_recurring_google_event(user, parent, calendar_id=calendar_id)
    if action in {"create", "update"}:
        return sync_parent_occurrences_to_google(
            user,
            parent,
            calendar_id=calendar_id,
            stale_google_event_ids=stale_google_event_ids,
        )
    return {"synced": False, "reason": f"unsupported_action:{action}"}

def sync_google_notifications_to_app(user, google_event: dict, parent=None, occurrence=None) -> dict:
    from tracker.models import TaskOccurrence

    reminder_offsets = _google_reminders_to_app_offsets(google_event)
    if not reminder_offsets:
        return {"synced": False, "reason": "no_google_reminders"}

    if occurrence is None and parent is not None:
        occurrence = parent.occurrences.filter(is_deleted=False).order_by("scheduled_date", "scheduled_time", "created_at").first()
    if occurrence is None:
        return {"synced": False, "reason": "occurrence_not_found"}

    removed_ids = []
    existing = occurrence.reminders.all()
    for reminder in existing:
        reminder.is_deleted = True
        reminder.synced_to_google = True
        reminder.save(update_fields=["is_deleted", "synced_to_google", "updated_at"])
        removed_ids.append(str(reminder.id))

    created_ids = []
    for reminder in reminder_offsets:
        modes = reminder["modes"]
        minutes = reminder["value"] * 60 if reminder["unit"] == "hours" else reminder["value"]
        for mode in modes:
            reminder_row, _ = occurrence.reminders.update_or_create(
                offset_minutes=minutes,
                mode=mode,
                defaults={
                    "remind_at": _occurrence_due_dt(occurrence) - timedelta(minutes=minutes),
                    "event_emitted": False,
                    "event_emitted_at": None,
                    "sent": False,
                    "sent_at": None,
                    "google_notification_id": google_event.get("id"),
                    "synced_to_google": True,
                    "is_deleted": False,
                },
            )
            created_ids.append(str(reminder_row.id))

    if parent is not None:
        parent.reminder_enabled = True
        parent.reminder_offset = reminder_offsets
        parent.save(update_fields=["reminder_enabled", "reminder_offset", "updated_at"])

    return {"synced": True, "created": created_ids, "removed": removed_ids}


def _map_google_status_to_app(google_status: str, extended_props: dict) -> str:
    """
    Map Google Calendar event status back to app occurrence status.
    Uses both google_status and stored app_status in extendedProperties.
    """
    app_status = extended_props.get("app_status")
    
    if google_status == "cancelled":
        return app_status if app_status in ["missed", "skipped"] else "skipped"
    
    # google_status == "confirmed"
    return app_status if app_status == "completed" else "pending"


def pull_google_delta_for_watch(watch: GoogleCalendarWatch) -> dict:
    connection = GoogleCalendarConnection.objects.filter(user=watch.user, is_active=True).first()
    if not connection:
        raise ValueError("Google Calendar connection not found")
    access_token, error = _coerce_access_token_result(ensure_valid_access_token(connection))
    if error:
        return {"synced": False, "reason": f"access_token_error: {error}"}
    data = google_list_events(access_token, calendar_id=watch.calendar_id, sync_token=watch.sync_token, user=watch.user)
    items = data.get("items", [])
    changed = upsert_mirror_events(watch.user, watch.calendar_id, items)
    
    recurring_synced = 0
    for item in items:
        if item.get("recurringEventId") or item.get("recurrence"):
            result = sync_google_recurring_change(watch.user, item, calendar_id=watch.calendar_id)
            if result.get("synced"):
                recurring_synced += 1
        else:
            external_result = sync_external_google_event_to_app(watch.user, item, calendar_id=watch.calendar_id)
            if external_result.get("synced"):
                recurring_synced += 1

    # Sync status changes from Google to app occurrences
    status_synced = sync_google_status_to_app_occurrences(watch.user, watch.calendar_id, items)
    
    next_sync_token = data.get("nextSyncToken")
    if next_sync_token:
        watch.sync_token = next_sync_token
        watch.save(update_fields=["sync_token", "updated_at"])
    return {
        "changed": changed,
        "recurring_synced": recurring_synced,
        "status_synced": status_synced,
        "next_sync_token": next_sync_token,
    }


def sync_google_status_to_app_occurrences(user, calendar_id: str, google_items: list[dict]) -> int:
    """
    Sync status changes from Google Calendar events to app occurrences.
    Used by pull_google_delta_for_watch to update occurrence statuses.
    """
    from tracker.models import TaskOccurrence
    
    synced_count = 0
    for item in google_items:
        google_event_id = item.get("id")
        if not google_event_id:
            continue
        
        # Find mapping between Google event and app occurrence
        mapping = EventSyncMap.objects.filter(
            user=user,
            calendar_id=calendar_id,
            google_event_id=google_event_id,
            is_deleted=False,
        ).first()
        
        if not mapping:
            continue
        
        try:
            occurrence = TaskOccurrence.objects.get(id=mapping.local_occurrence_id)
        except TaskOccurrence.DoesNotExist:
            continue

        if _is_google_update_stale(mapping, item):
            continue
        
        # Map Google status to app status
        google_status = item.get("status", "confirmed")
        extended_props = item.get("extendedProperties", {}).get("private", {})
        new_app_status = _map_google_status_to_app(google_status, extended_props)
        
        # Only update if status has changed
        if occurrence.status != new_app_status:
            occurrence.status = new_app_status
            occurrence.save(update_fields=["status", "updated_at"])
            synced_count += 1
            
            # Update mapping timestamps
            mapping.last_google_updated_at = dj_timezone.now()
            mapping.etag = item.get("etag")
            mapping.google_etag = item.get("etag")
            mapping.save(update_fields=["last_google_updated_at", "etag", "google_etag", "updated_at"])
    
    return synced_count


def _to_naive_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if dj_timezone.is_aware(value):
        # Extract the timezone name or offset
        tz_name = str(value.tzinfo)
        
        # Check if the incoming datetime is explicitly UTC
        if "UTC" in tz_name or value.utcoffset().total_seconds() == 0:
            # Convert UTC to local system time (IST), then strip tzinfo
            return dj_timezone.localtime(value).replace(tzinfo=None)
            
        # Check if the incoming datetime is already IST
        elif "IST" in tz_name or value.utcoffset().total_seconds() == 19800: # 5h 30m
            # Already IST, just strip the timezone info
            return value.replace(tzinfo=None)
            
        # Fallback for any other aware timezone
        return dj_timezone.localtime(value, timezone.utc).replace(tzinfo=None)
    return value


def _google_event_updated_at(google_event: dict) -> datetime | None:
    updated_raw = google_event.get("updated")
    if not updated_raw:
        return None
    try:
        updated_dt = datetime.fromisoformat(updated_raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if updated_dt.tzinfo is None:
        updated_dt = updated_dt.replace(tzinfo=timezone.utc)
    return updated_dt.astimezone(timezone.utc)


def _is_google_update_stale(mapping: EventSyncMap | None, google_event: dict) -> bool:
    if not mapping:
        return False
    google_updated_at = _google_event_updated_at(google_event)
    if not google_updated_at or not mapping.last_local_updated_at:
        return False
    local_updated_at = mapping.last_local_updated_at
    if dj_timezone.is_naive(local_updated_at):
        local_updated_at = dj_timezone.make_aware(local_updated_at, timezone.utc)
    return google_updated_at <= local_updated_at.astimezone(timezone.utc)


def _extract_rrule_from_google_event(google_event: dict) -> str | None:
    recurrence = google_event.get("recurrence") or []
    return RRuleHandler.google_recurrence_to_rrule(recurrence)


def _google_original_start_to_recurrence_id(original_start: dict | str | None) -> str | None:
    if not original_start:
        return None
    if isinstance(original_start, dict):
        raw = original_start.get("dateTime") or original_start.get("date")
    else:
        raw = original_start
    if not raw:
        return None
    if "T" not in raw:
        return f"{raw.replace('-', '')}T000000Z"
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _update_rrule_until_date(rrule_str: str, until_date) -> str:
    parts = [part for part in (rrule_str or "").split(";") if part and not part.startswith("UNTIL=")]
    parts.append(f"UNTIL={until_date.strftime('%Y%m%d')}")
    return ";".join(parts)


def _ensure_parent_recurrence_rule(parent):
    recurrence_rule = getattr(parent, "recurrence_rule", None)
    if recurrence_rule:
        return recurrence_rule

    recurrence_rule = build_recurrence_rule_for_entity(parent)
    if recurrence_rule:
        parent.recurrence_rule = recurrence_rule
        parent._skip_google_calendar_sync = True
        try:
            parent.save(update_fields=["recurrence_rule", "updated_at"])
        finally:
            parent._skip_google_calendar_sync = False
    return recurrence_rule


def _find_google_recurring_instance(access_token: str, calendar_id: str, task, occurrence):
    scheduled_time = occurrence.scheduled_time or task.start_time or time(0, 0)
    start_dt = datetime.combine(occurrence.scheduled_date, scheduled_time)
    time_min = start_dt - timedelta(minutes=1)
    time_max = start_dt + timedelta(days=1)
    data = google_list_event_instances(
        access_token,
        task.google_event_id,
        calendar_id=calendar_id,
        time_min=time_min,
        time_max=time_max,
    )
    target_recurrence_id = occurrence.google_recurrence_id or start_dt.strftime("%Y%m%dT%H%M%SZ")
    for item in data.get("items", []):
        recurrence_id = _google_original_start_to_recurrence_id(item.get("originalStartTime"))
        if recurrence_id == target_recurrence_id:
            return item
    return None


def _build_task_defaults_from_google_event(google_event: dict, rrule_str: str) -> dict:
    start_dt, end_dt, _ = _parse_google_event_datetime(google_event)
    start_dt = _to_naive_datetime(start_dt)
    end_dt = _to_naive_datetime(end_dt)
    parsed_rrule = RRuleHandler.parse_rrule(rrule_str)

    byday_codes = parsed_rrule.get("byday") or []
    day_map = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
    frequency_days = [day_map[code] for code in byday_codes if code in day_map]
    until_date = parsed_rrule.get("until_date")

    duration_config = None
    if start_dt and end_dt and end_dt > start_dt:
        duration_minutes = int((end_dt - start_dt).total_seconds() // 60)
        if duration_minutes > 0:
            duration_config = {"unit": "minutes", "value": duration_minutes}

    return {
        "title": google_event.get("summary") or "Google Calendar Event",
        "description": google_event.get("description") or "",
        "section": "personal",
        "status": "pending",
        "frequency_type": parsed_rrule.get("frequency_type", "once"),
        "frequency_interval": parsed_rrule.get("frequency_interval", 1),
        "frequency_days": frequency_days,
        "day_of_week": frequency_days[0] if len(frequency_days) == 1 else None,
        "day_of_month": parsed_rrule.get("bymonthday"),
        "start_date": start_dt.date() if start_dt else None,
        "end_date": until_date.date() if until_date else (end_dt.date() if end_dt else None),
        "start_time": start_dt.time() if start_dt else None,
        "end_time": end_dt.time() if end_dt else None,
        "duration_config": duration_config,
        "google_event_id": google_event.get("id"),
        "recurrence_rule": rrule_str,
        "external_google_id": True,
    }


def _get_parent_type_from_google_event(google_event: dict) -> str:
    ext_props = (google_event.get("extendedProperties") or {}).get("private", {})
    app_type = ext_props.get("app_type")
    return "habit" if app_type == "habit" else "task"


def _build_parent_defaults_from_google_event(google_event: dict, rrule_str: str, parent_type: str) -> dict:
    defaults = _build_task_defaults_from_google_event(google_event, rrule_str)
    if parent_type == "habit":
        defaults["status"] = "active"
    return defaults


def sync_google_recurring_to_app(
    user,
    google_event: dict,
    calendar_id: str = "primary",
    max_occurrences: int = 52,
) -> dict:
    """
    Sync a Google recurring event into a local Task plus TaskOccurrence rows.
    """
    from tracker.models import Task, Habit, TaskOccurrence

    google_event_id = google_event.get("id")
    if not google_event_id:
        return {"synced": False, "reason": "missing_google_event_id"}

    rrule_str = _extract_rrule_from_google_event(google_event)
    if not rrule_str:
        return {"synced": False, "reason": "not_recurring"}

    ext_props = (google_event.get("extendedProperties") or {}).get("private", {})
    app_task_id = ext_props.get("app_id")
    parent_type = _get_parent_type_from_google_event(google_event)
    parent_model = Habit if parent_type == "habit" else Task
    parent = None

    if app_task_id:
        parent = parent_model.objects.filter(id=app_task_id, user=user).first()
    if parent is None:
        parent = parent_model.objects.filter(user=user, google_event_id=google_event_id).first()

    parent_defaults = _build_parent_defaults_from_google_event(google_event, rrule_str, parent_type)

    if parent is None:
        if parent_defaults["start_date"] is None or parent_defaults["start_time"] is None:
            return {"synced": False, "reason": "missing_start_datetime"}
        parent = parent_model.objects.create(user=user, **parent_defaults)
        parent_created = True
    else:
        parent_created = False
        updated_fields = []
        for field, value in parent_defaults.items():
            if getattr(parent, field) != value:
                setattr(parent, field, value)
                updated_fields.append(field)
        if updated_fields:
            parent.save(update_fields=updated_fields + ["updated_at"])

    start_dt, _, _ = _parse_google_event_datetime(google_event)
    start_dt = _to_naive_datetime(start_dt)
    if start_dt is None:
        return {"synced": False, "reason": "missing_start_datetime"}

    occurrence_datetimes = RRuleHandler.expand_rrule(
        rrule_str=rrule_str,
        start_date=start_dt,
        count=max_occurrences,
    )

    created_count = 0
    for occurrence_dt in occurrence_datetimes:
        occurrence, created = TaskOccurrence.objects.update_or_create(
            task=parent if parent_type == "task" else None,
            habit=parent if parent_type == "habit" else None,
            scheduled_date=occurrence_dt.date(),
            scheduled_time=occurrence_dt.time(),
            defaults={
                "status": "pending",
                "google_event_id": google_event_id,
                "google_recurrence_id": occurrence_dt.strftime("%Y%m%dT%H%M%SZ"),
                "synced_from_google": True,
            },
        )
        if created:
            created_count += 1

    EventSyncMap.objects.update_or_create(
        user=user,
        calendar_id=calendar_id,
        google_event_id=google_event_id,
        defaults={
            "local_occurrence_id": parent.id,
            "local_parent_type": parent_type,
            "local_task_id": parent.id if parent_type == "task" else None,
            "is_recurring": True,
            "recurrence_rule": rrule_str,
            "google_etag": google_event.get("etag"),
            "etag": google_event.get("etag"),
            "last_google_updated_at": dj_timezone.now(),
            "is_deleted": False,
        },
    )

    if google_event.get("reminders"):
        sync_google_notifications_to_app(user, google_event, parent=parent)

    logger.info(
        "Synced recurring Google event %s to task %s (%s, %s new occurrences)",
        google_event_id,
        parent.id,
        "created" if parent_created else "updated",
        created_count,
    )
    return {
        "synced": True,
        "parent_type": parent_type,
        "task_id": str(parent.id),
        "task_created": parent_created,
        "occurrences_total": len(occurrence_datetimes),
        "occurrences_created": created_count,
    }


def sync_google_recurring_change(user, google_event: dict, calendar_id: str = "primary") -> dict:
    from tracker.models import Task, Habit, TaskOccurrence

    recurring_event_id = google_event.get("recurringEventId")
    google_event_id = google_event.get("id")
    status = google_event.get("status", "confirmed")
    mapping = EventSyncMap.objects.filter(
        user=user,
        calendar_id=calendar_id,
        google_event_id=google_event_id,
        is_deleted=False,
    ).first()
    if _is_google_update_stale(mapping, google_event):
        return {"synced": False, "reason": "stale_google_event"}

    if recurring_event_id:
        recurrence_id = _google_original_start_to_recurrence_id(google_event.get("originalStartTime"))
        occurrence = TaskOccurrence.objects.filter(
            task__user=user,
            task__google_event_id=recurring_event_id,
            google_recurrence_id=recurrence_id,
            is_deleted=False,
        ).first()
        if not occurrence:
            return {"synced": False, "reason": "occurrence_not_found"}

        extended_props = (google_event.get("extendedProperties") or {}).get("private", {})
        occurrence.status = _map_google_status_to_app(status, extended_props)
        occurrence.google_event_id = google_event_id
        occurrence.google_recurrence_id = recurrence_id
        occurrence.synced_from_google = True
        occurrence.save(update_fields=["status", "google_event_id", "google_recurrence_id", "synced_from_google", "updated_at"])

        EventSyncMap.objects.update_or_create(
            user=user,
            local_occurrence_id=occurrence.id,
            defaults={
                "local_parent_type": "task",
                "local_task_id": occurrence.task_id,
                "google_event_id": google_event_id,
                "calendar_id": calendar_id,
                "etag": google_event.get("etag"),
                "google_etag": google_event.get("etag"),
                "last_google_updated_at": dj_timezone.now(),
                "is_deleted": False,
            },
        )
        if google_event.get("reminders"):
            sync_google_notifications_to_app(user, google_event, occurrence=occurrence)
        return {"synced": True, "kind": "exception", "occurrence_id": str(occurrence.id)}

    parent = Task.objects.filter(user=user, google_event_id=google_event_id).first()
    parent_kind = "task"
    if not parent:
        parent = Habit.objects.filter(user=user, google_event_id=google_event_id).first()
        parent_kind = "habit"
    if not parent:
        return {"synced": False, "reason": "parent_not_found"}

    if status == "cancelled":
        parent.is_deleted = True
        parent.save(update_fields=["is_deleted", "updated_at"])
        parent.occurrences.filter(is_deleted=False).update(status="skipped", updated_at=dj_timezone.now())
        EventSyncMap.objects.filter(user=user, google_event_id=google_event_id).update(
            is_deleted=True,
            last_google_updated_at=dj_timezone.now(),
            updated_at=dj_timezone.now(),
        )
        return {"synced": True, "kind": "series_cancelled", "parent_type": parent_kind, "task_id": str(parent.id)}

    return sync_google_recurring_to_app(user, google_event, calendar_id=calendar_id)


def sync_external_google_event_to_app(user, google_event: dict, calendar_id: str = "primary") -> dict:
    from tracker.models import Task, TaskOccurrence

    google_event_id = google_event.get("id")
    if not google_event_id:
        return {"synced": False, "reason": "missing_google_event_id"}

    status = google_event.get("status", "confirmed")

    ext_props = (google_event.get("extendedProperties") or {}).get("private", {})
    if ext_props.get("created_by") == "sadhak_app":
        return {"synced": False, "reason": "app_created"}

    existing = EventSyncMap.objects.filter(
        user=user,
        calendar_id=calendar_id,
        google_event_id=google_event_id,
        is_deleted=False,
    ).first()
    if _is_google_update_stale(existing, google_event):
        return {"synced": False, "reason": "stale_google_event"}
    if existing:
        if status == "cancelled":
            parent = None
            if existing.local_parent_type == "habit":
                from tracker.models import Habit

                parent = Habit.objects.filter(id=existing.local_task_id, user=user).first() if existing.local_task_id else None
            else:
                parent = Task.objects.filter(id=existing.local_task_id, user=user).first() if existing.local_task_id else None
            occurrence = TaskOccurrence.objects.filter(id=existing.local_occurrence_id, is_deleted=False).first()
            if occurrence:
                occurrence.status = "skipped"
                occurrence.is_deleted = True
                occurrence.save(update_fields=["status", "is_deleted", "updated_at"])
            if parent:
                parent.is_deleted = True
                parent.save(update_fields=["is_deleted", "updated_at"])
            existing.is_deleted = True
            existing.save(update_fields=["is_deleted", "updated_at"])
            return {"synced": True, "reason": "external_cancelled"}
        if google_event.get("recurrence"):
            return sync_google_recurring_change(user, google_event, calendar_id=calendar_id)
        if google_event.get("reminders"):
            from tracker.models import Task, TaskOccurrence, Habit

            parent = None
            occurrence = None
            if existing.local_parent_type == "habit":
                parent = Habit.objects.filter(id=existing.local_task_id, user=user).first() if existing.local_task_id else None
            else:
                parent = Task.objects.filter(id=existing.local_task_id, user=user).first() if existing.local_task_id else None
            occurrence = TaskOccurrence.objects.filter(id=existing.local_occurrence_id, is_deleted=False).first()
            if parent or occurrence:
                sync_google_notifications_to_app(user, google_event, parent=parent, occurrence=occurrence)
                return {"synced": True, "reason": "reminders_updated"}

        parent = None
        if existing.local_parent_type == "habit":
            from tracker.models import Habit

            parent = Habit.objects.filter(id=existing.local_task_id, user=user).first() if existing.local_task_id else None
        else:
            parent = Task.objects.filter(id=existing.local_task_id, user=user).first() if existing.local_task_id else None
        if parent is not None:
            parent.title = google_event.get("summary") or parent.title
            parent.description = google_event.get("description") or parent.description
            parent.save(update_fields=["title", "description", "updated_at"])
            return {"synced": True, "reason": "updated_from_google"}
        return {"synced": False, "reason": "already_synced"}

    if google_event.get("recurrence"):
        return sync_google_recurring_to_app(user, google_event, calendar_id=calendar_id)

    if status == "cancelled":
        return {"synced": False, "reason": "cancelled_without_local_record"}

    start_dt, end_dt, _ = _parse_google_event_datetime(google_event)
    start_dt = _to_naive_datetime(start_dt)
    end_dt = _to_naive_datetime(end_dt)
    duration = (end_dt - start_dt) if start_dt and end_dt else None
    if start_dt is None:
        return {"synced": False, "reason": "missing_start_datetime"}

    all_day = bool((google_event.get("start") or {}).get("date"))
    normalized_end_date = start_dt.date()
    if all_day and end_dt:
        normalized_end_date = end_dt.date() - timedelta(days=1)
    elif end_dt:
        normalized_end_date = end_dt.date()

    reminder_offsets = _google_reminders_to_app_offsets(google_event)
    task = Task.objects.create(
        user=user,
        title=google_event.get("summary") or "Google Calendar Event",
        description=google_event.get("description") or "",
        section="personal",
        status="pending",
        frequency_type="once",
        start_date=start_dt.date(),
        end_date=normalized_end_date,
        start_time=start_dt.time(),
        end_time=end_dt.time() if end_dt and not all_day else None,
        duration_config={"unit": "minutes", "value": int(duration.total_seconds() // 60)} if duration else None,
        google_event_id=google_event_id,
        recurrence_rule=None,
        external_google_id=True,
        reminder_enabled=bool(reminder_offsets),
        reminder_offset=reminder_offsets,
    )
    occurrence = TaskOccurrence.objects.create(
        task=task,
        scheduled_date=start_dt.date(),
        scheduled_time=start_dt.time(),
        schedule_end_time=end_dt.time() if end_dt else None,
        status="pending",
        google_event_id=google_event_id,
        synced_from_google=True,
    )
    sync_google_notifications_to_app(user, google_event, parent=task, occurrence=occurrence)
    EventSyncMap.objects.create(
        user=user,
        local_occurrence_id=occurrence.id,
        local_parent_type="task",
        local_task_id=task.id,
        google_event_id=google_event_id,
        calendar_id=calendar_id,
        etag=google_event.get("etag"),
        last_google_updated_at=dj_timezone.now(),
        is_deleted=False,
    )
    return {"synced": True, "task_id": str(task.id), "occurrence_id": str(occurrence.id), "created": True}


def handle_recurring_occurrence_change(user, occurrence, action: str, calendar_id: str = "primary") -> dict:
    parent = occurrence.task or occurrence.habit
    parent_type = "task" if occurrence.task_id else "habit"
    recurrence_rule = _ensure_parent_recurrence_rule(parent) if parent else None
    if not parent or not recurrence_rule or not getattr(parent, "google_event_id", None):
        return {"pushed": False, "reason": "not_recurring"}

    connection = GoogleCalendarConnection.objects.filter(user=user, is_active=True).first()
    if not connection:
        return {"pushed": False, "reason": "not_connected"}

    access_token, error = _coerce_access_token_result(ensure_valid_access_token(connection))
    if error:
        return {"pushed": False, "reason": f"access_token_error: {error}"}
    instance = _find_google_recurring_instance(access_token, calendar_id, parent, occurrence)
    if not instance:
        return {"pushed": False, "reason": "instance_not_found"}

    instance_id = instance.get("id")
    if not instance_id:
        return {"pushed": False, "reason": "instance_missing_id"}

    encoded_calendar = urllib.parse.quote(calendar_id, safe="")
    encoded_event = urllib.parse.quote(instance_id, safe="")
    instance_url = f"https://www.googleapis.com/calendar/v3/calendars/{encoded_calendar}/events/{encoded_event}"
    extra_headers = {}
    if instance.get("etag"):
        extra_headers["If-Match"] = instance.get("etag")

    if action == "delete" or occurrence.is_deleted:
        _google_request_no_content("DELETE", instance_url, access_token, extra_headers=extra_headers)
        occurrence.google_event_id = instance_id
        occurrence.google_recurrence_id = _google_original_start_to_recurrence_id(instance.get("originalStartTime"))
        occurrence.save(update_fields=["google_event_id", "google_recurrence_id", "updated_at"])
        EventSyncMap.objects.update_or_create(
            user=user,
            local_occurrence_id=occurrence.id,
            defaults={
                "local_parent_type": parent_type,
                "local_task_id": parent.id if parent_type == "task" else None,
                "google_event_id": instance_id,
                "calendar_id": calendar_id,
                "etag": instance.get("etag"),
                "google_etag": instance.get("etag"),
                "is_deleted": True,
                "last_local_updated_at": dj_timezone.now(),
            },
        )
        return {"pushed": True, "action": "delete", "google_event_id": instance_id}

    google_status, extended_props = _map_app_status_to_google(occurrence.status)
    start_iso = (instance.get("start") or {}).get("dateTime")
    end_iso = (instance.get("end") or {}).get("dateTime")
    payload = {
        "summary": parent.title,
        "description": occurrence.notes or parent.description or "",
        "start": {"dateTime": start_iso, "timeZone": (instance.get("start") or {}).get("timeZone", "UTC")},
        "end": {"dateTime": end_iso, "timeZone": (instance.get("end") or {}).get("timeZone", "UTC")},
        "status": google_status,
        "extendedProperties": {"private": extended_props},
    }
    res = _google_request_json_with_retry("PATCH", instance_url, access_token, payload, extra_headers=extra_headers)
    recurrence_id = _google_original_start_to_recurrence_id(instance.get("originalStartTime"))
    occurrence.google_event_id = res.get("id", instance_id)
    occurrence.google_recurrence_id = recurrence_id
    occurrence.save(update_fields=["google_event_id", "google_recurrence_id", "updated_at"])
    EventSyncMap.objects.update_or_create(
        user=user,
        local_occurrence_id=occurrence.id,
        defaults={
            "local_parent_type": parent_type,
            "local_task_id": parent.id if parent_type == "task" else None,
            "google_event_id": res.get("id", instance_id),
            "calendar_id": calendar_id,
            "etag": res.get("etag"),
            "google_etag": res.get("etag"),
            "is_deleted": False,
            "last_local_updated_at": dj_timezone.now(),
            "last_google_updated_at": dj_timezone.now(),
        },
    )
    return {"pushed": True, "action": "update", "google_event_id": res.get("id", instance_id)}


def _delete_recurring_parent_occurrence_in_app(occurrence, delete_all_future: bool = False, calendar_id: str = "primary") -> dict:
    parent = occurrence.task or occurrence.habit
    if not parent:
        return {"deleted": False, "reason": "no_parent"}

    recurrence_rule = _ensure_parent_recurrence_rule(parent)
    if not recurrence_rule:
        occurrence.is_deleted = True
        occurrence.save(update_fields=["is_deleted", "updated_at"])
        return push_local_occurrence_change(parent.user, occurrence, action="delete", calendar_id=calendar_id)

    if delete_all_future:
        parent.recurrence_rule = _update_rrule_until_date(
            recurrence_rule,
            occurrence.scheduled_date - timedelta(days=1),
        )
        if parent.end_date is None or parent.end_date >= occurrence.scheduled_date:
            parent.end_date = occurrence.scheduled_date - timedelta(days=1)
        parent.save(update_fields=["recurrence_rule", "end_date", "updated_at"])
        parent.occurrences.filter(
            scheduled_date__gte=occurrence.scheduled_date,
            is_deleted=False,
        ).update(status="skipped", updated_at=dj_timezone.now())
        result = create_recurring_google_event(parent.user, parent, calendar_id=calendar_id)
        return {"deleted": True, "scope": "future", "sync_result": result}

    occurrence.status = "skipped"
    occurrence.save(update_fields=["status", "updated_at"])
    result = handle_recurring_occurrence_change(parent.user, occurrence, action="delete", calendar_id=calendar_id)
    return {"deleted": True, "scope": "single", "sync_result": result}


def delete_task_occurrence_in_app(occurrence, delete_all_future: bool = False, calendar_id: str = "primary") -> dict:
    return _delete_recurring_parent_occurrence_in_app(occurrence, delete_all_future=delete_all_future, calendar_id=calendar_id)


def delete_habit_occurrence_in_app(occurrence, delete_all_future: bool = False, calendar_id: str = "primary") -> dict:
    return _delete_recurring_parent_occurrence_in_app(occurrence, delete_all_future=delete_all_future, calendar_id=calendar_id)


def create_recurring_google_event(user, task, calendar_id: str = "primary") -> dict:
    """
    Create a recurring event in Google Calendar from a Task with recurrence_rule.
    
    Args:
        user: User object
        task: Task object with recurrence_rule set
        calendar_id: Google Calendar ID (default "primary")
    
    Returns:
        dict with keys: created, google_event_id, error (if any)
    """
    recurrence_rule = _ensure_parent_recurrence_rule(task)
    if not recurrence_rule:
        return {"created": False, "error": "Task has no recurrence_rule"}
    
    connection = GoogleCalendarConnection.objects.filter(user=user, is_active=True).first()
    if not connection:
        return {"created": False, "error": "Google Calendar not connected"}
    
    try:
        access_token, error = _coerce_access_token_result(ensure_valid_access_token(connection))
        if error:
            return {"created": False, "error": f"Access token error: {error}"}
        was_existing_event = bool(task.google_event_id)
        
        # Prepare event payload with recurrence
        get_duration_end_time_calculated = _get_task_duration_config(task)
        start_iso, tz = _google_date_time_payload(task.start_date, task.start_time)
        end_iso, _ = _google_date_time_payload(task.start_date, task.end_time or get_duration_end_time_calculated)
        
        # Convert RRULE to Google's recurrence format (list with "RRULE:" prefix)
        google_recurrence = RRuleHandler.rrule_to_google_event_recurrence(recurrence_rule)
        
        # Map task status (use pending for new task)
        google_status, extended_props = _map_app_status_to_google("pending")
        
        parent_type = "habit" if getattr(task, "is_habit", False) else "task"
        payload = {
            "summary": task.title,
            "description": task.description or "",
            "start": {"dateTime": start_iso, "timeZone": tz},
            "end": {"dateTime": end_iso, "timeZone": tz},
            "recurrence": google_recurrence,  # List of RRULE strings
            "status": google_status,
            "extendedProperties": {
                "private": {
                    **extended_props,
                    "app_id": str(task.id),
                    "app_type": parent_type,
                    "created_by": "sadhak_app",
                }
            },
            "reminders": _build_google_reminders_payload(task),
        }
        
        encoded_calendar = urllib.parse.quote(calendar_id, safe="")
        if task.google_event_id:
            encoded_event = urllib.parse.quote(task.google_event_id, safe="")
            event_url = f"https://www.googleapis.com/calendar/v3/calendars/{encoded_calendar}/events/{encoded_event}"
            event_mapping = EventSyncMap.objects.filter(
                user=user,
                calendar_id=calendar_id,
                google_event_id=task.google_event_id,
            ).first()
            extra_headers = {"If-Match": event_mapping.google_etag} if event_mapping and event_mapping.google_etag else None
            res = _google_request_json_with_retry("PATCH", event_url, access_token, payload, extra_headers=extra_headers)
            google_event_id = task.google_event_id
        else:
            event_url = f"https://www.googleapis.com/calendar/v3/calendars/{encoded_calendar}/events"
            res = _google_request_json("POST", event_url, access_token, payload)
            google_event_id = res.get("id", "")
        
        if not google_event_id:
            return {"created": False, "error": "Google Calendar did not return event ID"}
        
        # Update Task with google_event_id
        task.google_event_id = google_event_id
        task._skip_google_calendar_sync = True
        try:
            task.save(update_fields=["google_event_id", "updated_at"])
        finally:
            task._skip_google_calendar_sync = False
        
        # Create EventSyncMap for the recurring root event
        EventSyncMap.objects.update_or_create(
            user=user,
            local_task_id=task.id,
            google_event_id=google_event_id,
            defaults={
                "local_occurrence_id": task.id,  # Use task ID as occurrence ID for root
                "local_parent_type": "task",
                "calendar_id": calendar_id,
                "etag": res.get("etag"),
                "is_recurring": True,
                "recurrence_rule": recurrence_rule,
                "google_etag": res.get("etag"),
                "last_local_updated_at": dj_timezone.now(),
                "last_google_updated_at": dj_timezone.now(),
                "is_deleted": False,
            },
        )
        EventSyncMap.objects.filter(
            user=user,
            local_task_id=task.id,
            google_event_id=google_event_id,
        ).update(google_etag=res.get("etag"))
        
        logger.info(
            f"Created recurring Google Calendar event {google_event_id} for task {task.id} "
            f"(user {user.username}) with RRULE: {recurrence_rule}"
        )
        
        return {"created": True, "google_event_id": google_event_id, "action": "update" if was_existing_event else "create", "parent_type": parent_type}
    
    except Exception as e:
        logger.error(f"Error creating recurring Google event for task {task.id}: {str(e)}", exc_info=True)
        return {"created": False, "error": str(e)}


def push_local_occurrence_change(user, occurrence, action: str, calendar_id: str = "primary") -> dict:
    """
    Helper to be called from tracker flows.
    action: create|update|delete
    Includes bidirectional status sync using Google extendedProperties.
    
    For recurring tasks: syncs the root recurring event in Google Calendar.
    Individual occurrences of recurring tasks are not synced individually
    (they're generated by Google Calendar's recurrence rule).
    """
    # Check if this occurrence is part of a recurring task
    if occurrence.task_id:
        task = occurrence.task
        if task and _ensure_parent_recurrence_rule(task):
            return handle_recurring_occurrence_change(user, occurrence, action=action, calendar_id=calendar_id)
    if occurrence.habit_id:
        habit = occurrence.habit
        if habit and _ensure_parent_recurrence_rule(habit):
            return handle_recurring_occurrence_change(user, occurrence, action=action, calendar_id=calendar_id)
    
    connection = GoogleCalendarConnection.objects.filter(user=user, is_active=True).first()
    if not connection:
        return {"pushed": False, "reason": "not_connected"}
    access_token, error = _coerce_access_token_result(ensure_valid_access_token(connection))
    if error:
        return {"pushed": False, "reason": f"access_token_error: {error}"}
    title = (occurrence.task.title if occurrence.task_id else occurrence.habit.title) if (occurrence.task_id or occurrence.habit_id) else "Event"
    start_iso, tz = _google_date_time_payload(occurrence.scheduled_date, occurrence.scheduled_time)
    end_iso, _ = _google_date_time_payload(occurrence.scheduled_date, occurrence.schedule_end_time or occurrence.scheduled_time)
    
    
    # Map occurrence status to Google status
    google_status, extended_props = _map_app_status_to_google(occurrence.status)
    
    payload = {
        "summary": title,
        "description": occurrence.notes or "",
        "start": {"dateTime": start_iso, "timeZone": tz},
        "end": {"dateTime": end_iso, "timeZone": tz},
        "status": google_status,
        "extendedProperties": {"private": extended_props},
        "reminders": _build_google_reminders_payload(occurrence),
    }
    
    mapping = EventSyncMap.objects.filter(user=user, local_occurrence_id=occurrence.id).first()
    encoded_calendar = urllib.parse.quote(calendar_id, safe="")

    if action == "delete" or occurrence.is_deleted:
        if not mapping:
            return {"pushed": False, "reason": "mapping_not_found"}
        delete_url = (
            f"https://www.googleapis.com/calendar/v3/calendars/{encoded_calendar}/events/"
            f"{urllib.parse.quote(mapping.google_event_id, safe='')}"
        )
        extra_headers = {"If-Match": mapping.google_etag} if mapping.google_etag else None
        try:
            _google_request_no_content("DELETE", delete_url, access_token, extra_headers=extra_headers)
        except Exception:
            pass
        mapping.is_deleted = True
        mapping.save(update_fields=["is_deleted", "updated_at"])
        return {"pushed": True, "action": "delete"}

    if mapping and mapping.google_event_id:
        patch_url = (
            f"https://www.googleapis.com/calendar/v3/calendars/{encoded_calendar}/events/"
            f"{urllib.parse.quote(mapping.google_event_id, safe='')}"
        )
        extra_headers = {"If-Match": mapping.google_etag} if mapping.google_etag else None
        res = _google_request_json_with_retry("PATCH", patch_url, access_token, payload, extra_headers=extra_headers)
        mapping.etag = res.get("etag")
        mapping.google_etag = res.get("etag")
        mapping.last_local_updated_at = dj_timezone.now()
        mapping.last_google_updated_at = dj_timezone.now()
        mapping.is_deleted = False
        mapping.calendar_id = calendar_id
        mapping.save(
            update_fields=[
                "etag",
                "google_etag",
                "last_local_updated_at",
                "last_google_updated_at",
                "is_deleted",
                "calendar_id",
                "updated_at",
            ]
        )
        return {"pushed": True, "action": "update", "google_event_id": mapping.google_event_id}

    insert_url = f"https://www.googleapis.com/calendar/v3/calendars/{encoded_calendar}/events"
    res = _google_request_json("POST", insert_url, access_token, payload)
    EventSyncMap.objects.update_or_create(
        user=user,
        local_occurrence_id=occurrence.id,
        defaults={
            "local_parent_type": "task" if occurrence.task_id else "habit",
            "google_event_id": res.get("id", ""),
            "calendar_id": calendar_id,
            "etag": res.get("etag"),
            "google_etag": res.get("etag"),
            "last_local_updated_at": dj_timezone.now(),
            "last_google_updated_at": dj_timezone.now(),
            "is_deleted": False,
        },
    )
    return {"pushed": True, "action": "create", "google_event_id": res.get("id")}
