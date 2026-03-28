from django.db import models

from tracker.constants import MILESTONE_STATUS_CHOICES
from tracker.models.base import UUIDTimeStampedModel


class Milestone(UUIDTimeStampedModel):
    goal = models.ForeignKey("tracker.Goal", on_delete=models.CASCADE, related_name="milestones")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=MILESTONE_STATUS_CHOICES, default="active")
    override_completed = models.BooleanField(default=False)
    start_date = models.DateField()
    expected_achieved_date = models.DateField()
    achieved_date = models.DateField(blank=True, null=True)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        ordering = ("start_date", "created_at")
        indexes = [
            models.Index(fields=["goal", "status"]),
            models.Index(fields=["start_date", "expected_achieved_date"]),
        ]

    def __str__(self):
        return self.title
