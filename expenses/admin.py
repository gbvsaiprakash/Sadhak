from django.contrib import admin

from .models import ExpenseEntry, ExpenseReportPreference, BudgetPlan, BudgetAllocationLine

@admin.register(BudgetPlan)
class BudgetPlanAdmin(admin.ModelAdmin):
    list_display = ("user", "name", "period_type", "year", "month", "currency", "is_active")
    list_filter = ("period_type", "is_active")
    search_fields = ("name", "user__username", "user__email")


@admin.register(BudgetAllocationLine)
class BudgetAllocationLineAdmin(admin.ModelAdmin):
    list_display = ("budget_plan", "main_category", "sub_category", "item", "amount", "rollup_level", "is_active")
    list_filter = ("rollup_level", "is_active")
    search_fields = ("budget_plan__name", "main_category", "sub_category", "item")

@admin.register(ExpenseEntry)
class ExpenseEntryAdmin(admin.ModelAdmin):
    list_display = ("user", "amount", "main_category", "sub_category", "item", "source", "spent_at")
    list_filter = ("source",)
    search_fields = ("user__username", "user__email", "main_category", "sub_category", "item", "transaction_reference", "notes")


@admin.register(ExpenseReportPreference)
class ExpenseReportPreferenceAdmin(admin.ModelAdmin):
    list_display = ("user", "name", "frequency", "report_format", "delivery_email", "is_active")
    list_filter = ("frequency", "report_format", "is_active")
