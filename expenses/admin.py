from django.contrib import admin

from .models import BudgetAllocation, ExpenseEntry, ExpenseReportPreference


@admin.register(BudgetAllocation)
class BudgetAllocationAdmin(admin.ModelAdmin):
    list_display = ("user", "main_category", "sub_category", "amount", "frequency", "start_date", "is_active")
    list_filter = ("frequency", "is_active")
    search_fields = ("main_category", "sub_category", "user__username", "user__email")


@admin.register(ExpenseEntry)
class ExpenseEntryAdmin(admin.ModelAdmin):
    list_display = ("user", "amount", "main_category", "sub_category", "item", "source", "spent_at")
    list_filter = ("source",)
    search_fields = ("user__username", "user__email", "main_category", "sub_category", "item", "transaction_reference", "notes")


@admin.register(ExpenseReportPreference)
class ExpenseReportPreferenceAdmin(admin.ModelAdmin):
    list_display = ("user", "name", "frequency", "report_format", "delivery_email", "is_active")
    list_filter = ("frequency", "report_format", "is_active")
