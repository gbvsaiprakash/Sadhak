import uuid
from decimal import Decimal
from user_management.models import User
from django.conf import settings
from django.db import models
from django.db.models.functions import Lower
from sadhak_base.models import UUIDTimeStampedModel

class BudgetPlan(models.Model):
    PERIOD_CHOICES = (
        ("monthly", "Monthly"),
        ("yearly", "Yearly"),
        ("custom", "Custom"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="budget_plans")
    name = models.CharField(max_length=150)
    period_type = models.CharField(max_length=20, choices=PERIOD_CHOICES)
    year = models.PositiveIntegerField(null=True, blank=True)
    month = models.PositiveSmallIntegerField(null=True, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    currency = models.CharField(max_length=10, default="INR")
    is_active = models.BooleanField(default=True)
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "period_type", "is_active"]),
            models.Index(fields=["year", "month"]),
        ]


class BudgetAllocationLine(models.Model):
    ROLLUP_CHOICES = (
        ("category", "Category"),
        ("subcategory", "Sub Category"),
        ("item", "Item"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    budget_plan = models.ForeignKey(BudgetPlan, on_delete=models.CASCADE, related_name="lines")
    main_category = models.CharField(max_length=120)
    sub_category = models.CharField(max_length=120, blank=True, default="")
    item = models.CharField(max_length=160, blank=True, default="")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    rollup_level = models.CharField(max_length=20, choices=ROLLUP_CHOICES, default="item")
    notes = models.CharField(max_length=300, blank=True, default="")
    is_active = models.BooleanField(default=True)
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["budget_plan", "main_category", "sub_category", "item"]),
            models.Index(fields=["is_active", "is_deleted"]),
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
    last_run_at = models.DateTimeField(null=True, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "frequency", "is_active"])]


def quantize_amount(value):
    if value is None:
        return Decimal("0.00")
    return Decimal(value).quantize(Decimal("0.01"))

class ExpenseReportDeliveryLog(UUIDTimeStampedModel):
    STATUS_CHOICES = (
        ("success", "Success"),
        ("failure", "Failure"),
    )

    preference = models.ForeignKey(ExpenseReportPreference, on_delete=models.CASCADE, related_name="delivery_logs")
    delivered_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    error_message = models.TextField(blank=True, default="")
    period_start = models.DateField()
    period_end = models.DateField()

    class Meta:
        ordering = ["-delivered_at"]
        indexes = [models.Index(fields=["preference", "status"])]