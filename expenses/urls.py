from django.urls import path

from .views import (
    BudgetAllocationDetailAPIView,
    BudgetAllocationListCreateAPIView,
    ExpenseAnalyticsAPIView,
    ExpenseDashboardAPIView,
    ExpenseEntryDetailAPIView,
    ExpenseEntryListCreateAPIView,
    ExpenseReportDownloadAPIView,
    ExpenseReportPreferenceDetailAPIView,
    ExpenseReportPreferenceListCreateAPIView,
    ExpenseSuggestionsAPIView,
)

urlpatterns = [
    path("categories/suggestions/", ExpenseSuggestionsAPIView.as_view(), name="expense-category-suggestions"),
    path("budgets/", BudgetAllocationListCreateAPIView.as_view(), name="expense-budget-list"),
    path("budgets/<uuid:pk>/", BudgetAllocationDetailAPIView.as_view(), name="expense-budget-detail"),
    path("entries/", ExpenseEntryListCreateAPIView.as_view(), name="expense-entry-list"),
    path("entries/<uuid:pk>/", ExpenseEntryDetailAPIView.as_view(), name="expense-entry-detail"),
    path("analytics/", ExpenseAnalyticsAPIView.as_view(), name="expense-analytics"),
    path("dashboard/", ExpenseDashboardAPIView.as_view(), name="expense-dashboard"),
    path("reports/preferences/", ExpenseReportPreferenceListCreateAPIView.as_view(), name="expense-report-pref-list"),
    path("reports/preferences/<uuid:pk>/", ExpenseReportPreferenceDetailAPIView.as_view(), name="expense-report-pref-detail"),
    path("reports/download/", ExpenseReportDownloadAPIView.as_view(), name="expense-report-download"),
]
