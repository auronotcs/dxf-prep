from __future__ import annotations

import math

from .model import (
    Circle, Entity, OffsetSide, Operation, Point, Polyline, Rectangle,
    RouterOffsetOperation,
)


def classify_profile_sides(
    entities: list[Entity],
    outer_side: OffsetSide = OffsetSide.OUTSIDE,
) -> dict[str, OffsetSide]:
    """Classify nested closed contours by containment parity.

    ``outer_side`` is the operator's intent for every top-level contour.
    Each enclosed level uses the opposite compensation side so a female/inside
    letter profile gives its enclosed counter a male/outside toolpath.  The
    default retains the usual male/outside -> female/inside -> male/outside
    profile classification used by selection macros.
    """
    if outer_side == OffsetSide.ON:
        return {entity.id: OffsetSide.ON for entity in entities}
    if outer_side not in {OffsetSide.OUTSIDE, OffsetSide.INSIDE}:
        raise ValueError(f"Unsupported outer profile side: {outer_side!r}.")
    depths = profile_containment_depths(entities)
    alternate_side = (
        OffsetSide.INSIDE if outer_side == OffsetSide.OUTSIDE else OffsetSide.OUTSIDE
    )
    return {
        entity.id: alternate_side if depths[entity.id] % 2 else outer_side
        for entity in entities
    }


def profile_containers(entities: list[Entity]) -> dict[str, list[str]]:
    """Return every containing contour ID for each closed profile.

    The result deliberately describes geometry, not compensation side.  A
    depth-two island can use an outside/male offset and still has to be cut
    before the depth-one letter that contains it.
    """
    profiles = [_profile(entity) for entity in entities]
    return {
        entity.id: [
            other_entity.id
            for other_entity, other_area, other_bounds, _other_sample in profiles
            if other_area > area + 1e-12
            and _bounds_contain(other_bounds, bounds)
            and _contains(other_entity, sample)
        ]
        for entity, area, bounds, sample in profiles
    }


def profile_containment_depths(entities: list[Entity]) -> dict[str, int]:
    """Return nesting depth (zero for a top-level contour) by entity ID."""
    return {
        entity_id: len(container_ids)
        for entity_id, container_ids in profile_containers(entities).items()
    }


def upgrade_legacy_inside_profile_sides(
    entities: list[Entity], operations: list[Operation],
) -> list[str]:
    """Repair grouped female operations saved before containment alternation.

    Older builds explicitly stored ``inside`` for every selected contour in a
    Female / Inside operation. That makes an enclosed letter counter offset
    into the counter instead of outward into the letter material. Only the
    unambiguous legacy signature is upgraded: a multi-contour inside operation
    whose resolved stored sides are all inside and whose geometry actually has
    at least one contained level. Existing mixed assignments are preserved.

    Return the IDs of operations changed so callers may surface migration UI if
    appropriate.
    """
    entity_by_id = {entity.id: entity for entity in entities}
    changed: list[str] = []
    for operation in operations:
        if not isinstance(operation, RouterOffsetOperation) or operation.side != OffsetSide.INSIDE:
            continue
        profiles = [
            entity_by_id[entity_id]
            for entity_id in operation.entity_ids
            if entity_id in entity_by_id and _is_closed_profile(entity_by_id[entity_id])
        ]
        if len(profiles) < 2:
            continue
        stored = {
            operation.entity_sides.get(entity.id, operation.side.value)
            for entity in profiles
        }
        if stored != {OffsetSide.INSIDE.value}:
            continue
        classified = classify_profile_sides(profiles, OffsetSide.INSIDE)
        if OffsetSide.OUTSIDE not in classified.values():
            continue
        operation.entity_sides.update({
            entity_id: side.value for entity_id, side in classified.items()
        })
        if operation.name == "Female / Inside cut":
            outside_count = sum(side == OffsetSide.OUTSIDE for side in classified.values())
            inside_count = len(classified) - outside_count
            operation.name = f"Profile object ({outside_count} male, {inside_count} female) cut"
        changed.append(operation.id)
    return changed


def order_profile_entities(entities: list[Entity], mode: str) -> list[Entity]:
    """Order closed profiles without allowing a containing part to release early.

    ``inside_first`` is retained as the saved-file key for compatibility, but
    its safe meaning is *nested features first*: depth wins over compensation
    side. ``part_by_part`` keeps independent compound parts together, but emits
    every nested child before the contour that contains it. Source order is
    intentionally literal and is reserved for an operator-selected override.
    """
    if mode == "source_order":
        return list(entities)
    if mode not in {"inside_first", "part_by_part"}:
        raise ValueError(f"Unsupported contour order: {mode!r}.")

    source_index = {entity.id: index for index, entity in enumerate(entities)}
    containers = profile_containers(entities)

    if mode == "inside_first":
        # Compensation side alternates with each nesting level and therefore
        # cannot be used as a release-order phase.  A depth-two male opening in
        # a letter must precede the depth-one female letter, while a depth-zero
        # male sheet boundary must be last.
        return sorted(
            entities,
            key=lambda entity: (
                -len(containers[entity.id]),
                source_index[entity.id],
            ),
        )

    roots: list[str] = []
    root_for: dict[str, str] = {}
    for entity in entities:
        possible_roots = [
            container_id for container_id in containers[entity.id]
            if not containers[container_id]
        ]
        root_id = min(possible_roots, key=lambda value: source_index[value]) if possible_roots else entity.id
        root_for[entity.id] = root_id
        if root_id not in roots:
            roots.append(root_id)
    roots.sort(key=lambda value: source_index[value])

    result: list[Entity] = []
    for root_id in roots:
        members = [entity for entity in entities if root_for[entity.id] == root_id]
        members.sort(key=lambda entity: (-len(containers[entity.id]), source_index[entity.id]))
        result.extend(members)
    # A malformed/ambiguous containment sample must never silently discard a
    # contour. Keep any unmatched profile in stable source order.
    emitted = {entity.id for entity in result}
    result.extend(entity for entity in entities if entity.id not in emitted)
    return result


