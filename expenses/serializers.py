from rest_framework import serializers
from calendar import monthrange
from datetime import date
from .models import ExpenseEntry, ExpenseReportPreference, BudgetPlan, BudgetAllocationLine
from django.utils import timezone

def normalize_label(value):
    value = " ".join((value or "").strip().split())
    if not value:
        return ""
    words = []
    for token in value.split(" "):
        if token.isupper() and len(token) <= 4:
            words.append(token)
        else:
            words.append(token.capitalize())
    return " ".join(words)

def previous_month_last_day(year, month):
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1
    return date(prev_year, prev_month, monthrange(prev_year, prev_month)[1])


def month_last_day(year, month):
    return date(year, month, monthrange(year, month)[1])

class BudgetPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = BudgetPlan
        fields = [
            "id", "user", "name", "period_type", "year", "month", "start_date", "end_date", "currency", "is_active", "is_deleted", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "user", "is_deleted", "created_at", "updated_at"]

    def validate(self, attrs):
        user = getattr(self.context.get("request"), "user", None)

        period_type = attrs.get("period_type", getattr(self.instance, "period_type", None))
        year = attrs.get("year", getattr(self.instance, "year", None))
        month = attrs.get("month", getattr(self.instance, "month", None))
        start_date = attrs.get("start_date", getattr(self.instance, "start_date", None))
        end_date = attrs.get("end_date", getattr(self.instance, "end_date", None))
        is_active = attrs.get("is_active", getattr(self.instance, "is_active", True))

        if period_type == "monthly":
            if not year or not month:
                raise serializers.ValidationError("Monthly budget requires year and month.")
            if month < 1 or month > 12:
                raise serializers.ValidationError("Month should be between 1 and 12.")
        elif period_type == "yearly":
            if not year:
                raise serializers.ValidationError("Yearly budget requires year.")
        elif period_type == "custom":
            if not start_date or not end_date:
                raise serializers.ValidationError("Custom budget requires start_date and end_date.")
            if start_date > end_date:
                raise serializers.ValidationError("start_date cannot be greater than end_date.")
        
        if is_active and user and not getattr(user, "is_anonymous", True):
            qs = BudgetPlan.objects.filter(user=user, is_deleted=False, is_active=True, period_type=period_type)
            if self.instance:
                qs = qs.exclude(id=self.instance.id)

            if period_type == "monthly":
                qs = qs.filter(year=year, month=month)
                if qs.exists():
                    raise serializers.ValidationError("Only one active monthly budget plan is allowed for a specific month/year.")
            elif period_type == "yearly":
                qs = qs.filter(year=year)
                if qs.exists():
                    raise serializers.ValidationError("Only one active yearly budget plan is allowed for a specific year.")

        return attrs


class BudgetAllocationLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = BudgetAllocationLine
        fields = [
            "id", "budget_plan", "main_category", "sub_category", "item", "amount", "rollup_level", "notes", "is_active", "is_deleted", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "is_deleted", "created_at", "updated_at"]

    def validate_main_category(self, value):
        value = normalize_label(value)
        if not value:
            raise serializers.ValidationError("Main category is required.")
        return value

    def validate_sub_category(self, value):
        return normalize_label(value)

    def validate_item(self, value):
        return normalize_label(value)

    def validate(self, attrs):
        rollup_level = attrs.get("rollup_level", getattr(self.instance, "rollup_level", "item"))
        sub_category = attrs.get("sub_category", getattr(self.instance, "sub_category", ""))
        item = attrs.get("item", getattr(self.instance, "item", ""))

        if rollup_level == "category":
            attrs["sub_category"] = ""
            attrs["item"] = ""
        elif rollup_level == "subcategory":
            if not sub_category:
                raise serializers.ValidationError("Sub-category is required for subcategory rollup.")
            attrs["item"] = ""
        elif rollup_level == "item":
            if not sub_category or not item:
                raise serializers.ValidationError("Sub-category and item are required for item rollup.")

        return attrs


class BudgetAllocationLineBulkSerializer(serializers.Serializer):
    lines = BudgetAllocationLineSerializer(many=True)

class ExpenseEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = ExpenseEntry
        fields = [
            "id", "user", "amount", "main_category", "sub_category", "item", "spent_at", "source", "transaction_reference", "payment_method", "notes", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "user", "created_at", "updated_at"]

    def validate_main_category(self, value):
        value = normalize_label(value)
        if not value:
            raise serializers.ValidationError("Main category is required.")
        return value

    def validate_sub_category(self, value):
        return normalize_label(value)

    def validate_item(self, value):
        return normalize_label(value)


class ExpenseReportPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExpenseReportPreference
        fields = [
            "id", "user", "name", "frequency", "delivery_email", "report_format", "include_budget_vs_actual", "is_active", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "user", "created_at", "updated_at"]
