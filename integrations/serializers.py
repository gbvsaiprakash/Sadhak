from rest_framework import serializers
from integrations.models import GoogleCalendarMirrorEvent


class GoogleOAuthStartSerializer(serializers.Serializer):
    redirect_uri = serializers.URLField(required=False)
    mode = serializers.ChoiceField(choices=("auth", "calendar"), default="auth")


class GoogleOAuthCallbackSerializer(serializers.Serializer):
    code = serializers.CharField()
    state = serializers.CharField()


class GoogleCalendarSyncSerializer(serializers.Serializer):
    calendar_id = serializers.CharField(default="primary")


class GoogleCalendarMirrorEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = GoogleCalendarMirrorEvent
        fields = [
            "id",
            "calendar_id",
            "google_event_id",
            "title",
            "description",
            "start_at",
            "end_at",
            "timezone",
            "is_cancelled",
            "etag",
            "created_at",
            "updated_at",
        ]
