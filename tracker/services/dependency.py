from __future__ import annotations
from django.db.models import Q
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from tracker.models.task import Task
from tracker.models.habit import Habit
from tracker.models.occurrence import TaskOccurrence
from tracker.models.dependency import TrackerDependency
from tracker.exceptions import raise_tracker_error


ACTIVE_TASK_BLOCK = {"cancelled"}
ACTIVE_HABIT_BLOCK = {"stopped"}
RESOLVED_OCCURRENCE_STATUSES = {"completed", "skipped", "missed"}


@dataclass(frozen=True)
class EntityRef:
    kind: str  # "task" | "habit"
    id: UUID


def _owner_filter(entity):
    if getattr(entity, "is_habit", False):
        return {"owner_habit_id": entity.id}
    return {"owner_task_id": entity.id}


def _owner_key(entity) -> EntityRef:
    return EntityRef("habit" if getattr(entity, "is_habit", False) else "task", entity.id)


def _dep_key(dep: TrackerDependency) -> EntityRef:
    if dep.depends_on_task_id:
        return EntityRef("task", dep.depends_on_task_id)
    return EntityRef("habit", dep.depends_on_habit_id)

def soft_delete_owned_dependencies(entity):
    TrackerDependency.objects.filter(is_deleted=False, **_owner_filter(entity)).update(
        is_deleted=True,
        updated_at=timezone.now(),
    )

def _edge_filter_for_target(ref: EntityRef):
    if ref.kind == "task":
        return {"depends_on_task_id": ref.id}
    return {"depends_on_habit_id": ref.id}


def _normalize_dep_item(item: dict) -> EntityRef:
    kind = (item.get("type") or "").strip().lower()
    raw_id = item.get("id")
    if kind not in {"task", "habit"} or not raw_id:
        raise_tracker_error("INVALID_DEPENDENCIES", "Each dependency must include valid type and id.")
    try:
        return EntityRef(kind=kind, id=UUID(str(raw_id)))
    except Exception:
        raise_tracker_error("INVALID_DEPENDENCIES", f"Invalid dependency id: {raw_id}")


def _iter_outgoing_edges(ref: EntityRef) -> List[EntityRef]:
    if ref.kind == "task":
        qs = TrackerDependency.objects.filter(owner_task_id=ref.id, is_deleted=False)
    else:
        qs = TrackerDependency.objects.filter(owner_habit_id=ref.id, is_deleted=False)
    return [_dep_key(d) for d in qs.only("depends_on_task_id", "depends_on_habit_id")]


def _has_path(src: EntityRef, dst: EntityRef, visited: Set[Tuple[str, UUID]] | None = None) -> bool:
    if visited is None:
        visited = set()
    key = (src.kind, src.id)
    if key in visited:
        return False
    visited.add(key)

    for nxt in _iter_outgoing_edges(src):
        if nxt == dst:
            return True
        if _has_path(nxt, dst, visited):
            return True
    return False


def _would_create_cycle(owner, candidate: EntityRef) -> bool:
    owner_ref = _owner_key(owner)
    if candidate == owner_ref:
        return True
    # adding owner -> candidate creates cycle if candidate already reaches owner
    return _has_path(candidate, owner_ref)


def get_dependency_items(entity):
    qs = TrackerDependency.objects.filter(is_deleted=False, **_owner_filter(entity)).select_related(
        "depends_on_task", "depends_on_habit"
    )
    items = []
    for d in qs:
        if d.depends_on_task_id:
            parent = d.depends_on_task
            items.append(
                {
                    "id": str(parent.id),
                    "type": "task",
                    "title": parent.title,
                    "status": parent.status,
                }
            )
        elif d.depends_on_habit_id:
            parent = d.depends_on_habit
            items.append(
                {
                    "id": str(parent.id),
                    "type": "habit",
                    "title": parent.title,
                    "status": parent.status,
                }
            )
    return items

def get_dependencies(entity):
    # backward compatibility for existing serializers
    return get_dependency_items(entity)

