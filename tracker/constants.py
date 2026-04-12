SECTION_CHOICES = (
    ("career", "Career"),
    ("health", "Health"),
    ("wealth", "Wealth"),
)

GOAL_STATUS_CHOICES = (
    ("active", "Active"),
    ("completed", "Completed"),
    ("cancelled", "Cancelled"),
    ("overridden", "Overridden"),
)

MILESTONE_STATUS_CHOICES = GOAL_STATUS_CHOICES

TASK_STATUS_CHOICES = (
    ("pending", "Pending"),
    ("in_progress", "In Progress"),
    ("completed", "Completed"),
    ("cancelled", "Cancelled"),
    ("skipped", "Skipped"),
)

HABIT_STATUS_CHOICES = (
    ("active", "Active"),
    ("paused", "Paused"),
    ("stopped", "Stopped"),
    ("completed", "Completed"),
)

FREQUENCY_TYPE_CHOICES = (
    ("once", "Once"),
    ("daily", "Daily"),
    ("weekly", "Weekly"),
    ("monthly", "Monthly"),
    ("hourly", "Hourly"),
    ("custom", "Custom"),
)

FREQUENCY_PERIOD_CHOICES = (
    ("day", "Day"),
    ("week", "Week"),
    ("month", "Month"),
)

RECURRING_FREQUENCIES = {"daily", "weekly", "monthly", "hourly", "custom"}
ACTIVE_GOAL_STATUSES = {"active"}
ACTIVE_TASK_STATUSES = {"pending", "in_progress"}
ACTIVE_HABIT_STATUSES = {"active", "paused"}
RESOLVED_OCCURRENCE_STATUSES = {"completed", "skipped"}
