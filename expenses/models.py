import uuid
from decimal import Decimal
from user_management.models import User
from django.conf import settings
from django.db import models
from django.db.models.functions import Lower
from sadhak_base.models import UUIDTimeStampedModel


class BudgetAllocation(UUIDTimeStampedModel):
    FREQUENCY_CHOICES = (
        ("weekly", "Weekly"),
        ("monthly", "Monthly"),
        ("quarterly", "Quarterly"),
        ("yearly", "Yearly"),
    )

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="budget_allocations")
    main_category = models.CharField(max_length=120, blank=True, default="", null=True)
    sub_category = models.CharField(max_length=120, blank=True, default="")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    frequency = models.CharField(max_length=20, choices=FREQUENCY_CHOICES, default="monthly")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    notes = models.CharField(max_length=300, blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "frequency", "is_active"]),
            models.Index(fields=["start_date", "end_date"]),
        ]


class ExpenseEntry(UUIDTimeStampedModel):
    SOURCE_CHOICES = (
        ("manual", "Manual"),
        ("in_app", "In App"),
        ("webhook", "Webhook"),
        ("external", "External App"),
    )

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="expenses")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    main_category = models.CharField(max_length=120, blank=True, default="", null=True)
    sub_category = models.CharField(max_length=120, blank=True, default="")
    item = models.CharField(max_length=160, blank=True, default="")
    spent_at = models.DateTimeField()
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default="manual")
    transaction_reference = models.CharField(max_length=150, blank=True, default="")
    payment_method = models.CharField(max_length=100, blank=True, default="")
    notes = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        ordering = ["-spent_at"]
        indexes = [
            models.Index(fields=["user", "spent_at"]),
            models.Index(fields=["source", "spent_at"]),
            models.Index(fields=["user", "main_category", "sub_category"]),
        ]


class ExpenseReportPreference(UUIDTimeStampedModel):
    FREQUENCY_CHOICES = (
        ("daily", "Daily"),
        ("weekly", "Weekly"),
        ("monthly", "Monthly"),
        ("quarterly", "Quarterly"),
        ("yearly", "Yearly"),
    )

    FORMAT_CHOICES = (
        ("json", "JSON"),
        ("csv", "CSV"),
    )

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="expense_report_preferences")
    name = models.CharField(max_length=120, default="Default Expense Report")
    frequency = models.CharField(max_length=20, choices=FREQUENCY_CHOICES, default="monthly")
    delivery_email = models.EmailField(blank=True, default="")
    report_format = models.CharField(max_length=20, choices=FORMAT_CHOICES, default="json")
    include_budget_vs_actual = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "frequency", "is_active"])]


def quantize_amount(value):
    if value is None:
        return Decimal("0.00")
    return Decimal(value).quantize(Decimal("0.01"))