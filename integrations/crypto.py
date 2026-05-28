import os

from django.core.exceptions import ImproperlyConfigured

try:
    from cryptography.fernet import Fernet, InvalidToken
except Exception:  # pragma: no cover
    Fernet = None
    InvalidToken = Exception


def _get_fernet() -> Fernet: # type: ignore
    key = os.getenv("GOOGLE_TOKEN_ENCRYPTION_KEY", "")
    if not key:
        raise ImproperlyConfigured("GOOGLE_TOKEN_ENCRYPTION_KEY is required")
    if Fernet is None:
        raise ImproperlyConfigured("cryptography package is required for token encryption")
    return Fernet(key.encode())


def encrypt_token(raw: str | None) -> str | None:
    if not raw:
        return raw
    return _get_fernet().encrypt(raw.encode()).decode()


def decrypt_token(encrypted: str | None) -> str | None:
    if not encrypted:
        return encrypted
    try:
        return _get_fernet().decrypt(encrypted.encode()).decode()
    except InvalidToken:
        # Backward-compat for previously stored plain-text tokens.
        return encrypted
