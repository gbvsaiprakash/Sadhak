from django.conf import settings
from django.db import models

from tracker.constants import GOAL_STATUS_CHOICES, SECTION_CHOICES
from tracker.models.base import UUIDTimeStampedModel


class Goal(UUIDTimeStampedModel):
    user = models.ForeignKey("user_management.User", on_delete=models.CASCADE, related_name="tracker_goals")
    section = models.CharField(max_length=20, choices=SECTION_CHOICES)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=GOAL_STATUS_CHOICES, default="active")
    override_completed = models.BooleanField(default=False)
    start_date = models.DateField()
    expected_end_date = models.DateField(blank=True, null=True)
    achieved_date = models.DateField(blank=True, null=True)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["user", "section", "status"]),
            models.Index(fields=["start_date", "expected_end_date"]),
        ]

    def __str__(self):
        return self.title
