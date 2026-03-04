from django.urls import path

from .views import (
    CreateRefreshTokenAPIView,
    EmailVerificationAPIView,
    ForgotPasswordAPIView,
    LoginAPIView,
    LogoutAPIView,
    PasswordChangeAPIView,
    ProfileUpdateAPIView,
    RefreshAccessTokenAPIView,
    RegistrationAPIView,
    ResendOTPAPIView,
    ResetPasswordAPIView,
    SetupPasswordAPIView,
)


urlpatterns = [
    path("register/", RegistrationAPIView.as_view(), name="register"),
    path("verify-email/", EmailVerificationAPIView.as_view(), name="verify_email"),
    path("setup-password/", SetupPasswordAPIView.as_view(), name="setup_password"),
    path("otp/resend/", ResendOTPAPIView.as_view(), name="resend_otp"),
    path("login/", LoginAPIView.as_view(), name="login"),
    path("forgot-password/", ForgotPasswordAPIView.as_view(), name="forgot_password"),
    path("reset-password/", ResetPasswordAPIView.as_view(), name="reset_password"),
    path("change-password/", PasswordChangeAPIView.as_view(), name="change_password"),
    path("profile/update/", ProfileUpdateAPIView.as_view(), name="profile_update"),
    path("logout/", LogoutAPIView.as_view(), name="logout"),
    path("token/refresh/", RefreshAccessTokenAPIView.as_view(), name="token_refresh_access"),
    path("token/create-refresh/", CreateRefreshTokenAPIView.as_view(), name="token_create_refresh"),
]
