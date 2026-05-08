import csv
from datetime import timedelta
from decimal import Decimal

from django.db.models import Q, Sum, Count
from django.db.models.functions import TruncDate, TruncWeek, TruncMonth, TruncQuarter, TruncYear
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

from user_management.views import AuthenticatedAPIView
from .models import (
    BudgetAllocation,
    ExpenseEntry,
    ExpenseItem,
    ExpenseMainCategory,
    ExpenseReportPreference,
    ExpenseSubCategory,
)
from .serializers import (
    BudgetAllocationSerializer,
    CategoryTreeFilterSerializer,
    ExpenseEntrySerializer,
    ExpenseItemSerializer,
    ExpenseMainCategorySerializer,
    ExpenseReportPreferenceSerializer,
    ExpenseSubCategorySerializer,
)


class ExpenseBaseAPIView(AuthenticatedAPIView):
    def _parse_date(self, value, default):
        if not value:
            return default
        try:
            return timezone.datetime.fromisoformat(value).date()
        except ValueError:
            return default

    def _base_expense_queryset(self, request):
        queryset = ExpenseEntry.objects.filter(user=request.user, is_deleted=False)

        from_date = self._parse_date(request.query_params.get("from_date"), timezone.localdate() - timedelta(days=30))
        to_date = self._parse_date(request.query_params.get("to_date"), timezone.localdate())
        queryset = queryset.filter(spent_at__date__gte=from_date, spent_at__date__lte=to_date)

        main_category_id = request.query_params.get("main_category_id")
        sub_category_id = request.query_params.get("sub_category_id")
        source = request.query_params.get("source")
        payment_method = request.query_params.get("payment_method")
        search = request.query_params.get("search")

        if main_category_id:
            queryset = queryset.filter(main_category_id=main_category_id)
        if sub_category_id:
            queryset = queryset.filter(sub_category_id=sub_category_id)
        if source:
            queryset = queryset.filter(source=source)
        if payment_method:
            queryset = queryset.filter(payment_method__iexact=payment_method)
        if search:
            queryset = queryset.filter(
                Q(notes__icontains=search)
                | Q(transaction_reference__icontains=search)
                | Q(item__name__icontains=search)
                | Q(sub_category__name__icontains=search)
                | Q(main_category__name__icontains=search)
            )

        return queryset