def list_dependency_candidates_for_create(user):
    tasks = (
        Task.objects.filter(user=user, is_deleted=False)
        .exclude(status__in={"cancelled"})
        .only("id", "title", "status")
    )
    habits = (
        Habit.objects.filter(user=user, is_deleted=False)
        .exclude(status__in={"stopped"})
        .only("id", "title", "status")
    )

    out = []
    for t in tasks:
        out.append({
            "id": str(t.id),
            "type": "task",
            "title": t.title,
            "status": t.status,
            "is_selected": False,
            "is_selectable": True,
            "disable_reason_code": None,
            "disable_reason_message": None,
        })
    for h in habits:
        out.append({
            "id": str(h.id),
            "type": "habit",
            "title": h.title,
            "status": h.status,
            "is_selected": False,
            "is_selectable": True,
            "disable_reason_code": None,
            "disable_reason_message": None,
        })

    out.sort(key=lambda x: (x["type"], x["title"].lower()))
    return out

def list_dependency_candidates(entity, user):
    owner_ref = _owner_key(entity)
    selected = {
        (i["type"], UUID(i["id"]))
        for i in get_dependency_items(entity)
    }

    tasks = Task.objects.filter(user=user, is_deleted=False).exclude(status__in=ACTIVE_TASK_BLOCK).only(
        "id", "title", "status"
    )
    habits = Habit.objects.filter(user=user, is_deleted=False).exclude(status__in=ACTIVE_HABIT_BLOCK).only(
        "id", "title", "status"
    )

    out = []

    for t in tasks:
        ref = EntityRef("task", t.id)
        reason_code = None
        reason_message = None
        selectable = True
        if ref == owner_ref:
            selectable = False
            reason_code = "SELF_REFERENCE"
            reason_message = "Cannot depend on itself."
        elif _would_create_cycle(entity, ref):
            selectable = False
            reason_code = "CIRCULAR_DEPENDENCY"
            reason_message = "Selecting this creates a circular dependency."
        out.append(
            {
                "id": str(t.id),
                "type": "task",
                "title": t.title,
                "status": t.status,
                "is_selected": ("task", t.id) in selected,
                "is_selectable": selectable,
                "disable_reason_code": reason_code,
                "disable_reason_message": reason_message,
            }
        )

    for h in habits:
        ref = EntityRef("habit", h.id)
        reason_code = None
        reason_message = None
        selectable = True
        if ref == owner_ref:
            selectable = False
            reason_code = "SELF_REFERENCE"
            reason_message = "Cannot depend on itself."
        elif _would_create_cycle(entity, ref):
            selectable = False
            reason_code = "CIRCULAR_DEPENDENCY"
            reason_message = "Selecting this creates a circular dependency."
        out.append(
            {
                "id": str(h.id),
                "type": "habit",
                "title": h.title,
                "status": h.status,
                "is_selected": ("habit", h.id) in selected,
                "is_selectable": selectable,
                "disable_reason_code": reason_code,
                "disable_reason_message": reason_message,
            }
        )

    out.sort(key=lambda x: (x["type"], x["title"].lower()))
    return out


def _resolve_ref(ref: EntityRef, user):
    if ref.kind == "task":
        obj = Task.objects.filter(id=ref.id, user=user, is_deleted=False).exclude(status__in=ACTIVE_TASK_BLOCK).first()
    else:
        obj = Habit.objects.filter(id=ref.id, user=user, is_deleted=False).exclude(status__in=ACTIVE_HABIT_BLOCK).first()
    if not obj:
        raise_tracker_error("INVALID_DEPENDENCIES", f"Dependency {ref.kind}:{ref.id} is invalid/inactive.")
    return obj


