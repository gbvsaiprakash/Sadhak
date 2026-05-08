import firebase_admin
from firebase_admin import credentials
from django.conf import settings


def init_firebase():
    if firebase_admin._apps:
        return

    cred_path = getattr(settings, "FIREBASE_CREDENTIALS_PATH", None)
    if not cred_path:
        raise RuntimeError("FIREBASE_CREDENTIALS_PATH is not configured")

    cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred)
