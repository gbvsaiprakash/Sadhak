import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone

import jwt
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny, BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import TokenCookieAuthentication
from sadhak.app_settings import (
    ACCESS_TOKEN_COOKIE,
    ACCESS_TOKEN_TTL_SECONDS,
    ACCOUNT_VERIFICATION_CODE,
    AR_EXPIRY,
    FORGOT_VERIFICATION_CODE,
    FP_EXPIRY,
    REFRESH_TOKEN_COOKIE,
    REFRESH_TOKEN_TTL_SECONDS,
)

from .models import OTPVerification, User, UserAuthToken


SCOPE_FULL_AUTH = "full_auth"
SCOPE_EMAIL_VERIFY = "email_verify"
SCOPE_PASSWORD_RESET = "password_reset"


class HasRequiredScope(BasePermission):
    def has_permission(self, request, view):
        required_scope = getattr(view, "required_token_scope", None)
        if not required_scope:
            return True
        payload = getattr(request, "auth", None) or {}
        return payload.get("scope") == required_scope


class AuthenticatedAPIView(APIView):
    authentication_classes = [TokenCookieAuthentication]
    permission_classes = [IsAuthenticated, HasRequiredScope]
    required_token_scope = SCOPE_FULL_AUTH


class RegistrationAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        data = request.data
        username = (data.get("username") or "").strip()
        email = (data.get("email") or "").strip().lower()
        first_name = (data.get("first_name") or "").strip()
        last_name = (data.get("last_name") or "").strip()
        password = data.get("password")
        confirm_password = data.get("confirm_password")
        gender = data.get("gender")
        age = data.get("age")

        if not username or not email or not first_name or not password or not confirm_password:
            return Response(
                {"detail": "username, email, first_name, password and confirm_password are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if password != confirm_password:
            return Response({"detail": "Passwords do not match"}, status=status.HTTP_400_BAD_REQUEST)

        if User.objects.filter(username=username).exists():
            return Response({"detail": "Username already exists"}, status=status.HTTP_400_BAD_REQUEST)

        if User.objects.filter(email=email).exists():
            return Response({"detail": "Email already exists"}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            user = User.objects.create(
                username=username,
                email=email,
                first_name=first_name,
                last_name=last_name or None,
                gender=gender,
                age=age,
                password=make_password(password),
                is_active=True,
                is_email_verified=False,
            )
            user.add_role(User.ROLE_USER)
            otp_value = _create_email_verification_otp(user)

        _send_otp_email(user.email, otp_value, "Email verification OTP")
        access_token, _ = _create_access_token(user, scope=SCOPE_EMAIL_VERIFY)

        response = Response(
            {
                "detail": "Registration successful. Verify email using OTP.",
                "user_id": str(user.user_id),
            },
            status=status.HTTP_201_CREATED,
        )
        _set_auth_cookies(response=response, access_token=access_token, access_token_expiry=AR_EXPIRY)
        return response


class EmailVerificationAPIView(AuthenticatedAPIView):
    required_token_scope = SCOPE_EMAIL_VERIFY

    def post(self, request):
        otp = (request.data.get("otp") or "").strip()

        if not otp:
            return Response({"detail": "otp is required"}, status=status.HTTP_400_BAD_REQUEST)

        user = request.user

        otp_obj = (
            OTPVerification.objects.filter(user=user, verification_type=ACCOUNT_VERIFICATION_CODE, is_used=False)
            .order_by("-created_at")
            .first()
        )

        if not otp_obj:
            return Response({"detail": "No active OTP found"}, status=status.HTTP_400_BAD_REQUEST)

        if otp_obj.is_locked() or otp_obj.is_expired():
            otp_obj.invalidate()
            return Response({"detail": "OTP expired or locked"}, status=status.HTTP_400_BAD_REQUEST)

        if not check_password(otp, otp_obj.otp_hash):
            otp_obj.attempt_count += 1
            fields = ["attempt_count"]
            if otp_obj.attempt_count >= otp_obj.max_attempts:
                otp_obj.is_used = True
                fields.append("is_used")
            otp_obj.save(update_fields=fields)
            return Response({"detail": "Invalid OTP"}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            otp_obj.is_used = True
            otp_obj.save(update_fields=["is_used"])
            user.is_email_verified = True
            user.save(update_fields=["is_email_verified", "updated_at"])
            _blacklist_request_access_token(request)

        response = Response({"detail": "Email verified successfully"}, status=status.HTTP_200_OK)
        _clear_auth_cookies(response)
        return response


class LoginAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        identifier = (request.data.get("identifier") or "").strip().lower()
        password = request.data.get("password")

        if not identifier or not password:
            return Response({"detail": "identifier and password are required"}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(email=identifier, is_deleted=False).first()
        if not user:
            user = User.objects.filter(username=identifier, is_deleted=False).first()

        if not user or not check_password(password, user.password):
            return Response({"detail": "Invalid credentials"}, status=status.HTTP_401_UNAUTHORIZED)

        if not user.is_active:
            return Response({"detail": "Account is inactive"}, status=status.HTTP_403_FORBIDDEN)

        if not user.is_email_verified:
            return Response({"detail": "Email is not verified"}, status=status.HTTP_403_FORBIDDEN)

        old_refresh = _extract_bearer_or_cookie_token(request, REFRESH_TOKEN_COOKIE, allow_header=False)
        if old_refresh:
            _revoke_refresh_token_by_raw(old_refresh)

        access_token, refresh_token = _issue_token_pair(user)

        response = Response(
            {
                "detail": "Login successful",
                "user": {
                    "user_id": str(user.user_id),
                    "username": user.username,
                    "email": user.email,
                    "roles": user.get_roles(),
                },
            },
            status=status.HTTP_200_OK,
        )
        _set_auth_cookies(response, access_token=access_token, refresh_token=refresh_token)
        return response


class ForgotPasswordAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        username = (request.data.get("username") or "").strip().lower()
        if not username:
            return Response({"detail": "username is required"}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(username=username, is_deleted=False).first()
        if not user:
            return Response(
                {"detail": f"If the {username} exists, an OTP has been sent to registered Email Address"},
                status=status.HTTP_200_OK,
            )

        otp = _create_password_reset_otp(user)
        _send_otp_email(user.email, otp, "Password reset OTP")

        access_token, _ = _create_access_token(user, scope=SCOPE_PASSWORD_RESET)
        response = Response(
            {"detail": f"If the {username} exists, an OTP has been sent to registered Email Address"},
            status=status.HTTP_200_OK,
        )
        _set_auth_cookies(response=response, access_token=access_token, access_token_expiry=FP_EXPIRY)
        return response


class ResetPasswordAPIView(AuthenticatedAPIView):
    required_token_scope = SCOPE_PASSWORD_RESET

    def post(self, request):
        otp = (request.data.get("otp") or "").strip()
        new_password = request.data.get("new_password")
        confirm_password = request.data.get("confirm_password")

        if not otp or not new_password or not confirm_password:
            return Response(
                {"detail": "otp, new_password and confirm_password are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if new_password != confirm_password:
            return Response({"detail": "Passwords do not match"}, status=status.HTTP_400_BAD_REQUEST)

        user = request.user

        otp_obj = (
            OTPVerification.objects.filter(user=user, verification_type=FORGOT_VERIFICATION_CODE, is_used=False)
            .order_by("-created_at")
            .first()
        )
        if not otp_obj:
            return Response({"detail": "No active OTP found"}, status=status.HTTP_400_BAD_REQUEST)

        if otp_obj.is_locked() or otp_obj.is_expired():
            otp_obj.invalidate()
            return Response({"detail": "OTP expired or locked"}, status=status.HTTP_400_BAD_REQUEST)

        if not check_password(otp, otp_obj.otp_hash):
            otp_obj.attempt_count += 1
            fields = ["attempt_count"]
            if otp_obj.attempt_count >= otp_obj.max_attempts:
                otp_obj.is_used = True
                fields.append("is_used")
            otp_obj.save(update_fields=fields)
            return Response({"detail": "Invalid OTP"}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            user.password = make_password(new_password)
            user.token_version += 1
            user.save(update_fields=["password", "token_version", "updated_at"])
            otp_obj.is_used = True
            otp_obj.save(update_fields=["is_used"])
            _revoke_all_user_refresh_tokens(user)
            _blacklist_request_access_token(request)

        response = Response({"detail": "Password reset successful"}, status=status.HTTP_200_OK)
        _clear_auth_cookies(response)
        return response


class PasswordChangeAPIView(AuthenticatedAPIView):
    def post(self, request):
        old_password = request.data.get("old_password")
        new_password = request.data.get("new_password")
        confirm_password = request.data.get("confirm_password")

        if not old_password or not new_password or not confirm_password:
            return Response(
                {"detail": "old_password, new_password and confirm_password are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if new_password != confirm_password:
            return Response({"detail": "Passwords do not match"}, status=status.HTTP_400_BAD_REQUEST)

        user = request.user
        if not check_password(old_password, user.password):
            return Response({"detail": "Old password is incorrect"}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            user.password = make_password(new_password)
            user.token_version += 1
            user.save(update_fields=["password", "token_version", "updated_at"])
            _revoke_all_user_refresh_tokens(user)
            _blacklist_request_access_token(request)

        response = Response({"detail": "Password changed successfully"}, status=status.HTTP_200_OK)
        _clear_auth_cookies(response)
        return response


class ProfileUpdateAPIView(AuthenticatedAPIView):
    def patch(self, request):
        user = request.user
        allowed_fields = {"username", "first_name", "last_name", "age", "gender"}
        updates = {k: v for k, v in request.data.items() if k in allowed_fields}

        if not updates:
            return Response({"detail": "No valid profile fields provided"}, status=status.HTTP_400_BAD_REQUEST)

        if "username" in updates:
            username = (updates["username"] or "").strip()
            if not username:
                return Response({"detail": "username cannot be blank"}, status=status.HTTP_400_BAD_REQUEST)
            if User.objects.filter(username=username).exclude(user_id=user.user_id).exists():
                return Response({"detail": "Username already exists"}, status=status.HTTP_400_BAD_REQUEST)
            updates["username"] = username

        if "first_name" in updates and not str(updates["first_name"]).strip():
            return Response({"detail": "first_name cannot be blank"}, status=status.HTTP_400_BAD_REQUEST)

        if "gender" in updates:
            valid_genders = {choice[0] for choice in User.GENDER_CHOICES}
            if updates["gender"] not in valid_genders:
                return Response({"detail": "Invalid gender"}, status=status.HTTP_400_BAD_REQUEST)

        for field, value in updates.items():
            setattr(user, field, value)

        update_fields = list(updates.keys())
        if "updated_at" not in update_fields:
            update_fields.append("updated_at")
        user.save(update_fields=update_fields)

        return Response(
            {
                "detail": "Profile updated successfully",
                "user": {
                    "user_id": str(user.user_id),
                    "username": user.username,
                    "email": user.email,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "age": user.age,
                    "gender": user.gender,
                    "roles": user.get_roles(),
                },
            },
            status=status.HTTP_200_OK,
        )

    def put(self, request):
        return self.patch(request)


class LogoutAPIView(AuthenticatedAPIView):
    def post(self, request):
        with transaction.atomic():
            user = User.objects.select_for_update().get(user_id=request.user.user_id)
            user.token_version += 1
            user.save(update_fields=["token_version", "updated_at"])
            _revoke_all_user_refresh_tokens(user)
            _blacklist_request_access_token(request)

        response = Response({"detail": "Logout successful"}, status=status.HTTP_200_OK)
        _clear_auth_cookies(response)
        return response


class CreateRefreshTokenAPIView(AuthenticatedAPIView):
    def post(self, request):
        old_refresh = _extract_bearer_or_cookie_token(request, REFRESH_TOKEN_COOKIE, allow_header=False)
        if old_refresh:
            _revoke_refresh_token_by_raw(old_refresh)

        refresh_token, _ = _create_refresh_token(request.user)
        response = Response({"detail": "Refresh token created"}, status=status.HTTP_201_CREATED)
        _set_auth_cookies(response, refresh_token=refresh_token)
        return response


class RefreshAccessTokenAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        refresh_raw = _extract_bearer_or_cookie_token(request, REFRESH_TOKEN_COOKIE, allow_header=False)
        if not refresh_raw:
            response = Response({"detail": "Refresh token is required"}, status=status.HTTP_401_UNAUTHORIZED)
            _clear_auth_cookies(response)
            return response

        try:
            user, refresh_obj, _ = _validate_refresh_token(refresh_raw)
        except AuthenticationFailed as exc:
            response = Response({"detail": str(exc)}, status=status.HTTP_401_UNAUTHORIZED)
            _clear_auth_cookies(response)
            return response

        access_token, _ = _create_access_token(user, scope=SCOPE_FULL_AUTH)
        refresh_token = _rotate_refresh_token(refresh_obj, user)

        response = Response({"detail": "Access token refreshed"}, status=status.HTTP_200_OK)
        _set_auth_cookies(response, access_token=access_token, refresh_token=refresh_token)
        return response


def _create_email_verification_otp(user):
    otp = _generate_otp()
    OTPVerification.objects.create(
        user=user,
        otp_hash=make_password(otp),
        verification_type=ACCOUNT_VERIFICATION_CODE,
        expires_at=timezone.now() + timedelta(minutes=AR_EXPIRY),
    )
    return otp


def _create_password_reset_otp(user):
    otp = _generate_otp()
    OTPVerification.objects.create(
        user=user,
        otp_hash=make_password(otp),
        verification_type=FORGOT_VERIFICATION_CODE,
        expires_at=timezone.now() + timedelta(minutes=FP_EXPIRY),
    )
    return otp


def _generate_otp():
    return f"{secrets.randbelow(1_000_000):06d}"


def _send_otp_email(email, otp, subject):
    print(f"[{subject}] {email} -> {otp}")


def _jwt_secret():
    return getattr(settings, "JWT_SECRET_KEY", settings.SECRET_KEY)


def _jwt_algorithm():
    return getattr(settings, "JWT_ALGORITHM", "HS256")


def _token_digest(raw_token):
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _extract_bearer_or_cookie_token(request, cookie_name, allow_header=True):
    if allow_header:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.lower().startswith("bearer "):
            return auth_header.split(" ", 1)[1].strip()
    return request.COOKIES.get(cookie_name)


def _encode_jwt(user, token_type, ttl_seconds, scope=SCOPE_FULL_AUTH):
    now = int(timezone.now().timestamp())
    payload = {
        "sub": str(user.user_id),
        "jti": str(uuid.uuid4()),
        "token_type": token_type,
        "scope": scope,
        "token_version": user.token_version,
        "iat": now,
        "exp": now + int(ttl_seconds),
    }
    token = jwt.encode(payload, _jwt_secret(), algorithm=_jwt_algorithm())
    return token, payload


def _decode_jwt_token(raw_token, expected_type):
    try:
        payload = jwt.decode(raw_token, _jwt_secret(), algorithms=[_jwt_algorithm()])
    except jwt.ExpiredSignatureError:
        raise AuthenticationFailed("Token expired")
    except jwt.InvalidTokenError:
        raise AuthenticationFailed("Invalid token")

    if payload.get("token_type") != expected_type:
        raise AuthenticationFailed("Invalid token type")
    if "sub" not in payload or "jti" not in payload or "token_version" not in payload or "scope" not in payload:
        raise AuthenticationFailed("Malformed token")
    if expected_type == "access" and _is_access_jti_blocklisted(payload["jti"]):
        raise AuthenticationFailed("Access token revoked")

    return payload


def _get_user_for_payload(payload):
    user = User.objects.filter(user_id=payload["sub"], is_deleted=False).first()
    if not user:
        raise AuthenticationFailed("User not found")
    if not user.is_active:
        raise AuthenticationFailed("User account is inactive")
    if int(payload["token_version"]) != user.token_version:
        raise AuthenticationFailed("Session invalidated")
    return user


def _create_access_token(user, scope=SCOPE_FULL_AUTH):
    return _encode_jwt(user, "access", ACCESS_TOKEN_TTL_SECONDS, scope=scope)


def _create_refresh_token(user):
    refresh_token, payload = _encode_jwt(user, "refresh", REFRESH_TOKEN_TTL_SECONDS, scope=SCOPE_FULL_AUTH)
    expires_at = datetime.fromtimestamp(payload["exp"], tz=dt_timezone.utc)
    refresh_obj = UserAuthToken.objects.create(
        user=user,
        jti=payload["jti"],
        token_hash=_token_digest(refresh_token),
        expires_at=expires_at,
    )
    return refresh_token, refresh_obj


def _issue_token_pair(user):
    access_token, _ = _create_access_token(user, scope=SCOPE_FULL_AUTH)
    refresh_token, _ = _create_refresh_token(user)
    return access_token, refresh_token


def _validate_refresh_token(raw_token):
    payload = _decode_jwt_token(raw_token, expected_type="refresh")
    user = _get_user_for_payload(payload)

    refresh_obj = UserAuthToken.objects.filter(
        user=user,
        token_hash=_token_digest(raw_token),
    ).first()
    if not refresh_obj:
        raise AuthenticationFailed("Refresh token not recognized")
    if refresh_obj.is_revoked:
        raise AuthenticationFailed("Refresh token revoked")
    if refresh_obj.is_expired():
        refresh_obj.revoke()
        raise AuthenticationFailed("Refresh token expired")

    return user, refresh_obj, payload


def _rotate_refresh_token(old_obj, user):
    new_token, new_obj = _create_refresh_token(user)
    old_obj.replaced_by = new_obj
    old_obj.revoke()
    old_obj.save(update_fields=["replaced_by", "updated_at"])
    return new_token


def _revoke_refresh_token_by_raw(raw_token):
    if not raw_token:
        return
    token_obj = UserAuthToken.objects.filter(token_hash=_token_digest(raw_token)).first()
    if token_obj:
        token_obj.revoke()


def _revoke_all_user_refresh_tokens(user):
    now = timezone.now()
    UserAuthToken.objects.filter(user=user, is_revoked=False).update(
        is_revoked=True,
        revoked_at=now,
        updated_at=now,
    )


def _blacklist_cache_key_for_jti(jti):
    return f"access_blacklist:{jti}"


def _blacklist_access_jti(jti, exp_ts):
    ttl = int(exp_ts - timezone.now().timestamp())
    if ttl > 0:
        cache.set(_blacklist_cache_key_for_jti(jti), 1, timeout=ttl)


def _is_access_jti_blocklisted(jti):
    return bool(cache.get(_blacklist_cache_key_for_jti(jti)))


def _blacklist_request_access_token(request):
    payload = getattr(request, "auth", None) or {}
    jti = payload.get("jti")
    exp = payload.get("exp")
    if jti and exp:
        _blacklist_access_jti(jti, exp)


def _set_auth_cookies(response, access_token=None, refresh_token=None, access_token_expiry=ACCESS_TOKEN_TTL_SECONDS):
    if access_token:
        _set_cookie(response, ACCESS_TOKEN_COOKIE, access_token, access_token_expiry)
    if refresh_token:
        _set_cookie(response, REFRESH_TOKEN_COOKIE, refresh_token, REFRESH_TOKEN_TTL_SECONDS)


def _set_cookie(response, key, token, max_age):
    response.set_cookie(
        key=key,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=not settings.DEBUG,
        samesite="Lax",
        path="/",
    )


def _clear_auth_cookies(response):
    response.delete_cookie(ACCESS_TOKEN_COOKIE, path="/")
    response.delete_cookie(REFRESH_TOKEN_COOKIE, path="/")
