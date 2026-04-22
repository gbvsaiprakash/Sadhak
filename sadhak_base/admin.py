from django.contrib import admin

# Register your models here.
from .models import FeatureFlag, DomainEvent  # adjust import path

@admin.register(FeatureFlag)
class FeatureFlagAdmin(admin.ModelAdmin):
    list_display = ("key", "enabled", "rollout_percent", "created_at", "updated_at")
    list_filter = ("enabled",)
    search_fields = ("key", "description")


@admin.register(DomainEvent)
class DomainEventAdmin(admin.ModelAdmin):
    list_display = ("event_type", "actor", "object_type", "object_id", "processed", "created_at")
    list_filter = ("event_type", "processed", "created_at")
    search_fields = ("event_type", "object_type", "object_id")
    readonly_fields = ("id", "created_at", "updated_at")