import os

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.middleware.csrf import get_token
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from django.shortcuts import redirect
from integrations.models import GoogleAuthConnection, GoogleCalendarConnection
from integrations.crypto import encrypt_token
from integrations.serializers import (
    GoogleOAuthStartSerializer,
    GoogleOAuthCallbackSerializer,
    GoogleCalendarSyncSerializer,
    GoogleCalendarMirrorEventSerializer,
)
from integrations.services import (
    build_google_oauth_url,
    validate_state_and_get_mode,
    exchange_code_for_token,
    fetch_google_user_info,
    google_list_events,
    compute_expiry,
    ensure_watch,
    upsert_mirror_events,
    pull_google_delta_for_watch,
    ensure_valid_access_token,
    sync_google_status_to_app_occurrences,
)
from user_management.models import User
from user_management.views import AuthenticatedAPIView
from sadhak.app_settings import access_token_cookie, refresh_token_cookie, refresh_token_path


def _set_cookie(response, key, token, max_age, path):
    response.set_cookie(
        key=key,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=not settings.DEBUG,
        samesite=os.getenv("SAME_SITE_COOKIE"),
        path=path or "/",
    )


def _set_auth_cookies(response, access_token: str, refresh_token: str):
    _set_cookie(response, access_token_cookie, access_token, int(os.getenv("ACCESS_TOKEN_TTL_SECONDS", "900")), None)
    _set_cookie(response, refresh_token_cookie, refresh_token, int(os.getenv("REFRESH_TOKEN_TTL_SECONDS", "2592000")), refresh_token_path)


def _issue_token_pair(user: User) -> tuple[str, str]:
    refresh_raw = str(RefreshToken.for_user(user))
    refresh = RefreshToken(refresh_raw)
    refresh["token_version"] = user.token_version
    refresh["scope"] = os.getenv("SCOPE_FULL_AUTH")
    access = refresh.access_token
    access["token_version"] = user.token_version
    access["scope"] = os.getenv("SCOPE_FULL_AUTH")
    return str(access), refresh_raw


class GoogleOAuthStartAPIView(APIView):
    """
    Start OAuth for either login/register (`mode=auth`) or calendar integration (`mode=calendar`).
    """
    permission_classes = []
    authentication_classes = []

    def get(self, request):
        s = GoogleOAuthStartSerializer(data=request.query_params)
        s.is_valid(raise_exception=True)
        mode = s.validated_data["mode"]
        redirect_uri = s.validated_data.get("redirect_uri") or os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "")
        auth_url, state = build_google_oauth_url(mode, redirect_uri)
        return Response({"auth_url": auth_url, "state": state}, status=status.HTTP_200_OK)


