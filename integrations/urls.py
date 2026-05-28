from django.urls import path

from integrations.views import (
    GoogleOAuthStartAPIView,
    GoogleOAuthCallbackAPIView,
    GoogleCalendarStatusAPIView,
    GoogleCalendarDisconnectAPIView,
    GoogleCalendarFullSyncAPIView,
    GoogleCalendarWebhookAPIView,
    GoogleCalendarMirrorEventsAPIView,
)


urlpatterns = [
    path("google/oauth/start/", GoogleOAuthStartAPIView.as_view(), name="google-oauth-start"),
    path("google/oauth/callback/", GoogleOAuthCallbackAPIView.as_view(), name="google-oauth-callback"),
    path("google/calendar/status/", GoogleCalendarStatusAPIView.as_view(), name="google-calendar-status"),
    path("google/calendar/disconnect/", GoogleCalendarDisconnectAPIView.as_view(), name="google-calendar-disconnect"),
    path("google/calendar/sync/full/", GoogleCalendarFullSyncAPIView.as_view(), name="google-calendar-full-sync"),
    path("google/calendar/events/", GoogleCalendarMirrorEventsAPIView.as_view(), name="google-calendar-events"),
    path("google/calendar/webhook/", GoogleCalendarWebhookAPIView.as_view(), name="google-calendar-webhook"),
]
