import traceback

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from expenses.models import ExpenseReportDeliveryLog, ExpenseReportPreference
from expenses.services import (
    build_report_payload,
    calculate_next_run_at,
    period_bounds_for_frequency,
    report_as_json_bytes,
    report_as_csv_bytes,
)
from sadhak_base.models import DomainEvent
from sadhak_base.services import emit_event
from user_management.emails import send_email


EXPENSE_REPORT_EVENT = "expense.report_due"


@shared_task
def emit_due_expense_report_events_task(batch_size: int = 200):
    now = timezone.now()
    due = ExpenseReportPreference.objects.filter(
        is_deleted=False,
        is_active=True,
        next_run_at__isnull=False,
        next_run_at__lte=now,
    ).order_by("next_run_at")[:batch_size]

    count = 0
    for pref in due:
        emit_event(
            event_type=EXPENSE_REPORT_EVENT,
            actor=pref.user,
            object_type="expenses.expensereportpreference",
            object_id=str(pref.id),
            payload={"preference_id": str(pref.id)},
        )
        # Move next_run_at immediately to avoid repeated event emission.
        pref.next_run_at = calculate_next_run_at(pref.frequency, now)
        pref.save(update_fields=["next_run_at", "updated_at"])
        count += 1
    return count


@shared_task
def process_pending_expense_report_events(batch_size: int = 200):
    pending_ids = list(
        DomainEvent.objects.filter(processed=False, event_type=EXPENSE_REPORT_EVENT)
        .order_by("created_at")
        .values_list("id", flat=True)[:batch_size]
    )
    for event_id in pending_ids:
        process_expense_report_event.delay(str(event_id))
    return len(pending_ids)


@shared_task(bind=True)
def process_expense_report_event(self, event_id: str):
    event = DomainEvent.objects.filter(id=event_id).first()
    if not event:
        return "missing_event"
    if event.processed:
        return "already_processed"

    preference_id = (event.payload or {}).get("preference_id")
    if not preference_id:
        event.processed = True
        event.processed_at = timezone.now()
        event.save(update_fields=["processed", "processed_at", "updated_at"])
        return "missing_preference_id"

    preference = ExpenseReportPreference.objects.filter(id=preference_id, is_deleted=False, is_active=True).select_related("user").first()
    if not preference:
        event.processed = True
        event.processed_at = timezone.now()
        event.save(update_fields=["processed", "processed_at", "updated_at"])
        return "preference_missing_or_inactive"

    period_start, period_end = period_bounds_for_frequency(preference.frequency)

    try:
        payload = build_report_payload(preference.user, period_start, period_end)
        report_format = (preference.report_format or "csv").lower()
        if report_format == "json":
            attachment = report_as_json_bytes(payload)
        else:
            attachment = report_as_csv_bytes(payload)
        recipient = preference.delivery_email or preference.user.email

        subject = f"Expense Report ({preference.frequency.title()})"
        body = f"Hi {preference.user.first_name or preference.user.username},\n\nPlease find your expense report attached.\nPeriod: {period_start} to {period_end}."

        ok, msg = send_email(
            subject=subject,
            body=body,
            recipient_email=recipient,
            body_type="plain",
            attachment=attachment,
            attachment_name=f"expense-report-{period_start}-to-{period_end}.{report_format}",
            is_unique_subject=False,
        )

        now = timezone.now()
        with transaction.atomic():
            if ok:
                ExpenseReportDeliveryLog.objects.create(
                    preference=preference,
                    status="success",
                    error_message="",
                    period_start=period_start,
                    period_end=period_end,
                )
                preference.last_run_at = now
                preference.save(update_fields=["last_run_at", "updated_at"])
            else:
                ExpenseReportDeliveryLog.objects.create(
                    preference=preference,
                    status="failure",
                    error_message=msg or "send_email_failed",
                    period_start=period_start,
                    period_end=period_end,
                )

            event.processed = True
            event.processed_at = now
            event.save(update_fields=["processed", "processed_at", "updated_at"])

        return "processed_success" if ok else "processed_failure"

    except Exception as exc:
        ExpenseReportDeliveryLog.objects.create(
            preference=preference,
            status="failure",
            error_message=f"{exc}\n{traceback.format_exc()}",
            period_start=period_start,
            period_end=period_end,
        )
        event.processed = True
        event.processed_at = timezone.now()
        event.save(update_fields=["processed", "processed_at", "updated_at"])
        return "processed_exception"
