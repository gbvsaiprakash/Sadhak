import json
import os
import secrets
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

from django.core.cache import cache
from django.utils import timezone as dj_timezone
from integrations.crypto import encrypt_token, decrypt_token
from integrations.models import GoogleCalendarConnection, GoogleCalendarMirrorEvent, GoogleCalendarWatch, EventSyncMap


GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
GOOGLE_CALENDAR_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
GOOGLE_CALENDAR_WATCH_URL = "https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/watch"


def _env(name: str, default: str | None = None) -> str | None:
    return os.getenv(name, default)


def build_google_oauth_url(mode: str, redirect_uri: str) -> tuple[str, str]:
    client_id = _env("GOOGLE_OAUTH_CLIENT_ID", "")
    state = secrets.token_urlsafe(32)
    cache.set(f"google_oauth_state:{state}", mode, timeout=900)

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


def validate_state_and_get_mode(state: str) -> str | None:
    mode = cache.get(f"google_oauth_state:{state}")
    if mode:
        cache.delete(f"google_oauth_state:{state}")
    return mode


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
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


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
        return decrypt_token(connection.access_token)

    token_data = refresh_access_token(decrypt_token(connection.refresh_token))
    access_token = token_data.get("access_token")
    if not access_token:
        raise ValueError("Unable to refresh Google access token")
    connection.access_token = encrypt_token(access_token)
    expires_in = token_data.get("expires_in")
    connection.token_expiry = compute_expiry(expires_in)
    if token_data.get("scope"):
        connection.scope = token_data.get("scope")
    connection.save(update_fields=["access_token", "token_expiry", "scope", "updated_at"])
    return access_token


def _google_request_json(method: str, url: str, access_token: str, data: dict | None = None) -> dict:
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read().decode() or "{}"
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        detail = e.read().decode() if hasattr(e, "read") else str(e)
        raise ValueError(f"Google API error {e.code}: {detail}")


def google_list_events(access_token: str, calendar_id: str = "primary", sync_token: str | None = None) -> dict:
    params = {"singleEvents": "true", "maxResults": "2500"}
    if sync_token:
        params["syncToken"] = sync_token
    else:
        params["timeMin"] = datetime.now(timezone.utc).isoformat()
    url = GOOGLE_CALENDAR_EVENTS_URL.format(
        calendar_id=urllib.parse.quote(calendar_id, safe="")
    ) + "?" + urllib.parse.urlencode(params)

    return _google_request_json("GET", url, access_token)


def ensure_watch(user, calendar_id: str = "primary") -> GoogleCalendarWatch:
    connection = GoogleCalendarConnection.objects.filter(user=user, is_active=True).first()
    if not connection:
        raise ValueError("Google Calendar not connected")
    access_token = ensure_valid_access_token(connection)

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


def pull_google_delta_for_watch(watch: GoogleCalendarWatch) -> dict:
    connection = GoogleCalendarConnection.objects.filter(user=watch.user, is_active=True).first()
    if not connection:
        raise ValueError("Google Calendar connection not found")
    access_token = ensure_valid_access_token(connection)
    data = google_list_events(access_token, calendar_id=watch.calendar_id, sync_token=watch.sync_token)
    items = data.get("items", [])
    changed = upsert_mirror_events(watch.user, watch.calendar_id, items)
    next_sync_token = data.get("nextSyncToken")
    if next_sync_token:
        watch.sync_token = next_sync_token
        watch.save(update_fields=["sync_token", "updated_at"])
    return {"changed": changed, "next_sync_token": next_sync_token}


def push_local_occurrence_change(user, occurrence, action: str, calendar_id: str = "primary") -> dict:
    """
    Helper to be called from tracker flows.
    action: create|update|delete
    """
    connection = GoogleCalendarConnection.objects.filter(user=user, is_active=True).first()
    if not connection:
        return {"pushed": False, "reason": "not_connected"}
    access_token = ensure_valid_access_token(connection)
    title = (occurrence.task.title if occurrence.task_id else occurrence.habit.title) if (occurrence.task_id or occurrence.habit_id) else "Event"
    tz = "UTC"
    date_str = occurrence.scheduled_date.isoformat()
    start_time = occurrence.scheduled_time.isoformat() if occurrence.scheduled_time else "00:00:00"
    end_time = occurrence.schedule_end_time.isoformat() if occurrence.schedule_end_time else start_time
    start_iso = f"{date_str}T{start_time}+00:00"
    end_iso = f"{date_str}T{end_time}+00:00"
    payload = {
        "summary": title,
        "description": occurrence.notes or "",
        "start": {"dateTime": start_iso, "timeZone": tz},
        "end": {"dateTime": end_iso, "timeZone": tz},
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
        try:
            _google_request_json("DELETE", delete_url, access_token)
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
        res = _google_request_json("PATCH", patch_url, access_token, payload)
        mapping.etag = res.get("etag")
        mapping.last_local_updated_at = dj_timezone.now()
        mapping.last_google_updated_at = dj_timezone.now()
        mapping.is_deleted = False
        mapping.calendar_id = calendar_id
        mapping.save(
            update_fields=[
                "etag",
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
            "last_local_updated_at": dj_timezone.now(),
            "last_google_updated_at": dj_timezone.now(),
            "is_deleted": False,
        },
    )
    return {"pushed": True, "action": "create", "google_event_id": res.get("id")}
