from django.db.models import Q
from rest_framework import serializers

from .models import (
    BudgetAllocation,
    ExpenseEntry,
    ExpenseItem,
    ExpenseMainCategory,
    ExpenseReportPreference,
    ExpenseSubCategory,
)


class ExpenseMainCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ExpenseMainCategory
        fields = [
            "id",
            "user",
            "name",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "user", "created_at", "updated_at"]


class ExpenseSubCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ExpenseSubCategory
        fields = [
            "id",
            "user",
            "main_category",
            "name",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "user", "created_at", "updated_at"]


class ExpenseItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExpenseItem
        fields = [
            "id",
            "user",
            "sub_category",
            "name",
            "description",
            "is_frequent",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "user", "created_at", "updated_at"]


class BudgetAllocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = BudgetAllocation
        fields = [
            "id",
            "user",
            "main_category",
            "sub_category",
            "amount",
            "frequency",
            "start_date",
            "end_date",
            "notes",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "user", "created_at", "updated_at"]

    def validate(self, attrs):
        main_category = attrs.get("main_category") or getattr(self.instance, "main_category", None)
        sub_category = attrs.get("sub_category") or getattr(self.instance, "sub_category", None)

        if sub_category and main_category and sub_category.main_category_id != main_category.id:
            raise serializers.ValidationError({"sub_category": "Sub-category must belong to selected main category."})

        return attrs


class ExpenseEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = ExpenseEntry
        fields = [
            "id",
            "user",
            "main_category",
            "sub_category",
            "item",
            "amount",
            "spent_at",
            "source",
            "transaction_reference",
            "payment_method",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "user", "created_at", "updated_at"]

    def validate(self, attrs):
        main_category = attrs.get("main_category") or getattr(self.instance, "main_category", None)
        sub_category = attrs.get("sub_category") or getattr(self.instance, "sub_category", None)
        item = attrs.get("item") or getattr(self.instance, "item", None)

        if sub_category and main_category and sub_category.main_category_id != main_category.id:
            raise serializers.ValidationError({"sub_category": "Sub-category must belong to selected main category."})

        if item and sub_category and item.sub_category_id != sub_category.id:
            raise serializers.ValidationError({"item": "Item must belong to selected sub-category."})

        return attrs


class ExpenseReportPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExpenseReportPreference
        fields = [
            "id",
            "user",
            "name",
            "frequency",
            "delivery_email",
            "report_format",
            "include_budget_vs_actual",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "user", "created_at", "updated_at"]


class CategoryTreeFilterSerializer(serializers.Serializer):
    search = serializers.CharField(required=False, allow_blank=True)

    def filter_query(self, user):
        search = (self.validated_data.get("search") or "").strip()
        query = ExpenseMainCategory.objects.filter(is_deleted=False, is_active=True).filter(
            Q(user=user) | Q(user__isnull=True)
        )
        if search:
            query = query.filter(name__icontains=search)
        return query
