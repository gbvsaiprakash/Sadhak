from django.shortcuts import render

# Create your views here.
from django.utils import timezone
from rest_framework.response import Response
from rest_framework import status
from sadhak_base.models import UserNotification
from sadhak_base.serializers import UserNotificationSerializer

from tracker.views.mixins import TrackerAPIViewMixin  # or your AuthenticatedAPIView base

class NotificationListAPIView(TrackerAPIViewMixin):
    def get(self, request):
        qs = UserNotification.objects.filter(user=request.user, is_deleted=False)
        return Response(UserNotificationSerializer(qs, many=True).data, status=status.HTTP_200_OK)

class NotificationReadAPIView(TrackerAPIViewMixin):
    def patch(self, request, notification_id):
        obj = UserNotification.objects.get(id=notification_id, user=request.user, is_deleted=False)
        if not obj.is_read:
            obj.is_read = True
            obj.read_at = timezone.now()
            obj.save(update_fields=["is_read", "read_at", "updated_at"])
        return Response({"detail": "Marked as read"}, status=status.HTTP_200_OK)

class NotificationReadAllAPIView(TrackerAPIViewMixin):
    def patch(self, request):
        now = timezone.now()
        UserNotification.objects.filter(user=request.user, is_read=False, is_deleted=False).update(
            is_read=True, read_at=now, updated_at=now
        )
        return Response({"detail": "All notifications marked as read"}, status=status.HTTP_200_OK)