class GoogleOAuthCallbackAPIView(APIView):
    permission_classes = []

    def get(self, request):
        s = GoogleOAuthCallbackSerializer(data=request.query_params)
        s.is_valid(raise_exception=True)
        code = s.validated_data["code"]
        state = s.validated_data["state"]
        redirect_uri = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "")

        mode = validate_state_and_get_mode(state)
        if not mode:
            return Response({"message": "Invalid or expired OAuth state"}, status=status.HTTP_400_BAD_REQUEST)

        token_data = exchange_code_for_token(code, redirect_uri)
        access_token = token_data.get("access_token")
        if not access_token:
            return Response({"message": "Failed to get access token from Google"}, status=status.HTTP_400_BAD_REQUEST)

        profile = fetch_google_user_info(access_token)
        email = (profile.get("email") or "").strip().lower()
        google_sub = profile.get("sub")
        if not email or not google_sub:
            return Response({"message": "Invalid Google profile payload"}, status=status.HTTP_400_BAD_REQUEST)

        created = False
        if mode == "calendar":
            if not request.user or request.user.is_anonymous:
                return Response(
                    {"message": "Authenticated user is required to link Google Calendar"},
                    status=status.HTTP_401_UNAUTHORIZED,
                )
            user = request.user
        else:
            user = User.objects.filter(email=email, is_deleted=False).first()
            if not user:
                created = True
                base_username = (profile.get("name") or email.split("@")[0] or "google_user")[:30]
                username = base_username
                i = 1
                while User.objects.filter(username__iexact=username, is_deleted=False).exists():
                    suffix = str(i)
                    username = f"{base_username[: max(1, 30 - len(suffix))]}{suffix}"
                    i += 1

                user = User.objects.create(
                    username=username,
                    email=email,
                    first_name=(profile.get("given_name") or username),
                    last_name=(profile.get("family_name") or ""),
                    is_active=True,
                    is_email_verified=True,
                    is_password_set=False,
                    password=make_password(None),
                )

        GoogleAuthConnection.objects.update_or_create(
            user=user,
            defaults={"google_sub": google_sub, "email": email, "is_active": True},
        )

        if mode == "calendar":
            refresh_token = token_data.get("refresh_token")
            if not refresh_token:
                return Response(
                    {"message": "Google did not return refresh_token. Please reconnect with consent prompt."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            GoogleCalendarConnection.objects.update_or_create(
                user=user,
                defaults={
                    "email": email,
                    "google_sub": google_sub,
                    "refresh_token": encrypt_token(refresh_token),
                    "access_token": encrypt_token(access_token),
                    "token_expiry": compute_expiry(token_data.get("expires_in")),
                    "scope": token_data.get("scope"),
                    "is_active": True,
                },
            )
            try:
                ensure_watch(user, calendar_id="primary")
            except Exception:
                # Watch creation can fail if webhook URL is not configured;
                # connection should still succeed.
                pass
            # return Response({"message": "Google Calendar connected", "email": email}, status=status.HTTP_200_OK)
            return redirect(os.getenv("frontend_url") + "/integrations/google/oauth/callback")

        # mode=auth -> app login/register via Google
        user.token_version += 1
        user.save(update_fields=["token_version", "updated_at"])
        access_raw, refresh_raw = _issue_token_pair(user)
        response = Response(
            {
                "message": "Google authentication successful",
                "is_new_user": created,
                "user": {
                    "user_id": str(user.user_id),
                    "username": user.username,
                    "email": user.email,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "is_email_verified": user.is_email_verified,
                },
            },
            status=status.HTTP_200_OK,
        )
        _set_auth_cookies(response, access_raw, refresh_raw)
        get_token(request)
        return response


class GoogleCalendarStatusAPIView(AuthenticatedAPIView):
    def get(self, request):
        c = GoogleCalendarConnection.objects.filter(user=request.user, is_active=True).first()
        watch = request.user.google_calendar_watches.filter(calendar_id="primary", is_active=True).first()
        return Response(
            {
                "connected": bool(c),
                "email": c.email if c else None,
                "watch_active": bool(watch),
                "watch_expiration": watch.expiration if watch else None,
            },
            status=status.HTTP_200_OK,
        )


class GoogleCalendarDisconnectAPIView(AuthenticatedAPIView):
    def post(self, request):
        GoogleCalendarConnection.objects.filter(user=request.user).update(is_active=False)
        request.user.google_calendar_watches.update(is_active=False)
        return Response({"message": "Google Calendar disconnected"}, status=status.HTTP_200_OK)


class GoogleCalendarFullSyncAPIView(AuthenticatedAPIView):
    """
    Full sync of Google Calendar events with bidirectional status sync.
    Fetches all events from Google Calendar and syncs status changes.
    """
    def post(self, request):
        s = GoogleCalendarSyncSerializer(data=request.data or {})
        s.is_valid(raise_exception=True)
        calendar_id = s.validated_data["calendar_id"]

        c = GoogleCalendarConnection.objects.filter(user=request.user, is_active=True).first()
        if not c:
            return Response({"message": "Google Calendar is not connected"}, status=status.HTTP_400_BAD_REQUEST)
        access_token = ensure_valid_access_token(c)

        watch = request.user.google_calendar_watches.filter(calendar_id=calendar_id, is_active=True).first()
        if not watch:
            watch = ensure_watch(request.user, calendar_id=calendar_id)

        data = google_list_events(access_token, calendar_id=calendar_id)
        items = data.get("items", [])
        changed = upsert_mirror_events(request.user, calendar_id, items)
        
        # Sync status changes from Google to app occurrences
        status_synced = sync_google_status_to_app_occurrences(request.user, calendar_id, items)
        
        next_sync_token = data.get("nextSyncToken")
        if next_sync_token:
            watch.sync_token = next_sync_token
            watch.save(update_fields=["sync_token", "updated_at"])
        print(f"Full sync: fetched {len(items)} events, upserted {changed} events, synced {status_synced} statuses for user {request.user.user_id}")
        return Response(
            {
                "message": "Full sync fetched successfully",
                "fetched_events": len(items),
                "upserted_events": changed,
                "status_synced": status_synced,
                "next_sync_token": next_sync_token,
            },
            status=status.HTTP_200_OK,
        )


class GoogleCalendarWebhookAPIView(APIView):
    """
    Webhook handler for Google Calendar push notifications.
    Syncs both event data and status changes bidirectionally.
    """
    permission_classes = []
    authentication_classes = []

    def post(self, request):
        channel_id = request.headers.get("X-Goog-Channel-ID")
        resource_id = request.headers.get("X-Goog-Resource-ID")
        if not channel_id or not resource_id:
            return Response({"message": "Missing Google watch headers"}, status=status.HTTP_400_BAD_REQUEST)

        from integrations.models import GoogleCalendarWatch

        watch = GoogleCalendarWatch.objects.filter(
            channel_id=channel_id,
            resource_id=resource_id,
            is_active=True,
        ).select_related("user").first()
        if not watch:
            return Response({"message": "Watch not found"}, status=status.HTTP_404_NOT_FOUND)

        try:
            result = pull_google_delta_for_watch(watch)
            return Response({
                "ok": True,
                "changed": result.get("changed", 0),
                "status_synced": result.get("status_synced", 0),
                "next_sync_token": result.get("next_sync_token"),
            }, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({
                "ok": False,
                "error": str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GoogleCalendarMirrorEventsAPIView(AuthenticatedAPIView):
    def get(self, request):
        calendar_id = request.query_params.get("calendar_id", "primary")
        qs = request.user.google_calendar_events.filter(
            calendar_id=calendar_id,
            is_cancelled=False,
        ).order_by("start_at")
        data = GoogleCalendarMirrorEventSerializer(qs, many=True).data
        return Response({"events": data}, status=status.HTTP_200_OK)


class GoogleCalendarDebugStatusAPIView(AuthenticatedAPIView):
    """
    Debug endpoint: Check sync status for the authenticated user.
    Returns detailed info about connection, watches, and synced occurrences.
    """
    def get(self, request):
        from integrations.models import EventSyncMap
        from tracker.models import TaskOccurrence
        from datetime import date

        user = request.user
        
        # Check connection
        connection = GoogleCalendarConnection.objects.filter(user=user, is_active=True).first()
        connected = bool(connection)
        
        # Check watches
        watches = user.google_calendar_watches.filter(is_active=True)
        watch_count = watches.count()
        
        # Check synced occurrences
        synced_maps = EventSyncMap.objects.filter(user=user, is_deleted=False).count()
        
        # Check pending occurrences (future dates, not synced)
        pending_occs = TaskOccurrence.objects.filter(
            task__user=user,
            scheduled_date__gte=date.today(),
            is_deleted=False
        ).exclude(id__in=EventSyncMap.objects.filter(user=user).values_list('local_occurrence_id', flat=True))
        pending_count = pending_occs.count()

        return Response({
            "debug": {
                "user": user.username,
                "google_calendar_connected": connected,
                "connection_email": connection.email if connection else None,
                "watches_active": watch_count,
                "occurrences_synced": synced_maps,
                "pending_occurrences": pending_count,
                "pending_sample": [
                    {
                        "id": str(occ.id),
                        "title": occ.task.title if occ.task else occ.habit.title,
                        "date": str(occ.scheduled_date),
                    }
                    for occ in pending_occs[:5]
                ] if pending_count > 0 else []
            }
        }, status=status.HTTP_200_OK)
