from rest_framework.throttling import SimpleRateThrottle


class ClientIPRateThrottle(SimpleRateThrottle):
    scope = "client_ip"

    def get_cache_key(self, request, view):
        ident = self.get_ident(request)
        if not ident:
            return None
        return self.cache_format % {"scope": self.scope, "ident": ident}


class LoginRateThrottle(ClientIPRateThrottle):
    scope = "login"
    rate = "10/min"


class OTPVerifyRateThrottle(ClientIPRateThrottle):
    scope = "otp_verify"
    rate = "8/min"


class ForgotPasswordRateThrottle(ClientIPRateThrottle):
    scope = "forgot_password"
    rate = "5/min"


class ResetPasswordRateThrottle(ClientIPRateThrottle):
    scope = "reset_password"
    rate = "6/min"


class UserRateThrottle(SimpleRateThrottle):
    scope = "user_generic"
    rate = "60/min"

    def get_cache_key(self, request, view):
        user = getattr(request, "user", None)
        if not user or not getattr(user, "is_authenticated", False):
            return None
        return self.cache_format % {"scope": self.scope, "ident": str(user.user_id)}


class IdentifierIPRateThrottle(SimpleRateThrottle):
    scope = "identifier_ip_generic"
    rate = "10/min"
    identifier_fields = ()

    def _get_identifier(self, request):
        for field in self.identifier_fields:
            value = request.data.get(field)
            if value:
                return str(value).strip().lower()
        return ""

    def get_cache_key(self, request, view):
        identifier = self._get_identifier(request)
        ident = self.get_ident(request) or "unknown"
        if not identifier:
            identifier = "missing"
        composite = f"{identifier}:{ident}"
        return self.cache_format % {"scope": self.scope, "ident": composite}


class UserOTPVerifyRateThrottle(UserRateThrottle):
    scope = "otp_verify_user"
    rate = "8/min"


class UserResetPasswordRateThrottle(UserRateThrottle):
    scope = "reset_password_user"
    rate = "6/min"


class LoginIdentifierRateThrottle(IdentifierIPRateThrottle):
    scope = "login_identifier"
    rate = "10/min"
    identifier_fields = ("identifier", "username", "email")


class ForgotPasswordIdentifierRateThrottle(IdentifierIPRateThrottle):
    scope = "forgot_password_identifier"
    rate = "5/min"
    identifier_fields = ("username", "email")

class RefreshRateThrottle(ClientIPRateThrottle):
    scope = "refresh"
    rate = "12/min"

class AccountDeletionRateThrottle(UserRateThrottle):
    scope = "account_delete_identifier"
    rate = "6/min"