@transaction.atomic
def set_dependencies(entity, dependency_payload, user):
    refs: List[EntityRef] = []
    seen = set()
    invalid = []

    for raw in (dependency_payload or []):
        ref = _normalize_dep_item(raw)
        key = (ref.kind, ref.id)
        if key in seen:
            continue
        seen.add(key)
        _resolve_ref(ref, user)

        if _would_create_cycle(entity, ref):
            invalid.append({"type": ref.kind, "id": str(ref.id), "reason": "CIRCULAR_DEPENDENCY"})
        refs.append(ref)

    if invalid:
        raise_tracker_error(
            "CIRCULAR_DEPENDENCY",
            "One or more dependencies create circular reference.",
            details={"invalid_dependencies": invalid},
        )

    desired = {(r.kind, r.id) for r in refs}

    owner_qs = TrackerDependency.objects.filter(is_deleted=False, **_owner_filter(entity))
    current = set()
    for d in owner_qs.only("depends_on_task_id", "depends_on_habit_id"):
        k = _dep_key(d)
        current.add((k.kind, k.id))

    # soft delete removed edges
    for d in owner_qs:
        k = _dep_key(d)
        if (k.kind, k.id) not in desired:
            d.is_deleted = True
            d.updated_at = timezone.now()
            d.save(update_fields=["is_deleted", "updated_at"])

    # add/restore desired edges
    for ref in refs:
        lookup = _owner_filter(entity)
        if ref.kind == "task":
            lookup["depends_on_task_id"] = ref.id
            lookup["depends_on_habit_id"] = None
        else:
            lookup["depends_on_habit_id"] = ref.id
            lookup["depends_on_task_id"] = None

        dep = TrackerDependency.objects.filter(**lookup).order_by("-created_at").first()
        if dep:
            if dep.is_deleted:
                dep.is_deleted = False
                dep.updated_at = timezone.now()
                dep.save(update_fields=["is_deleted", "updated_at"])
        else:
            create_data = _owner_filter(entity)
            create_data["depends_on_task_id"] = ref.id if ref.kind == "task" else None
            create_data["depends_on_habit_id"] = ref.id if ref.kind == "habit" else None
            TrackerDependency.objects.create(**create_data)


def ensure_not_depended_on(entity):
    ref = _owner_key(entity)
    q = (TrackerDependency.objects.filter(is_deleted=False, **_edge_filter_for_target(ref)).filter(
            Q(owner_task__isnull=False, owner_task__is_deleted=False) |
            Q(owner_habit__isnull=False, owner_habit__is_deleted=False)
        )
        .exclude(
            Q(owner_task__isnull=False, owner_task__status__in={"cancelled"}) |
            Q(owner_habit__isnull=False, owner_habit__status__in={"stopped"})
        ))
    if q.exists():
        raise_tracker_error(
            "DEPENDENCY_EXISTS",
            "This item is used as dependency. Remove dependency links first.",
        )


def ensure_dependencies_completed_for_occurrence(entity, scheduled_date):
    deps = TrackerDependency.objects.filter(is_deleted=False, **_owner_filter(entity))
    unmet = []
    for d in deps:
        if d.depends_on_task_id:
            occ = TaskOccurrence.objects.filter(
                task_id=d.depends_on_task_id,
                scheduled_date=scheduled_date,
                is_deleted=False,
            ).exclude(status__in={"cancelled"}).first()
            title = d.depends_on_task.title if d.depends_on_task else "Task"
            dep_type = "task"
            dep_id = d.depends_on_task_id
        else:
            occ = TaskOccurrence.objects.filter(
                habit_id=d.depends_on_habit_id,
                scheduled_date=scheduled_date,
                is_deleted=False,
            ).exclude(status__in={"stopped"}).first()
            title = d.depends_on_habit.title if d.depends_on_habit else "Habit"
            dep_type = "habit"
            dep_id = d.depends_on_habit_id

        if not occ or occ.status != "completed":
            unmet.append({"id": str(dep_id), "type": dep_type, "title": title})

    if unmet:
        raise_tracker_error(
            "DEPENDENCY_NOT_COMPLETED",
            "Complete all dependency occurrences first.",
            details={"unmet_dependencies": unmet},
        )
