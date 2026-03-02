from django.db import models
import uuid
from datetime import timedelta
from django.utils import timezone
import os

def otp_expiry_time(verification_type):
    return timezone.now() + timedelta(minutes=os.getenv(f"{verification_type}_EXPIRY"))


class OTPVerification(models.Model):
    """Stores OTPs for email verification."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey("User", on_delete=models.CASCADE, related_name="email_otps")
    otp_hash = models.CharField(max_length=128)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    VERIFICATION_CHOICES = (
        ("ARV","Account Registration"),
        ("FRPV","Forgot/Reset Password"),
        ("ADV","Account Deletion")
        )
    
    verification_type = models.CharField(max_length=4, choices=VERIFICATION_CHOICES)
    expires_at = models.DateTimeField(default=otp_expiry_time(verification_type))
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



class User(models.Model):
    """
    Custom User model with:
    - UUID primary key
    - Bitmask-based role management
    - Audit timestamps
    """

    # -------------------------
    # Core Identity Fields
    # -------------------------
    user_id = models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    username = models.CharField(max_length=30,unique=True)
    email = models.EmailField(max_length=255,unique=True)
    password = models.CharField(max_length=128)

    first_name = models.CharField(max_length=255)
    last_name = models.CharField(max_length=255,null=True,blank=True)
    age = models.PositiveSmallIntegerField(null=True,blank=True)

    GENDER_CHOICES = (
        ("M", "Male"),
        ("F", "Female"),
        ("O", "Other"),
    )

    gender = models.CharField(max_length=1,choices=GENDER_CHOICES,null=True,blank=True)

    # -------------------------
    # Role Management (Bitmask)
    # -------------------------
    roles = models.PositiveIntegerField(default=0,help_text="Bitmask value representing user roles")

    ROLE_USER = 1       # 0001
    ROLE_ADMIN = 2      # 0010

    # -------------------------
    # Role Helpers
    # -------------------------
    def add_role(self, role):
        self.roles |= role
        self.save(update_fields=["roles"])

    def remove_role(self, role):
        self.roles &= ~role
        self.save(update_fields=["roles"])

    def has_role(self, role):
        return (self.roles & role) == role

    def get_roles(self):
        """
        Human-readable roles list
        """
        role_names = []

        if self.has_role(self.ROLE_USER):
            role_names.append("USER")
        if self.has_role(self.ROLE_ADMIN):
            role_names.append("ADMIN")

        return role_names

    # -------------------------
    # Status Flags (important)
    # -------------------------
    is_active = models.BooleanField(default=True)
    is_email_verified = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False)

    # -------------------------
    # Audit Fields
    # -------------------------
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # -------------------------
    # String Representation
    # -------------------------
    def __str__(self):
        return self.username
    



class UserAuthToken(models.Model):
    """Opaque token storage for access/refresh authentication."""

    TYPE_ACCESS = "access"
    TYPE_REFRESH = "refresh"
    TOKEN_TYPE_CHOICES = (
        (TYPE_ACCESS, "Access"),
        (TYPE_REFRESH, "Refresh"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    jti = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey("User", on_delete=models.CASCADE, related_name="auth_tokens")
    token_type = models.CharField(max_length=20, choices=TOKEN_TYPE_CHOICES)
    token_hash = models.CharField(max_length=64, unique=True)

    expires_at = models.DateTimeField()
    is_revoked = models.BooleanField(default=False)
    revoked_at = models.DateTimeField(null=True, blank=True)

    replaced_by = models.OneToOneField(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="replaces",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "token_type", "is_revoked"]),
            models.Index(fields=["expires_at"]),
        ]

    def is_expired(self):
        return timezone.now() >= self.expires_at

    def revoke(self):
        if not self.is_revoked:
            self.is_revoked = True
            self.revoked_at = timezone.now()
            self.save(update_fields=["is_revoked", "revoked_at", "updated_at"])