from datetime import date as date_cls, datetime, timedelta

from django.db.models import Q
from rest_framework import status
from rest_framework.response import Response

from tracker.models import TaskOccurrence
from tracker.serializers.calendar import CalendarOccurrenceSerializer, CalendarQuerySerializer
from tracker.views.mixins import TrackerAPIViewMixin


def _parse_iso_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except Exception:
        return None


def _month_bounds(d):
    start = d.replace(day=1)
    # next month first day minus one
    month = d.month + 1
    year = d.year + (month - 1) // 12
    month = ((month - 1) % 12) + 1
    next_month = d.replace(year=year, month=month, day=1)
    end = next_month - timedelta(days=1)
    return start, end


def _week_bounds(d):
    start = d - timedelta(days=d.weekday())
    end = start + timedelta(days=6)
    return start, end


def _iter_days(start, end):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _filter_occurrences(user, start, end, section=None, status_value=None, only_type=None):
    qs = (
        TaskOccurrence.objects.filter(
            scheduled_date__range=(start, end),
        )
        .select_related("task", "habit")
        .filter(Q(task__user=user) | Q(habit__user=user))
        .filter(Q(task__isnull=False, task__is_deleted=False) | Q(habit__isnull=False, habit__is_deleted=False))
    )
    if only_type == "task":
        qs = qs.filter(task__isnull=False)
    elif only_type == "habit":
        qs = qs.filter(habit__isnull=False)
    if status_value:
        qs = qs.filter(status=status_value)
    if section:
        qs = qs.filter(Q(task__section=section) | Q(habit__section=section))
    return qs


class CalendarCombinedAPIView(TrackerAPIViewMixin):
    serializer_class = CalendarQuerySerializer
    def get(self, request):
        serializer = self.get_serializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        return _calendar_response(request, serializer.validated_data, only_type="None")


class CalendarTaskAPIView(TrackerAPIViewMixin):
    serializer_class = CalendarQuerySerializer
    def get(self, request):
        serializer = self.get_serializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        return _calendar_response(request, serializer.validated_data, only_type="task")


class CalendarHabitAPIView(TrackerAPIViewMixin):
    serializer_class = CalendarQuerySerializer
    def get(self, request):
        serializer = self.get_serializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        return _calendar_response(request, serializer.validated_data, only_type="habit")


# def _calendar_response(request, only_type=None):
#     view_type = request.query_params.get("view_type")
#     date_value = request.query_params.get("date")
#     section = request.query_params.get("section")
#     status_value = request.query_params.get("status")

#     if view_type not in {"month", "week", "day"}:
#         return Response({"error": True, "code": "INVALID_VIEW", "message": "view_type must be month|week|day", "details": {}}, status=status.HTTP_400_BAD_REQUEST)
#     d = _parse_iso_date(date_value or "")
#     if not d:
#         return Response({"error": True, "code": "INVALID_DATE", "message": "date must be YYYY-MM-DD", "details": {}}, status=status.HTTP_400_BAD_REQUEST)

#     if view_type == "month":
#         start, end = _month_bounds(d)
#         qs = _filter_occurrences(request.user, start, end, section=section, status_value=status_value, only_type=only_type)
#         by_day = {day.isoformat(): {"tasks": [], "habits": []} for day in _iter_days(start, end)}
#         serializer = CalendarOccurrenceSerializer(qs, many=True)
#         for item in serializer.data:
#             day_key = item["scheduled_date"]
#             t = item["type"]
#             if t == "task":
#                 by_day[day_key]["tasks"].append(item)
#             else:
#                 by_day[day_key]["habits"].append(item)
#         return Response(
#             {
#                 "view_type": "month",
#                 "year": start.year,
#                 "month": start.month,
#                 "days": by_day,
#             },
#             status=status.HTTP_200_OK,
#         )

#     if view_type == "week":
#         start, end = _week_bounds(d)
#         qs = _filter_occurrences(request.user, start, end, section=section, status_value=status_value, only_type=only_type)
#         by_day = {day.isoformat(): {"tasks": [], "habits": []} for day in _iter_days(start, end)}
#         serializer = CalendarOccurrenceSerializer(qs, many=True)
#         for item in serializer.data:
#             day_key = item["scheduled_date"]
#             t = item["type"]
#             if t == "task":
#                 by_day[day_key]["tasks"].append(item)
#             else:
#                 by_day[day_key]["habits"].append(item)
#         return Response(
#             {
#                 "view_type": "week",
#                 "week_start": start.isoformat(),
#                 "week_end": end.isoformat(),
#                 "days": by_day,
#             },
#             status=status.HTTP_200_OK,
#         )

