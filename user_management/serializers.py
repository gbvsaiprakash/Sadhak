from rest_framework import serializers
from user_management.models import NotificationPreference, UserDeviceToken


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = (
            "notifications_enabled",
            "in_app_enabled",
            "push_enabled",
            "email_enabled",
        )

class UserDeviceTokenSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserDeviceToken
        fields = ("token", "platform")
        extra_kwargs = {
            "token": {"validators": []},
        }
