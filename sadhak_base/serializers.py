from rest_framework import serializers
from sadhak_base.models import UserNotification

class UserNotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserNotification
        fields = ("id", "title", "message", "payload", "is_read", "read_at", "created_at")