#     # day
#     start = end = d
#     qs = _filter_occurrences(request.user, start, end, section=section, status_value=status_value, only_type=only_type).order_by("scheduled_time")
#     serializer = CalendarOccurrenceSerializer(qs, many=True)
#     slots = []
#     for item in serializer.data:
#         slots.append(
#             {
#                 "start_time": item["start_time"],
#                 "end_time": item["end_time"],
#                 "type": item["type"],
#                 "occurrence_id": item["occurrence_id"],
#                 "parent_id": item["parent_id"],
#                 "parent_title": item["parent_title"],
#                 "context_title": item.get("context_title"),
#                 "status": item["status"],
#                 "section": item["section"],
#             }
#         )
#     slots.sort(key=lambda s: s["start_time"] or "")
#     return Response({"view_type": "day", "date": d.isoformat(), "slots": slots}, status=status.HTTP_200_OK)

def _calendar_response(request, params, only_type=None):
    view_type = params["view_type"]
    d = params["date"]
    section = params.get("section")
    status_value = params.get("status")


    if view_type not in {"month", "week", "day"}:
        return Response({"error": True, "code": "INVALID_VIEW", "message": "view_type must be month|week|day", "details": {}}, status=status.HTTP_400_BAD_REQUEST)
    # d = _parse_iso_date(date_value or "")
    # if not d:
    #     return Response({"error": True, "code": "INVALID_DATE", "message": "date must be YYYY-MM-DD", "details": {}}, status=status.HTTP_400_BAD_REQUEST)

    if view_type == "month":
        start, end = _month_bounds(d)
        qs = _filter_occurrences(request.user, start, end, section=section, status_value=status_value, only_type=only_type)
        by_day = {day.isoformat(): {"tasks": [], "habits": []} for day in _iter_days(start, end)}
        serializer = CalendarOccurrenceSerializer(qs, many=True)
        for item in serializer.data:
            day_key = item["scheduled_date"]
            t = item["type"]
            if t == "task":
                by_day[day_key]["tasks"].append(item)
            else:
                by_day[day_key]["habits"].append(item)
        return Response(
            {
                "view_type": "month",
                "year": start.year,
                "month": start.month,
                "days": by_day,
            },
            status=status.HTTP_200_OK,
        )

    if view_type == "week":
        start, end = _week_bounds(d)
        qs = _filter_occurrences(request.user, start, end, section=section, status_value=status_value, only_type=only_type)
        by_day = {day.isoformat(): {"tasks": [], "habits": []} for day in _iter_days(start, end)}
        serializer = CalendarOccurrenceSerializer(qs, many=True)
        for item in serializer.data:
            day_key = item["scheduled_date"]
            t = item["type"]
            if t == "task":
                by_day[day_key]["tasks"].append(item)
            else:
                by_day[day_key]["habits"].append(item)
        return Response(
            {
                "view_type": "week",
                "week_start": start.isoformat(),
                "week_end": end.isoformat(),
                "days": by_day,
            },
            status=status.HTTP_200_OK,
        )

    # day
    start = end = d
    qs = _filter_occurrences(request.user, start, end, section=section, status_value=status_value, only_type=only_type).order_by("scheduled_time")
    serializer = CalendarOccurrenceSerializer(qs, many=True)
    slots = []
    for item in serializer.data:
        slots.append(
            {
                "start_time": item["start_time"],
                "end_time": item["end_time"],
                "type": item["type"],
                "occurrence_id": item["occurrence_id"],
                "parent_id": item["parent_id"],
                "parent_title": item["parent_title"],
                "context_title": item.get("context_title"),
                "status": item["status"],
                "section": item["section"],
            }
        )
    slots.sort(key=lambda s: s["start_time"] or "")
    return Response({"view_type": "day", "date": d.isoformat(), "slots": slots}, status=status.HTTP_200_OK)


