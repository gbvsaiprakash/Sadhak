import uuid
from datetime import timedelta

from django.db import models
from django.db.models.functions import Lower
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin

from django.utils import timezone


def default_otp_expiry():
    return timezone.now() + timedelta(minutes=10)


class OTPVerification(models.Model):
    """Stores OTPs for account verification and reset-password flows."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey("User", on_delete=models.CASCADE, related_name="email_otps")
    otp_hash = models.CharField(max_length=128)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)

    VERIFICATION_CHOICES = (
        ("ARV", "Account Registration"),
        ("FRPV", "Forgot/Reset Password"),
        ("ADV", "Account Deletion"),
    )
    verification_type = models.CharField(max_length=4, choices=VERIFICATION_CHOICES)

    expires_at = models.DateTimeField(default=default_otp_expiry)
    is_used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "is_used"]),
            models.Index(fields=["expires_at"]),
        ]

    def is_expired(self):
        return timezone.now() > self.expires_at

    def is_locked(self):
        return self.attempt_count >= self.max_attempts

    def invalidate(self):
        self.is_used = True
        self.save(update_fields=["is_used"])


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, username, email, password=None, **extra_fields):
        if not username:
            raise ValueError("Username is required")
        if not email:
            raise ValueError("Email is required")
        username = username.strip()
        email = self.normalize_email(email)
        extra_fields.setdefault("is_active", True)
        user = self.model(username=username, email=email, **extra_fields)
        if password:
            user.set_password(password)
            user.is_password_set = True
        else:
            user.set_unusable_password()
            user.is_password_set = False
        user.save(using=self._db)
        return user

    def create_superuser(self, username, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("is_email_verified", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self.create_user(username, email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """Custom auth user compatible with Django auth/SimpleJWT token_blacklist."""

    user_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = models.CharField(max_length=30,unique=True)
    email = models.EmailField(max_length=255)
    first_name = models.CharField(max_length=255)
    last_name = models.CharField(max_length=255, null=True, blank=True)
    age = models.PositiveSmallIntegerField(null=True, blank=True)

    GENDER_CHOICES = (
        ("M", "Male"),
        ("F", "Female"),
        ("O", "Other"),
    )
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES, null=True, blank=True)

    roles = models.PositiveIntegerField(default=0, help_text="Bitmask value representing user roles")
    ROLE_USER = 1
    ROLE_ADMIN = 2

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    is_email_verified = models.BooleanField(default=False)
    is_password_set = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False)

    # Increment on logout/password reset to invalidate all active access JWTs.
    token_version = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ["email", "first_name"]

    def add_role(self, role):
        self.roles |= role
        self.save(update_fields=["roles"])

    def remove_role(self, role):
        self.roles &= ~role
        self.save(update_fields=["roles"])

    def has_role(self, role):
        return (self.roles & role) == role

    def get_roles(self):
        role_names = []
        if self.has_role(self.ROLE_USER):
            role_names.append("USER")
        if self.has_role(self.ROLE_ADMIN):
            role_names.append("ADMIN")
        return role_names

    def __str__(self):
        return self.username
    
    class Meta:
        constraints = [models.UniqueConstraint(fields=["email"],
                condition=models.Q(is_deleted=False),
                name="unique_active_email"),models.UniqueConstraint(Lower("username"),
                condition=models.Q(is_deleted=False),
                name="unique_active_username")]
