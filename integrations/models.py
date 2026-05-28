from django.conf import settings
from django.db import models

from sadhak_base.models import UUIDTimeStampedModel


class GoogleAuthConnection(UUIDTimeStampedModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="google_auth_connection",
    )
    google_sub = models.CharField(max_length=255, db_index=True, unique=True)
    email = models.EmailField()
    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [models.Index(fields=["user", "is_active"])]


class GoogleCalendarConnection(UUIDTimeStampedModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="google_calendar_connection",
    )
    email = models.EmailField()
    google_sub = models.CharField(max_length=255, db_index=True)
    refresh_token = models.TextField()
    access_token = models.TextField(blank=True, null=True)
    token_expiry = models.DateTimeField(blank=True, null=True)
    scope = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [models.Index(fields=["user", "is_active"])]


class GoogleCalendarWatch(UUIDTimeStampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="google_calendar_watches",
    )
    calendar_id = models.CharField(max_length=255, default="primary")
    channel_id = models.CharField(max_length=255, db_index=True, unique=True)
    resource_id = models.CharField(max_length=255, db_index=True)
    expiration = models.DateTimeField(blank=True, null=True)
    sync_token = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [models.Index(fields=["user", "calendar_id", "is_active"])]


class EventSyncMap(UUIDTimeStampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="google_event_maps",
    )
    local_occurrence_id = models.UUIDField(db_index=True)
    local_parent_type = models.CharField(
        max_length=10,
        choices=(("task", "Task"), ("habit", "Habit")),
    )
    google_event_id = models.CharField(max_length=255, db_index=True)
    calendar_id = models.CharField(max_length=255, default="primary")
    etag = models.CharField(max_length=255, blank=True, null=True)
    last_local_updated_at = models.DateTimeField(blank=True, null=True)
    last_google_updated_at = models.DateTimeField(blank=True, null=True)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "calendar_id", "google_event_id"],
                name="uniq_google_event_per_user_calendar",
            ),
            models.UniqueConstraint(
                fields=["user", "local_occurrence_id"],
                name="uniq_local_occurrence_per_user",
            ),
        ]


class GoogleCalendarMirrorEvent(UUIDTimeStampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="google_calendar_events",
    )
    calendar_id = models.CharField(max_length=255, default="primary")
    google_event_id = models.CharField(max_length=255, db_index=True)
    title = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField(blank=True, null=True)
    timezone = models.CharField(max_length=64, blank=True, null=True)
    is_cancelled = models.BooleanField(default=False)
    etag = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "calendar_id", "google_event_id"],
                name="uniq_google_mirror_event_per_user_calendar",
            )
        ]