class MainCategoryListCreateAPIView(ExpenseBaseAPIView):
    def get(self, request):
        search = request.query_params.get("search")
        queryset = ExpenseMainCategory.objects.filter(is_deleted=False).filter(Q(user=request.user) | Q(user__isnull=True))
        if search:
            queryset = queryset.filter(name__icontains=search)
        serializer = ExpenseMainCategorySerializer(queryset.order_by("name"), many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = ExpenseMainCategorySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(user=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class SubCategoryListCreateAPIView(ExpenseBaseAPIView):
    def get(self, request):
        search = request.query_params.get("search")
        main_category_id = request.query_params.get("main_category_id")
        queryset = ExpenseSubCategory.objects.filter(is_deleted=False).filter(Q(user=request.user) | Q(user__isnull=True))
        if main_category_id:
            queryset = queryset.filter(main_category_id=main_category_id)
        if search:
            queryset = queryset.filter(name__icontains=search)
        serializer = ExpenseSubCategorySerializer(queryset.order_by("name"), many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = ExpenseSubCategorySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(user=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class ExpenseItemListCreateAPIView(ExpenseBaseAPIView):
    def get(self, request):
        search = request.query_params.get("search")
        sub_category_id = request.query_params.get("sub_category_id")
        frequent_only = request.query_params.get("frequent_only")

        queryset = ExpenseItem.objects.filter(is_deleted=False).filter(Q(user=request.user) | Q(user__isnull=True))
        if sub_category_id:
            queryset = queryset.filter(sub_category_id=sub_category_id)
        if search:
            queryset = queryset.filter(name__icontains=search)
        if frequent_only and frequent_only.lower() == "true":
            queryset = queryset.filter(is_frequent=True)

        serializer = ExpenseItemSerializer(queryset.order_by("name"), many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = ExpenseItemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(user=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class CategoryTreeAPIView(ExpenseBaseAPIView):
    def get(self, request):
        filter_serializer = CategoryTreeFilterSerializer(data=request.query_params)
        filter_serializer.is_valid(raise_exception=True)

        main_categories = filter_serializer.filter_query(request.user)
        sub_categories = ExpenseSubCategory.objects.filter(
            is_deleted=False,
            is_active=True,
        ).filter(Q(user=request.user) | Q(user__isnull=True))
        items = ExpenseItem.objects.filter(
            is_deleted=False,
            is_active=True,
        ).filter(Q(user=request.user) | Q(user__isnull=True))

        payload = []
        for main in main_categories:
            main_sub_categories = sub_categories.filter(main_category=main)
            subs_payload = []
            for sub in main_sub_categories:
                subs_payload.append(
                    {
                        "id": str(sub.id),
                        "name": sub.name,
                        "items": [
                            {
                                "id": str(item.id),
                                "name": item.name,
                                "is_frequent": item.is_frequent,
                            }
                            for item in items.filter(sub_category=sub)
                        ],
                    }
                )
            payload.append({"id": str(main.id), "name": main.name, "sub_categories": subs_payload})

        return Response(payload, status=status.HTTP_200_OK)


class BudgetAllocationListCreateAPIView(ExpenseBaseAPIView):
    def get(self, request):
        queryset = BudgetAllocation.objects.filter(user=request.user, is_deleted=False).order_by("-created_at")
        serializer = BudgetAllocationSerializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = BudgetAllocationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(user=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class BudgetAllocationDetailAPIView(ExpenseBaseAPIView):
    def _get_object(self, request, pk):
        return BudgetAllocation.objects.filter(user=request.user, is_deleted=False, id=pk).first()

    def patch(self, request, pk):
        budget = self._get_object(request, pk)
        if not budget:
            return Response({"message": "Budget allocation not found"}, status=status.HTTP_404_NOT_FOUND)
        serializer = BudgetAllocationSerializer(budget, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    def delete(self, request, pk):
        budget = self._get_object(request, pk)
        if not budget:
            return Response({"message": "Budget allocation not found"}, status=status.HTTP_404_NOT_FOUND)
        budget.is_deleted = True
        budget.is_active = False
        budget.save(update_fields=["is_deleted", "is_active", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class ExpenseEntryListCreateAPIView(ExpenseBaseAPIView):
    def get(self, request):
        queryset = self._base_expense_queryset(request).order_by("-spent_at")
        serializer = ExpenseEntrySerializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = ExpenseEntrySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(user=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class ExpenseEntryDetailAPIView(ExpenseBaseAPIView):
    def _get_object(self, request, pk):
        return ExpenseEntry.objects.filter(user=request.user, is_deleted=False, id=pk).first()

    def patch(self, request, pk):
        expense = self._get_object(request, pk)
        if not expense:
            return Response({"message": "Expense entry not found"}, status=status.HTTP_404_NOT_FOUND)
        serializer = ExpenseEntrySerializer(expense, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    def delete(self, request, pk):
        expense = self._get_object(request, pk)
        if not expense:
            return Response({"message": "Expense entry not found"}, status=status.HTTP_404_NOT_FOUND)
        expense.is_deleted = True
        expense.save(update_fields=["is_deleted", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class ExpenseAnalyticsAPIView(ExpenseBaseAPIView):
    trunc_map = {
        "daily": TruncDate,
        "weekly": TruncWeek,
        "monthly": TruncMonth,
        "quarterly": TruncQuarter,
        "yearly": TruncYear,
    }

    def get(self, request):
        period = request.query_params.get("period", "monthly")
        trunc_fn = self.trunc_map.get(period)
        if not trunc_fn:
            return Response({"message": "Unsupported period"}, status=status.HTTP_400_BAD_REQUEST)

        queryset = self._base_expense_queryset(request)
        trend = (
            queryset.annotate(period_bucket=trunc_fn("spent_at"))
            .values("period_bucket")
            .annotate(total=Sum("amount"), count=Count("id"))
            .order_by("period_bucket")
        )

        by_main = (
            queryset.values("main_category__name")
            .annotate(total=Sum("amount"), count=Count("id"))
            .order_by("-total")
        )

        by_sub = (
            queryset.values("sub_category__name")
            .annotate(total=Sum("amount"), count=Count("id"))
            .order_by("-total")
        )

        return Response(
            {
                "period": period,
                "trend": list(trend),
                "by_main_category": list(by_main),
                "by_sub_category": list(by_sub),
                "total_spent": queryset.aggregate(total=Sum("amount")).get("total") or Decimal("0.00"),
                "entries": queryset.count(),
            },
            status=status.HTTP_200_OK,
        )


class ExpenseDashboardAPIView(ExpenseBaseAPIView):
    def get(self, request):
        queryset = self._base_expense_queryset(request)

        total_spent = queryset.aggregate(total=Sum("amount")).get("total") or Decimal("0.00")
        total_entries = queryset.count()

        budgets = BudgetAllocation.objects.filter(user=request.user, is_deleted=False, is_active=True)
        budget_total = budgets.aggregate(total=Sum("amount")).get("total") or Decimal("0.00")

        recent_expenses = ExpenseEntrySerializer(queryset.order_by("-spent_at")[:10], many=True).data

        top_items = (
            queryset.values("item__name")
            .annotate(total=Sum("amount"), count=Count("id"))
            .order_by("-total")[:10]
        )

        return Response(
            {
                "totals": {
                    "spent": total_spent,
                    "budget": budget_total,
                    "remaining": budget_total - total_spent,
                    "entries": total_entries,
                },
                "top_items": list(top_items),
                "recent_expenses": recent_expenses,
                "applied_filters": {
                    "from_date": request.query_params.get("from_date"),
                    "to_date": request.query_params.get("to_date"),
                    "main_category_id": request.query_params.get("main_category_id"),
                    "sub_category_id": request.query_params.get("sub_category_id"),
                    "source": request.query_params.get("source"),
                    "payment_method": request.query_params.get("payment_method"),
                    "search": request.query_params.get("search"),
                },
            },
            status=status.HTTP_200_OK,
        )


class ExpenseReportPreferenceListCreateAPIView(ExpenseBaseAPIView):
    def get(self, request):
        queryset = ExpenseReportPreference.objects.filter(user=request.user, is_deleted=False)
        return Response(ExpenseReportPreferenceSerializer(queryset, many=True).data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = ExpenseReportPreferenceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(user=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class ExpenseReportPreferenceDetailAPIView(ExpenseBaseAPIView):
    def _get_object(self, request, pk):
        return ExpenseReportPreference.objects.filter(user=request.user, is_deleted=False, id=pk).first()

    def patch(self, request, pk):
        report_pref = self._get_object(request, pk)
        if not report_pref:
            return Response({"message": "Report preference not found"}, status=status.HTTP_404_NOT_FOUND)
        serializer = ExpenseReportPreferenceSerializer(report_pref, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    def delete(self, request, pk):
        report_pref = self._get_object(request, pk)
        if not report_pref:
            return Response({"message": "Report preference not found"}, status=status.HTTP_404_NOT_FOUND)
        report_pref.is_deleted = True
        report_pref.is_active = False
        report_pref.save(update_fields=["is_deleted", "is_active", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class ExpenseReportDownloadAPIView(ExpenseBaseAPIView):
    def get(self, request):
        format_type = request.query_params.get("format", "json").lower()
        queryset = self._base_expense_queryset(request).select_related("main_category", "sub_category", "item")

        if format_type == "csv":
            response = HttpResponse(content_type="text/csv")
            response["Content-Disposition"] = 'attachment; filename="expense_report.csv"'

            writer = csv.writer(response)
            writer.writerow([
                "spent_at",
                "amount",
                "main_category",
                "sub_category",
                "item",
                "source",
                "payment_method",
                "transaction_reference",
                "notes",
            ])

            for entry in queryset:
                writer.writerow([
                    entry.spent_at.isoformat(),
                    entry.amount,
                    entry.main_category.name if entry.main_category else "",
                    entry.sub_category.name if entry.sub_category else "",
                    entry.item.name if entry.item else "",
                    entry.source,
                    entry.payment_method,
                    entry.transaction_reference,
                    entry.notes,
                ])

            return response

        serializer = ExpenseEntrySerializer(queryset, many=True)
        return Response(
            {
                "summary": {
                    "total_spent": queryset.aggregate(total=Sum("amount")).get("total") or Decimal("0.00"),
                    "entries": queryset.count(),
                },
                "results": serializer.data,
            },
            status=status.HTTP_200_OK,
        )
