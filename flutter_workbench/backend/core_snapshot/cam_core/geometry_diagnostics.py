"""Non-destructive geometry diagnostics with locations suitable for canvas markers."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

from .model import Circle, Entity, Line, Point, Polyline, Rectangle


@dataclass(frozen=True)
class GeometryDiagnostic:
    severity: str
    code: str
    message: str
    point: Point
    entity_ids: tuple[str, ...]
    segment_indices: tuple[int, ...] = ()


@dataclass(frozen=True)
class GeometryDiagnosticOptions:
    gap_min: float = 0.0005
    gap_max: float = 0.03
    short_segment: float = 0.005
    spike_angle_degrees: float = 12.0


@dataclass(frozen=True)
class RepeatedTraversalRepair:
    """One closed polyline whose identical repeated traversal was collapsed."""

    entity_id: str
    original_point_count: int
    repaired_point_count: int
    traversal_count: int


def repeated_closed_polyline_period(
    entity: Polyline,
    tolerance: float = 1e-6,
) -> int | None:
    """Find an exact repeated point-sequence period in a closed polyline.

    This intentionally recognizes only the narrow corruption where an entire
    closed loop is serialized two or more times in the same direction and from
    the same start point.  Similar-looking or merely overlapping artwork is not
    changed.
    """

    points = entity.points
    count = len(points)
    if not entity.closed or count < 6 or tolerance < 0:
        return None
    for period in range(3, count // 2 + 1):
        if count % period:
            continue
        if all(
            abs(points[index].x - points[index % period].x) <= tolerance
            and abs(points[index].y - points[index % period].y) <= tolerance
            for index in range(period, count)
        ):
            return period
    return None


def repair_repeated_closed_polyline_traversals(
    entities: Iterable[Entity],
    tolerance: float = 1e-6,
) -> list[RepeatedTraversalRepair]:
    """Collapse only exact, redundant full-loop repeats in closed polylines."""

    repairs: list[RepeatedTraversalRepair] = []
    for entity in entities:
        if not isinstance(entity, Polyline):
            continue
        period = repeated_closed_polyline_period(entity, tolerance)
        if period is None:
            continue
        original_count = len(entity.points)
        traversal_count = original_count // period
        entity.points = list(entity.points[:period])
        entity.metadata["geometry_repair"] = "collapsed_repeated_closed_traversal"
        entity.metadata["geometry_repair_traversal_count"] = traversal_count
        entity.metadata["geometry_repair_original_point_count"] = original_count
        repairs.append(RepeatedTraversalRepair(
            entity.id,
            original_count,
            period,
            traversal_count,
        ))
    return repairs


def diagnose_geometry(
    entities: Iterable[Entity],
    options: GeometryDiagnosticOptions | None = None,
) -> list[GeometryDiagnostic]:
    options = options or GeometryDiagnosticOptions()
    if options.gap_min < 0 or options.gap_max < options.gap_min:
        raise ValueError("Geometry gap limits must be nonnegative and maximum must not be below minimum.")
    if options.short_segment < 0 or not 0 < options.spike_angle_degrees < 180:
        raise ValueError("Short-segment tolerance must be nonnegative and spike angle must be between 0 and 180 degrees.")

    entity_list = list(entities)
    issues: list[GeometryDiagnostic] = []
    open_endpoints: list[tuple[str, int, Point]] = []
    all_segments: list[tuple[str, int, Point, Point]] = []

    for entity in entity_list:
        if isinstance(entity, Circle):
            if entity.radius <= options.gap_min:
                issues.append(GeometryDiagnostic(
                    "error", "geometry.degenerate_circle", "Circle has zero or negligible radius.",
                    entity.center, (entity.id,),
                ))
            continue
        if isinstance(entity, Rectangle):
            width = abs(entity.corner_b.x - entity.corner_a.x)
            height = abs(entity.corner_b.y - entity.corner_a.y)
            if width <= options.gap_min or height <= options.gap_min:
                issues.append(GeometryDiagnostic(
                    "error", "geometry.degenerate_rectangle", "Rectangle has zero or negligible width or height.",
                    Point((entity.corner_a.x + entity.corner_b.x) / 2, (entity.corner_a.y + entity.corner_b.y) / 2),
                    (entity.id,),
                ))
            continue

        points: list[Point]
        closed = False
        if isinstance(entity, Line):
            points = [entity.start, entity.end]
            open_endpoints.extend(((entity.id, 0, entity.start), (entity.id, 1, entity.end)))
        elif isinstance(entity, Polyline):
            points = entity.points
            closed = entity.closed
            if not closed and points:
                open_endpoints.extend(((entity.id, 0, points[0]), (entity.id, max(0, len(points) - 1), points[-1])))
            if len(points) < 2:
                point = points[0] if points else Point(0.0, 0.0)
                issues.append(GeometryDiagnostic(
                    "error", "geometry.insufficient_points", "Polyline has fewer than two points.", point, (entity.id,),
                ))
                continue
        else:
            continue

        segments = _segments(points, closed)
        all_segments.extend((entity.id, index, first, second) for index, first, second in segments)
        for index, first, second in segments:
            length = first.distance_to(second)
            midpoint = Point((first.x + second.x) / 2, (first.y + second.y) / 2)
            if length <= options.gap_min:
                issues.append(GeometryDiagnostic(
                    "error", "geometry.zero_segment", f"Segment {index + 1} has zero or negligible length.",
                    midpoint, (entity.id,), (index,),
                ))
            elif length < options.short_segment:
                issues.append(GeometryDiagnostic(
                    "warning", "geometry.short_segment", f"Segment {index + 1} is only {length:g} units long.",
                    midpoint, (entity.id,), (index,),
                ))

        if isinstance(entity, Polyline):
            issues.extend(_spike_issues(entity, options))
            issues.extend(_self_intersection_issues(entity, options.gap_min))
            if not entity.closed and len(entity.points) > 2:
                gap = entity.points[0].distance_to(entity.points[-1])
                if options.gap_min < gap <= options.gap_max:
                    issues.append(GeometryDiagnostic(
                        "warning", "geometry.near_closed", f"Open contour endpoints are {gap:g} units apart.",
                        _midpoint(entity.points[0], entity.points[-1]), (entity.id,), (0, len(entity.points) - 1),
                    ))

    issues.extend(_endpoint_gap_issues(open_endpoints, options))
    issues.extend(_duplicate_segment_issues(all_segments, max(options.gap_min, 1e-9)))
    return sorted(issues, key=lambda item: ({"error": 0, "warning": 1}.get(item.severity, 2), item.code, item.entity_ids, item.segment_indices))


def _segments(points: list[Point], closed: bool) -> list[tuple[int, Point, Point]]:
    pairs = list(zip(points, points[1:]))
    if closed and len(points) > 2:
        pairs.append((points[-1], points[0]))
    return [(index, first, second) for index, (first, second) in enumerate(pairs)]


def _spike_issues(entity: Polyline, options: GeometryDiagnosticOptions) -> list[GeometryDiagnostic]:
    points = entity.points
    if len(points) < 3:
        return []
    indexes = range(len(points)) if entity.closed else range(1, len(points) - 1)
    issues = []
    threshold = math.radians(options.spike_angle_degrees)
    for index in indexes:
        previous, current, following = points[index - 1], points[index], points[(index + 1) % len(points)]
        first = Point(previous.x - current.x, previous.y - current.y)
        second = Point(following.x - current.x, following.y - current.y)
        first_length = math.hypot(first.x, first.y)
        second_length = math.hypot(second.x, second.y)
        if first_length <= options.gap_min or second_length <= options.gap_min:
            continue
        cosine = max(-1.0, min(1.0, (first.x * second.x + first.y * second.y) / (first_length * second_length)))
        angle = math.acos(cosine)
        if angle < threshold:
            issues.append(GeometryDiagnostic(
                "warning", "geometry.spike",
                f"Sharp reversal at point {index + 1} forms a {math.degrees(angle):.2f}° spike.",
                current, (entity.id,), (index,),
            ))
    return issues


def _self_intersection_issues(entity: Polyline, tolerance: float) -> list[GeometryDiagnostic]:
    segments = _segments(entity.points, entity.closed)
    # Sweep on minimum X so ordinary production contours do not pay the full
    # all-pairs cost. Highly overlapping segments retain the conservative
    # exact test below.
    ordered = sorted(
        ((index, first, second, _segment_bounds(first, second)) for index, first, second in segments),
        key=lambda item: item[3][0],
    )
    issues = []
    for position, (first_index, first_a, first_b, first_bounds) in enumerate(ordered):
        for second_index, second_a, second_b, second_bounds in ordered[position + 1:]:
            if second_bounds[0] > first_bounds[2] + tolerance:
                break
            if abs(first_index - second_index) <= 1:
                continue
            if entity.closed and {first_index, second_index} == {0, len(segments) - 1}:
                continue
            if not _bounds_overlap(first_bounds, second_bounds, tolerance):
                continue
            intersection = _proper_segment_intersection(first_a, first_b, second_a, second_b, tolerance)
            if intersection is not None:
                issues.append(GeometryDiagnostic(
                    "error", "geometry.self_intersection",
                    f"Contour crosses itself between segments {first_index + 1} and {second_index + 1}.",
                    intersection, (entity.id,), (first_index, second_index),
                ))
    return issues


def _endpoint_gap_issues(endpoints: list[tuple[str, int, Point]], options: GeometryDiagnosticOptions) -> list[GeometryDiagnostic]:
    if not endpoints or options.gap_max <= 0:
        return []
    cell_size = max(options.gap_max, 1e-9)
    cells: dict[tuple[int, int], list[tuple[str, int, Point]]] = {}
    issues = []
    seen: set[tuple[str, int, str, int]] = set()
    for entity_id, endpoint_index, point in endpoints:
        cell = (math.floor(point.x / cell_size), math.floor(point.y / cell_size))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for other_id, other_index, other in cells.get((cell[0] + dx, cell[1] + dy), []):
                    if other_id == entity_id:
                        continue
                    key = min((entity_id, endpoint_index, other_id, other_index), (other_id, other_index, entity_id, endpoint_index))
                    if key in seen:
                        continue
                    gap = point.distance_to(other)
                    if options.gap_min < gap <= options.gap_max:
                        seen.add(key)
                        issues.append(GeometryDiagnostic(
                            "warning", "geometry.endpoint_gap",
                            f"Separate open endpoints are {gap:g} units apart.",
                            _midpoint(point, other), tuple(sorted((entity_id, other_id))), (endpoint_index, other_index),
                        ))
        cells.setdefault(cell, []).append((entity_id, endpoint_index, point))
    return issues


def _duplicate_segment_issues(segments: list[tuple[str, int, Point, Point]], tolerance: float) -> list[GeometryDiagnostic]:
    buckets: dict[tuple[tuple[int, int], tuple[int, int]], tuple[str, int, Point, Point]] = {}
    issues = []
    for entity_id, index, first, second in segments:
        quantized = sorted((
            (round(first.x / tolerance), round(first.y / tolerance)),
            (round(second.x / tolerance), round(second.y / tolerance)),
        ))
        key = (quantized[0], quantized[1])
        previous = buckets.get(key)
        if previous is not None and previous[0] != entity_id:
            issues.append(GeometryDiagnostic(
                "warning", "geometry.duplicate_segment",
                "Two geometry objects contain the same segment.",
                _midpoint(first, second), tuple(sorted((previous[0], entity_id))), (previous[1], index),
            ))
        else:
            buckets[key] = (entity_id, index, first, second)
    return issues


def _proper_segment_intersection(a: Point, b: Point, c: Point, d: Point, tolerance: float) -> Point | None:
    r = Point(b.x - a.x, b.y - a.y)
    s = Point(d.x - c.x, d.y - c.y)
    denominator = r.x * s.y - r.y * s.x
    if abs(denominator) <= tolerance * tolerance:
        return None
    offset = Point(c.x - a.x, c.y - a.y)
    t = (offset.x * s.y - offset.y * s.x) / denominator
    u = (offset.x * r.y - offset.y * r.x) / denominator
    endpoint_margin = tolerance / max(math.hypot(r.x, r.y), math.hypot(s.x, s.y), tolerance)
    if endpoint_margin < t < 1 - endpoint_margin and endpoint_margin < u < 1 - endpoint_margin:
        return Point(a.x + t * r.x, a.y + t * r.y)
    return None


def _midpoint(first: Point, second: Point) -> Point:
    return Point((first.x + second.x) / 2, (first.y + second.y) / 2)


def _segment_bounds(first: Point, second: Point) -> tuple[float, float, float, float]:
    return min(first.x, second.x), min(first.y, second.y), max(first.x, second.x), max(first.y, second.y)


def _bounds_overlap(first, second, tolerance: float) -> bool:
    return not (
        first[2] < second[0] - tolerance or second[2] < first[0] - tolerance
        or first[3] < second[1] - tolerance or second[3] < first[1] - tolerance
    )
