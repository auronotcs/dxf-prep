"""Independent, controller-neutral verification of generated CAM motion.

The verifier intentionally consumes the finished motion stream and the source
model instead of calling any path-planning helper.  This gives preflight a
second implementation with which to catch planner regressions.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from ._vendor import pyclipper
from .model import CamProject, Circle, IslandFillOperation, Point, Polyline, Rectangle
from .toolpath import GeneratedToolpath, Motion, MotionKind, ToolPoint


TOLERANCE = 1e-7
_SCALE = 1_000_000.0


@dataclass(frozen=True)
class MotionVerificationIssue:
    severity: str
    code: str
    message: str
    entity_ids: tuple[str, ...] = ()
    point: Point | None = None


def verify_generated_motion(
    project: CamProject,
    operation,
    generated: GeneratedToolpath,
) -> tuple[MotionVerificationIssue, ...]:
    from .calculation_cache import reuse_motion_audit
    return reuse_motion_audit(project, operation, generated,
        lambda: _verify_generated_motion_uncached(project, operation, generated))


def _verify_generated_motion_uncached(project, operation, generated):
    """Audit one generated operation without mutating the job.

    Generic motion checks apply to every operation.  Island Fill additionally
    receives an independent final-depth swept-area comparison because missing
    pocket motion is otherwise difficult to see in a dense preview.
    """
    from .depth_intent import resolve_fill_depth
    try:
        operation = resolve_fill_depth(project, operation)
    except (ValueError, TypeError) as exc:
        return (MotionVerificationIssue("error", "motion.depth_intent", str(exc), tuple(operation.entity_ids)),)
    issues: list[MotionVerificationIssue] = []
    tool = project.tool_library.by_station(operation.tool_station)
    tool_radius = tool.diameter / 2.0 if tool is not None else 0.0
    deepest = 0.0
    cutting_motion_count = 0

    for block_index, block in enumerate(generated.blocks, start=1):
        if not block.motions:
            issues.append(MotionVerificationIssue(
                "error", "motion.empty_block",
                f"{operation.name} block {block_index} contains no motion.",
                tuple(operation.entity_ids),
            ))
            continue
        first = block.motions[0]
        last = block.motions[-1]
        linked_from = bool(block.metadata.get("low_retract_from_previous"))
        linked_to = bool(block.metadata.get("low_retract_to_next"))
        start_height = float(block.metadata.get("low_retract_height", operation.safe_z)) if linked_from else operation.safe_z
        end_height = float(block.metadata.get("low_retract_height", operation.safe_z)) if linked_to else operation.safe_z
        if first.kind is not MotionKind.RAPID or first.end.z < start_height - TOLERANCE:
            issues.append(MotionVerificationIssue(
                "error", "motion.unsafe_start",
                f"{operation.name} block {block_index} does not begin at its required {start_height:g} travel height.",
                _block_entity_ids(block, operation), Point(first.end.x, first.end.y),
            ))
        if last.kind is not MotionKind.RAPID or last.end.z < end_height - TOLERANCE:
            issues.append(MotionVerificationIssue(
                "error", "motion.unsafe_end",
                f"{operation.name} block {block_index} does not finish at its required {end_height:g} travel height.",
                _block_entity_ids(block, operation), Point(last.end.x, last.end.y),
            ))
        if linked_from and (block_index == 1 or not generated.blocks[block_index - 2].metadata.get("low_retract_to_next")):
            issues.append(MotionVerificationIssue(
                "error", "motion.low_link_orphan",
                f"{operation.name} block {block_index} claims a low-retract entry without a linked preceding block.",
                _block_entity_ids(block, operation), Point(first.end.x, first.end.y),
            ))
        if linked_to and (block_index == len(generated.blocks) or not generated.blocks[block_index].metadata.get("low_retract_from_previous")):
            issues.append(MotionVerificationIssue(
                "error", "motion.low_link_orphan",
                f"{operation.name} block {block_index} claims a low-retract exit without a linked following block.",
                _block_entity_ids(block, operation), Point(last.end.x, last.end.y),
            ))
        previous: ToolPoint | None = None
        for motion_index, motion in enumerate(block.motions, start=1):
            point = motion.end
            values = (point.x, point.y, point.z)
            if not all(math.isfinite(value) for value in values):
                issues.append(MotionVerificationIssue(
                    "error", "motion.non_finite",
                    f"{operation.name} block {block_index}, motion {motion_index} contains a non-finite coordinate.",
                    _block_entity_ids(block, operation),
                ))
                previous = point
                continue
            deepest = max(deepest, -point.z)
            if motion.kind is MotionKind.RAPID and point.z < -TOLERANCE:
                issues.append(MotionVerificationIssue(
                    "error", "motion.rapid_below_surface",
                    f"{operation.name} block {block_index}, motion {motion_index} rapids below the stock surface.",
                    _block_entity_ids(block, operation), Point(point.x, point.y),
                ))
            if motion.kind not in {MotionKind.RAPID, MotionKind.DWELL}:
                cutting_motion_count += 1
                if motion.feed is None or not math.isfinite(motion.feed) or motion.feed <= 0:
                    issues.append(MotionVerificationIssue(
                        "error", "motion.invalid_feed",
                        f"{operation.name} block {block_index}, motion {motion_index} has no positive finite feed.",
                        _block_entity_ids(block, operation), Point(point.x, point.y),
                    ))
            if motion.kind in {MotionKind.ARC_CW, MotionKind.ARC_CCW}:
                _verify_arc(operation, block, motion, previous, block_index, motion_index, issues)
            previous = point

    if cutting_motion_count == 0:
        issues.append(MotionVerificationIssue(
            "error", "motion.no_cutting_motion",
            f"{operation.name} emits no feed-controlled cutting motion.", tuple(operation.entity_ids),
        ))
    if deepest > operation.target_depth + max(TOLERANCE, operation.target_depth * 1e-6):
        issues.append(MotionVerificationIssue(
            "error", "motion.depth_exceeded",
            f"{operation.name} reaches {deepest:g}, deeper than its {operation.target_depth:g} target.",
            tuple(operation.entity_ids),
        ))
    elif cutting_motion_count and deepest < operation.target_depth - max(TOLERANCE, operation.target_depth * 1e-6):
        issues.append(MotionVerificationIssue(
            "error", "motion.target_depth_missing",
            f"{operation.name} only reaches {deepest:g}; its target depth is {operation.target_depth:g}.",
            tuple(operation.entity_ids),
        ))

    if isinstance(operation, IslandFillOperation) and tool_radius > TOLERANCE and generated.blocks:
        regions = _fill_regions(project, operation, tool_radius)
        # Read-only, operation-local memo. Prefix checks still request only
        # earlier blocks; cached future blocks can never establish clearance.
        sweep_cache = {}
        issues.extend(_verify_island_fill_sweep(project, operation, generated, tool_radius, regions, sweep_cache))
        issues.extend(_verify_stay_down_links(project, operation, generated, tool_radius, regions))
        issues.extend(_verify_speed_center_prefix(operation, generated, tool_radius, sweep_cache))
    from .cleared_travel import verify_cleared_lead_returns
    issues.extend(verify_cleared_lead_returns(project, operation, generated))
    return tuple(_deduplicate(issues))


def _verify_speed_center_prefix(operation, generated, radius, sweep_cache=None):
    """Prove full-depth center cleanup using earlier actual motions only.

    A planner's residual_safe marker is not evidence. Reconstruct the swept
    material at this depth and check both remaining engagement and every
    full-depth re-entry against previously machined centerline segments.
    """
    issues = []
    prior = []
    depth = operation.target_depth
    segment_cache = {}
    for block in generated.blocks:
        if block.metadata.get('role') != 'speed_center_cleanup':
            prior.append(block)
            continue
        region = block.metadata.get('outer_entity_id')
        relevant = [b for b in prior if b.metadata.get('outer_entity_id') == region]
        cleared = _full_depth_sweep(GeneratedToolpath(relevant), depth, radius, sweep_cache)
        center = _full_depth_sweep(GeneratedToolpath([block]), depth, radius, sweep_cache)
        uncleared = _difference(center, cleared)
        if uncleared and _offset(uncleared, -operation.speed_max_full_depth_engagement / 2):
            issues.append(MotionVerificationIssue(
                'error', 'motion.speed_center_engagement',
                f'{operation.name} full-depth center cleanup exceeds its remaining-material limit.',
                _block_entity_ids(block, operation), _paths_focus(uncleared)))
        segments = []
        for previous_block in relevant:
            key = id(previous_block)
            if key not in segment_cache:
                local = []
                for a, motion in zip(previous_block.motions, previous_block.motions[1:]):
                    if motion.kind in {MotionKind.RAPID, MotionKind.DWELL}: continue
                    points = _flatten_motion(a.end, motion)
                    local.extend((start, end) for start, end in zip(points, points[1:])
                                 if max(start.z, end.z) <= -depth + TOLERANCE)
                segment_cache[key] = local
            segments.extend(segment_cache[key])
        def previously_cut(point):
            for start, end in segments:
                dx, dy = end.x-start.x, end.y-start.y
                length2 = dx*dx+dy*dy
                fraction = max(0., min(1., ((point.x-start.x)*dx+(point.y-start.y)*dy)/length2)) if length2 else 0.
                if math.hypot(point.x-start.x-fraction*dx, point.y-start.y-fraction*dy) <= 2e-6:
                    return True
            return False
        for previous, motion in zip(block.motions, block.motions[1:]):
            if previous.end.z >= -TOLERANCE and motion.end.z <= -depth + TOLERANCE:
                if not previously_cut(motion.end):
                    issues.append(MotionVerificationIssue(
                        'error', 'motion.speed_center_entry',
                        f'{operation.name} enters full-depth cleanup away from a previously cleared wall track.',
                        _block_entity_ids(block, operation), Point(motion.end.x, motion.end.y)))
        prior.append(block)
    return tuple(issues)


def _verify_arc(operation, block, motion: Motion, previous: ToolPoint | None, block_index, motion_index, issues) -> None:
    if motion.center is None or previous is None:
        issues.append(MotionVerificationIssue(
            "error", "motion.arc_definition",
            f"{operation.name} block {block_index}, motion {motion_index} has an incomplete arc definition.",
            _block_entity_ids(block, operation), Point(motion.end.x, motion.end.y),
        ))
        return
    start_radius = math.hypot(previous.x - motion.center.x, previous.y - motion.center.y)
    end_radius = math.hypot(motion.end.x - motion.center.x, motion.end.y - motion.center.y)
    if abs(start_radius - end_radius) > max(1e-5, start_radius * 1e-4):
        issues.append(MotionVerificationIssue(
            "error", "motion.arc_radius",
            f"{operation.name} block {block_index}, motion {motion_index} changes arc radius from {start_radius:g} to {end_radius:g}.",
            _block_entity_ids(block, operation), Point(motion.end.x, motion.end.y),
        ))
    configured_pitch = float(getattr(operation, "helix_pitch", 0.0) or 0.0)
    ramp_angle = float(getattr(operation, "helix_ramp_angle", 0.0) or 0.0)
    if previous.z != motion.end.z and configured_pitch > TOLERANCE:
        sweep = _arc_sweep(previous, motion)
        axial_per_revolution = abs(motion.end.z - previous.z) * 2.0 * math.pi / max(abs(sweep), TOLERANCE)
        allowed_pitch = configured_pitch
        if ramp_angle > TOLERANCE:
            angle_pitch = 2.0 * math.pi * start_radius * math.tan(math.radians(ramp_angle))
            allowed_pitch = min(allowed_pitch, angle_pitch)
        if axial_per_revolution > allowed_pitch + max(1e-6, allowed_pitch * 1e-4):
            issues.append(MotionVerificationIssue(
                "error", "motion.helix_pitch",
                f"{operation.name} emits a {axial_per_revolution:g} depth/revolution helix, exceeding its effective {allowed_pitch:g} pitch/angle limit.",
                _block_entity_ids(block, operation), Point(motion.end.x, motion.end.y),
            ))


def _fill_regions(project, operation, tool_radius):
    """Independently rebuild each pocket with only its immediate islands.

    Union(outers) - Union(islands) erases pockets nested inside islands. Assign
    each declared island to its smallest containing outer before subtraction;
    only then union the finished regions. No planner metadata is trusted here.
    """
    entities = {entity.id: entity for entity in project.entities}
    outer_paths = [_entity_path(entities.get(entity_id)) for entity_id in operation.entity_ids]
    island_paths = [_entity_path(entities.get(entity_id)) for entity_id in operation.island_entity_ids]
    if any(not path for path in [*outer_paths, *island_paths]):
        return None
    local_islands = [[] for _ in outer_paths]
    areas = [abs(pyclipper.Area(path)) for path in outer_paths]
    for island in island_paths:
        island_area = abs(pyclipper.Area(island))
        owners = [index for index, outer in enumerate(outer_paths)
                  if areas[index] > island_area and not _difference([island], [outer])]
        if not owners:
            # Malformed roles are rejected by generation; fail closed if a
            # supplied motion artifact bypasses that check.
            return None
        local_islands[min(owners, key=areas.__getitem__)].append(island)
    physical, accessible = [], []
    for outer, islands in zip(outer_paths, local_islands):
        island_region = _union(islands)
        physical.extend(_difference([outer], island_region))
        accessible.extend(_difference(
            _offset([outer], -tool_radius), _offset(island_region, tool_radius),
        ))
    return _union(physical), _union(accessible)


def _verify_island_fill_sweep(project, operation, generated, tool_radius, regions=None, sweep_cache=None):
    regions = _fill_regions(project, operation, tool_radius) if regions is None else regions
    if regions is None:
        return (MotionVerificationIssue(
            "error", "motion.fill_region_invalid",
            f"{operation.name} has invalid or uncontained fill boundary/island geometry.",
            tuple([*operation.entity_ids, *operation.island_entity_ids]),
        ),)
    physical_region, accessible_centers = regions
    # A round cutter cannot occupy the mathematically sharp corners of a
    # pocket without crossing its boundary.  Compare against the independently
    # reconstructed *machinable* envelope: erode to legal cutter centers,
    # remove expanded islands, then dilate the legal centers by the tool.
    intended = _intersection(_offset(accessible_centers, tool_radius), physical_region)
    swept = _full_depth_sweep(generated, operation.target_depth, tool_radius, sweep_cache)
    if not intended or not swept:
        return (MotionVerificationIssue(
            "error", "motion.fill_sweep_missing",
            f"{operation.name} has no independently verifiable cutter sweep at final depth.",
            tuple(operation.entity_ids),
        ),)
    missing = _difference(intended, swept)
    # The eroded/dilated envelope estimates required coverage. Its round
    # offset approximation can omit legal material near pocket corners,
    # especially for small cutters. Overcut must instead cross the physical
    # pocket boundary or enter a protected island. Those source boundaries
    # are reconstructed independently above; no planner metadata is trusted.
    excess = _difference(swept, physical_region)
    intended_area = abs(sum(pyclipper.Area(path) for path in intended)) / (_SCALE * _SCALE)
    missing_area = abs(sum(pyclipper.Area(path) for path in missing)) / (_SCALE * _SCALE)
    excess_area = abs(sum(pyclipper.Area(path) for path in excess)) / (_SCALE * _SCALE)
    # Polygon stroking and circular source approximation leave tiny numerical
    # fringes.  The allowance is deliberately much smaller than one cutter
    # footprint, so a dropped loop still fails loudly.
    area_allowance = max(math.pi * tool_radius * tool_radius * 0.08, intended_area * 0.001)
    result = []
    if missing_area > area_allowance:
        result.append(MotionVerificationIssue(
            "error", "motion.fill_undercoverage",
            f"{operation.name} leaves about {missing_area:g} square project units of its intended pocket unswept at final depth.",
            tuple(operation.entity_ids), _paths_focus(missing),
        ))
    if excess_area > area_allowance:
        result.append(MotionVerificationIssue(
            "error", "motion.fill_overcut",
            f"{operation.name} sweeps about {excess_area:g} square project units outside its intended fill region.",
            tuple([*operation.entity_ids, *operation.island_entity_ids]), _paths_focus(excess),
        ))
    return tuple(result)


def _verify_stay_down_links(project, operation, generated, tool_radius, regions=None):
    if not operation.keep_tool_down and operation.cut_direction != "bidirectional":
        return ()
    cutter_diameter = tool_radius * 2.0
    maximum_distance = operation.stay_down_max_distance
    maximum_engagement = operation.stay_down_max_engagement
    if operation.cut_direction == "bidirectional" and not operation.keep_tool_down:
        maximum_distance = max(maximum_distance, cutter_diameter)
        maximum_engagement = max(
            maximum_engagement,
            min(operation.stepover or tool_radius, cutter_diameter),
        )
    regions = _fill_regions(project, operation, tool_radius) if regions is None else regions
    if regions is None:
        return ()  # The full-depth sweep check reports the invalid region.
    _physical, accessible = regions
    accessible = _ContainmentPaths(accessible)
    result = []
    for block in generated.blocks:
        prefix_sweep = _PrefixSweep(block.motions, tool_radius)
        for link in block.metadata.get("stay_down_links", ()):
            try:
                motion_index = int(link["motion_index"])
                motion = block.motions[motion_index]
                start = ToolPoint(float(link["start"][0]), float(link["start"][1]), -float(link["depth"]))
                expected_end = Point(float(link["end"][0]), float(link["end"][1]))
                depth = float(link["depth"])
                if motion_index <= 0 or not math.isfinite(depth) or depth < 0:
                    raise ValueError("Invalid link position or depth")
            except (IndexError, KeyError, TypeError, ValueError):
                result.append(MotionVerificationIssue(
                    "error", "motion.stay_down_attribution",
                    f"{operation.name} contains an unreadable Keep Tool Down link marker.",
                    _block_entity_ids(block, operation),
                ))
                continue
            end = Point(motion.end.x, motion.end.y)
            if (
                motion.kind is not MotionKind.LINEAR
                or Point(block.motions[motion_index-1].end.x, block.motions[motion_index-1].end.y).distance_to(Point(start.x,start.y)) > 1e-6
                or abs(block.motions[motion_index-1].end.z + depth) > 1e-6
                or end.distance_to(expected_end) > 1e-6
                or abs(motion.end.z + depth) > 1e-6
                or Point(start.x, start.y).distance_to(end) > maximum_distance + TOLERANCE
            ):
                result.append(MotionVerificationIssue(
                    "error", "motion.stay_down_definition",
                    f"{operation.name} has a Keep Tool Down connector that does not match its declared distance/depth.",
                    _block_entity_ids(block, operation), end,
                ))
                continue
            if not _segment_inside_paths(Point(start.x, start.y), end, accessible):
                result.append(MotionVerificationIssue(
                    "error", "motion.stay_down_boundary",
                    f"{operation.name} has a Keep Tool Down connector outside the cutter-accessible pocket.",
                    _block_entity_ids(block, operation), end,
                ))
                continue
            cleared = prefix_sweep.before(motion_index, depth)
            connector = _stroke_segment(start, motion.end, tool_radius)
            uncleared = _difference(_union(connector), cleared)
            if uncleared and _offset(uncleared, -maximum_engagement):
                result.append(MotionVerificationIssue(
                    "error", "motion.stay_down_engagement",
                    f"{operation.name} has a Keep Tool Down connector wider than its {maximum_engagement:g} engagement limit.",
                    _block_entity_ids(block, operation), end,
                ))
    return tuple(result)


class _PrefixSweep:
    """Prior-only swept region, scoped to one block and one active depth.

    Normal link markers advance monotonically at each pass depth. Rebuild on
    a backwards marker or depth change, so unusual ordering cannot reuse later
    motion or clearance at a shallower depth. Memory does not grow with passes.
    """
    def __init__(self, motions, radius):
        self.motions, self.radius = motions, radius
        self.stop = 0
        self.depth = None
        self.paths = []

    def before(self, stop_index, depth):
        if self.depth != depth or stop_index < self.stop:
            self.stop, self.depth, self.paths = 0, depth, []
        strokes = []
        for index in range(max(1, self.stop), stop_index):
            motion = self.motions[index]
            previous = self.motions[index - 1].end
            if motion.kind in {MotionKind.RAPID, MotionKind.DWELL}:
                continue
            # The original test requires both segment ends to reach this
            # depth; retain its exact flattening/threshold semantics.
            if min(previous.z, motion.end.z) > -depth + TOLERANCE:
                continue
            points = _flatten_motion(previous, motion)
            for start, end in zip(points, points[1:]):
                if start.z <= -depth + TOLERANCE and end.z <= -depth + TOLERANCE:
                    strokes.extend(_stroke_segment(start, end, self.radius))
        if strokes:
            self.paths = _union([*self.paths, *_union_strokes(strokes)])
        self.stop = stop_index
        return self.paths


def _sweep_before_index(motions, stop_index, depth, radius):
    strokes = []
    previous = None
    for index, motion in enumerate(motions):
        if index >= stop_index:
            break
        if previous is not None and motion.kind not in {MotionKind.RAPID, MotionKind.DWELL}:
            points = _flatten_motion(previous, motion)
            for start, end in zip(points, points[1:]):
                if start.z <= -depth + TOLERANCE and end.z <= -depth + TOLERANCE:
                    strokes.extend(_stroke_segment(start, end, radius))
        previous = motion.end
    return _union(strokes)


class _ContainmentPaths:
    """Immutable operation-local bounds/signs; exact winding tests are retained."""
    def __init__(self, paths):
        self.records = [(path, (min(x for x,y in path), min(y for x,y in path),
                                max(x for x,y in path), max(y for x,y in path)),
                         1 if pyclipper.Orientation(path) else -1) for path in paths if path]


def _segment_inside_paths(start: Point, end: Point, paths) -> bool:
    if not isinstance(paths, _ContainmentPaths):
        paths = _ContainmentPaths(paths)
    # A sample's rounded coordinate cannot leave the rounded endpoint bounds.
    # Retain original path order, including holes and boundary-first semantics.
    x0,x1 = sorted((round(start.x*_SCALE),round(end.x*_SCALE)))
    y0,y1 = sorted((round(start.y*_SCALE),round(end.y*_SCALE)))
    records = [(p,b,s) for p,b,s in paths.records
               if b[0] <= x1+1 and b[2] >= x0-1 and b[1] <= y1+1 and b[3] >= y0-1]
    distance = start.distance_to(end)
    count = max(4, min(96, math.ceil(distance / 0.01)))
    for index in range(count + 1):
        fraction = index / count
        point = Point(start.x + (end.x - start.x) * fraction, start.y + (end.y - start.y) * fraction)
        location = (round(point.x * _SCALE), round(point.y * _SCALE))
        winding = 0
        for path, bounds, sign in records:
            if not (bounds[0] <= location[0] <= bounds[2] and bounds[1] <= location[1] <= bounds[3]):
                continue
            relation = pyclipper.PointInPolygon(location, path)
            if relation == -1:
                winding = 1
                break
            if relation:
                winding += sign
        if winding <= 0:
            return False
    return True


def _full_depth_sweep(generated, target_depth, radius, _cache=None):
    regions = []
    for block in generated.blocks:
        key = (id(block), target_depth, radius)
        if _cache is not None and key in _cache and _cache[key][0] is block:
            regions.extend(_cache[key][1])
            continue
        strokes = []
        previous: ToolPoint | None = None
        for motion in block.motions:
            if (previous is not None and motion.kind not in {MotionKind.RAPID, MotionKind.DWELL}
                    and min(previous.z, motion.end.z) <= -target_depth + TOLERANCE):
                points = _flatten_motion(previous, motion)
                for start, end in zip(points, points[1:]):
                    if start.z <= -target_depth + TOLERANCE and end.z <= -target_depth + TOLERANCE:
                        strokes.extend(_stroke_segment(start, end, radius))
            if motion.end.z <= -target_depth + TOLERANCE and motion.kind not in {MotionKind.RAPID, MotionKind.DWELL}:
                strokes.append(_circle_path(Point(motion.end.x, motion.end.y), radius, 32))
            previous = motion.end
        # Union locally before combining regions: the monolithic union of
        # every overlapping stroke/end cap scales poorly on dense artwork.
        local = _union_strokes(strokes)
        if _cache is not None:
            _cache[key] = (block, local)
        regions.extend(local)
    return _union(regions)


def _union_strokes(paths):
    """Union positive cutter footprints in bounded, consecutive batches.

    Raw capsule/end-cap footprints all have positive winding. Reducing each
    batch before the final union avoids the huge intersection graph made by
    thousands of densely overlapping strokes. No motion, footprint, arc
    sample, or verification tolerance is omitted or simplified. Do not apply
    this grouping to arbitrary signed polygons: a hole must stay with its
    owning outer region. Unknown winding takes the original exact path.
    """
    if len(paths) <= 128 or any(not pyclipper.Orientation(p) for p in paths):
        return _union(paths)
    reduced = []
    for index in range(0, len(paths), 128):
        reduced.extend(_union(paths[index:index+128]))
    return _union(reduced)


def _flatten_motion(start: ToolPoint, motion: Motion) -> list[ToolPoint]:
    if motion.kind not in {MotionKind.ARC_CW, MotionKind.ARC_CCW} or motion.center is None:
        return [start, motion.end]
    sweep = _arc_sweep(start, motion)
    radius = math.hypot(start.x - motion.center.x, start.y - motion.center.y)
    count = max(4, math.ceil(abs(sweep) / (math.pi / 36.0)), math.ceil(abs(sweep) * radius / 0.01))
    start_angle = math.atan2(start.y - motion.center.y, start.x - motion.center.x)
    return [
        ToolPoint(
            motion.center.x + radius * math.cos(start_angle + sweep * index / count),
            motion.center.y + radius * math.sin(start_angle + sweep * index / count),
            start.z + (motion.end.z - start.z) * index / count,
        )
        for index in range(count + 1)
    ]


def _arc_sweep(start: ToolPoint, motion: Motion) -> float:
    center = motion.center
    start_angle = math.atan2(start.y - center.y, start.x - center.x)
    end_angle = math.atan2(motion.end.y - center.y, motion.end.x - center.x)
    if motion.kind is MotionKind.ARC_CCW:
        sweep = (end_angle - start_angle) % (2.0 * math.pi)
        return sweep or 2.0 * math.pi
    sweep = -((start_angle - end_angle) % (2.0 * math.pi))
    return sweep or -2.0 * math.pi


def _stroke_segment(start: ToolPoint, end: ToolPoint, radius: float):
    if math.hypot(end.x - start.x, end.y - start.y) <= TOLERANCE:
        return [_circle_path(Point(start.x, start.y), radius, 32)]
    path = [
        (round(start.x * _SCALE), round(start.y * _SCALE)),
        (round(end.x * _SCALE), round(end.y * _SCALE)),
    ]
    offsetter = pyclipper.PyclipperOffset(8.0, 0.01 * _SCALE)
    offsetter.AddPath(path, pyclipper.JT_ROUND, pyclipper.ET_OPENROUND)
    return offsetter.Execute(radius * _SCALE)


def _entity_path(entity):
    if isinstance(entity, Circle):
        return _circle_path(entity.center, entity.radius, max(96, math.ceil(entity.radius * 64)))
    if isinstance(entity, Rectangle):
        points = [
            Point(entity.corner_a.x, entity.corner_a.y), Point(entity.corner_b.x, entity.corner_a.y),
            Point(entity.corner_b.x, entity.corner_b.y), Point(entity.corner_a.x, entity.corner_b.y),
        ]
    elif isinstance(entity, Polyline) and entity.closed:
        points = entity.points
    else:
        return []
    path = [(round(point.x * _SCALE), round(point.y * _SCALE)) for point in points]
    if len(path) >= 3 and not pyclipper.Orientation(path):
        path.reverse()
    return path


def _circle_path(center: Point, radius: float, count: int):
    path = [
        (
            round((center.x + radius * math.cos(2.0 * math.pi * index / count)) * _SCALE),
            round((center.y + radius * math.sin(2.0 * math.pi * index / count)) * _SCALE),
        )
        for index in range(count)
    ]
    if not pyclipper.Orientation(path):
        path.reverse()
    return path


def _union(paths):
    paths = [path for path in paths if len(path) >= 3]
    if not paths:
        return []
    clipper = pyclipper.Pyclipper()
    clipper.AddPaths(paths, pyclipper.PT_SUBJECT, True)
    return clipper.Execute(pyclipper.CT_UNION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)


def _difference(subject, clip):
    if not subject:
        return []
    engine = pyclipper.Pyclipper()
    engine.AddPaths(subject, pyclipper.PT_SUBJECT, True)
    if clip:
        engine.AddPaths(clip, pyclipper.PT_CLIP, True)
    return engine.Execute(pyclipper.CT_DIFFERENCE, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)


def _intersection(subject, clip):
    if not subject or not clip:
        return []
    engine = pyclipper.Pyclipper()
    engine.AddPaths(subject, pyclipper.PT_SUBJECT, True)
    engine.AddPaths(clip, pyclipper.PT_CLIP, True)
    return engine.Execute(pyclipper.CT_INTERSECTION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)


def _offset(paths, amount):
    if not paths:
        return []
    engine = pyclipper.PyclipperOffset(8.0, 0.01 * _SCALE)
    engine.AddPaths(paths, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
    return engine.Execute(amount * _SCALE)


def _paths_focus(paths) -> Point | None:
    points = [(x / _SCALE, y / _SCALE) for path in paths for x, y in path]
    if not points:
        return None
    return Point(sum(x for x, _y in points) / len(points), sum(y for _x, y in points) / len(points))


def _block_entity_ids(block, operation) -> tuple[str, ...]:
    values = [str(value) for value in block.metadata.get("entity_ids", ()) if value]
    if block.metadata.get("entity_id"):
        values.append(str(block.metadata["entity_id"]))
    return tuple(dict.fromkeys(values or operation.entity_ids))


def _deduplicate(issues):
    result = []
    seen = set()
    for issue in issues:
        key = (issue.severity, issue.code, issue.message)
        if key not in seen:
            seen.add(key)
            result.append(issue)
    return result
