from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError
from tracker.constants import FREQUENCY_PERIOD_CHOICES, FREQUENCY_TYPE_CHOICES, SECTION_CHOICES, TASK_STATUS_CHOICES, REMINDER_MODE_CHOICES, REMINDER_MODE_SET
from sadhak_base.models import UUIDTimeStampedModel

def default_duration_config():
        return {"value": 30, "unit": "minutes"}

def default_reminder_config():
        return [{"value": 10, "unit": "minutes", "modes": ["in-app"]}]

class Task(UUIDTimeStampedModel):
    user = models.ForeignKey(getattr(settings, "AUTH_USER_MODEL", "auth.User"), on_delete=models.CASCADE, related_name="tracker_tasks")
    goal = models.ForeignKey("tracker.Goal", on_delete=models.CASCADE, related_name="tasks", blank=True, null=True)
    milestone = models.ForeignKey("tracker.Milestone", on_delete=models.CASCADE, related_name="tasks", blank=True, null=True)
    section = models.CharField(max_length=20, choices=SECTION_CHOICES)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=TASK_STATUS_CHOICES, default="pending")
    frequency_type = models.CharField(max_length=20, choices=FREQUENCY_TYPE_CHOICES, default="once")
    frequency_interval = models.IntegerField(default=1)
    frequency_days = models.JSONField(default=list, blank=True)
    frequency_times_per_period = models.PositiveIntegerField(blank=True, null=True)
    frequency_period = models.CharField(max_length=10, choices=FREQUENCY_PERIOD_CHOICES, blank=True, null=True)
    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    start_time = models.TimeField()
    end_time = models.TimeField(blank=True, null=True)
    duration_config = models.JSONField(default=default_duration_config, blank=True)
    reminder_enabled = models.BooleanField(default=False)
    reminder_mode_all = models.BooleanField(blank=True, null=True) # If True, all reminders of a task have same mode. If False, each occurrence can have its own mode.
    reminder_offset = models.JSONField(default=list, blank=True)
    day_of_week = models.PositiveSmallIntegerField(blank=True, null=True)
    day_of_month = models.PositiveSmallIntegerField(blank=True, null=True)
    interval_hours = models.PositiveSmallIntegerField(blank=True, null=True)
    is_habit = models.BooleanField(default=False, editable=False)
    is_deleted = models.BooleanField(default=False)
    
    # Google Calendar sync fields for recurring events
    google_event_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        db_index=True,
        help_text="Google Calendar root event ID for recurring tasks"
    )
    recurrence_rule = models.TextField(
        null=True,
        blank=True,
        help_text="RRULE format: FREQ=DAILY;UNTIL=20260630;INTERVAL=1"
    )
    external_google_id = models.BooleanField(
        default=False,
        help_text="True if created in Google Calendar and synced to app"
    )

    class Meta:
        ordering = ("start_date", "start_time", "created_at")
        indexes = [
            models.Index(fields=["user", "status", "section"]),
            models.Index(fields=["goal", "milestone"]),
            models.Index(fields=["start_date", "end_date"]),
        ]

    def __str__(self):
        return self.title

    def build_recurrence_rule(self):
        from integrations.rrule_handler import build_recurrence_rule_for_entity
        return build_recurrence_rule_for_entity(self)
    
    def get_normalized_reminders(self):
        """
        Return:
        [{"value": int, "unit": "minutes|hours", "modes": ["in-app","push","email"]}]
        """
        items = self.reminder_offset or []
        if not self.reminder_enabled:
            return []

        normalized = []
        default_modes = None

        for item in items:
            value = int(item.get("value", 0) or 0)
            unit = item.get("unit")
            modes = item.get("modes")

            # backward compatibility: accept old single mode
            if not modes and item.get("mode"):
                modes = [item.get("mode")]

            if unit not in ("minutes", "hours") or value <= 0:
                continue

            if self.reminder_mode_all:
                if default_modes is None:
                    default_modes = modes or ["in-app"]
                modes = default_modes
            else:
                modes = modes or ["in-app"]

            modes = [m for m in modes if m in REMINDER_MODE_SET]
            modes = sorted(set(modes))
            if not modes:
                continue

            normalized.append({"value": value, "unit": unit, "modes": modes})

        return normalized

    def _normalize_duration_config(self):
        cfg = self.duration_config or default_duration_config()
        try:
            value = int(cfg.get("value", 30) or 30)
        except (TypeError, ValueError):
            value = 30
        unit = str(cfg.get("unit", "minutes")).lower()
        if unit not in ("minutes", "hours"):
            unit = "minutes"
        self.duration_config = {"value": value, "unit": unit}
    
    def clean(self):
        super().clean()
        self._normalize_duration_config()

        if not self.reminder_enabled:
            return

        if not isinstance(self.reminder_offset, list) or not self.reminder_offset:
            raise ValidationError({"reminder_offset": "Provide at least one reminder."})

        seen_pairs = set()  # (minutes, mode)
        all_modes_seen = set()

        for i, item in enumerate(self.reminder_offset):
            if not isinstance(item, dict):
                raise ValidationError({f"reminder_offset[{i}]": "Each reminder must be an object."})

            value = item.get("value")
            unit = item.get("unit")
            modes = item.get("modes")

            # backward compatibility
            if not modes and item.get("mode"):
                modes = [item.get("mode")]

            if not isinstance(value, int) or value <= 0:
                raise ValidationError({f"reminder_offset[{i}].value": "Must be a positive integer."})
            if unit not in ("minutes", "hours"):
                raise ValidationError({f"reminder_offset[{i}].unit": "Must be 'minutes' or 'hours'."})
            if not isinstance(modes, list) or not modes:
                raise ValidationError({f"reminder_offset[{i}].modes": "At least one mode is required."})

            clean_modes = []
            for m in modes:
                if m not in REMINDER_MODE_SET:
                    raise ValidationError({f"reminder_offset[{i}].modes": "Invalid mode present."})
                clean_modes.append(m)

            clean_modes = sorted(set(clean_modes))
            minutes = value * 60 if unit == "hours" else value

            for m in clean_modes:
                key = (minutes, m)
                if key in seen_pairs:
                    raise ValidationError({"reminder_offset": "Duplicate reminder (time + mode) not allowed."})
                seen_pairs.add(key)
            all_modes_seen.add(tuple(clean_modes))

        if self.reminder_mode_all and len(all_modes_seen) > 1:
            raise ValidationError({"reminder_offset": "All reminders must use same mode set when reminder_mode_all=True."})

    def save(self, *args, **kwargs):
        if self.reminder_enabled and not self.reminder_offset:
            self.reminder_offset = default_reminder_config()
        elif not self.reminder_enabled:
            self.reminder_offset = []
        self.full_clean()
        recurrence_rule = self.build_recurrence_rule()
        if recurrence_rule is None and self.recurrence_rule:
            recurrence_rule = self.recurrence_rule
        if self.recurrence_rule != recurrence_rule:
            self.recurrence_rule = recurrence_rule
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                update_fields = set(update_fields)
                update_fields.add("recurrence_rule")
                kwargs["update_fields"] = list(update_fields)
        super().save(*args, **kwargs)
        
