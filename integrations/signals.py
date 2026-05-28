from django.db.models.signals import post_save
from django.dispatch import receiver

from tracker.models import TaskOccurrence
from integrations.services import push_local_occurrence_change


@receiver(post_save, sender=TaskOccurrence)
def sync_occurrence_to_google(sender, instance: TaskOccurrence, created: bool, **kwargs):
    parent = instance.task or instance.habit
    user = getattr(parent, "user", None)
    if not user:
        return

    try:
        if instance.is_deleted:
            push_local_occurrence_change(user, instance, action="delete")
            return

        if created:
            push_local_occurrence_change(user, instance, action="create")
            return

        push_local_occurrence_change(user, instance, action="update")
    except Exception:
        # Calendar sync failures should not block tracker writes.
        return
