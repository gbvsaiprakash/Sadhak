import csv
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Sum, Q
from django.db.models.functions import TruncDate, TruncMonth, TruncQuarter, TruncWeek, TruncYear
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from sadhak.app_settings import SYSTEM_DEFAULT_EXPENSES as SYSTEM_DEFAULTS
from user_management.views import AuthenticatedAPIView
from .models import BudgetAllocation, ExpenseEntry, ExpenseReportPreference
from .serializers import BudgetAllocationSerializer, ExpenseEntrySerializer, ExpenseReportPreferenceSerializer





def _dedupe_case_insensitive(values):
    seen = {}
    for raw in values:
        value = " ".join((raw or "").strip().split())
        if not value:
            continue
        key = value.lower()
        if key not in seen:
            seen[key] = value
    return sorted(seen.values(), key=lambda v: v.lower())


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

        main_category = request.query_params.get("main_category")
        sub_category = request.query_params.get("sub_category")
        item = request.query_params.get("item")
        source = request.query_params.get("source")
        payment_method = request.query_params.get("payment_method")
        search = request.query_params.get("search")

        if main_category:
            queryset = queryset.filter(main_category__iexact=main_category)
        if sub_category:
            queryset = queryset.filter(sub_category__iexact=sub_category)
        if item:
            queryset = queryset.filter(item__iexact=item)
        if source:
            queryset = queryset.filter(source=source)
        if payment_method:
            queryset = queryset.filter(payment_method__iexact=payment_method)
        if search:
            queryset = queryset.filter(
                Q(notes__icontains=search)
                | Q(transaction_reference__icontains=search)
                | Q(item__icontains=search)
                | Q(sub_category__icontains=search)
                | Q(main_category__icontains=search)
            )
        return queryset


class ExpenseSuggestionsAPIView(ExpenseBaseAPIView):
    def get(self, request):
        qs = ExpenseEntry.objects.filter(user=request.user, is_deleted=False)

        main_categories = _dedupe_case_insensitive(SYSTEM_DEFAULTS["main_categories"] + list(qs.exclude(main_category="").values_list("main_category", flat=True).distinct()))
        sub_categories = _dedupe_case_insensitive(SYSTEM_DEFAULTS["sub_categories"] + list(qs.exclude(sub_category="").values_list("sub_category", flat=True).distinct()))
        items = _dedupe_case_insensitive(SYSTEM_DEFAULTS["items"] + list(qs.exclude(item="").values_list("item", flat=True).distinct()))

        return Response({"main_categories": main_categories, "sub_categories": sub_categories, "items": items}, status=status.HTTP_200_OK)


class BudgetAllocationListCreateAPIView(ExpenseBaseAPIView):
    def get(self, request):
        queryset = BudgetAllocation.objects.filter(user=request.user, is_deleted=False).order_by("-created_at")
        return Response(BudgetAllocationSerializer(queryset, many=True).data)

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
        return Response(serializer.data)

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
        return Response(ExpenseEntrySerializer(queryset, many=True).data)

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
        return Response(serializer.data)

    def delete(self, request, pk):
        expense = self._get_object(request, pk)
        if not expense:
            return Response({"message": "Expense entry not found"}, status=status.HTTP_404_NOT_FOUND)
        expense.is_deleted = True
        expense.save(update_fields=["is_deleted", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class ExpenseAnalyticsAPIView(ExpenseBaseAPIView):
    trunc_map = {"daily": TruncDate, "weekly": TruncWeek, "monthly": TruncMonth, "quarterly": TruncQuarter, "yearly": TruncYear}

    def get(self, request):
        period = request.query_params.get("period", "monthly")
        trunc_fn = self.trunc_map.get(period)
        if not trunc_fn:
            return Response({"message": "Unsupported period"}, status=status.HTTP_400_BAD_REQUEST)

        queryset = self._base_expense_queryset(request)

        trend = queryset.annotate(period_bucket=trunc_fn("spent_at")).values("period_bucket").annotate(total=Sum("amount"), count=Count("id")).order_by("period_bucket")
        by_main = queryset.values("main_category").annotate(total=Sum("amount"), count=Count("id")).order_by("-total")
        by_sub = queryset.values("sub_category").annotate(total=Sum("amount"), count=Count("id")).order_by("-total")

        return Response({"period": period, "trend": list(trend), "by_main_category": list(by_main), "by_sub_category": list(by_sub), "total_spent": queryset.aggregate(total=Sum("amount")).get("total") or Decimal("0.00"), "entries": queryset.count()})


class ExpenseDashboardAPIView(ExpenseBaseAPIView):
    def get(self, request):
        queryset = self._base_expense_queryset(request)
        total_spent = queryset.aggregate(total=Sum("amount")).get("total") or Decimal("0.00")
        total_entries = queryset.count()

        budgets = BudgetAllocation.objects.filter(user=request.user, is_deleted=False, is_active=True)
        budget_total = budgets.aggregate(total=Sum("amount")).get("total") or Decimal("0.00")

        recent_expenses = ExpenseEntrySerializer(queryset.order_by("-spent_at")[:10], many=True).data
        top_items = queryset.values("item").annotate(total=Sum("amount"), count=Count("id")).order_by("-total")[:10]

        return Response({
            "totals": {"spent": total_spent, "budget": budget_total, "remaining": budget_total - total_spent, "entries": total_entries},
            "top_items": list(top_items),
            "recent_expenses": recent_expenses,
            "applied_filters": {
                "from_date": request.query_params.get("from_date"),
                "to_date": request.query_params.get("to_date"),
                "main_category": request.query_params.get("main_category"),
                "sub_category": request.query_params.get("sub_category"),
                "item": request.query_params.get("item"),
                "source": request.query_params.get("source"),
                "payment_method": request.query_params.get("payment_method"),
                "search": request.query_params.get("search"),
            },
        })


class ExpenseReportPreferenceListCreateAPIView(ExpenseBaseAPIView):
    def get(self, request):
        queryset = ExpenseReportPreference.objects.filter(user=request.user, is_deleted=False)
        return Response(ExpenseReportPreferenceSerializer(queryset, many=True).data)

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
        return Response(serializer.data)

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
        queryset = self._base_expense_queryset(request)

        if format_type == "csv":
            response = HttpResponse(content_type="text/csv")
            response["Content-Disposition"] = 'attachment; filename="expense_report.csv"'
            writer = csv.writer(response)
            writer.writerow(["spent_at", "amount", "main_category", "sub_category", "item", "source", "payment_method", "transaction_reference", "notes"])
            for entry in queryset:
                writer.writerow([entry.spent_at.isoformat(), entry.amount, entry.main_category, entry.sub_category, entry.item, entry.source, entry.payment_method, entry.transaction_reference, entry.notes])
            return response

        return Response({"summary": {"total_spent": queryset.aggregate(total=Sum("amount")).get("total") or Decimal("0.00"), "entries": queryset.count()}, "results": ExpenseEntrySerializer(queryset, many=True).data})
