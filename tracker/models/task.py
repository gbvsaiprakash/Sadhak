from django.conf import settings
from django.db import models

from tracker.constants import FREQUENCY_TYPE_CHOICES, SECTION_CHOICES, TASK_STATUS_CHOICES
from tracker.models.base import UUIDTimeStampedModel


class Task(UUIDTimeStampedModel):
    user = models.ForeignKey(getattr(settings, "AUTH_USER_MODEL", "auth.User"), on_delete=models.CASCADE, related_name="tracker_tasks")
    goal = models.ForeignKey("tracker.Goal", on_delete=models.CASCADE, related_name="tasks", blank=True, null=True)
    milestone = models.ForeignKey("tracker.Milestone", on_delete=models.CASCADE, related_name="tasks", blank=True, null=True)
    section = models.CharField(max_length=20, choices=SECTION_CHOICES)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=TASK_STATUS_CHOICES, default="pending")
    frequency_type = models.CharField(max_length=20, choices=FREQUENCY_TYPE_CHOICES, default="once")
    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    time_of_day = models.TimeField(blank=True, null=True)
    day_of_week = models.PositiveSmallIntegerField(blank=True, null=True)
    day_of_month = models.PositiveSmallIntegerField(blank=True, null=True)
    interval_hours = models.PositiveSmallIntegerField(blank=True, null=True)
    is_habit = models.BooleanField(default=False, editable=False)

    class Meta:
        ordering = ("start_date", "time_of_day", "created_at")
        indexes = [
            models.Index(fields=["user", "status", "section"]),
            models.Index(fields=["goal", "milestone"]),
            models.Index(fields=["start_date", "end_date"]),
        ]

    def __str__(self):
        return self.title
