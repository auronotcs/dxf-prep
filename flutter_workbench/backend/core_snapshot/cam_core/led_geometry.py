"""Translate CAM drawing contours into inch-based LED layout regions."""

from __future__ import annotations

import math
from typing import Iterable

from .led_layout import LEDRegion
from .model import Circle, Entity, Point, Polyline, Rectangle
from .topology import profile_containers
from .units import UNIT_TO_INCHES


def _closed_ring(entity: Entity, factor: float, chord_tolerance_in: float) -> tuple[tuple[float, float], ...]:
    if isinstance(entity, Rectangle):
        left, right = sorted((entity.corner_a.x, entity.corner_b.x))
        bottom, top = sorted((entity.corner_a.y, entity.corner_b.y))
        points = (Point(left, bottom), Point(right, bottom), Point(right, top), Point(left, top))
    elif isinstance(entity, Circle):
        radius_in = entity.radius * factor
        if radius_in <= 0:
            raise ValueError(f"Circle {entity.id} has no usable LED region.")
        tolerance = min(chord_tolerance_in, radius_in / 4)
        step = 2 * math.acos(max(-1.0, min(1.0, 1 - tolerance / radius_in)))
        segments = max(24, min(720, math.ceil(math.tau / max(step, 1e-6))))
        points = tuple(
            Point(
                entity.center.x + entity.radius * math.cos(math.tau * index / segments),
                entity.center.y + entity.radius * math.sin(math.tau * index / segments),
            )
            for index in range(segments)
        )
    elif isinstance(entity, Polyline) and entity.closed and len(entity.points) >= 3:
        points = tuple(entity.points)
    else:
        raise ValueError(
            "LED layout requires selected closed circles, rectangles, or contours. "
            "Convert editable text to curves and merge open segments first."
        )
    return tuple((point.x * factor, point.y * factor) for point in points)


def build_led_regions(
    entities: Iterable[Entity],
    units: str,
    *,
    chord_tolerance_in: float = 0.01,
) -> tuple[LEDRegion, ...]:
    """Build one solid region per even-depth contour, with direct holes.

    Region owners use the stable outer-contour entity ID. Coordinates are
    converted to inches before entering the LED layout engine.
    """
    if units not in UNIT_TO_INCHES:
        raise ValueError(f"Unsupported drawing units for LED layout: {units!r}.")
    if not math.isfinite(chord_tolerance_in) or chord_tolerance_in <= 0:
        raise ValueError("LED curve tolerance must be positive.")
    selected = list(entities)
    if not selected:
        raise ValueError("Select one or more closed letter or sign contours first.")
    for entity in selected:
        _closed_ring(entity, UNIT_TO_INCHES[units], chord_tolerance_in)
    containers = profile_containers(selected)
    depths = {entity.id: len(containers[entity.id]) for entity in selected}
    factor = UNIT_TO_INCHES[units]
    regions: list[LEDRegion] = []
    for outer in selected:
        depth = depths[outer.id]
        if depth % 2:
            continue
        holes = tuple(
            _closed_ring(candidate, factor, chord_tolerance_in)
            for candidate in selected
            if depths[candidate.id] == depth + 1 and outer.id in containers[candidate.id]
        )
        regions.append(LEDRegion(
            owner_part_id=outer.id,
            outer=_closed_ring(outer, factor, chord_tolerance_in),
            holes=holes,
        ))
    if not regions:
        raise ValueError("The selection contains holes but no owning outer letter contour.")
    return tuple(regions)


def entity_reference_point_in(entity: Entity, units: str) -> tuple[float, float]:
    """Stable transform reference used to carry owner-linked LED placements."""
    factor = UNIT_TO_INCHES[units]
    if isinstance(entity, Circle):
        point = entity.center
    elif isinstance(entity, Rectangle):
        point = Point(
            (entity.corner_a.x + entity.corner_b.x) / 2,
            (entity.corner_a.y + entity.corner_b.y) / 2,
        )
    elif isinstance(entity, Polyline) and entity.points:
        point = Point(
            sum(item.x for item in entity.points) / len(entity.points),
            sum(item.y for item in entity.points) / len(entity.points),
        )
    else:
        raise ValueError(f"Entity {entity.id} cannot own an LED layout.")
    return point.x * factor, point.y * factor
