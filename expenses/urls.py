from django.urls import path

from .views import (
    BudgetAllocationDetailAPIView,
    BudgetAllocationListCreateAPIView,
    CategoryTreeAPIView,
    ExpenseAnalyticsAPIView,
    ExpenseDashboardAPIView,
    ExpenseEntryDetailAPIView,
    ExpenseEntryListCreateAPIView,
    ExpenseItemListCreateAPIView,
    ExpenseReportDownloadAPIView,
    ExpenseReportPreferenceDetailAPIView,
    ExpenseReportPreferenceListCreateAPIView,
    MainCategoryListCreateAPIView,
    SubCategoryListCreateAPIView,
)

urlpatterns = [
    path("categories/main/", MainCategoryListCreateAPIView.as_view(), name="expense-main-category-list"),
    path("categories/sub/", SubCategoryListCreateAPIView.as_view(), name="expense-sub-category-list"),
    path("categories/items/", ExpenseItemListCreateAPIView.as_view(), name="expense-item-list"),
    path("categories/tree/", CategoryTreeAPIView.as_view(), name="expense-category-tree"),
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
