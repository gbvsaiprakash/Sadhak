import secrets
from datetime import timedelta
import logging
import re
from django.core.cache import cache
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.decorators import method_decorator
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny, BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from django.core.exceptions import ValidationError
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from django.middleware.csrf import get_token
from user_management.emails import send_email,account_registration_template_html,password_reset_template_html,account_deletion_template_html
import os
from .authentication import TokenCookieAuthentication
from .throttles import (
    ForgotPasswordIdentifierRateThrottle,
    LoginIdentifierRateThrottle,
    RefreshRateThrottle,
    UserOTPVerifyRateThrottle,
    UserResetPasswordRateThrottle,
    AccountDeletionRateThrottle,
)
from sadhak.app_settings import (
    access_token_cookie,
    account_verification_code,
    account_deletion_code,
    ar_expiry,
    setup_expiry,
    ad_expiry,
    forgot_verification_code,
    fp_expiry,
    refresh_token_cookie,
    access_token_seconds,
    refresh_token_seconds,
    SCOPE_FULL_AUTH,
    SCOPE_EMAIL_VERIFY,
    SCOPE_PASSWORD_RESET,
    SCOPE_SETUP_PASSWORD,
    refresh_token_path,
)
from .models import OTPVerification, User


logger = logging.getLogger(__name__)


class HasRequiredScope(BasePermission):
    def has_permission(self, request, view):
        required_scope = getattr(view, "required_token_scope", None)
        if not required_scope:
            return True
        token = getattr(request, "auth", None)
        if token is None:
            return False
        return token.get("scope") == required_scope


class AuthenticatedAPIView(APIView):
    authentication_classes = [TokenCookieAuthentication]
    permission_classes = [IsAuthenticated, HasRequiredScope]
    required_token_scope = SCOPE_FULL_AUTH

class CheckUsernameAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        data = request.data
        username = (data.get("username") or "").strip()
        if not username:
            return Response(
                {"message": "username, email, first_name are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        # if username exists
        if User.objects.filter(username__iexact=username,is_deleted=False).exists():
            return Response({"message": "Username is not available","available":False}, status=status.HTTP_400_BAD_REQUEST)
        
        return Response({"message": "Username is Available","available":True}, status=status.HTTP_200_OK)
        


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

        if not username or not email or not first_name:# or not password or not confirm_password:
            return Response(
                {"message": "username, email, first_name are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # if verified user with username exists
        if User.objects.filter(username__iexact=username, is_email_verified=True, is_deleted=False).exists():
            return Response({"message": "Username is not available"}, status=status.HTTP_400_BAD_REQUEST)
        # existing verified user
        existing_verified_by_email = User.objects.filter(email=email, is_email_verified=True, is_deleted=False).first()
        if existing_verified_by_email:
            # if password is not set
            if not existing_verified_by_email.is_password_set:
                access_token,_ = _get_or_issue_scoped_access_token(existing_verified_by_email, scope=SCOPE_EMAIL_VERIFY,ttl_seconds=ar_expiry)
                response = Response(
                    {
                        "message": "Email already verified. Complete password setup.",
                        "next_step": "setup_password",
                    },
                    status=status.HTTP_200_OK,
                )
                _set_auth_cookies(response=response, access_token=access_token, access_token_expiry=ar_expiry)
                return response
            # if all set return email already exists
            return Response({"message": "Email already registered. Please login or reset your password."}, status=status.HTTP_400_BAD_REQUEST)

        # check unverfied email exists
        existing_unverified_by_email = User.objects.filter(
            email=email,
            is_email_verified=False,
            is_deleted=False,
        ).first()
        if existing_unverified_by_email:
            # check if a person try to register with username exists with same email
            if (
                User.objects.filter(username__iexact=username,is_deleted=False)
                .exclude(user_id=existing_unverified_by_email.user_id)
                .exists()
            ):
                return Response({"message": "Username is not available"}, status=status.HTTP_400_BAD_REQUEST)

            active_otp = _get_active_otp(existing_unverified_by_email, account_verification_code)
            # if active otp not exists send email to verify
            if not active_otp:
                otp_value = _create_email_verification_otp(existing_unverified_by_email)
                if not _send_otp_email(
                    existing_unverified_by_email.email,
                    existing_unverified_by_email.first_name or existing_unverified_by_email.username,
                    otp_value,
                    "Email verification OTP",
                ):
                    return Response({"message":"Unable to send email. please try again later."},status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            access_token,_ = _get_or_issue_scoped_access_token(existing_unverified_by_email, scope=SCOPE_EMAIL_VERIFY,ttl_seconds=ar_expiry)
            # if active otp exists
            response = Response(
                {
                    "message": "An OTP has been sent to your email. Please verify your email to continue.",
                    "next_step": "Verify_email",
                },
                status=status.HTTP_200_OK,
            )
            _set_auth_cookies(response=response, access_token=access_token, access_token_expiry=ar_expiry)
            return response
        
        if (
            User.objects.filter(username__iexact=username, is_email_verified=False, is_deleted=False)
            .exclude(email=email)
            .exists()
        ):
            return Response({"message": "Username is not available"}, status=status.HTTP_400_BAD_REQUEST)

        if age:
            if not isinstance(age,int) or age<=0:
                return Response({"message": "Age should be a positive number"}, status=status.HTTP_400_BAD_REQUEST)
        
        if gender:
            valid_genders = {choice[0] for choice in User.GENDER_CHOICES}
            if gender not in valid_genders:
                return Response({"message": "Invalid gender"}, status=status.HTTP_400_BAD_REQUEST)
            
        with transaction.atomic():
            user = User.objects.create(
                username=username,
                email=email,
                first_name=first_name,
                last_name=last_name or None,
                gender=gender,
                age=age,
                is_active=True,
                is_email_verified=False,
                is_password_set=False,
            )
            user.set_unusable_password()
            user.save(update_fields=["password"])
            user.add_role(User.ROLE_USER)
            otp_value = _create_email_verification_otp(user)

        if not _send_otp_email(user.email, user.first_name, otp_value, "Email verification OTP"):
            return Response({"message":"Unable to send email. please try again later."},status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        access_token,_ = _get_or_issue_scoped_access_token(user, scope=SCOPE_EMAIL_VERIFY,ttl_seconds=ar_expiry)

        response = Response(
            {
                "message": "Registration successful. Verify email using OTP.",
                "access_token":access_token,
                # "user_id": str(user.user_id),
            },
            status=status.HTTP_201_CREATED,
        )
        # _set_auth_cookies(response=response, access_token=access_token, access_token_expiry=ar_expiry)
        return response


class EmailVerificationAPIView(AuthenticatedAPIView):
    required_token_scope = SCOPE_EMAIL_VERIFY
    throttle_classes = [UserOTPVerifyRateThrottle]

    def post(self, request):
        otp = (request.data.get("otp") or "").strip()
        if not otp:
            return Response({"message": "otp is required"}, status=status.HTTP_400_BAD_REQUEST)

        user = request.user
        otp_obj = (
            OTPVerification.objects.filter(user=user, verification_type=account_verification_code, is_used=False)
            .order_by("-created_at")
            .first()
        )

        if not otp_obj:
            logger.warning("email_verification_no_active_otp user_id=%s", user.user_id)
            return Response({"message": "OTP has been expired"}, status=status.HTTP_400_BAD_REQUEST)
        if otp_obj.is_locked() or otp_obj.is_expired():
            logger.warning("email_verification_otp_locked_or_expired user_id=%s", user.user_id)
            otp_obj.invalidate()
            return Response({"message": "OTP has been expired"}, status=status.HTTP_400_BAD_REQUEST)

        if not check_password(otp, otp_obj.otp_hash):
            otp_obj.attempt_count += 1
            fields = ["attempt_count"]
            if otp_obj.attempt_count >= otp_obj.max_attempts:
                otp_obj.is_used = True
                fields.append("is_used")
            otp_obj.save(update_fields=fields)
            logger.warning("email_verification_invalid_otp user_id=%s", user.user_id)
            return Response({"message": "Invalid OTP"}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            otp_obj.is_used = True
            otp_obj.save(update_fields=["is_used"])
            user.is_email_verified = True
            user.verified_at = timezone.now()
            user.save(update_fields=["is_email_verified", "verified_at", "updated_at"])
            _blacklist_request_access_token(request)
            setup_token, _ = _get_or_issue_scoped_access_token(user, SCOPE_SETUP_PASSWORD, setup_expiry)

        response = Response({"message": "Email verified successfully","access_token":setup_token}, status=status.HTTP_200_OK)
        logger.info("email_verification_success user_id=%s", user.user_id)
        # _set_auth_cookies(response=response,access_token=setup_token,access_token_expiry=setup_expiry,)
        return response


class SetupPasswordAPIView(AuthenticatedAPIView):
    required_token_scope = SCOPE_SETUP_PASSWORD

    def post(self, request):
        new_password = request.data.get("new_password")
        confirm_password = request.data.get("confirm_password")
        if not new_password or not confirm_password:
            return Response(
                {"message": "new_password and confirm_password are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if new_password != confirm_password:
            return Response({"message": "Passwords do not match"}, status=status.HTTP_400_BAD_REQUEST)
        
        if not _password_check(new_password):
            return Response({"message":"Password must contain atleast 8 characters that include one uppercase, one lowercase, one number, and one special character."},status=status.HTTP_400_BAD_REQUEST)

        user = request.user
        if not user.is_email_verified:
            return Response({"message": "Email verification required"}, status=status.HTTP_403_FORBIDDEN)

        with transaction.atomic():
            user.set_password(new_password)
            user.is_password_set = True
            user.token_version += 1
            user.save(update_fields=["password", "is_password_set", "token_version", "updated_at"])
            _blacklist_request_access_token(request)

        access_token, refresh_token = _issue_token_pair(user)
        response = Response({"message": "Password setup successful","access_token":access_token}, status=status.HTTP_200_OK)
        _set_auth_cookies(response, access_token=None, refresh_token=refresh_token)
        return response


class ResendOTPAPIView(APIView):
    authentication_classes = [TokenCookieAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [UserOTPVerifyRateThrottle]

    def post(self, request):
        user = request.user
        token = getattr(request, "auth", None) or {}
        scope = token.get("scope")
        if scope == SCOPE_EMAIL_VERIFY:
            if user.is_email_verified:
                return Response({"message": "Email has been registered"}, status=status.HTTP_200_OK)
            verification_type = account_verification_code
            subject = "Email verification OTP"
            create_otp_fn = _create_email_verification_otp
        elif scope == SCOPE_PASSWORD_RESET:
            verification_type = forgot_verification_code
            subject = "Password reset OTP"
            create_otp_fn = _create_password_reset_otp
        else:
            return Response(
                {"message": "This token scope cannot resend OTP."},
                status=status.HTTP_403_FORBIDDEN,
            )

        active_otp = _get_active_otp(user, verification_type)
        if active_otp and (timezone.now() - active_otp.created_at).seconds < 60:
            return Response({"message": "An OTP has been sent to your registered email."}, status=status.HTTP_200_OK)

        with transaction.atomic():
            OTPVerification.objects.filter(
                user=user,
                verification_type=verification_type,
                is_used=False,
            ).update(is_used=True)
            otp = create_otp_fn(user)

        if not _send_otp_email(user.email, user.username or user.first_name, otp, subject):
            return Response({"message":"Unable to send email. please try again later."},status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response({"message": "OTP resent successfully"}, status=status.HTTP_200_OK)


class LoginAPIView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [LoginIdentifierRateThrottle]

    def post(self, request):
        identifier = (request.data.get("identifier") or "").strip()
        password = request.data.get("password")

        if not identifier or not password:
            return Response({"message": "identifier and password are required"}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(email=identifier, is_deleted=False).first()
        if not user:
            user = User.objects.filter(username=identifier, is_deleted=False).first()

        if not user or not check_password(password, user.password):
            logger.warning("login_failed_invalid_credentials identifier=%s", identifier)
            return Response({"message": "Invalid credentials"}, status=status.HTTP_401_UNAUTHORIZED)
        if not user.is_active:
            logger.warning("login_failed_inactive user_id=%s", user.user_id)
            return Response({"message": "Account is inactive"}, status=status.HTTP_403_FORBIDDEN)
        if not user.is_email_verified:
            logger.warning("login_failed_email_unverified user_id=%s", user.user_id)
            return Response({"message": "Email is not verified"}, status=status.HTTP_403_FORBIDDEN)
        if not user.is_password_set:
            logger.warning("login_failed_password_not_set user_id=%s", user.user_id)
            return Response(
                {"message": "Your account does not have a password set yet. Please create a password to continue."},
                status=status.HTTP_403_FORBIDDEN,
            )
        
        user.token_version += 1
        user.last_login = timezone.now()
        user.save(update_fields=["token_version","last_login"])
        old_refresh = _extract_cookie_token(request, refresh_token_cookie)
        if old_refresh:
            _blacklist_refresh_token_by_raw(old_refresh)

        access_token, refresh_token = _issue_token_pair(user)
        response = Response(
            {
                "message": "Login successful",
                "user": {
                    "username": user.username,
                    "email": user.email,
                    "roles": user.get_roles(),
                },
                "access_token":access_token,
            },
            status=status.HTTP_200_OK,
        )
        _set_auth_cookies(response, access_token=None, refresh_token=refresh_token)
        logger.info("login_success user_id=%s", user.user_id)
        return response


class ForgotPasswordAPIView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ForgotPasswordIdentifierRateThrottle]

    def post(self, request):
        username = (request.data.get("username") or "").strip()
        if not username:
            return Response({"message": "username is required"}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(username=username, is_deleted=False).first()
        if not user:
            logger.info("forgot_password_requested_unknown_username username=%s", username)
            return Response(
                {"message": f"If the {username} exists, an OTP has been sent to registered Email Address"},
                status=status.HTTP_200_OK,
            )
        active_otp = _get_active_otp(user, forgot_verification_code)
        if active_otp and (timezone.now() - active_otp.created_at).seconds < 60:
            return Response({"message": "An OTP has been sent to your registered email."}, status=status.HTTP_200_OK)
        otp = _create_password_reset_otp(user)
        if not _send_otp_email(user.email, user.username or user.first_name, otp, "Password reset OTP"):
            return Response({"message":"Unable to send email. please try again later."},status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        access_token,_ = _get_or_issue_scoped_access_token(user, scope=SCOPE_PASSWORD_RESET,ttl_seconds=fp_expiry)
        response = Response(
            {"message": f"If the {username} exists, an OTP has been sent to registered Email Address", "access_token":access_token},
            status=status.HTTP_200_OK,
        )
        get_token(request)
        # _set_auth_cookies(response=response, access_token=access_token, access_token_expiry=fp_expiry)
        logger.info("forgot_password_otp_issued user_id=%s", user.user_id)
        return response

class DeleteAccountAPIView(AuthenticatedAPIView):
    throttle_classes = [AccountDeletionRateThrottle]

    def post(self,request):
        otp = (request.data.get("otp") or "").strip()
        if not otp:
            return Response({"message": "otp is required"}, status=status.HTTP_400_BAD_REQUEST)
        user = request.user
        otp_obj = (
            OTPVerification.objects.filter(user=user, verification_type=account_deletion_code, is_used=False)
            .order_by("-created_at")
            .first()
        )

        if not otp_obj:
            logger.warning("account_deletion_no_active_otp user_id=%s", user.user_id)
            return Response({"message": "No active OTP found"}, status=status.HTTP_400_BAD_REQUEST)
        if otp_obj.is_locked() or otp_obj.is_expired():
            logger.warning("account_deletion_otp_locked_or_expired user_id=%s", user.user_id)
            otp_obj.invalidate()
            return Response({"message": "OTP expired or locked"}, status=status.HTTP_400_BAD_REQUEST)
        
        if not check_password(otp, otp_obj.otp_hash):
            otp_obj.attempt_count += 1
            fields = ["attempt_count"]
            if otp_obj.attempt_count >= otp_obj.max_attempts:
                otp_obj.is_used = True
                fields.append("is_used")
            otp_obj.save(update_fields=fields)
            logger.warning("reset_password_invalid_otp user_id=%s", user.user_id)
            return Response({"message": "Invalid OTP"}, status=status.HTTP_400_BAD_REQUEST)
        
        with transaction.atomic():
            user.token_version += 1
            user.is_deleted = True
            user.save(update_fields=["is_deleted", "token_version", "updated_at"])
            otp_obj.is_used = True
            otp_obj.save(update_fields=["is_used"])
            _blacklist_refresh_token_by_raw(_extract_cookie_token(request, refresh_token_cookie))
            _blacklist_request_access_token(request)

        response = Response({"message": "Account Deleted Successfully, Sad to see you go!"}, status=status.HTTP_200_OK)
        logger.info("account_deletion_success user_id=%s", user.user_id)
        _clear_auth_cookies(response)
        return response




class DeleteOTPRequestAPIView(AuthenticatedAPIView):
    throttle_classes = [AccountDeletionRateThrottle]

    def post(self, request):
        user = request.user
        user = User.objects.filter(username=user.username, is_deleted=False).first()
        if not user:
            username = user.user_name
            logger.info("account_deletion_requested_unknown_username username=%s", username)
            return Response(
                {"message": f"An OTP has been sent to registered Email Address"},
                status=status.HTTP_200_OK,
            )
        active_otp = _get_active_otp(user, account_deletion_code)
        if active_otp and (timezone.now() - active_otp.created_at).seconds < 60:
            return Response({"message": "An OTP has been sent to your registered email."}, status=status.HTTP_200_OK)
        otp = _create_account_delete_otp(user)
        if not _send_otp_email(user.email, user.username or user.first_name, otp, "Account Deletion OTP"):
            return Response({"message":"Unable to send email. please try again later."},status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        response = Response(
            {"message": f"An OTP has been sent to registered Email Address"},
            status=status.HTTP_200_OK,
        )
        logger.info("account_deletion_otp_issued user_id=%s", user.user_id)
        return response

class ResetPasswordAPIView(AuthenticatedAPIView):
    required_token_scope = SCOPE_PASSWORD_RESET
    throttle_classes = [UserResetPasswordRateThrottle]

    def post(self, request):
        otp = (request.data.get("otp") or "").strip()
        new_password = request.data.get("new_password")
        confirm_password = request.data.get("confirm_password")

        if not otp or not new_password or not confirm_password:
            return Response(
                {"message": "otp, new_password and confirm_password are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if new_password != confirm_password:
            return Response({"message": "Passwords do not match"}, status=status.HTTP_400_BAD_REQUEST)
        if not _password_check(new_password):
            return Response({"message":"Password must contain atleast 8 characters that include one uppercase, one lowercase, one number, and one special character."})

        user = request.user
        otp_obj = (
            OTPVerification.objects.filter(user=user, verification_type=forgot_verification_code, is_used=False)
            .order_by("-created_at")
            .first()
        )

        if not otp_obj:
            logger.warning("reset_password_no_active_otp user_id=%s", user.user_id)
            return Response({"message": "No active OTP found"}, status=status.HTTP_400_BAD_REQUEST)
        if otp_obj.is_locked() or otp_obj.is_expired():
            logger.warning("reset_password_otp_locked_or_expired user_id=%s", user.user_id)
            otp_obj.invalidate()
            return Response({"message": "OTP expired or locked"}, status=status.HTTP_400_BAD_REQUEST)

        if not check_password(otp, otp_obj.otp_hash):
            otp_obj.attempt_count += 1
            fields = ["attempt_count"]
            if otp_obj.attempt_count >= otp_obj.max_attempts:
                otp_obj.is_used = True
                fields.append("is_used")
            otp_obj.save(update_fields=fields)
            logger.warning("reset_password_invalid_otp user_id=%s", user.user_id)
            return Response({"message": "Invalid OTP"}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            user.set_password(new_password)
            user.is_password_set = True
            user.token_version += 1
            user.save(update_fields=["password", "is_password_set", "token_version", "updated_at"])
            otp_obj.is_used = True
            otp_obj.save(update_fields=["is_used"])
            _blacklist_refresh_token_by_raw(_extract_cookie_token(request, refresh_token_cookie))
            _blacklist_request_access_token(request)

        response = Response({"message": "Password reset successful"}, status=status.HTTP_200_OK)
        logger.info("reset_password_success user_id=%s", user.user_id)
        _clear_auth_cookies(response)
        return response


class PasswordChangeAPIView(AuthenticatedAPIView):
    def put(self, request):
        old_password = request.data.get("old_password")
        new_password = request.data.get("new_password")
        confirm_password = request.data.get("confirm_password")

        if not old_password or not new_password or not confirm_password:
            return Response(
                {"message": "old_password, new_password and confirm_password are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if old_password == new_password:
            return Response({"message":"New Password should not match old passwords."})
        if new_password != confirm_password:
            return Response({"message": "Passwords do not match"}, status=status.HTTP_400_BAD_REQUEST)
        if not _password_check(new_password):
            return Response({"message":"Password must contain atleast 8 characters that include one uppercase, one lowercase, one number, and one special character."})


        user = request.user
        if not check_password(old_password, user.password):
            logger.warning("password_change_failed_old_password user_id=%s", user.user_id)
            return Response({"message": "Old password is incorrect"}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            user.set_password(new_password)
            user.is_password_set = True
            user.token_version += 1
            user.save(update_fields=["password", "is_password_set", "token_version", "updated_at"])
            _blacklist_refresh_token_by_raw(_extract_cookie_token(request, refresh_token_cookie))
            _blacklist_request_access_token(request)

        response = Response({"message": "Password changed successfully"}, status=status.HTTP_200_OK)
        logger.info("password_change_success user_id=%s", user.user_id)
        # _clear_auth_cookies(response)
        return response


class ProfileAPIView(AuthenticatedAPIView):
    def get(self,request):
        user = request.user
        return Response(
            {
                "message": "Profile fetched successfully",
                "user": {
                    "username": user.username,
                    "email": user.email,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "age": user.age,
                    "gender": user.gender,
                    "is_email_verified": user.is_email_verified,
                    "verified_at": user.verified_at,
                    "roles": user.get_roles(),
                },
            },
            status=status.HTTP_200_OK,
        )
    
    def patch(self, request):
        user = request.user
        allowed_fields = {"username", "first_name", "last_name", "age", "gender"}
        updates = {k: v for k, v in request.data.items() if k in allowed_fields}

        if not updates:
            return Response({"message": "No valid profile fields provided"}, status=status.HTTP_400_BAD_REQUEST)

        if "username" in updates:
            username = (updates["username"] or "").strip()
            if not username:
                return Response({"message": "username cannot be blank"}, status=status.HTTP_400_BAD_REQUEST)
            if User.objects.filter(username__iexact=username).exclude(user_id=user.user_id).exists():
                return Response({"message": "Username already exists"}, status=status.HTTP_400_BAD_REQUEST)
            updates["username"] = username

        if "first_name" in updates and not str(updates["first_name"]).strip():
            return Response({"message": "first_name cannot be blank"}, status=status.HTTP_400_BAD_REQUEST)

        if "gender" in updates:
            valid_genders = {choice[0] for choice in User.GENDER_CHOICES}
            if updates["gender"] not in valid_genders:
                return Response({"message": "Invalid gender"}, status=status.HTTP_400_BAD_REQUEST)
        
        if "age" in updates:
            if not isinstance(updates.get("age"),int) or updates.get("age")<=0:
                return Response({"message": "Age should be a positive number"}, status=status.HTTP_400_BAD_REQUEST)

        for field, value in updates.items():
            setattr(user, field, value)

        update_fields = list(updates.keys())
        if "updated_at" not in update_fields:
            update_fields.append("updated_at")
        user.save(update_fields=update_fields)

        return Response(
            {
                "message": "Profile updated successfully",
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
            _blacklist_refresh_token_by_raw(_extract_cookie_token(request, refresh_token_cookie))
            _blacklist_request_access_token(request)

        response = Response({"message": "Logout successful"}, status=status.HTTP_200_OK)
        logger.info("logout_success user_id=%s", request.user.user_id)
        _clear_auth_cookies(response)
        return response


class CreateRefreshTokenAPIView(AuthenticatedAPIView):
    def post(self, request):
        old_refresh = _extract_cookie_token(request, refresh_token_cookie)
        if old_refresh:
            _blacklist_refresh_token_by_raw(old_refresh)

        refresh_token = _create_refresh_token(request.user)
        response = Response({"message": "Refresh token created"}, status=status.HTTP_201_CREATED)
        _set_auth_cookies(response, refresh_token=refresh_token)
        return response


class RefreshAccessTokenAPIView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [RefreshRateThrottle]

    def post(self, request):
        refresh_raw = _extract_cookie_token(request, refresh_token_cookie)
        if not refresh_raw:
            logger.warning("refresh_access_failed_missing_refresh_cookie")
            response = Response({"message": "Refresh token is required"}, status=status.HTTP_401_UNAUTHORIZED)
            _clear_auth_cookies(response)
            return response

        try:
            user, refresh_token = _validate_refresh_token(refresh_raw)
        except AuthenticationFailed as exc:
            logger.warning("refresh_access_failed_invalid_refresh reason=%s", str(exc))
            response = Response({"message": str(exc)}, status=status.HTTP_401_UNAUTHORIZED)
            _clear_auth_cookies(response)
            return response

        access_token,_ = _get_or_issue_scoped_access_token(user, scope=SCOPE_FULL_AUTH,ttl_seconds=access_token_seconds)
        refresh_token = _rotate_refresh_token(refresh_raw, refresh_token, user)

        response = Response({"message": "Access token refreshed","access_token":access_token}, status=status.HTTP_200_OK)
        _set_auth_cookies(response, access_token=None, refresh_token=refresh_token)
        logger.info("refresh_access_success user_id=%s", user.user_id)
        return response

def _create_email_verification_otp(user):
    otp = _generate_otp()
    expiry_seconds = int(ar_expiry)
    OTPVerification.objects.create(
        user=user,
        otp_hash=make_password(otp),
        verification_type=account_verification_code,
        expires_at=timezone.now() + timedelta(seconds=expiry_seconds),
    )
    return otp

def _create_account_delete_otp(user):
    otp = _generate_otp()
    expiry_seconds = int(ad_expiry)
    OTPVerification.objects.create(
        user=user,
        otp_hash=make_password(otp),
        verification_type=account_deletion_code,
        expires_at=timezone.now() + timedelta(seconds=expiry_seconds),
    )
    return otp

def _create_password_reset_otp(user):
    otp = _generate_otp()
    expiry_seconds = int(fp_expiry)
    OTPVerification.objects.create(
        user=user,
        otp_hash=make_password(otp),
        verification_type=forgot_verification_code,
        expires_at=timezone.now() + timedelta(seconds=expiry_seconds),
    )
    return otp


def _generate_otp():
    return f"{secrets.randbelow(1_000_000):06d}"


def _get_active_otp(user, verification_type):
    otp = (
        OTPVerification.objects.filter(
            user=user,
            verification_type=verification_type,
            is_used=False,
        )
        .order_by("-created_at")
        .first()
    )
    if not otp:
        return None
    if otp.is_expired() or otp.is_locked():
        otp.invalidate()
        return None
    return otp


def _send_otp_email(email, username, otp, subject):
    body = ""
    if subject == "Email verification OTP":
        body = account_registration_template_html(username, otp)
    elif subject == "Password reset OTP":
        body = password_reset_template_html(username, otp)
    elif subject == "Account Deletion OTP":
        body = account_deletion_template_html(username, otp)
    sent,message = send_email(subject,body,email,'html')
    if message == "error me":
        return False
    return True

def _access_lifetime_seconds():
    simple_jwt = getattr(settings, "SIMPLE_JWT", {})
    lifetime = simple_jwt.get("ACCESS_TOKEN_LIFETIME")
    if lifetime is not None:
        return int(lifetime.total_seconds())
    return  access_token_seconds


def _refresh_lifetime_seconds():
    simple_jwt = getattr(settings, "SIMPLE_JWT", {})
    lifetime = simple_jwt.get("REFRESH_TOKEN_LIFETIME")
    if lifetime is not None:
        return int(lifetime.total_seconds())
    return refresh_token_seconds


def _create_access_token(user, scope=SCOPE_FULL_AUTH):
    token = AccessToken.for_user(user)
    token["token_version"] = user.token_version
    token["scope"] = scope
    return str(token)

def _scoped_token_cache_key(user, scope: str) -> str:
    return f"scoped_access:{user.user_id}:{scope}"

def _get_or_issue_scoped_access_token(user, scope: str, ttl_seconds: int):
    key = _scoped_token_cache_key(user, scope)
    try:
        cached = cache.get(key)
    except Exception as e:
        cached = None
        logger.warning("cache_get_failed key=%s", key, e)

    if cached:
        try:
            stored_token = AccessToken(cached)  # validates exp/signature
            if not cache.get(f"access_blacklist:{stored_token.get('jti')}"):
                return cached, False
        except TokenError:
            pass

    token = _create_access_token(user, scope=scope)

    try:
        cache.set(key, token, timeout=ttl_seconds)
    except Exception as e:
        logger.warning("cache_set_failed key=%s", key, e)

    return token, True

def _create_refresh_token(user):
    refresh = RefreshToken.for_user(user)
    refresh["token_version"] = user.token_version
    refresh["scope"] = SCOPE_FULL_AUTH
    return str(refresh)


def _issue_token_pair(user):
    refresh_raw = _create_refresh_token(user)
    refresh = RefreshToken(refresh_raw)
    access = refresh.access_token
    access["token_version"] = user.token_version
    access["scope"] = SCOPE_FULL_AUTH
    return str(access), refresh_raw


def _lookup_user_from_refresh_token(refresh_token):
    simple_jwt = getattr(settings, "SIMPLE_JWT", {})
    claim_name = simple_jwt.get("USER_ID_CLAIM", "user_id")
    lookup_field = str(simple_jwt.get("USER_ID_FIELD", "id")).lower()
    claim_value = refresh_token.get(claim_name)

    if claim_value is None:
        raise AuthenticationFailed("Malformed refresh token")

    filters = {lookup_field: claim_value, "is_deleted": False}
    user = User.objects.filter(**filters).first()
    if not user:
        raise AuthenticationFailed("User not found")
    if not user.is_active:
        raise AuthenticationFailed("User account is inactive")
    return user


def _validate_refresh_token(raw_token):
    try:
        refresh_token = RefreshToken(raw_token)
    except TokenError:
        raise AuthenticationFailed("Invalid or expired refresh token")
    if BlacklistedToken.objects.filter(token__jti=refresh_token.get("jti")).exists():
        raise AuthenticationFailed("Refresh token revoked")

    user = _lookup_user_from_refresh_token(refresh_token)
    if int(refresh_token.get("token_version", -1)) != user.token_version:
        raise AuthenticationFailed("Session invalidated")

    return user, refresh_token


def _rotate_refresh_token(old_raw, old_refresh_token, user):
    new_raw = _create_refresh_token(user)
    _blacklist_refresh_token_instance(old_refresh_token)
    _blacklist_refresh_token_by_raw(old_raw)
    return new_raw


def _blacklist_refresh_token_instance(refresh_token):
    if not refresh_token:
        return
    try:
        refresh_token.blacklist()
    except Exception:
        # If already blacklisted or token cannot be blacklisted, ignore here.
        pass


def _blacklist_refresh_token_by_raw(raw_token):
    if not raw_token:
        return
    try:
        refresh_token = RefreshToken(raw_token)
    except TokenError:
        return
    _blacklist_refresh_token_instance(refresh_token)


def _blacklist_access_jti(jti, exp_ts):
    ttl = int(exp_ts - timezone.now().timestamp())
    if ttl > 0:
        cache.set(f"access_blacklist:{jti}", 1, timeout=ttl)


def _blacklist_request_access_token(request):
    token = getattr(request, "auth", None)
    if token is None:
        return
    jti = token.get("jti")
    exp = token.get("exp")
    if jti and exp:
        _blacklist_access_jti(jti, exp)


def _extract_cookie_token(request, cookie_name):
    return request.COOKIES.get(cookie_name)

def _password_check(password):
    pattern = r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*()_]).{8,}$'

    if not re.match(pattern, password):
        # raise ValidationError(
        #     "Password must contain atleast 8 characters that include one uppercase, one lowercase, one number, and one special character."
        # )
        return False
    return True

def _set_auth_cookies(response, access_token=None, refresh_token=None, access_token_expiry=None,refresh_token_expiry=None):
    if access_token:
        max_age = int(access_token_expiry) if access_token_expiry is not None else _access_lifetime_seconds()
        _set_cookie(response, access_token_cookie, access_token, max_age, None)
    if refresh_token:
        max_age = int(refresh_token_expiry) if refresh_token_expiry is not None else _refresh_lifetime_seconds()
        _set_cookie(response, refresh_token_cookie, refresh_token, max_age, refresh_token_path)
    


def _set_cookie(response, key, token, max_age,path):
    response.set_cookie(
        key=key,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=not settings.DEBUG,
        samesite=os.getenv("SAME_SITE_COOKIE"),
        path=path or "/",
    )


def _clear_auth_cookies(response):
    response.delete_cookie(access_token_cookie, path="/")
    response.delete_cookie(refresh_token_cookie, path="/")
