from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import math

from .model import Circle, Point, Polyline


@dataclass(frozen=True)
class CircleFit:
    """Measured circle fit for a closed polygonal contour."""

    center: Point
    radius: float
    maximum_error: float
    rms_error: float


def fit_circular_polyline(
    entity: object,
    *,
    absolute_tolerance: float = 0.001,
    relative_tolerance: float = 0.005,
    minimum_vertices: int = 8,
    minimum_diameter: float | None = None,
    maximum_diameter: float | None = None,
) -> CircleFit | None:
    """Fit a closed polyline to a circle and reject partial or visibly noncircular contours.

    Tolerances are radial.  The accepted limit is the greater of the absolute
    tolerance and ``radius * relative_tolerance``.
    """
    if absolute_tolerance < 0 or relative_tolerance < 0:
        raise ValueError("Circle recognition tolerances cannot be negative.")
    if minimum_vertices < 3:
        raise ValueError("Circle recognition requires at least three vertices.")
    if minimum_diameter is not None and minimum_diameter < 0:
        raise ValueError("Minimum diameter cannot be negative.")
    if maximum_diameter is not None and maximum_diameter <= 0:
        raise ValueError("Maximum diameter must be positive.")
    if minimum_diameter is not None and maximum_diameter is not None and minimum_diameter > maximum_diameter:
        raise ValueError("Minimum diameter cannot exceed maximum diameter.")
    if not isinstance(entity, Polyline) or not entity.closed:
        return None

    points = list(entity.points)
    if len(points) > 1 and points[0].distance_to(points[-1]) <= 1e-12:
        points.pop()
    if len(points) < minimum_vertices:
        return None

    # Center coordinates before the algebraic fit so geometry hundreds of
    # inches from drawing zero remains as stable as geometry near the origin.
    mean_x = sum(point.x for point in points) / len(points)
    mean_y = sum(point.y for point in points) / len(points)
    centered = [(point.x - mean_x, point.y - mean_y) for point in points]
    suu = sum(x * x for x, _ in centered)
    svv = sum(y * y for _, y in centered)
    suv = sum(x * y for x, y in centered)
    squared = [x * x + y * y for x, y in centered]
    right_u = -sum(x * value for (x, _), value in zip(centered, squared))
    right_v = -sum(y * value for (_, y), value in zip(centered, squared))
    determinant = suu * svv - suv * suv
    if abs(determinant) <= 1e-18:
        return None
    coefficient_x = (right_u * svv - suv * right_v) / determinant
    coefficient_y = (suu * right_v - right_u * suv) / determinant
    constant = -sum(squared) / len(squared)
    local_center_x = -coefficient_x / 2.0
    local_center_y = -coefficient_y / 2.0
    center = Point(mean_x + local_center_x, mean_y + local_center_y)
    radius_squared = local_center_x * local_center_x + local_center_y * local_center_y - constant
    if radius_squared <= 1e-18:
        return None
    radius = math.sqrt(radius_squared)
    diameter = radius * 2.0
    if minimum_diameter is not None and diameter < minimum_diameter - 1e-12:
        return None
    if maximum_diameter is not None and diameter > maximum_diameter + 1e-12:
        return None

    deviations = [abs(point.distance_to(center) - radius) for point in points]
    maximum_error = max(deviations)
    rms_error = math.sqrt(sum(error * error for error in deviations) / len(deviations))
    allowed_error = max(absolute_tolerance, radius * relative_tolerance)
    if maximum_error > allowed_error + 1e-12 or rms_error > allowed_error * 0.55 + 1e-12:
        return None

    # A nearly circular arc closed by a chord can fit well enough radially.  Require
    # vertices around the full circumference and reject a large missing sector.
    angles = sorted(math.atan2(point.y - center.y, point.x - center.x) % math.tau for point in points)
    gaps = [angles[index + 1] - angles[index] for index in range(len(angles) - 1)]
    gaps.append(angles[0] + math.tau - angles[-1])
    typical_gap = math.tau / len(angles)
    if max(gaps) > max(math.pi / 2.0, typical_gap * 5.0):
        return None
    return CircleFit(center, radius, maximum_error, rms_error)


def circular_polyline_to_circle(entity: object, **fit_options: object) -> Circle | None:
    """Return an ID/layer-preserving true circle when a polyline passes the fit."""
    fit = fit_circular_polyline(entity, **fit_options)
    if fit is None or not isinstance(entity, Polyline):
        return None
    metadata = dict(entity.metadata)
    metadata.update({
        "recognized_from": "closed_polyline",
        "circle_fit_max_error": fit.maximum_error,
        "circle_fit_rms_error": fit.rms_error,
        "circle_fit_vertex_count": len(entity.points),
    })
    return Circle(id=entity.id, layer=entity.layer, metadata=metadata, appearance=entity.appearance, center=fit.center, radius=fit.radius)


def recognize_circular_polylines(
    entities: Iterable[object],
    *,
    entity_ids: Iterable[str] | None = None,
    layers: Iterable[str] | None = None,
    **fit_options: object,
) -> tuple[list[object], list[Circle]]:
    """Replace qualifying polylines while preserving list order and stable IDs."""
    allowed_ids = None if entity_ids is None else set(entity_ids)
    allowed_layers = None if layers is None else set(layers)
    converted: list[Circle] = []
    output: list[object] = []
    for entity in entities:
        eligible = (
            isinstance(entity, Polyline)
            and (allowed_ids is None or entity.id in allowed_ids)
            and (allowed_layers is None or entity.layer in allowed_layers)
        )
        circle = circular_polyline_to_circle(entity, **fit_options) if eligible else None
        if circle is not None:
            output.append(circle)
            converted.append(circle)
        else:
            output.append(entity)
    return output, converted


def circles_by_diameter(
    entities: Iterable[object],
    target_diameter: float,
    tolerance: float,
    *,
    layers: Iterable[str] | None = None,
    recognize_polylines: bool = True,
) -> list[Circle]:
    """Return diameter candidates only; callers must explicitly confirm them.

    DXF producers do not consistently preserve the native CIRCLE entity. By
    default, essentially circular closed polylines are represented by a
    temporary, ID-preserving :class:`Circle` candidate for selection. The
    source geometry is not converted or otherwise mutated by this query.
    """
    if target_diameter <= 0:
        raise ValueError("Target circle diameter must be positive.")
    if tolerance < 0:
        raise ValueError("Circle diameter tolerance cannot be negative.")
    allowed_layers = None if layers is None else set(layers)
    matches: list[Circle] = []
    for entity in entities:
        if allowed_layers is not None and getattr(entity, "layer", None) not in allowed_layers:
            continue
        candidate = entity if isinstance(entity, Circle) else None
        if candidate is None and recognize_polylines and isinstance(entity, Polyline):
            candidate = circular_polyline_to_circle(entity)
        if candidate is not None and abs(candidate.radius * 2.0 - target_diameter) <= tolerance + 1e-12:
            matches.append(candidate)
    return matches
