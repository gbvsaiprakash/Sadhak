from django.db import models
from django.db.models import F, Q

from sadhak_base.models import UUIDTimeStampedModel


class TrackerDependency(UUIDTimeStampedModel):
    owner_task = models.ForeignKey(
        "tracker.Task",
        on_delete=models.CASCADE,
        related_name="dependency_edges",
        null=True,
        blank=True,
    )
    owner_habit = models.ForeignKey(
        "tracker.Habit",
        on_delete=models.CASCADE,
        related_name="dependency_edges",
        null=True,
        blank=True,
    )

    depends_on_task = models.ForeignKey(
        "tracker.Task",
        on_delete=models.CASCADE,
        related_name="dependent_edges",
        null=True,
        blank=True,
    )
    depends_on_habit = models.ForeignKey(
        "tracker.Habit",
        on_delete=models.CASCADE,
        related_name="dependent_edges",
        null=True,
        blank=True,
    )

    is_deleted = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=["owner_task", "is_deleted"]),
            models.Index(fields=["owner_habit", "is_deleted"]),
            models.Index(fields=["depends_on_task", "is_deleted"]),
            models.Index(fields=["depends_on_habit", "is_deleted"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    (Q(owner_task__isnull=False) & Q(owner_habit__isnull=True))
                    | (Q(owner_task__isnull=True) & Q(owner_habit__isnull=False))
                ),
                name="tracker_dep_single_owner",
            ),
            models.CheckConstraint(
                condition=(
                    (Q(depends_on_task__isnull=False) & Q(depends_on_habit__isnull=True))
                    | (Q(depends_on_task__isnull=True) & Q(depends_on_habit__isnull=False))
                ),
                name="tracker_dep_single_target",
            ),
            models.CheckConstraint(
                condition=~Q(owner_task=F("depends_on_task")),
                name="tracker_dep_no_task_self",
            ),
            models.CheckConstraint(
                condition=~Q(owner_habit=F("depends_on_habit")),
                name="tracker_dep_no_habit_self",
            ),
            models.UniqueConstraint(
                fields=["owner_task", "depends_on_task"],
                condition=Q(owner_task__isnull=False, depends_on_task__isnull=False, is_deleted=False),
                name="tracker_dep_uq_task_task_active",
            ),
            models.UniqueConstraint(
                fields=["owner_task", "depends_on_habit"],
                condition=Q(owner_task__isnull=False, depends_on_habit__isnull=False, is_deleted=False),
                name="tracker_dep_uq_task_habit_active",
            ),
            models.UniqueConstraint(
                fields=["owner_habit", "depends_on_task"],
                condition=Q(owner_habit__isnull=False, depends_on_task__isnull=False, is_deleted=False),
                name="tracker_dep_uq_habit_task_active",
            ),
            models.UniqueConstraint(
                fields=["owner_habit", "depends_on_habit"],
                condition=Q(owner_habit__isnull=False, depends_on_habit__isnull=False, is_deleted=False),
                name="tracker_dep_uq_habit_habit_active",
            ),
        ]
