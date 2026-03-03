import jwt
from django.apps import apps
from django.conf import settings
from django.core.cache import cache
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from sadhak.app_settings import ACCESS_TOKEN_COOKIE


def _jwt_secret():
    return getattr(settings, "JWT_SECRET_KEY", settings.SECRET_KEY)


def _jwt_algorithm():
    return getattr(settings, "JWT_ALGORITHM", "HS256")


def _extract_bearer_or_cookie_token(request):
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1].strip()
    return request.COOKIES.get(ACCESS_TOKEN_COOKIE)


def _is_access_jti_blocklisted(jti):
    return bool(cache.get(f"access_blacklist:{jti}"))


def _decode_access_jwt(raw_token):
    try:
        payload = jwt.decode(raw_token, _jwt_secret(), algorithms=[_jwt_algorithm()])
    except jwt.ExpiredSignatureError:
        raise AuthenticationFailed("Token expired")
    except jwt.InvalidTokenError:
        raise AuthenticationFailed("Invalid token")

    if payload.get("token_type") != "access":
        raise AuthenticationFailed("Invalid token type")

    required_fields = {"sub", "jti", "token_version"}
    if not required_fields.issubset(payload.keys()):
        raise AuthenticationFailed("Malformed token")

    if _is_access_jti_blocklisted(payload["jti"]):
        raise AuthenticationFailed("Access token revoked")

    return payload


def _get_user_for_payload(payload):
    User = apps.get_model("user_management", "User")
    user = User.objects.filter(user_id=payload["sub"], is_deleted=False).first()
    if not user:
        raise AuthenticationFailed("User not found")
    if not user.is_active:
        raise AuthenticationFailed("User account is inactive")
    if int(payload["token_version"]) != user.token_version:
        raise AuthenticationFailed("Session invalidated")
    return user


class TokenCookieAuthentication(BaseAuthentication):
    """JWT access-token auth via Authorization header or access cookie."""

    def authenticate(self, request):
        token = _extract_bearer_or_cookie_token(request)
        if not token:
            return None

        payload = _decode_access_jwt(token)
        user = _get_user_for_payload(payload)
        return (user, payload)
