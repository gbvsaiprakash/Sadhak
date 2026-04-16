from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from tracker.exceptions import raise_tracker_error
from tracker.models import Habit, Task, TrackerDependency, TaskOccurrence


def _entity_node(entity):
    return ("habit", str(entity.id)) if getattr(entity, "is_habit", False) else ("task", str(entity.id))


def _edge_owner_filter(entity):
    if getattr(entity, "is_habit", False):
        return {"owner_habit": entity}
    return {"owner_task": entity}


def _load_target(user, dep_type, dep_id):
    if dep_type == "task":
        obj = Task.objects.filter(id=dep_id, user=user, is_deleted=False).first()
    else:
        obj = Habit.objects.filter(id=dep_id, user=user, is_deleted=False).first()

    if obj is None:
        raise_tracker_error("INVALID_DEPENDENCY", f"Dependency {dep_type}:{dep_id} not found.")
    return obj


def _iter_outgoing(node):
    kind, node_id = node
    if kind == "task":
        qs = TrackerDependency.objects.filter(owner_task_id=node_id, is_deleted=False)
    else:
        qs = TrackerDependency.objects.filter(owner_habit_id=node_id, is_deleted=False)

    for e in qs:
        if e.depends_on_task_id:
            yield ("task", str(e.depends_on_task_id))
        elif e.depends_on_habit_id:
            yield ("habit", str(e.depends_on_habit_id))


def _has_path(start_node, target_node):
    stack = [start_node]
    seen = set()
    while stack:
        cur = stack.pop()
        if cur == target_node:
            return True
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(_iter_outgoing(cur))
    return False


def _ensure_no_cycle(owner_entity, dep_obj):
    owner_node = _entity_node(owner_entity)
    dep_node = ("habit", str(dep_obj.id)) if getattr(dep_obj, "is_habit", False) else ("task", str(dep_obj.id))
    if dep_node == owner_node:
        raise_tracker_error("DEPENDENCY_CYCLE_DETECTED", "Self dependency is not allowed.")
    if _has_path(dep_node, owner_node):
        raise_tracker_error("DEPENDENCY_CYCLE_DETECTED", "Circular dependency detected.")


@transaction.atomic
def set_dependencies(owner_entity, dependencies_payload, user):
    owner_filter = _edge_owner_filter(owner_entity)
    TrackerDependency.objects.filter(**owner_filter, is_deleted=False).update(is_deleted=True, updated_at=timezone.now())

    for item in dependencies_payload:
        dep_type = item["type"]
        dep_id = item["id"]
        dep_obj = _load_target(user, dep_type, dep_id)

        _ensure_no_cycle(owner_entity, dep_obj)

        edge_data = {
            **owner_filter,
            "is_deleted": False,
        }
        if dep_type == "task":
            edge_data["depends_on_task"] = dep_obj
        else:
            edge_data["depends_on_habit"] = dep_obj

        TrackerDependency.objects.create(**edge_data)


def get_dependencies(owner_entity):
    owner_filter = _edge_owner_filter(owner_entity)
    qs = TrackerDependency.objects.filter(**owner_filter, is_deleted=False).select_related("depends_on_task", "depends_on_habit")
    out = []
    for e in qs:
        if e.depends_on_task_id:
            out.append({"type": "task", "id": str(e.depends_on_task_id), "title": e.depends_on_task.title})
        else:
            out.append({"type": "habit", "id": str(e.depends_on_habit_id), "title": e.depends_on_habit.title})
    return out


def ensure_not_depended_on(entity):
    cond = Q(depends_on_task=entity) if not getattr(entity, "is_habit", False) else Q(depends_on_habit=entity)
    exists = TrackerDependency.objects.filter(cond, is_deleted=False).exists()
    if exists:
        raise_tracker_error("DEPENDENCY_IN_USE", "This item is used as dependency. Remove dependencies first.")


def ensure_dependencies_completed_for_occurrence(entity, scheduled_date):
    owner_filter = _edge_owner_filter(entity)
    edges = TrackerDependency.objects.filter(**owner_filter, is_deleted=False)
    blockers = []

    for e in edges:
        if e.depends_on_task_id:
            ok = TaskOccurrence.objects.filter(
                task_id=e.depends_on_task_id,
                scheduled_date=scheduled_date,
                status="completed",
                is_deleted=False,
            ).exists()
            if not ok:
                blockers.append({"type": "task", "id": str(e.depends_on_task_id), "title": e.depends_on_task.title})
        else:
            ok = TaskOccurrence.objects.filter(
                habit_id=e.depends_on_habit_id,
                scheduled_date=scheduled_date,
                status="completed",
                is_deleted=False,
            ).exists()
            if not ok:
                blockers.append({"type": "habit", "id": str(e.depends_on_habit_id), "title": e.depends_on_habit.title})

    if blockers:
        raise_tracker_error(
            "DEPENDENCY_NOT_COMPLETED",
            "Complete dependencies first.",
            details={"blockers": blockers},
        )
