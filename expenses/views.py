import csv
from datetime import timedelta, date
from decimal import Decimal
import json
import os
from calendar import monthrange
from django.db.models import Count, Sum, Q
from django.db.models.functions import TruncDate, TruncMonth, TruncQuarter, TruncWeek, TruncYear
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from sadhak.app_settings import SYSTEM_DEFAULT_EXPENSES as SYSTEM_DEFAULTS
from user_management.views import AuthenticatedAPIView
from .models import ExpenseEntry, ExpenseReportPreference, BudgetAllocationLine, BudgetPlan
from .serializers import ExpenseEntrySerializer, ExpenseReportPreferenceSerializer, BudgetAllocationLineBulkSerializer, BudgetAllocationLineSerializer, BudgetPlanSerializer
from .services import mark_schedule_on_create, mark_schedule_on_frequency_change, mark_schedule_on_disable

try:
    SYSTEM_DEFAULTS = json.loads(os.getenv("SYSTEM_DEFAULT_EXPENSES", "{}"))
except json.JSONDecodeError:
    SYSTEM_DEFAULTS = {}



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

def _month_last_day(year, month):
    return date(year, month, monthrange(year, month)[1])


def _next_month(year, month):
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _can_create_monthly_plan(year, month, today=None):
    today = today or timezone.localdate()
    if month == 1:
        prev_y, prev_m = year - 1, 12
    else:
        prev_y, prev_m = year, month - 1
    window_start = _month_last_day(prev_y, prev_m)
    window_end = _month_last_day(year, month)
    return window_start <= today <= window_end


def _has_active_conflict(user, period_type, year=None, month=None, exclude_id=None):
    qs = BudgetPlan.objects.filter(user=user, is_deleted=False, is_active=True, period_type=period_type)
    if exclude_id:
        qs = qs.exclude(id=exclude_id)
    if period_type == "monthly":
        return qs.filter(year=year, month=month).exists()
    if period_type == "yearly":
        return qs.filter(year=year).exists()
    return False

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
    
    def _current_plan_queryset(self, user):
        today = timezone.localdate()
        return BudgetPlan.objects.filter(user=user, is_deleted=False, is_active=True).filter(
            Q(period_type="yearly", year=today.year)
            | Q(period_type="monthly", year=today.year, month=today.month)
            | Q(period_type="custom", start_date__lte=today, end_date__gte=today)
        )

