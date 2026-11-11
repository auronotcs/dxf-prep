"""Persistent, operator-facing identities for drawing objects.

Entity UUIDs are deliberately opaque.  These helpers give every entity a
short document-level number which survives moves, sorting, nesting, and
save/reopen.  Legacy drawings are numbered once in visual left-to-right order;
new objects are appended so existing identities never change.
"""

from __future__ import annotations

from typing import Iterable

from .layout import entity_bounds
from .model import CamProject, Entity


OBJECT_NUMBER_KEY = "object_number"
OBJECT_CREATED_ORDER_KEY = "object_created_order"
OBJECT_IDENTITY_REGISTRY_KEY = "_object_identity_registry"


def _positive_integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _visual_order_key(indexed_entity: tuple[int, Entity]) -> tuple[float, ...]:
    index, entity = indexed_entity
    bounds = entity_bounds(entity)
    if bounds is None:
        return (float("inf"), float("inf"), float("inf"), float("inf"), index)
    min_x, min_y, max_x, max_y = bounds
    # X first matches the way sign copy is read across adjacent plates.  Bounds
    # and original order provide deterministic tie-breakers for stacked and
    # compound contours.
    return (min_x, min_y, max_x, max_y, index)


def ensure_project_object_identities(project: CamProject) -> tuple[int, int]:
    """Assign missing stable display and creation-order numbers.

    Returns ``(object_numbers_assigned, creation_orders_assigned)``.  Existing
    valid, unique values are preserved.  If a legacy project has no object
    numbers at all, its initial numbers follow visual left-to-right order.  Any
    entities added later receive the next number in project insertion order.
    """

    raw_registry = project.metadata.get(OBJECT_IDENTITY_REGISTRY_KEY, {})
    registry: dict[str, dict[str, int]] = {}
    if isinstance(raw_registry, dict):
        for entity_id, values in raw_registry.items():
            if not isinstance(values, dict):
                continue
            number = _positive_integer(values.get("number"))
            created = _positive_integer(values.get("created"))
            if number is not None or created is not None:
                registry[str(entity_id)] = {
                    **({"number": number} if number is not None else {}),
                    **({"created": created} if created is not None else {}),
                }

    numbered: dict[int, str] = {
        values["number"]: entity_id
        for entity_id, values in registry.items()
        if "number" in values
    }
    missing_numbers: list[tuple[int, Entity]] = []
    assigned_numbers = 0
    for index, entity in enumerate(project.entities):
        registered = _positive_integer(registry.get(entity.id, {}).get("number"))
        if registered is not None and numbered.get(registered) == entity.id:
            if entity.metadata.get(OBJECT_NUMBER_KEY) != registered:
                entity.metadata[OBJECT_NUMBER_KEY] = registered
                assigned_numbers += 1
            continue
        number = _positive_integer(entity.metadata.get(OBJECT_NUMBER_KEY))
        if number is None or (number in numbered and numbered[number] != entity.id):
            missing_numbers.append((index, entity))
        else:
            numbered[number] = entity.id
            registry.setdefault(entity.id, {})["number"] = number

    next_number = max(numbered, default=0) + 1
    if not registry and not numbered:
        candidates = sorted(missing_numbers, key=_visual_order_key)
        next_number = 1
    else:
        candidates = missing_numbers
    for _index, entity in candidates:
        entity.metadata[OBJECT_NUMBER_KEY] = next_number
        registry.setdefault(entity.id, {})["number"] = next_number
        numbered[next_number] = entity.id
        next_number += 1
        assigned_numbers += 1

    created: dict[int, str] = {
        values["created"]: entity_id
        for entity_id, values in registry.items()
        if "created" in values
    }
    missing_created: list[Entity] = []
    assigned_created = 0
    for entity in project.entities:
        registered = _positive_integer(registry.get(entity.id, {}).get("created"))
        if registered is not None and created.get(registered) == entity.id:
            if entity.metadata.get(OBJECT_CREATED_ORDER_KEY) != registered:
                entity.metadata[OBJECT_CREATED_ORDER_KEY] = registered
                assigned_created += 1
            continue
        order = _positive_integer(entity.metadata.get(OBJECT_CREATED_ORDER_KEY))
        if order is None or (order in created and created[order] != entity.id):
            missing_created.append(entity)
        else:
            created[order] = entity.id
            registry.setdefault(entity.id, {})["created"] = order
    next_created = max(created, default=0) + 1
    for entity in missing_created:
        entity.metadata[OBJECT_CREATED_ORDER_KEY] = next_created
        registry.setdefault(entity.id, {})["created"] = next_created
        created[next_created] = entity.id
        next_created += 1
        assigned_created += 1

    project.metadata[OBJECT_IDENTITY_REGISTRY_KEY] = registry
    return assigned_numbers, assigned_created


def project_object_number(project: CamProject, entity_or_id: Entity | str) -> int:
    entity = (
        entity_or_id if isinstance(entity_or_id, Entity)
        else next((item for item in project.entities if item.id == entity_or_id), None)
    )
    if entity is None:
        return 0
    number = _positive_integer(entity.metadata.get(OBJECT_NUMBER_KEY))
    if number is None:
        ensure_project_object_identities(project)
        number = _positive_integer(entity.metadata.get(OBJECT_NUMBER_KEY))
    return number or 0


def project_object_created_order(project: CamProject, entity_or_id: Entity | str) -> int:
    entity = (
        entity_or_id if isinstance(entity_or_id, Entity)
        else next((item for item in project.entities if item.id == entity_or_id), None)
    )
    if entity is None:
        return 0
    order = _positive_integer(entity.metadata.get(OBJECT_CREATED_ORDER_KEY))
    if order is None:
        ensure_project_object_identities(project)
        order = _positive_integer(entity.metadata.get(OBJECT_CREATED_ORDER_KEY))
    return order or 0


def object_identity_label(
    project: CamProject,
    entity_or_id: Entity | str,
    *,
    include_number_with_name: bool = True,
) -> str:
    """Return an explicit name when present, otherwise the stable Object N."""

    entity = (
        entity_or_id if isinstance(entity_or_id, Entity)
        else next((item for item in project.entities if item.id == entity_or_id), None)
    )
    if entity is None:
        return "Unknown object"
    number = project_object_number(project, entity)
    fallback = f"Object {number}" if number else "Unknown object"
    explicit_name = str(entity.metadata.get("name", "")).strip()
    if not explicit_name:
        return fallback
    return f"{explicit_name} ({fallback})" if include_number_with_name and number else explicit_name


def object_numbers(project: CamProject, entities: Iterable[Entity]) -> tuple[int, ...]:
    """Return sorted stable numbers for a related object/group."""

    values_to_number = list(entities)
    ensure_project_object_identities(project)
    values = {
        _positive_integer(entity.metadata.get(OBJECT_NUMBER_KEY)) or 0
        for entity in values_to_number
    }
    return tuple(sorted(value for value in values if value))
