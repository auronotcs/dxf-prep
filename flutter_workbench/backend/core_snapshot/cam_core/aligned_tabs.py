"""Shared sheet-coordinate retaining bands for a grouped profile operation.

Bands are widened by the cutter diameter before intersecting tool CENTER paths.
Every tab-eligible selected contour observes every band, including curved/diagonal segments;
placing unrelated per-contour centers would not protect a bridge from its neighbor.
The computed plan is attached only to a temporary operation, never to the job.
"""
from dataclasses import replace
import math

from .layout import entity_bounds, entity_perimeter
from .model import Circle, OffsetSide, Polyline, Rectangle

PLAN_KEY = "_computed_aligned_tab_bands"
EPS = 1e-9


def prepare_aligned_tabs(operation, entities):
    if operation.tab_mode != "aligned":
        return operation
    if (operation.entry_mode != "plunge" or operation.lead_in > EPS
            or operation.lead_out > EPS):
        raise ValueError("Aligned tabs require Plunge entry with zero lead-in and lead-out, so entries cannot cut a shared bridge.")
    if operation.remove_tabs_after and operation.tab_cleanup_order != "end_of_operation":
        raise ValueError("Aligned tab removal requires End of operation, not After each part; all profile cuts must finish before shared bridges are removed.")
    if not math.isfinite(operation.tab_spacing) or operation.tab_spacing <= 0:
        raise ValueError("Aligned tabs require a positive finite maximum spacing.")
    axes = [set(), set()]
    for entity in entities:
        if entity is None:
            raise ValueError("Aligned tab operation references missing geometry.")
        side = operation.entity_sides.get(entity.id, operation.side.value)
        # Match generate_closed_router_offset's female-tab threshold exactly.
        # Small inside cutouts have no retaining tabs, so neither constrain nor
        # seed the shared outside holding bands (including circular holes).
        if side == OffsetSide.INSIDE.value and (
            isinstance(entity, (Circle, Rectangle))
            or isinstance(entity, Polyline) and entity.closed
        ):
            if entity_perimeter(entity) + EPS < operation.female_tab_min_perimeter:
                continue
            raise ValueError("An inside contour qualifies for retaining tabs at the Female tabs above perimeter setting. Put that tabbed inside cut in a separate operation; small tab-free inside cuts may stay in this aligned operation.")
        if not isinstance(entity, Rectangle) and not (isinstance(entity, Polyline) and entity.closed):
            raise ValueError("Aligned Rows / Columns supports rectangles and closed polygon outlines (including rounded rectangles), not circles or open contours.")
        if side != OffsetSide.OUTSIDE.value:
            raise ValueError("Aligned tabs require Outside cuts on tabbed contours; small tab-free inside cuts may share the operation.")
        bounds = entity_bounds(entity)
        spans = [bounds[2] - bounds[0], bounds[3] - bounds[1]]
        if min(spans) <= EPS or not all(math.isfinite(v) for v in bounds):
            raise ValueError("Aligned tabs require finite, nonzero part dimensions.")
        pairs = max(2, math.ceil(operation.tab_count / 2))
        x_count = max(1, min(pairs - 1, round(pairs * spans[0] / sum(spans))))
        for axis, span in enumerate(spans):
            count = max(math.ceil(span / operation.tab_spacing),
                        x_count if axis == 0 else pairs - x_count)
            if count > 1000:
                raise ValueError("Too many aligned tab bands; increase maximum spacing or reduce tab count.")
            axes[axis].update(bounds[axis] + span * (i + .5) / count for i in range(count))
    if sum(map(len, axes)) > 4000:
        raise ValueError("Too many aligned tab bands in this operation; split the sheet into smaller groups.")
    return replace(operation, recipe_metadata={
        **operation.recipe_metadata, PLAN_KEY: [sorted(axis) for axis in axes],
    })


def aligned_intervals(sequence, operation):
    """Clip the whole contour against the union of shared X/Y bands.

    Unlike ordinary tab pruning, never remove a shared span independently.
    Refuse a pattern covering too much of a contour instead of silently dropping
    its bridges. No geometry/part positions are modified.
    """
    axes = operation.recipe_metadata.get(PLAN_KEY)
    if not axes:
        raise ValueError("Aligned tab plan is missing; regenerate the grouped Outside cut.")
    half = operation.tab_width / 2  # generator already added cutter diameter
    intervals = []
    traveled = 0.0
    for a, b in zip(sequence, sequence[1:]):
        length = a.distance_to(b)
        if length <= EPS:
            continue
        for axis, centers in enumerate(axes):
            start, end = (a.x, b.x) if axis == 0 else (a.y, b.y)
            for center in centers:
                low, high = center - half, center + half
                if abs(end - start) <= EPS:
                    if low <= start <= high:
                        intervals.append((traveled, traveled + length))
                else:
                    t0, t1 = sorted(((low - start) / (end - start), (high - start) / (end - start)))
                    t0, t1 = max(0.0, t0), min(1.0, t1)
                    if t1 > t0 + EPS:
                        intervals.append((traveled + t0 * length, traveled + t1 * length))
        traveled += length
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1] + EPS:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    covered = sum(b - a for a, b in merged)
    if not merged or covered >= traveled * .5:
        raise ValueError("Aligned tab bands cover too much of a part (or miss it). Reduce tab width/count, increase spacing, or use a more regular row/column group.")
    longest = max(b - a for a, b in merged)
    if len(merged) > 1 and merged[0][0] <= EPS and merged[-1][1] >= traveled - EPS:
        longest = max(longest, merged[0][1] + traveled - merged[-1][0])
    if longest > operation.tab_width * 4:
        raise ValueError("An aligned holding band runs along a part edge instead of crossing it. Use a more regular row/column group or change the tab count/spacing.")
    return merged
