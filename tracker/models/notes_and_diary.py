from django.conf import settings
from django.db import models
from user_management.models import User
from django.core.exceptions import ValidationError
from tracker.constants import SECTION_CHOICES, NOTES_CHOICES
from sadhak_base.models import UUIDTimeStampedModel

class Notes(UUIDTimeStampedModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="tracker_notes")
    type = models.CharField(max_length=20, choices=NOTES_CHOICES)
    title = models.CharField(max_length=255)
    date = models.DateField(null=True, blank=True)
    tags = models.JSONField(default=list, blank=True)
    is_pinned = models.BooleanField(default=False)
    description = models.TextField(blank=True, null=True)
    section = models.CharField(max_length=20, choices=SECTION_CHOICES, blank=True, null=True)
    reminder_id = models.ForeignKey("tracker.habit", null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ("created_at",)

    def __str__(self):
        return self.title


class NoteContent(UUIDTimeStampedModel):
    notes_id = models.ForeignKey(Notes, on_delete=models.CASCADE, related_name="contents")
    content = models.TextField(null=True, blank=True)

    class Meta:
        ordering = ("id",)

class DiaryPage(UUIDTimeStampedModel):
    diary_id = models.ForeignKey(Notes, on_delete=models.CASCADE, related_name="tracker_diary_pages")
    date = models.DateField()
    content = models.TextField(null=True, blank=True)
    # tags = models.JSONField(default=list, blank=True)
    # is_pinned = models.BooleanField(default=False)

    # date must be unique for a given diary_id
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["diary_id", "date"], 
                name="unique_diary_page_date"
            )
        ]
        ordering = ("date",)

    def __str__(self):
        return f"Diary Page for {self.diary_id.title} on {self.date}"