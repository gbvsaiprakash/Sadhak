from datetime import datetime, time

from django.utils import timezone
from rest_framework import serializers

from tracker.models import TaskOccurrence
from tracker.services.occurrence import _add_duration, _entity_duration


class CalendarQuerySerializer(serializers.Serializer):
    VIEW_CHOICES = ("month", "week", "day")

    view_type = serializers.ChoiceField(choices=VIEW_CHOICES)
    date = serializers.DateField(input_formats=["%Y-%m-%d"])
    section = serializers.CharField(required=False, allow_blank=True)
    status = serializers.CharField(required=False, allow_blank=True)


class CalendarOccurrenceSerializer(serializers.ModelSerializer):
    occurrence_id = serializers.UUIDField(source="id", read_only=True)
    parent_id = serializers.SerializerMethodField()
    parent_title = serializers.SerializerMethodField()
    type = serializers.SerializerMethodField()
    section = serializers.SerializerMethodField()
    start_time = serializers.SerializerMethodField()
    end_time = serializers.SerializerMethodField()

    class Meta:
        model = TaskOccurrence
        fields = (
            "occurrence_id",
            "scheduled_date",
            "start_time",
            "end_time",
            "type",
            "parent_id",
            "parent_title",
            "context_title",
            "status",
            "section",
        )

    def _parent(self, obj):
        return obj.task or obj.habit

    def get_parent_id(self, obj):
        parent = self._parent(obj)
        return str(parent.id) if parent else None

    def get_parent_title(self, obj):
        parent = self._parent(obj)
        return parent.title if parent else None

    def get_type(self, obj):
        return "task" if obj.task_id else "habit"

    def get_section(self, obj):
        parent = self._parent(obj)
        return getattr(parent, "section", None) if parent else None

    def get_start_time(self, obj):
        t = obj.scheduled_time
        if t is None:
            parent = self._parent(obj)
            t = getattr(parent, "start_time", None)
        if t is None:
            return None
        return t.replace(second=0, microsecond=0).strftime("%H:%M")

    def get_end_time(self, obj):
        parent = self._parent(obj)
        if parent is None:
            return None
        start_t = obj.scheduled_time or getattr(parent, "start_time", None)
        if start_t is None:
            return None
        duration = _entity_duration(parent)
        end_t = _add_duration(start_t, duration)
        return end_t.strftime("%H:%M")

