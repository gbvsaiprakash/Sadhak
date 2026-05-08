from django.contrib import admin

from .models import (
    BudgetAllocation,
    ExpenseEntry,
    ExpenseItem,
    ExpenseMainCategory,
    ExpenseReportPreference,
    ExpenseSubCategory,
)


@admin.register(ExpenseMainCategory)
class ExpenseMainCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "is_active", "created_at")
    search_fields = ("name", "user__username", "user__email")


@admin.register(ExpenseSubCategory)
class ExpenseSubCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "main_category", "user", "is_active", "created_at")
    search_fields = ("name", "main_category__name", "user__username", "user__email")


@admin.register(ExpenseItem)
class ExpenseItemAdmin(admin.ModelAdmin):
    list_display = ("name", "sub_category", "user", "is_frequent", "is_active", "created_at")
    search_fields = ("name", "sub_category__name", "user__username", "user__email")


@admin.register(BudgetAllocation)
class BudgetAllocationAdmin(admin.ModelAdmin):
    list_display = ("user", "main_category", "sub_category", "amount", "frequency", "start_date", "is_active")
    list_filter = ("frequency", "is_active")


@admin.register(ExpenseEntry)
class ExpenseEntryAdmin(admin.ModelAdmin):
    list_display = ("user", "amount", "main_category", "sub_category", "source", "spent_at")
    list_filter = ("source", "main_category", "sub_category")
    search_fields = ("user__username", "user__email", "transaction_reference", "notes")


@admin.register(ExpenseReportPreference)
class ExpenseReportPreferenceAdmin(admin.ModelAdmin):
    list_display = ("user", "name", "frequency", "report_format", "delivery_email", "is_active")
    list_filter = ("frequency", "report_format", "is_active")
