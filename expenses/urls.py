from django.urls import path

from .views import (
    ExpenseAnalyticsAPIView,
    ExpenseDashboardAPIView,
    ExpenseEntryDetailAPIView,
    ExpenseEntryListCreateAPIView,
    ExpenseReportDownloadAPIView,
    ExpenseReportPreferenceDetailAPIView,
    ExpenseReportPreferenceListCreateAPIView,
    ExpenseSuggestionsAPIView,
    BudgetPlanListCreateAPIView,
    BudgetPlanDetailAPIView,
    BudgetPlanCloneAPIView,
    BudgetPlanLineListCreateAPIView,
    BudgetPlanLineDetailAPIView,
    BudgetPlanTrackingAPIView,

)

urlpatterns = [
    # common api's
    path("categories/suggestions/", ExpenseSuggestionsAPIView.as_view(), name="expense-category-suggestions"),

    # budget api's
    path("budget-plans/", BudgetPlanListCreateAPIView.as_view(), name="expense-budget-plan-list"),
    path("budget-plans/<uuid:pk>/", BudgetPlanDetailAPIView.as_view(), name="expense-budget-plan-detail"),
    path("budget-plans/<uuid:budget_id>/lines/", BudgetPlanLineListCreateAPIView.as_view(), name="expense-budget-plan-lines"),
    path("budget-plans/<uuid:budget_id>/lines/<uuid:line_id>/", BudgetPlanLineDetailAPIView.as_view(), name="expense-budget-plan-line-detail"),
    path("budget-plans/<uuid:budget_id>/tracking/", BudgetPlanTrackingAPIView.as_view(), name="expense-budget-plan-tracking"),
    path("budget-plans/<uuid:budget_id>/clone/", BudgetPlanCloneAPIView.as_view(), name="expense-budget-plan-clone"),

    # expense api's
    path("entries/", ExpenseEntryListCreateAPIView.as_view(), name="expense-entry-list"),
    path("entries/<uuid:pk>/", ExpenseEntryDetailAPIView.as_view(), name="expense-entry-detail"),
    path("analytics/", ExpenseAnalyticsAPIView.as_view(), name="expense-analytics"),
    path("dashboard/", ExpenseDashboardAPIView.as_view(), name="expense-dashboard"),
    path("reports/preferences/", ExpenseReportPreferenceListCreateAPIView.as_view(), name="expense-report-pref-list"),
    path("reports/preferences/<uuid:pk>/", ExpenseReportPreferenceDetailAPIView.as_view(), name="expense-report-pref-detail"),
    path("reports/download/", ExpenseReportDownloadAPIView.as_view(), name="expense-report-download"),
]
