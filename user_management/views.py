from django.shortcuts import render

# Create your views here.
import hashlib
import secrets
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.contrib.auth.hashers import check_password, make_password
from rest_framework import status
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import OTPVerification, User, UserAuthToken
from sadhak.app_settings import ACCESS_TOKEN_COOKIE,REFRESH_TOKEN_COOKIE,ACCESS_TOKEN_TTL_SECONDS,REFRESH_TOKEN_TTL_SECONDS,ACCOUNT_VERIFICATION_CODE,FORGOT_VERIFICATION_CODE


class TokenCookieAuthentication(BaseAuthentication):
    """Authenticates using bearer token from Authorization header or httpOnly cookie."""

    def authenticate(self, request):
        token = _extract_bearer_or_cookie_token(request, ACCESS_TOKEN_COOKIE)
        if not token:
            return None

        token_obj = _get_valid_token(token, UserAuthToken.TYPE_ACCESS)
        if not token_obj:
            raise AuthenticationFailed("Invalid or expired access token")

        if not token_obj.user.is_active or token_obj.user.is_deleted:
            raise AuthenticationFailed("User account is inactive")

        return (token_obj.user, token_obj)


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
            return Response({"detail": "username, email, first_name, password and confirm_password are required"}, status=status.HTTP_400_BAD_REQUEST)

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

        return Response(
            {
                "detail": "Registration successful. Verify email using OTP.",
                "user_id": str(user.user_id),
            },
            status=status.HTTP_201_CREATED,
        )


class EmailVerificationAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        otp = (request.data.get("otp") or "").strip()

        if not email or not otp:
            return Response({"detail": "email and otp are required"}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(email=email, is_deleted=False).first()
        if not user:
            return Response({"detail": "User not found"}, status=status.HTTP_404_NOT_FOUND)

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
            if otp_obj.is_locked():
                otp_obj.is_used = True
                fields.append("is_used")
            otp_obj.save(update_fields=fields)
            return Response({"detail": "Invalid OTP"}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            otp_obj.is_used = True
            otp_obj.save(update_fields=["is_used"])
            user.is_email_verified = True
            user.save(update_fields=["is_email_verified", "updated_at"])

        return Response({"detail": "Email verified successfully"}, status=status.HTTP_200_OK)


class LoginAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        identifier = (request.data.get("identifier") or "").strip().lower()
        password = request.data.get("password")

        if not identifier or not password:
            return Response({"detail": "identifier and password are required"}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(is_deleted=False).filter(email=identifier).first()
        if not user:
            user = User.objects.filter(is_deleted=False).filter(username=identifier).first()

        if not user or not check_password(password, user.password):
            return Response({"detail": "Invalid credentials"}, status=status.HTTP_401_UNAUTHORIZED)

        if not user.is_active:
            return Response({"detail": "Account is inactive"}, status=status.HTTP_403_FORBIDDEN)

        if not user.is_email_verified:
            return Response({"detail": "Email is not verified"}, status=status.HTTP_403_FORBIDDEN)

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
        _set_auth_cookies(response, access_token, refresh_token)
        return response


class ForgotPasswordAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        if not email:
            return Response({"detail": "email is required"}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(email=email, is_deleted=False).first()
        if not user:
            return Response({"detail": "If the email exists, an OTP has been sent"}, status=status.HTTP_200_OK)

        otp = _create_password_reset_otp(user)
        _send_otp_email(user.email, otp, "Password reset OTP")

        return Response({"detail": "If the email exists, an OTP has been sent"}, status=status.HTTP_200_OK)


class ResetPasswordAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        otp = (request.data.get("otp") or "").strip()
        new_password = request.data.get("new_password")
        confirm_password = request.data.get("confirm_password")

        if not email or not otp or not new_password or not confirm_password:
            return Response(
                {"detail": "email, otp, new_password and confirm_password are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if new_password != confirm_password:
            return Response({"detail": "Passwords do not match"}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(email=email, is_deleted=False).first()
        if not user:
            return Response({"detail": "Invalid request"}, status=status.HTTP_400_BAD_REQUEST)

        otp_obj = (
            OTPVerification.objects.filter(user=user, verification_type=FORGOT_VERIFICATION_CODE,is_used=False)
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
            if otp_obj.is_locked():
                otp_obj.is_used = True
                fields.append("is_used")
            otp_obj.save(update_fields=fields)
            return Response({"detail": "Invalid OTP"}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            user.password = make_password(new_password)
            user.save(update_fields=["password", "updated_at"])
            otp_obj.is_used = True
            otp_obj.save(update_fields=["is_used"])

            # Invalidate all active sessions/tokens after password reset.
            _revoke_all_user_tokens(user)

        response = Response({"detail": "Password reset successful"}, status=status.HTTP_200_OK)
        _clear_auth_cookies(response)
        return response


class LogoutAPIView(APIView):
    authentication_classes = [TokenCookieAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        all_devices = str(request.data.get("all_devices", "true")).lower() == "true"

        if all_devices:
            _revoke_all_user_tokens(request.user)
        else:
            access_token = _extract_bearer_or_cookie_token(request, ACCESS_TOKEN_COOKIE)
            refresh_token = _extract_bearer_or_cookie_token(request, REFRESH_TOKEN_COOKIE)
            _revoke_token_by_raw(access_token, UserAuthToken.TYPE_ACCESS)
            _revoke_token_by_raw(refresh_token, UserAuthToken.TYPE_REFRESH)

        response = Response({"detail": "Logout successful"}, status=status.HTTP_200_OK)
        _clear_auth_cookies(response)
        return response


class CreateRefreshTokenAPIView(APIView):
    authentication_classes = [TokenCookieAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        # Explicit endpoint to rotate/generate a new refresh token while authenticated.
        old_refresh = _extract_bearer_or_cookie_token(request, REFRESH_TOKEN_COOKIE)
        if old_refresh:
            _revoke_token_by_raw(old_refresh, UserAuthToken.TYPE_REFRESH)

        refresh_token = _create_token(request.user, UserAuthToken.TYPE_REFRESH, REFRESH_TOKEN_TTL_SECONDS)
        response = Response({"detail": "Refresh token created"}, status=status.HTTP_201_CREATED)
        _set_cookie(response, REFRESH_TOKEN_COOKIE, refresh_token, REFRESH_TOKEN_TTL_SECONDS)
        return response


class RefreshAccessTokenAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        refresh_raw = _extract_bearer_or_cookie_token(request, REFRESH_TOKEN_COOKIE)
        if not refresh_raw:
            return Response({"detail": "Refresh token is required"}, status=status.HTTP_401_UNAUTHORIZED)

        refresh_obj = _get_valid_token(refresh_raw, UserAuthToken.TYPE_REFRESH)
        if not refresh_obj:
            return Response({"detail": "Invalid or expired refresh token"}, status=status.HTTP_401_UNAUTHORIZED)

        user = refresh_obj.user
        access_token = _create_token(user, UserAuthToken.TYPE_ACCESS, ACCESS_TOKEN_TTL_SECONDS)

        # Rotate refresh token on each use for better security.
        refresh_token = _rotate_refresh_token(refresh_raw, refresh_obj)

        response = Response({"detail": "Access token refreshed"}, status=status.HTTP_200_OK)
        _set_auth_cookies(response, access_token, refresh_token)
        return response


def _create_email_verification_otp(user):
    otp = _generate_otp()
    OTPVerification.objects.create(
        user=user,
        otp_hash=make_password(otp),
        verification_type=ACCOUNT_VERIFICATION_CODE,
        expires_at=timezone.now() + timezone.timedelta(minutes=10),
    )
    return otp


def _create_password_reset_otp(user):
    otp = _generate_otp()
    OTPVerification.objects.create(
        user=user,
        otp_hash=make_password(otp),
        verification_type=FORGOT_VERIFICATION_CODE,
        expires_at=timezone.now() + timezone.timedelta(minutes=10),
    )
    return otp


def _generate_otp():
    return f"{secrets.randbelow(1_000_000):06d}"


def _send_otp_email(email, otp, subject):
    # Replace with django.core.mail.send_mail or provider integration.
    print(f"[{subject}] {email} -> {otp}")


def _token_digest(raw_token):
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _create_token(user, token_type, ttl_seconds):
    raw = secrets.token_urlsafe(48)
    UserAuthToken.objects.create(
        user=user,
        token_type=token_type,
        token_hash=_token_digest(raw),
        expires_at=timezone.now() + timezone.timedelta(seconds=ttl_seconds),
    )
    return raw


def _issue_token_pair(user):
    access = _create_token(user, UserAuthToken.TYPE_ACCESS, ACCESS_TOKEN_TTL_SECONDS)
    refresh = _create_token(user, UserAuthToken.TYPE_REFRESH, REFRESH_TOKEN_TTL_SECONDS)
    return access, refresh


def _rotate_refresh_token(old_raw, old_obj):
    new_raw = _create_token(old_obj.user, UserAuthToken.TYPE_REFRESH, REFRESH_TOKEN_TTL_SECONDS)
    new_hash = _token_digest(new_raw)
    new_obj = UserAuthToken.objects.get(token_hash=new_hash)

    old_obj.replaced_by = new_obj
    old_obj.revoke()
    old_obj.save(update_fields=["replaced_by", "updated_at"])
    if old_raw:
        _revoke_token_by_raw(old_raw, UserAuthToken.TYPE_REFRESH)
    return new_raw


def _get_valid_token(raw_token, token_type):
    token_hash = _token_digest(raw_token)
    token_obj = UserAuthToken.objects.filter(token_hash=token_hash, token_type=token_type).select_related("user").first()
    if not token_obj:
        return None
    if token_obj.is_revoked or token_obj.is_expired():
        if token_obj.is_expired() and not token_obj.is_revoked:
            token_obj.revoke()
        return None
    return token_obj


def _revoke_token_by_raw(raw_token, token_type):
    if not raw_token:
        return
    token_hash = _token_digest(raw_token)
    token_obj = UserAuthToken.objects.filter(token_hash=token_hash, token_type=token_type).first()
    if token_obj:
        token_obj.revoke()


def _revoke_all_user_tokens(user):
    now = timezone.now()
    UserAuthToken.objects.filter(user=user, is_revoked=False).update(is_revoked=True, revoked_at=now, updated_at=now)


def _extract_bearer_or_cookie_token(request, cookie_name):
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1].strip()
    return request.COOKIES.get(cookie_name)


def _set_auth_cookies(response, access_token, refresh_token):
    _set_cookie(response, ACCESS_TOKEN_COOKIE, access_token, ACCESS_TOKEN_TTL_SECONDS)
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
