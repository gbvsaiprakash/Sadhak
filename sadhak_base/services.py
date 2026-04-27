import hashlib
from .models import FeatureFlag, DomainEvent, AuditLog

def is_feature_enabled(key: str, user_id: int | None = None) -> bool:
    try:
        flag = FeatureFlag.objects.get(key=key)
    except FeatureFlag.DoesNotExist:
        return False
    if not flag.enabled:
        return False
    if user_id is None or flag.rollout_percent >= 100:
        return flag.enabled
    bucket = int(hashlib.sha256(f"{key}:{user_id}".encode()).hexdigest(), 16) % 100
    return bucket < flag.rollout_percent

def emit_event(event_type: str, actor=None, object_type="", object_id="", payload=None):
    return DomainEvent.objects.create(
        event_type=event_type,
        actor=actor,
        object_type=object_type,
        object_id=str(object_id) if object_id else "",
        payload=payload or {},
    )

def write_audit(**kwargs):
    return AuditLog.objects.create(**kwargs)
