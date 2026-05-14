from rest_framework import serializers

from .models import BudgetAllocation, ExpenseEntry, ExpenseReportPreference


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


class BudgetAllocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = BudgetAllocation
        fields = [
            "id", "user", "main_category", "sub_category", "amount", "frequency", "start_date", "end_date", "notes", "is_active", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "user", "created_at", "updated_at"]

    def validate_main_category(self, value):
        value = normalize_label(value)
        if not value:
            raise serializers.ValidationError("Main category is required.")
        return value

    def validate_sub_category(self, value):
        return normalize_label(value)


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
