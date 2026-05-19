import json
from datetime import timedelta

from django.db.models import Sum
from django.utils import timezone

from expenses.models import ExpenseEntry, ExpenseReportPreference


def calculate_next_run_at(frequency: str, from_dt=None):
    from_dt = from_dt or timezone.now()
    if frequency == "daily":
        return from_dt + timedelta(days=1)
    if frequency == "weekly":
        return from_dt + timedelta(weeks=1)
    if frequency == "monthly":
        return from_dt + timedelta(days=30)
    if frequency == "quarterly":
        return from_dt + timedelta(days=90)
    if frequency == "yearly":
        return from_dt + timedelta(days=365)
    return from_dt + timedelta(days=30)


def mark_schedule_on_create(preference: ExpenseReportPreference):
    now = timezone.now()
    preference.last_run_at = now
    preference.next_run_at = now # calculate_next_run_at(preference.frequency, now)


def mark_schedule_on_frequency_change(preference: ExpenseReportPreference):
    now = timezone.now()
    preference.next_run_at = calculate_next_run_at(preference.frequency, now)


def mark_schedule_on_disable(preference: ExpenseReportPreference):
    preference.next_run_at = None


def period_bounds_for_frequency(frequency: str, now=None):
    now = now or timezone.now()
    today = timezone.localdate()

    if frequency == "daily":
        return today, today
    if frequency == "weekly":
        start = today - timedelta(days=today.weekday())
        return start, today
    if frequency == "monthly":
        start = today.replace(day=1)
        return start, today
    if frequency == "quarterly":
        quarter = ((today.month - 1) // 3) + 1
        start_month = (quarter - 1) * 3 + 1
        start = today.replace(month=start_month, day=1)
        return start, today
    if frequency == "yearly":
        start = today.replace(month=1, day=1)
        return start, today
    return today.replace(day=1), today


def build_report_payload(user, period_start, period_end):
    queryset = ExpenseEntry.objects.filter(
        user=user,
        is_deleted=False,
        spent_at__date__gte=period_start,
        spent_at__date__lte=period_end,
    ).order_by("-spent_at")

    summary = {
        "total_spent": queryset.aggregate(total=Sum("amount")).get("total") or 0,
        "entries": queryset.count(),
        "period_start": str(period_start),
        "period_end": str(period_end),
    }

    results = [
        {
            "spent_at": entry.spent_at.isoformat(),
            "amount": float(entry.amount),
            "main_category": entry.main_category,
            "sub_category": entry.sub_category,
            "item": entry.item,
            "source": entry.source,
            "payment_method": entry.payment_method,
            "transaction_reference": entry.transaction_reference,
            "notes": entry.notes,
        }
        for entry in queryset
    ]

    return {"summary": summary, "results": results}


def report_as_json_bytes(payload):
    return json.dumps(payload, indent=2, default=str).encode("utf-8")
