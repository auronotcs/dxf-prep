"""Raster medial guides and manual mounting-hole placement checks.

These guides are geometric references through the local center of letter
strokes.  They are not whole-part bounding-box axes and are never machining
geometry.  The caller supplies the raster detail and spur-pruning tolerance in
project units; neither value is a mounting-spacing or structural rule.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable, Iterable

try:
    import numpy as np
except ImportError:  # pragma: no cover - bundled desktop runtime includes NumPy.
    np = None

from .model import Point
from .mounting import MountingFeatureSpec, MountingPart


@dataclass(frozen=True)
class MountingCenterlineSettings:
    sample_step: float
    spur_prune_length: float
    max_cells: int = 400_000

    def __post_init__(self) -> None:
        if not math.isfinite(self.sample_step) or self.sample_step <= 0:
            raise ValueError("Centerline sample detail must be positive and finite.")
        if not math.isfinite(self.spur_prune_length) or self.spur_prune_length < 0:
            raise ValueError("Centerline spur-pruning length must be finite and nonnegative.")
        if isinstance(self.max_cells, bool) or not isinstance(self.max_cells, int) or self.max_cells < 1_000:
            raise ValueError("Centerline maximum cell count must be an integer of at least 1000.")


@dataclass(frozen=True)
class MountingCenterGuide:
    owner_entity_id: str
    owner_part_id: str
    label: str
    paths: tuple[tuple[Point, ...], ...]
    junctions: tuple[Point, ...]
    endpoints: tuple[Point, ...]
    sample_step: float
    spur_prune_length: float
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class MountingGuideSnap:
    point: Point
    snapped: bool
    path_index: int | None
    segment_index: int | None
    distance: float


def _point_in_loop(point: Point, loop: tuple[Point, ...]) -> bool:
    inside = False
    prior = loop[-1]
    for current in loop:
        if (current.y > point.y) != (prior.y > point.y):
            crossing = (prior.x - current.x) * (point.y - current.y) / (prior.y - current.y) + current.x
            if point.x < crossing:
                inside = not inside
        prior = current
    return inside


def point_in_mounting_part(point: Point, part: MountingPart) -> bool:
    """Even/odd material membership across the part outer loop and counters."""
    return sum(1 for loop in part.loops if _point_in_loop(point, loop)) % 2 == 1


def _distance_to_segment(point: Point, start: Point, end: Point) -> tuple[float, Point]:
    dx, dy = end.x - start.x, end.y - start.y
    length2 = dx * dx + dy * dy
    if length2 <= 1e-24:
        return point.distance_to(start), start
    fraction = max(0.0, min(1.0, ((point.x - start.x) * dx + (point.y - start.y) * dy) / length2))
    projected = Point(start.x + dx * fraction, start.y + dy * fraction)
    return point.distance_to(projected), projected


def _boundary_distance(point: Point, part: MountingPart) -> float:
    return min(
        _distance_to_segment(point, loop[index], loop[(index + 1) % len(loop)])[0]
        for loop in part.loops
        for index in range(len(loop))
    )


def snap_to_mounting_centerline(
    point: Point,
    guide: MountingCenterGuide,
    *,
    enabled: bool,
    temporary_invert: bool = False,
    capture_distance: float,
) -> MountingGuideSnap:
    """Project to the nearest medial-guide segment when controlled snap is active."""
    if not math.isfinite(capture_distance) or capture_distance < 0:
        raise ValueError("Centerline snap capture distance must be finite and nonnegative.")
    effective = bool(enabled) ^ bool(temporary_invert)
    if not effective:
        return MountingGuideSnap(point, False, None, None, float("inf"))
    nearest: tuple[float, Point, int, int] | None = None
    for path_index, path in enumerate(guide.paths):
        for segment_index in range(len(path) - 1):
            distance, projected = _distance_to_segment(point, path[segment_index], path[segment_index + 1])
            candidate = (distance, projected, path_index, segment_index)
            if nearest is None or distance < nearest[0]:
                nearest = candidate
    if nearest is None or nearest[0] > capture_distance:
        return MountingGuideSnap(point, False, None, None, nearest[0] if nearest else float("inf"))
    return MountingGuideSnap(nearest[1], True, nearest[2], nearest[3], nearest[0])


def validate_round_mounting_hole(
    part: MountingPart,
    center: Point,
    diameter: float,
    existing: Iterable[MountingFeatureSpec] = (),
    *,
    extra_clearance: float = 0.0,
) -> None:
    """Require the complete circular footprint to fit material and other holes.

    ``extra_clearance`` is explicit caller input.  Zero means physical
    non-overlap only; this function does not invent a shop clearance.
    """
    if not math.isfinite(diameter) or diameter <= 0:
        raise ValueError("Manual mounting-hole diameter must be positive and finite.")
    if not math.isfinite(extra_clearance) or extra_clearance < 0:
        raise ValueError("Manual mounting-hole clearance must be finite and nonnegative.")
    radius = diameter / 2.0
    if not point_in_mounting_part(center, part):
        raise ValueError("The clicked hole center is outside this letter's material.")
    available = _boundary_distance(center, part)
    if available + 1e-9 < radius + extra_clearance:
        raise ValueError(
            f"The hole footprint needs {radius + extra_clearance:g} units to the letter edge; "
            f"only {available:g} is available here."
        )
    for feature in existing:
        if feature.owner_part_id != part.part_id:
            continue
        if feature.points:
            if _point_in_loop(center, tuple(feature.points)):
                raise ValueError(f"The hole footprint overlaps the reviewed {feature.kind.replace('_', ' ')}.")
            clearance = min(
                _distance_to_segment(center, feature.points[index], feature.points[(index + 1) % len(feature.points)])[0]
                for index in range(len(feature.points))
            )
            if clearance + 1e-9 < radius + extra_clearance:
                raise ValueError(f"The hole footprint overlaps the reviewed {feature.kind.replace('_', ' ')}.")
        elif feature.diameter > 0:
            needed = radius + feature.diameter / 2.0 + extra_clearance
            if center.distance_to(feature.center) + 1e-9 < needed:
                raise ValueError(f"The hole footprint overlaps the reviewed {feature.kind.replace('_', ' ')}.")


def _rasterize_part(
    part: MountingPart,
    settings: MountingCenterlineSettings,
    cancel_check: Callable[[], bool] | None,
) -> tuple[object, float, float, float, tuple[str, ...]]:
    if np is None:
        raise RuntimeError("Local stroke-center generation requires NumPy in the desktop runtime.")
    min_x, min_y, max_x, max_y = part.bounds
    width, height = max_x - min_x, max_y - min_y
    if width <= 0 or height <= 0:
        raise ValueError(f"{part.label}: mounting part has no usable area.")
    step = settings.sample_step
    columns = max(3, math.ceil(width / step))
    rows = max(3, math.ceil(height / step))
    warnings: list[str] = []
    if rows * columns > settings.max_cells:
        scale = math.sqrt(rows * columns / settings.max_cells)
        step *= scale
        columns = max(3, math.ceil(width / step))
        rows = max(3, math.ceil(height / step))
        warnings.append(
            f"{part.label}: guide detail adapted from {settings.sample_step:g} to {step:g} units "
            f"to stay within {settings.max_cells} raster cells."
        )
    mask = np.zeros((rows + 2, columns + 2), dtype=np.bool_)
    for row in range(rows):
        if cancel_check and row % 32 == 0 and cancel_check():
            raise InterruptedError("Mounting centerline generation canceled.")
        y = min_y + (row + 0.5) * step
        crossings: list[float] = []
        for loop in part.loops:
            prior = loop[-1]
            for current in loop:
                if (current.y > y) != (prior.y > y):
                    crossings.append(
                        current.x + (y - current.y) * (prior.x - current.x) / (prior.y - current.y)
                    )
                prior = current
        crossings.sort()
        for left, right in zip(crossings[0::2], crossings[1::2]):
            first = max(0, math.ceil((left - min_x) / step - 0.5))
            last = min(columns - 1, math.floor((right - min_x) / step - 0.5))
            if last >= first:
                mask[row + 1, first + 1:last + 2] = True
    if not mask.any():
        raise ValueError(f"{part.label}: no material remained at the requested centerline detail.")
    return mask, min_x, min_y, step, tuple(warnings)


def _thin(mask, cancel_check: Callable[[], bool] | None):
    work = mask.copy()
    for iteration in range(max(work.shape) * 2):
        if cancel_check and iteration % 4 == 0 and cancel_check():
            raise InterruptedError("Mounting centerline generation canceled.")
        changed = False
        for first_pass in (True, False):
            center = work[1:-1, 1:-1]
            p2, p3, p4 = work[:-2, 1:-1], work[:-2, 2:], work[1:-1, 2:]
            p5, p6, p7 = work[2:, 2:], work[2:, 1:-1], work[2:, :-2]
            p8, p9 = work[1:-1, :-2], work[:-2, :-2]
            neighbors = p2.astype(np.uint8)+p3+p4+p5+p6+p7+p8+p9
            transitions = (
                (~p2 & p3).astype(np.uint8)+(~p3 & p4)+(~p4 & p5)+(~p5 & p6)
                +(~p6 & p7)+(~p7 & p8)+(~p8 & p9)+(~p9 & p2)
            )
            remove = center & (neighbors >= 2) & (neighbors <= 6) & (transitions == 1)
            if first_pass:
                remove &= ~(p2 & p4 & p6) & ~(p4 & p6 & p8)
            else:
                remove &= ~(p2 & p4 & p8) & ~(p2 & p6 & p8)
            if remove.any():
                center[remove] = False
                changed = True
        if not changed:
            break
    return work


def _neighbors(pixel: tuple[int, int], pixels: set[tuple[int, int]]) -> list[tuple[int, int]]:
    row, column = pixel
    result: list[tuple[int, int]] = []
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if not (dr or dc):
                continue
            candidate = row + dr, column + dc
            if candidate not in pixels:
                continue
            # Avoid artificial triangles where a diagonal and its orthogonal
            # bridge pixels all survived thinning.
            if dr and dc and ((row, column + dc) in pixels or (row + dr, column) in pixels):
                continue
            result.append(candidate)
    return result


def _edge_length(first: tuple[int, int], second: tuple[int, int], step: float) -> float:
    return step * math.hypot(first[0] - second[0], first[1] - second[1])


def _prune_spurs(pixels: set[tuple[int, int]], threshold: float, step: float) -> None:
    if threshold <= 0:
        return
    while True:
        removed: set[tuple[int, int]] = set()
        endpoints = [pixel for pixel in pixels if len(_neighbors(pixel, pixels)) == 1]
        for endpoint in endpoints:
            path = [endpoint]
            prior = None
            current = endpoint
            length = 0.0
            while True:
                following = [item for item in _neighbors(current, pixels) if item != prior]
                if len(following) != 1:
                    break
                next_pixel = following[0]
                length += _edge_length(current, next_pixel, step)
                prior, current = current, next_pixel
                if len(_neighbors(current, pixels)) != 2:
                    break
                path.append(current)
            if len(_neighbors(current, pixels)) >= 3 and length < threshold - 1e-9:
                removed.update(path)
        if not removed:
            return
        pixels.difference_update(removed)


def _simplify(points: list[Point], tolerance: float) -> tuple[Point, ...]:
    if len(points) <= 2:
        return tuple(points)
    start, end = points[0], points[-1]
    distances = [_distance_to_segment(point, start, end)[0] for point in points[1:-1]]
    largest = max(distances, default=0.0)
    if largest <= tolerance:
        return start, end
    index = distances.index(largest) + 1
    return (*_simplify(points[:index + 1], tolerance)[:-1], *_simplify(points[index:], tolerance))


def _trace_paths(
    pixels: set[tuple[int, int]], min_x: float, min_y: float, step: float,
) -> tuple[tuple[tuple[Point, ...], ...], tuple[Point, ...], tuple[Point, ...]]:
    def world(pixel: tuple[int, int]) -> Point:
        row, column = pixel
        return Point(min_x + (column - 0.5) * step, min_y + (row - 0.5) * step)

    degree = {pixel: len(_neighbors(pixel, pixels)) for pixel in pixels}
    vertices = {pixel for pixel, value in degree.items() if value != 2}
    visited: set[frozenset[tuple[int, int]]] = set()
    traced: list[tuple[Point, ...]] = []

    def follow(start: tuple[int, int], following: tuple[int, int]) -> None:
        chain = [start]
        prior, current = start, following
        visited.add(frozenset((prior, current)))
        while True:
            chain.append(current)
            if current in vertices:
                break
            choices = [item for item in _neighbors(current, pixels) if item != prior]
            if not choices:
                break
            next_pixel = choices[0]
            edge = frozenset((current, next_pixel))
            if edge in visited:
                break
            visited.add(edge)
            prior, current = current, next_pixel
        if len(chain) >= 2:
            traced.append(_simplify([world(item) for item in chain], step * 0.55))

    for vertex in sorted(vertices):
        for neighbor in _neighbors(vertex, pixels):
            if frozenset((vertex, neighbor)) not in visited:
                follow(vertex, neighbor)
    # Closed medial loops have no degree!=2 vertices.
    for pixel in sorted(pixels):
        for neighbor in _neighbors(pixel, pixels):
            if frozenset((pixel, neighbor)) not in visited:
                follow(pixel, neighbor)
    junctions = tuple(world(pixel) for pixel, value in degree.items() if value >= 3)
    endpoints = tuple(world(pixel) for pixel, value in degree.items() if value == 1)
    return tuple(traced), junctions, endpoints


def generate_mounting_centerline(
    part: MountingPart,
    settings: MountingCenterlineSettings,
    *,
    progress_callback: Callable[[str, float], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> MountingCenterGuide:
    """Generate a local stroke medial skeleton for one material part."""
    if progress_callback:
        progress_callback(f"{part.label}: rasterizing letter material", 0.05)
    mask, min_x, min_y, step, warnings = _rasterize_part(part, settings, cancel_check)
    if progress_callback:
        progress_callback(f"{part.label}: thinning to local stroke centers", 0.35)
    skeleton = _thin(mask, cancel_check)
    pixels = {tuple(int(value) for value in item) for item in np.argwhere(skeleton)}
    if not pixels:
        raise ValueError(f"{part.label}: centerline thinning produced no usable guide.")
    if progress_callback:
        progress_callback(f"{part.label}: resolving branches and pruning short spurs", 0.80)
    _prune_spurs(pixels, settings.spur_prune_length, step)
    paths, junctions, endpoints = _trace_paths(pixels, min_x, min_y, step)
    paths = tuple(path for path in paths if len(path) >= 2)
    if not paths:
        raise ValueError(f"{part.label}: no centerline paths remained after spur pruning.")
    if progress_callback:
        progress_callback(f"{part.label}: center guide ready", 1.0)
    return MountingCenterGuide(
        part.owner_entity_id, part.part_id, part.label, paths, junctions, endpoints,
        step, settings.spur_prune_length, warnings,
    )

