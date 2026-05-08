from django.urls import path
from .views import (
    NotificationListAPIView, NotificationReadAPIView, NotificationReadAllAPIView
)

urlpatterns = [
    path("notifications/", NotificationListAPIView.as_view()),
    path("notifications/read-all/", NotificationReadAllAPIView.as_view()),
    path("notifications/<uuid:notification_id>/read/", NotificationReadAPIView.as_view()),
]
