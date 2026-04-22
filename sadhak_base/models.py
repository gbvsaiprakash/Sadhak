import uuid
from django.db import models
from user_management.models import User

# Create your models here.


class UUIDTimeStampedModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        abstract = True

class FeatureFlag(UUIDTimeStampedModel):
    key = models.CharField(max_length=120, unique=True)
    description = models.CharField(max_length=255, blank=True)
    enabled = models.BooleanField(default=False)
    rollout_percent = models.PositiveSmallIntegerField(default=100)  # 0-100

    def __str__(self):
        return f"{self.key}={self.enabled}"


class DomainEvent(UUIDTimeStampedModel):
    event_type = models.CharField(max_length=120, db_index=True)
    actor = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="domain_events"
    )
    object_type = models.CharField(max_length=120, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    processed = models.BooleanField(default=False, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)

class AuditLog(models.Model):

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=100, db_index=True)
    endpoint = models.CharField(max_length=255)
    ip_address = models.GenericIPAddressField(null=True)
    device_type = models.CharField(max_length=300, null=True)
    request_data = models.JSONField(null=True, blank=True)
    response_data = models.JSONField(null=True, blank=True)
    status_code = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

