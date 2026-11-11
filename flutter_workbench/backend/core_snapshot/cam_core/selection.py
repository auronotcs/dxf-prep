"""Reusable property-based geometry selection helpers."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from .layout import entity_bounds, entity_perimeter
from .model import Circle, Entity, Line, OffsetSide, Polyline, Rectangle
from .topology import classify_profile_sides


SELECTION_ACTIONS = ("replace", "add", "remove", "intersect")
_GEOMETRY_TOLERANCE = 1e-9


def is_closed_entity(entity: Entity) -> bool:
    return isinstance(entity, (Circle, Rectangle)) or isinstance(entity, Polyline) and entity.closed and len(entity.points) >= 3


def _machinable_polyline_points(entity: Polyline) -> list:
    """Apply the same duplicate/collinear cleanup required by CAM offsets."""
    cleaned = []
    for point in entity.points:
        if not cleaned or cleaned[-1].distance_to(point) > _GEOMETRY_TOLERANCE:
            cleaned.append(point)
    if len(cleaned) > 1 and cleaned[0].distance_to(cleaned[-1]) <= _GEOMETRY_TOLERANCE:
        cleaned.pop()
    changed = True
    while changed and len(cleaned) >= 3:
        changed = False
        result = []
        for index, point in enumerate(cleaned):
            previous = cleaned[index - 1]
            following = cleaned[(index + 1) % len(cleaned)]
            first = (point.x - previous.x, point.y - previous.y)
            second = (following.x - point.x, following.y - point.y)
            cross = first[0] * second[1] - first[1] * second[0]
            if abs(cross) <= _GEOMETRY_TOLERANCE and first[0] * second[0] + first[1] * second[1] >= 0:
                changed = True
                continue
            result.append(point)
        cleaned = result
    return cleaned


def is_machinable_entity(entity: Entity) -> bool:
    """Return whether an entity retains enough geometry to create a toolpath.

    Some DXF writers emit microscopic three-segment loops whose vertices are
    effectively collinear.  They render as a dot, but a broad selection macro
    must not present them to closed-profile CAM as legitimate contours.
    """
    if entity.metadata.get("machinable") is False or entity.metadata.get("reference_only") or entity.metadata.get("role") == "reference_only":
        return False
    if isinstance(entity, Line):
        return entity.start.distance_to(entity.end) > _GEOMETRY_TOLERANCE
    if isinstance(entity, Circle):
        return entity.radius > _GEOMETRY_TOLERANCE
    if isinstance(entity, Rectangle):
        return (
            abs(entity.corner_b.x - entity.corner_a.x) > _GEOMETRY_TOLERANCE
            and abs(entity.corner_b.y - entity.corner_a.y) > _GEOMETRY_TOLERANCE
        )
    if isinstance(entity, Polyline):
        cleaned = _machinable_polyline_points(entity)
        return len(cleaned) >= (3 if entity.closed else 2)
    return False


def hole_entity_ids(entities: Sequence[Entity]) -> set[str]:
    """Return closed contours at odd containment depth (inside/female profiles)."""
    closed = [entity for entity in entities if is_closed_entity(entity)]
    if not closed:
        return set()
    return {
        entity_id for entity_id, side in classify_profile_sides(closed).items()
        if side == OffsetSide.INSIDE
    }


def filter_entities(
    entities: Sequence[Entity], *, kinds: Iterable[str] | None = None,
    layers: Iterable[str] | None = None, closed: bool | None = None,
    holes_only: bool = False, perimeter_min: float | None = None,
    perimeter_max: float | None = None, width_min: float | None = None,
    width_max: float | None = None, height_min: float | None = None,
    height_max: float | None = None,
) -> list[Entity]:
    """Filter entities without changing project or selection state."""
    kind_set = {value.casefold() for value in kinds or () if value}
    layer_set = {value.casefold() for value in layers or () if value}
    hole_ids = hole_entity_ids(entities) if holes_only else set()
    matches: list[Entity] = []
    for entity in entities:
        if kind_set and entity.kind.casefold() not in kind_set:
            continue
        if layer_set and entity.layer.casefold() not in layer_set:
            continue
        if closed is not None and is_closed_entity(entity) is not closed:
            continue
        if holes_only and entity.id not in hole_ids:
            continue
        perimeter = entity_perimeter(entity)
        if perimeter_min is not None and perimeter < perimeter_min:
            continue
        if perimeter_max is not None and perimeter > perimeter_max:
            continue
        bounds = entity_bounds(entity)
        if bounds is None:
            continue
        left, bottom, right, top = bounds
        width, height = right - left, top - bottom
        if width_min is not None and width < width_min:
            continue
        if width_max is not None and width > width_max:
            continue
        if height_min is not None and height < height_min:
            continue
        if height_max is not None and height > height_max:
            continue
        matches.append(entity)
    return matches


def combine_selection(current: set[str], matches: Iterable[str], action: str) -> set[str]:
    match_set = set(matches)
    if action == "replace":
        return match_set
    if action == "add":
        return current | match_set
    if action == "remove":
        return current - match_set
    if action == "intersect":
        return current & match_set
    raise ValueError(f"Unsupported selection action: {action!r}")
