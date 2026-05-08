import uuid
from decimal import Decimal
from user_management.models import User
from django.conf import settings
from django.db import models
from django.db.models.functions import Lower
from sadhak_base.models import UUIDTimeStampedModel

class ExpenseMainCategory(UUIDTimeStampedModel):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="expense_main_categories",
        null=True,
        blank=True,
        help_text="Null means system/global category available to all users.",
    )
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                Lower("name"),
                "user",
                condition=models.Q(is_deleted=False),
                name="uniq_exp_main_name_per_user_active",
            )
        ]

    def __str__(self):
        return self.name


class ExpenseSubCategory(UUIDTimeStampedModel):
    main_category = models.ForeignKey(
        ExpenseMainCategory,
        on_delete=models.CASCADE,
        related_name="sub_categories",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="expense_sub_categories",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                Lower("name"),
                "main_category",
                "user",
                condition=models.Q(is_deleted=False),
                name="uniq_exp_sub_name_per_main_user_active",
            )
        ]

    def __str__(self):
        return f"{self.main_category.name} / {self.name}"


class ExpenseItem(UUIDTimeStampedModel):
    sub_category = models.ForeignKey(
        ExpenseSubCategory,
        on_delete=models.CASCADE,
        related_name="items",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="expense_items",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=160)
    description = models.CharField(max_length=300, blank=True, default="")
    is_frequent = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                Lower("name"),
                "sub_category",
                "user",
                condition=models.Q(is_deleted=False),
                name="uniq_exp_item_name_per_sub_user_active",
            )
        ]

    def __str__(self):
        return self.name


class BudgetAllocation(UUIDTimeStampedModel):
    FREQUENCY_CHOICES = (
        ("weekly", "Weekly"),
        ("monthly", "Monthly"),
        ("quarterly", "Quarterly"),
        ("yearly", "Yearly"),
    )
    
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="budget_allocations",
    )
    main_category = models.ForeignKey(
        ExpenseMainCategory,
        on_delete=models.PROTECT,
        related_name="budget_allocations",
    )
    sub_category = models.ForeignKey(
        ExpenseSubCategory,
        on_delete=models.PROTECT,
        related_name="budget_allocations",
        null=True,
        blank=True,
    )
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

    def __str__(self):
        return f"{self.user_id} | {self.main_category.name} | {self.amount}"


class ExpenseEntry(UUIDTimeStampedModel):
    SOURCE_CHOICES = (
        ("manual", "Manual"),
        ("in_app", "In App"),
        ("webhook", "Webhook"),
        ("external", "External App"),
    )

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="expenses",
    )
    main_category = models.ForeignKey(ExpenseMainCategory, on_delete=models.PROTECT, related_name="expenses")
    sub_category = models.ForeignKey(
        ExpenseSubCategory,
        on_delete=models.PROTECT,
        related_name="expenses",
        null=True,
        blank=True,
    )
    item = models.ForeignKey(
        ExpenseItem,
        on_delete=models.SET_NULL,
        related_name="expenses",
        null=True,
        blank=True,
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
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
            models.Index(fields=["main_category", "sub_category"]),
        ]

    def __str__(self):
        return f"{self.user_id} | {self.amount} | {self.spent_at}"


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

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="expense_report_preferences",
    )
    name = models.CharField(max_length=120, default="Default Expense Report")
    frequency = models.CharField(max_length=20, choices=FREQUENCY_CHOICES, default="monthly")
    delivery_email = models.EmailField(blank=True, default="")
    report_format = models.CharField(max_length=20, choices=FORMAT_CHOICES, default="json")
    include_budget_vs_actual = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "frequency", "is_active"])]

    def __str__(self):
        return f"{self.user_id} | {self.name}"


def quantize_amount(value):
    if value is None:
        return Decimal("0.00")
    return Decimal(value).quantize(Decimal("0.01"))
