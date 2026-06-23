from django.db import migrations


def backfill_recurrence_rule(apps, schema_editor):
    Task = apps.get_model("tracker", "Task")
    Habit = apps.get_model("tracker", "Habit")

    from integrations.rrule_handler import build_recurrence_rule_for_entity

    for model in (Task, Habit):
        queryset = model.objects.filter(recurrence_rule__isnull=True).exclude(frequency_type="once")
        for instance in queryset.iterator():
            recurrence_rule = build_recurrence_rule_for_entity(instance)
            if recurrence_rule:
                model.objects.filter(pk=instance.pk).update(recurrence_rule=recurrence_rule)


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0011_occurrencereminder_google_notification_id_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_recurrence_rule, migrations.RunPython.noop),
    ]