class ExpenseSuggestionsAPIView(ExpenseBaseAPIView):
    def _find_case_insensitive(self, values, target):
        target_key = (target or "").strip().lower()
        if not target_key:
            return None
        for value in values:
            if (value or "").strip().lower() == target_key:
                return value
        return None

    def _build_default_tree(self):
        if isinstance(SYSTEM_DEFAULTS, dict):
            return SYSTEM_DEFAULTS
        return {}

    def _build_merged_tree(self, user):
        default_tree = self._build_default_tree()
        history_qs = ExpenseEntry.objects.filter(user=user, is_deleted=False)
        budget_qs = BudgetPlan.objects.filter(user=user, is_deleted=False)
        history_budget_qs = BudgetAllocationLine.objects.filter(budget_plan__in=budget_qs, is_deleted=False)

        tree = {}
        for main, sub_map in default_tree.items():
            main_name = " ".join(str(main).split())
            if not main_name:
                continue
            tree.setdefault(main_name, {})
            if isinstance(sub_map, dict):
                for sub, item_list in sub_map.items():
                    sub_name = " ".join(str(sub).split())
                    if not sub_name:
                        continue
                    tree[main_name].setdefault(sub_name, set())
                    if isinstance(item_list, list):
                        for item in item_list:
                            item_name = " ".join(str(item).split())
                            if item_name:
                                tree[main_name][sub_name].add(item_name)

        for main, sub, item in history_qs.values_list("main_category", "sub_category", "item"):
            main_name = " ".join((main or "").split())
            sub_name = " ".join((sub or "").split())
            item_name = " ".join((item or "").split())
            if not main_name:
                continue
            tree.setdefault(main_name, {})
            if sub_name:
                tree[main_name].setdefault(sub_name, set())
                if item_name:
                    tree[main_name][sub_name].add(item_name)
        
        for main, sub, item in history_budget_qs.values_list("main_category", "sub_category", "item"):
            main_name = " ".join((main or "").split())
            sub_name = " ".join((sub or "").split())
            item_name = " ".join((item or "").split())
            if not main_name:
                continue
            tree.setdefault(main_name, {})
            if sub_name:
                tree[main_name].setdefault(sub_name, set())
                if item_name:
                    tree[main_name][sub_name].add(item_name)
        
        return tree

    def get(self, request):
        request_type = (request.query_params.get("type") or "all").strip().lower()
        if request_type not in {"all", "main_category", "sub_category", "item"}:
            return Response(
                {"message": "Invalid type. Allowed values: all, main_category, sub_category, item."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        selected_main = (request.query_params.get("main_category") or "").strip()
        selected_sub = (request.query_params.get("sub_category") or "").strip()
        selected_item = (request.query_params.get("item") or "").strip()

        tree = self._build_merged_tree(request.user)
        main_categories = _dedupe_case_insensitive(list(tree.keys()))

        # Match selected values to canonical values from tree.
        matched_main = self._find_case_insensitive(main_categories, selected_main)

        all_subs = _dedupe_case_insensitive([sub for sub_map in tree.values() for sub in sub_map.keys()])
        matched_sub = self._find_case_insensitive(all_subs, selected_sub)

        all_items = _dedupe_case_insensitive([i for sub_map in tree.values() for items in sub_map.values() for i in items])
        matched_item = self._find_case_insensitive(all_items, selected_item)

        # Find all branches that satisfy selected filters at any level.
        branches = []
        for main, sub_map in tree.items():
            for sub, items_set in sub_map.items():
                branch_items = list(items_set)
                if matched_main and main.lower() != matched_main.lower():
                    continue
                if matched_sub and sub.lower() != matched_sub.lower():
                    continue
                if matched_item and all(i.lower() != matched_item.lower() for i in branch_items):
                    continue
                branches.append((main, sub, branch_items))

        # If no direct branch match and only item is provided, do reverse lookup by item.
        if not branches and matched_item:
            for main, sub_map in tree.items():
                for sub, items_set in sub_map.items():
                    if any(i.lower() == matched_item.lower() for i in items_set):
                        branches.append((main, sub, list(items_set)))

        # Derive inferred values for auto-fill on UI.
        inferred_main = _dedupe_case_insensitive([b[0] for b in branches])
        inferred_sub = _dedupe_case_insensitive([b[1] for b in branches])
        inferred_items = _dedupe_case_insensitive([i for _, _, branch_items in branches for i in branch_items])

        # Compute suggestion lists depending on current selections.
        if branches:
            scoped_main = inferred_main
            scoped_sub = inferred_sub
            scoped_items = inferred_items
        else:
            scoped_main = main_categories
            scoped_sub = all_subs
            scoped_items = all_items

        # If only main is selected, scope sub/items by that main.
        if matched_main and not matched_sub and not matched_item:
            sub_source = list(tree.get(matched_main, {}).keys())
            item_source = [i for items in tree.get(matched_main, {}).values() for i in items]
            scoped_sub = _dedupe_case_insensitive(sub_source)
            scoped_items = _dedupe_case_insensitive(item_source)

        # If main+sub selected, items must come from exact branch.
        if matched_main and matched_sub and not matched_item:
            scoped_items = _dedupe_case_insensitive(list(tree.get(matched_main, {}).get(matched_sub, set())))
            scoped_sub = [matched_sub] if matched_sub else scoped_sub

        payload_common = {
            "selected": {
                "main_category": matched_main,
                "sub_category": matched_sub,
                "item": matched_item,
            },
            "inferred": {
                "main_categories": inferred_main,
                "sub_categories": inferred_sub,
                "items": inferred_items,
                "auto_main_category": inferred_main[0] if len(inferred_main) == 1 else None,
                "auto_sub_category": inferred_sub[0] if len(inferred_sub) == 1 else None,
            },
        }

        if request_type == "main_category":
            return Response({"main_categories": scoped_main, **payload_common}, status=status.HTTP_200_OK)
        if request_type == "sub_category":
            return Response({"sub_categories": scoped_sub, **payload_common}, status=status.HTTP_200_OK)
        if request_type == "item":
            return Response({"items": scoped_items, **payload_common}, status=status.HTTP_200_OK)

        response_tree = {
            main: {sub: sorted(list(items_set), key=lambda x: x.lower()) for sub, items_set in sub_map.items()}
            for main, sub_map in tree.items()
        }

        return Response(
            {
                "main_categories": scoped_main,
                "sub_categories": scoped_sub,
                "items": scoped_items,
                "tree": response_tree,
                **payload_common,
            },
            status=status.HTTP_200_OK,
        )


class BudgetPlanListCreateAPIView(ExpenseBaseAPIView):
    def get(self, request):
        queryset = BudgetPlan.objects.filter(user=request.user, is_deleted=False).order_by("-created_at")
        return Response(BudgetPlanSerializer(queryset, many=True).data)

    def post(self, request):
        serializer = BudgetPlanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(user=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class BudgetPlanDetailAPIView(ExpenseBaseAPIView):
    def _get_object(self, request, pk):
        return BudgetPlan.objects.filter(user=request.user, is_deleted=False, id=pk).first()

    def get(self, request, pk):
        plan = self._get_object(request, pk)
        if not plan:
            return Response({"message": "Budget plan not found"}, status=status.HTTP_404_NOT_FOUND)
        data = BudgetPlanSerializer(plan).data
        data["lines"] = BudgetAllocationLineSerializer(plan.lines.filter(is_deleted=False), many=True).data
        return Response(data)

    def patch(self, request, pk):
        plan = self._get_object(request, pk)
        if not plan:
            return Response({"message": "Budget plan not found"}, status=status.HTTP_404_NOT_FOUND)
        
        serializer = BudgetPlanSerializer(plan, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk):
        plan = self._get_object(request, pk)
        if not plan:
            return Response({"message": "Budget plan not found"}, status=status.HTTP_404_NOT_FOUND)
        plan.is_deleted = True
        plan.is_active = False
        plan.save(update_fields=["is_deleted", "is_active", "updated_at"])
        plan.lines.filter(is_deleted=False).update(is_deleted=True, is_active=False, updated_at=timezone.now())
        return Response(status=status.HTTP_204_NO_CONTENT)


class BudgetPlanLineListCreateAPIView(ExpenseBaseAPIView):
    def _get_plan(self, request, budget_id):
        return BudgetPlan.objects.filter(user=request.user, is_deleted=False, id=budget_id).first()

    def get(self, request, budget_id):
        plan = self._get_plan(request, budget_id)
        if not plan:
            return Response({"message": "Budget plan not found"}, status=status.HTTP_404_NOT_FOUND)
        queryset = plan.lines.filter(is_deleted=False).order_by("-created_at")
        return Response(BudgetAllocationLineSerializer(queryset, many=True).data)

    def post(self, request, budget_id):
        plan = self._get_plan(request, budget_id)
        if not plan:
            return Response({"message": "Budget plan not found"}, status=status.HTTP_404_NOT_FOUND)

        if isinstance(request.data, dict) and isinstance(request.data.get("lines"), list):
            for line_data in request.data["lines"]:
                line_data["budget_plan"] = budget_id
            serializer = BudgetAllocationLineBulkSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            created = []
            for line_data in serializer.validated_data["lines"]:
                line_data["budget_plan"] = plan
                created.append(BudgetAllocationLine.objects.create(**line_data))
            return Response(BudgetAllocationLineSerializer(created, many=True).data, status=status.HTTP_201_CREATED)

        if isinstance(request.data, dict):
            request.data["budget_plan"] = budget_id
        serializer = BudgetAllocationLineSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(budget_plan=plan)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class BudgetPlanLineDetailAPIView(ExpenseBaseAPIView):
    def _get_line(self, request, budget_id, line_id):
        return BudgetAllocationLine.objects.filter(budget_plan__user=request.user, budget_plan_id=budget_id, is_deleted=False, id=line_id).first()

    def patch(self, request, budget_id, line_id):
        line = self._get_line(request, budget_id, line_id)
        if not line:
            return Response({"message": "Budget line not found"}, status=status.HTTP_404_NOT_FOUND)
        request.data["budget_plan"] = budget_id
        serializer = BudgetAllocationLineSerializer(line, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, budget_id, line_id):
        line = self._get_line(request, budget_id, line_id)
        if not line:
            return Response({"message": "Budget line not found"}, status=status.HTTP_404_NOT_FOUND)
        line.is_deleted = True
        line.is_active = False
        line.save(update_fields=["is_deleted", "is_active", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class BudgetPlanTrackingAPIView(ExpenseBaseAPIView):
    def _get_plan(self, request, budget_id):
        return BudgetPlan.objects.filter(user=request.user, is_deleted=False, id=budget_id).first()

    def get(self, request, budget_id):
        plan = self._get_plan(request, budget_id)
        if not plan:
            return Response({"message": "Budget plan not found"}, status=status.HTTP_404_NOT_FOUND)

        expenses = ExpenseEntry.objects.filter(user=request.user, is_deleted=False)
        if plan.period_type == "monthly" and plan.year and plan.month:
            expenses = expenses.filter(spent_at__year=plan.year, spent_at__month=plan.month)
        elif plan.period_type == "yearly" and plan.year:
            expenses = expenses.filter(spent_at__year=plan.year)
        elif plan.period_type == "custom" and plan.start_date and plan.end_date:
            expenses = expenses.filter(spent_at__date__gte=plan.start_date, spent_at__date__lte=plan.end_date)

        lines = plan.lines.filter(is_deleted=False, is_active=True)
        total_budget = lines.aggregate(total=Sum("amount")).get("total") or Decimal("0.00")
        total_spent = expenses.aggregate(total=Sum("amount")).get("total") or Decimal("0.00")

        line_rows = []
        for line in lines:
            line_exp = expenses.filter(main_category__iexact=line.main_category)
            if line.sub_category:
                line_exp = line_exp.filter(sub_category__iexact=line.sub_category)
            if line.item:
                line_exp = line_exp.filter(item__iexact=line.item)
            line_spent = line_exp.aggregate(total=Sum("amount")).get("total") or Decimal("0.00")
            line_rows.append({
                "line_id": str(line.id),
                "main_category": line.main_category,
                "sub_category": line.sub_category,
                "item": line.item,
                "budget_amount": line.amount,
                "spent_amount": line_spent,
                "remaining_amount": line.amount - line_spent,
                "rollup_level": line.rollup_level,
            })

        return Response(
            {
                "budget_plan": BudgetPlanSerializer(plan).data,
                "summary": {
                    "total_budget": total_budget,
                    "total_spent": total_spent,
                    "total_remaining": total_budget - total_spent,
                },
                "lines": line_rows,
            },
            status=status.HTTP_200_OK,
        )

class BudgetPlanCloneAPIView(ExpenseBaseAPIView):
    def _get_plan(self, request, budget_id):
        return BudgetPlan.objects.filter(user=request.user, is_deleted=False, id=budget_id).first()

    def post(self, request, budget_id):
        source = self._get_plan(request, budget_id)
        if not source:
            return Response({"message": "Budget plan not found"}, status=status.HTTP_404_NOT_FOUND)

        period_type = request.data.get("period_type") or source.period_type
        year = request.data.get("year")
        month = request.data.get("month")
        start_date = request.data.get("start_date")
        end_date = request.data.get("end_date")

        if period_type == "monthly":
            if year is None or month is None:
                if source.period_type == "monthly" and source.year and source.month:
                    year, month = _next_month(source.year, source.month)
                else:
                    today = timezone.localdate()
                    year, month = _next_month(today.year, today.month)
            year, month = int(year), int(month)
            if not _can_create_monthly_plan(year, month):
                return Response({"message": f"Monthly budget for {month}/{year} can be created only between previous month end and month end."}, status=status.HTTP_400_BAD_REQUEST)
            if _has_active_conflict(request.user, "monthly", year=year, month=month):
                return Response({"message": "An active monthly budget already exists for this month/year."}, status=status.HTTP_400_BAD_REQUEST)

        if period_type == "yearly":
            if year is None:
                year = (source.year + 1) if source.year else (timezone.localdate().year + 1)
            year = int(year)
            if _has_active_conflict(request.user, "yearly", year=year):
                return Response({"message": "An active yearly budget already exists for this year."}, status=status.HTTP_400_BAD_REQUEST)

        payload = {
            "name": request.data.get("name") or f"{source.name} (Copy)",
            "period_type": period_type,
            "year": year if period_type in {"monthly", "yearly"} else None,
            "month": month if period_type == "monthly" else None,
            "start_date": start_date if period_type == "custom" else None,
            "end_date": end_date if period_type == "custom" else None,
            "currency": request.data.get("currency") or source.currency,
            "is_active": request.data.get("is_active", True),
        }

        serializer = BudgetPlanSerializer(data=payload, context={"request": request})
        serializer.is_valid(raise_exception=True)
        cloned = serializer.save(user=request.user)

        source_lines = source.lines.filter(is_deleted=False)
        for line in source_lines:
            BudgetAllocationLine.objects.create(
                budget_plan=cloned,
                main_category=line.main_category,
                sub_category=line.sub_category,
                item=line.item,
                amount=line.amount,
                rollup_level=line.rollup_level,
                notes=line.notes,
                is_active=line.is_active,
            )

        data = BudgetPlanSerializer(cloned).data
        data["lines"] = BudgetAllocationLineSerializer(cloned.lines.filter(is_deleted=False), many=True).data
        return Response(data, status=status.HTTP_201_CREATED)

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
    def _period_bounds_for_plan(self, plan):
        if plan.period_type == "monthly" and plan.year and plan.month:
            return date(plan.year, plan.month, 1), _month_last_day(plan.year, plan.month)
        if plan.period_type == "yearly" and plan.year:
            return date(plan.year, 1, 1), date(plan.year, 12, 31)
        if plan.period_type == "custom" and plan.start_date and plan.end_date:
            return plan.start_date, plan.end_date
        return None, None

    def get(self, request):
        queryset = self._base_expense_queryset(request)
        total_spent = queryset.aggregate(total=Sum("amount")).get("total") or Decimal("0.00")
        total_entries = queryset.count()

        from_date = self._parse_date(request.query_params.get("from_date"), timezone.localdate() - timedelta(days=30))
        to_date = self._parse_date(request.query_params.get("to_date"), timezone.localdate())

        main_category = request.query_params.get("main_category")
        sub_category = request.query_params.get("sub_category")
        item = request.query_params.get("item")
        search = request.query_params.get("search")

        active_plans = BudgetPlan.objects.filter(user=request.user, is_deleted=False, is_active=True)
        scoped_plan_ids = []
        for plan in active_plans:
            p_start, p_end = self._period_bounds_for_plan(plan)
            if p_start and p_end and p_start <= to_date and p_end >= from_date:
                scoped_plan_ids.append(plan.id)

        plan_lines = BudgetAllocationLine.objects.filter(
            budget_plan_id__in=scoped_plan_ids,
            is_deleted=False,
            is_active=True,
        )

        if main_category:
            plan_lines = plan_lines.filter(main_category__iexact=main_category)
        if sub_category:
            plan_lines = plan_lines.filter(sub_category__iexact=sub_category)
        if item:
            plan_lines = plan_lines.filter(item__iexact=item)
        if search:
            plan_lines = plan_lines.filter(
                Q(notes__icontains=search)
                | Q(item__icontains=search)
                | Q(sub_category__icontains=search)
                | Q(main_category__icontains=search)
            )

        budget_total = plan_lines.aggregate(total=Sum("amount")).get("total") or Decimal("0.00")
        has_budget = budget_total > 0
        remaining_budget = max(budget_total - total_spent, Decimal("0.00"))
        over_budget_amount = max(total_spent - budget_total, Decimal("0.00"))
        utilization_pct = (total_spent / budget_total * Decimal("100.00")) if budget_total > 0 else Decimal("0.00")

        recent_expenses = ExpenseEntrySerializer(queryset.order_by("-spent_at")[:10], many=True).data
        top_items_qs = queryset.values("item").annotate(total=Sum("amount"), count=Count("id")).order_by("-total")[:10]
        top_items = []
        for row in top_items_qs:
            item_total = row.get("total") or Decimal("0.00")
            pct = (item_total / total_spent * Decimal("100.00")) if total_spent > 0 else Decimal("0.00")
            top_items.append({
                "item": row.get("item"),
                "total": item_total,
                "count": row.get("count", 0),
                "percentage": round(pct, 2),
            })

        return Response({
            "totals": {
                "spent": total_spent,
                "budget": budget_total,
                "remaining": remaining_budget,
                "entries": total_entries,
                "has_budget": has_budget,
                "over_budget_amount": over_budget_amount,
                "utilization_pct": round(utilization_pct, 2),
                "is_over_budget": over_budget_amount > 0,
            },
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
        pref = serializer.save(user=request.user)
        mark_schedule_on_create(pref)
        pref.save(update_fields=["last_run_at", "next_run_at", "updated_at"])
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class ExpenseReportPreferenceDetailAPIView(ExpenseBaseAPIView):
    def _get_object(self, request, pk):
        return ExpenseReportPreference.objects.filter(user=request.user, is_deleted=False, id=pk).first()

    def patch(self, request, pk):
        report_pref = self._get_object(request, pk)
        if not report_pref:
            return Response({"message": "Report preference not found"}, status=status.HTTP_404_NOT_FOUND)
        
        old_frequency = report_pref.frequency
        old_active = report_pref.is_active

        serializer = ExpenseReportPreferenceSerializer(report_pref, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        pref = serializer.save()

        if pref.frequency != old_frequency:
            mark_schedule_on_frequency_change(pref)
        if old_active and not pref.is_active:
            mark_schedule_on_disable(pref)
        if (not old_active) and pref.is_active and pref.next_run_at is None:
            mark_schedule_on_frequency_change(pref)

        pref.save(update_fields=["next_run_at", "updated_at"])
        return Response(ExpenseReportPreferenceSerializer(pref).data)

    def delete(self, request, pk):
        report_pref = self._get_object(request, pk)
        if not report_pref:
            return Response({"message": "Report preference not found"}, status=status.HTTP_404_NOT_FOUND)
        report_pref.is_deleted = True
        report_pref.is_active = False
        mark_schedule_on_disable(report_pref)
        report_pref.save(update_fields=["is_deleted", "is_active", "next_run_at", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class ExpenseReportDownloadAPIView(ExpenseBaseAPIView):
    def get(self, request):
        format_type = request.query_params.get("file_format", "json").lower()
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
