from django.core.cache import cache
from rest_framework.authentication import CSRFCheck
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.settings import api_settings

from sadhak.app_settings import access_token_cookie


class TokenCookieAuthentication(JWTAuthentication):
    """SimpleJWT auth with cookie fallback and CSRF protection for cookie auth."""


    def get_user(self, validated_token):
        user_id = validated_token.get(api_settings.USER_ID_CLAIM)
        if user_id is None:
            raise AuthenticationFailed("Malformed token")

        lookup_field = str(api_settings.USER_ID_FIELD).lower()
        user = self.user_model.objects.filter(**{lookup_field: user_id, "is_deleted": False}).first()
        if not user:
            raise AuthenticationFailed("User not found")
        if not user.is_active:
            raise AuthenticationFailed("User account is inactive")
        return user

    def _enforce_csrf(self, request):
        check = CSRFCheck(lambda req: None)
        check.process_request(request)
        reason = check.process_view(request, None, (), {})
        if reason:
            raise AuthenticationFailed(f"CSRF Failed: {reason}")

    def authenticate(self, request):
        header = self.get_header(request)
        from_cookie = False

        if header is None:
            raw_token = request.COOKIES.get(access_token_cookie)
            from_cookie = True
            if raw_token is None:
                return None
        else:
            raw_token = self.get_raw_token(header)
            if raw_token is None:
                return None

        if from_cookie:
            self._enforce_csrf(request)

        validated_token = self.get_validated_token(raw_token)
        user = self.get_user(validated_token)

        jti = validated_token.get("jti")
        if jti and cache.get(f"access_blacklist:{jti}"):
            raise AuthenticationFailed("Access token revoked")

        token_version = validated_token.get("token_version")
        if token_version is None:
            raise AuthenticationFailed("Malformed token")
        if int(token_version) != user.token_version:
            raise AuthenticationFailed("Session invalidated")

        if user.is_deleted:
            raise AuthenticationFailed("User not found")

        return (user, validated_token)
