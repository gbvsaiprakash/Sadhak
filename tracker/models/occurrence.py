from django.db import models

from tracker.models.base import UUIDTimeStampedModel


class TaskOccurrence(UUIDTimeStampedModel):
    STATUS_CHOICES = (
        ("pending", "Pending"),
        ("completed", "Completed"),
        ("skipped", "Skipped"),
        ("missed", "Missed"),
    )

    task = models.ForeignKey("tracker.Task", on_delete=models.CASCADE, related_name="occurrences", blank=True, null=True)
    habit = models.ForeignKey("tracker.Habit", on_delete=models.CASCADE, related_name="occurrences", blank=True, null=True)
    scheduled_date = models.DateField()
    scheduled_time = models.TimeField(blank=True, null=True)
    schedule_end_time = models.TimeField(blank=True, null=True)  # for tasks with time windows
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    completed_at = models.DateTimeField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    context_title = models.CharField(max_length=255, blank=True, null=True)
    context_description = models.TextField(blank=True, null=True)
    context_checklist = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ("scheduled_date", "scheduled_time", "created_at")
        indexes = [
            models.Index(fields=["task", "scheduled_date", "status"]),
            models.Index(fields=["habit", "scheduled_date", "status"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    (models.Q(task__isnull=False) & models.Q(habit__isnull=True))
                    | (models.Q(task__isnull=True) & models.Q(habit__isnull=False))
                ),
                name="tracker_occurrence_single_parent",
            ),
            models.UniqueConstraint(
                fields=["task", "scheduled_date", "scheduled_time"],
                condition=models.Q(task__isnull=False),
                name="tracker_unique_task_occurrence",
            ),
            models.UniqueConstraint(
                fields=["habit", "scheduled_date", "scheduled_time"],
                condition=models.Q(habit__isnull=False),
                name="tracker_unique_habit_occurrence",
            ),
        ]

    def __str__(self):
        target = self.task or self.habit
        return f"{target} @ {self.scheduled_date}"
