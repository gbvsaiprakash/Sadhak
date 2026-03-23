from django.http import Http404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from user_management.views import AuthenticatedAPIView


class TrackerAPIViewMixin(AuthenticatedAPIView):
    serializer_class = None
    list_serializer_class = None
    detail_serializer_class = None
    queryset = None

    def filter_common(self, queryset):
        params = self.request.query_params
        section = params.get("section")
        status_value = params.get("status")
        goal_id = params.get("goal_id")
        milestone_id = params.get("milestone_id")
        from_date = params.get("from_date")
        to_date = params.get("to_date")
        is_overdue = params.get("is_overdue")
        search = params.get("search")

        if section:
            queryset = queryset.filter(section=section)
        if status_value:
            queryset = queryset.filter(status=status_value)
        if goal_id:
            queryset = queryset.filter(goal_id=goal_id)
        if milestone_id:
            queryset = queryset.filter(milestone_id=milestone_id)
        if from_date:
            queryset = queryset.filter(start_date__gte=from_date)
        if to_date:
            queryset = queryset.filter(start_date__lte=to_date)
        if search:
            queryset = queryset.filter(title__icontains=search)
        if is_overdue is not None:
            today = timezone.localdate()
            wants_overdue = is_overdue.lower() == "true"
            model = queryset.model
            if hasattr(model, "expected_end_date"):
                lookup = {"expected_end_date__lt": today} if wants_overdue else {"expected_end_date__gte": today}
                queryset = queryset.filter(**lookup)
            elif hasattr(model, "expected_achieved_date"):
                lookup = {"expected_achieved_date__lt": today} if wants_overdue else {"expected_achieved_date__gte": today}
                queryset = queryset.filter(**lookup)
            elif hasattr(model, "end_date"):
                lookup = {"end_date__lt": today} if wants_overdue else {"end_date__gte": today}
                queryset = queryset.filter(**lookup)
        return queryset

    def get_queryset(self):
        return self.queryset

    def get_serializer_class(self):
        return self.serializer_class or self.detail_serializer_class

    def get_serializer(self, *args, **kwargs):
        serializer_class = self.get_serializer_class()
        kwargs.setdefault("context", self.get_serializer_context())
        return serializer_class(*args, **kwargs)

    def get_serializer_context(self):
        return {"request": self.request, "view": self}

    def get_not_found_code(self):
        return "TRACKER_NOT_FOUND"

    def finalize_error(self, code, message, http_status=status.HTTP_404_NOT_FOUND):
        return Response({"error": True, "code": code, "message": message, "details": {}}, status=http_status)

    def handle_not_found(self):
        raise NotFound(detail={"error": True, "code": self.get_not_found_code(), "message": "Requested resource was not found.", "details": {}})

    def get_object(self, queryset=None, **lookup):
        queryset = queryset or self.get_queryset()
        obj = queryset.filter(**lookup).first()
        if obj is None:
            self.handle_not_found()
        return obj