def _profile(entity: Entity) -> tuple[Entity, float, tuple[float, float, float, float], Point]:
    if isinstance(entity, Circle):
        r = entity.radius
        return entity, math.pi * r * r, (entity.center.x - r, entity.center.y - r, entity.center.x + r, entity.center.y + r), entity.center
    if isinstance(entity, Rectangle):
        min_x, max_x = sorted((entity.corner_a.x, entity.corner_b.x))
        min_y, max_y = sorted((entity.corner_a.y, entity.corner_b.y))
        return entity, (max_x - min_x) * (max_y - min_y), (min_x, min_y, max_x, max_y), Point((min_x + max_x) / 2, (min_y + max_y) / 2)
    if isinstance(entity, Polyline) and entity.closed and len(entity.points) >= 3:
        xs = [point.x for point in entity.points]
        ys = [point.y for point in entity.points]
        twice_area = sum(first.x * second.y - second.x * first.y for first, second in zip(entity.points, entity.points[1:] + entity.points[:1]))
        area = abs(twice_area) / 2.0
        sample = _polyline_interior_sample(entity)
        return entity, area, (min(xs), min(ys), max(xs), max(ys)), sample
    raise ValueError("Profile classification requires circles, rectangles, or closed polylines.")


def _is_closed_profile(entity: Entity) -> bool:
    return (
        isinstance(entity, (Circle, Rectangle))
        or isinstance(entity, Polyline) and entity.closed and len(entity.points) >= 3
    )


def _polyline_interior_sample(entity: Polyline) -> Point:
    """Return a point that is actually inside a closed polyline.

    The arithmetic mean of the vertices is not a reliable containment probe:
    it can land in the open bay of a concave letter or in the counter of an
    unevenly segmented glyph.  That previously made a genuinely enclosed
    contour look like another top-level contour and an Inside operation kept
    it female instead of alternating it to male.

    Prefer the polygon centroid when it is interior.  For concave profiles,
    test horizontal bands between vertex Y values and choose the midpoint of
    the widest interior interval.  The latter is deterministic, avoids points
    on vertices, and works without adding a geometry dependency.
    """
    points = entity.points
    signed_twice_area = sum(
        first.x * second.y - second.x * first.y
        for first, second in zip(points, points[1:] + points[:1])
    )
    if abs(signed_twice_area) > 1e-18:
        factor = 1.0 / (3.0 * signed_twice_area)
        centroid = Point(
            sum(
                (first.x + second.x) * (first.x * second.y - second.x * first.y)
                for first, second in zip(points, points[1:] + points[:1])
            ) * factor,
            sum(
                (first.y + second.y) * (first.x * second.y - second.x * first.y)
                for first, second in zip(points, points[1:] + points[:1])
            ) * factor,
        )
        if _contains(entity, centroid):
            return centroid

    y_values = sorted(set(point.y for point in points))
    candidates = [
        (lower + upper) / 2.0
        for lower, upper in zip(y_values, y_values[1:])
        if upper - lower > 1e-12
    ]
    widest: tuple[float, Point] | None = None
    for y in candidates:
        intersections: list[float] = []
        for first, second in zip(points, points[1:] + points[:1]):
            if (first.y > y) == (second.y > y):
                continue
            x = first.x + (y - first.y) * (second.x - first.x) / (second.y - first.y)
            intersections.append(x)
        intersections.sort()
        for left, right in zip(intersections[::2], intersections[1::2]):
            width = right - left
            if width <= 1e-12:
                continue
            sample = Point((left + right) / 2.0, y)
            if _contains(entity, sample) and (widest is None or width > widest[0]):
                widest = (width, sample)
    if widest is not None:
        return widest[1]

    # Degenerate closed profiles are rejected later by offset generation. Keep
    # classification deterministic here so the caller receives that specific
    # machinability error instead of failing while building the side map.
    return Point(sum(point.x for point in points) / len(points), sum(point.y for point in points) / len(points))


def _bounds_contain(outer, inner, tolerance: float = 1e-9) -> bool:
    return outer[0] <= inner[0] + tolerance and outer[1] <= inner[1] + tolerance and outer[2] >= inner[2] - tolerance and outer[3] >= inner[3] - tolerance


def _contains(entity: Entity, point: Point) -> bool:
    if isinstance(entity, Circle):
        return entity.center.distance_to(point) < entity.radius - 1e-9
    if isinstance(entity, Rectangle):
        min_x, max_x = sorted((entity.corner_a.x, entity.corner_b.x))
        min_y, max_y = sorted((entity.corner_a.y, entity.corner_b.y))
        return min_x < point.x < max_x and min_y < point.y < max_y
    if isinstance(entity, Polyline):
        inside = False
        previous = entity.points[-1]
        for current in entity.points:
            if (current.y > point.y) != (previous.y > point.y):
                crossing_x = (previous.x - current.x) * (point.y - current.y) / (previous.y - current.y) + current.x
                if point.x < crossing_x:
                    inside = not inside
            previous = current
        return inside
    return False
