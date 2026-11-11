from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import copy
import math

from ._vendor import pyclipper

from .model import (
    CamProject,
    Circle,
    DrillCirclesOperation,
    EngraveOperation,
    FillStrategy,
    IslandFillOperation,
    Line,
    OffsetSide,
    Point,
    Polyline,
    Rectangle,
    RouterOffsetOperation,
    RestMachiningOperation,
    ProfileRestMachiningOperation,
    effective_tool,
    ThreadMillOperation,
    new_id,
)
from .thread_specs import thread_standard
from .layout import entity_bounds, entity_perimeter
from .operation_links import profile_rest_dependency_status
from .topology import order_profile_entities, profile_containment_depths
from .units import unit_scale
from .micro_backtracks import remove_micro_backtracks, MICRO_BACKTRACK_INCHES


TOLERANCE = 1e-9


def offset_closed_geometry(entity, signed_distance: float):
    """Create editable outline/inline geometry; positive is outside, negative inside."""
    side = OffsetSide.OUTSIDE if signed_distance >= 0 else OffsetSide.INSIDE
    distance = abs(signed_distance)
    metadata = {**entity.metadata, "offset_from": entity.id, "offset_distance": signed_distance}
    if isinstance(entity, Circle):
        center, radius = _offset_circle(entity, distance, side)
        return Circle(id=new_id("entity"), layer=entity.layer, metadata=metadata, appearance=copy.deepcopy(entity.appearance), center=center, radius=radius)
    if isinstance(entity, Rectangle):
        points = _offset_closed_polygon(_rectangle_points(entity), distance, side)
        return Polyline(id=new_id("entity"), layer=entity.layer, metadata=metadata, appearance=copy.deepcopy(entity.appearance), points=points, closed=True)
    if isinstance(entity, Polyline) and entity.closed:
        points = _offset_closed_polygon(entity.points, distance, side)
        return Polyline(id=new_id("entity"), layer=entity.layer, metadata=metadata, appearance=copy.deepcopy(entity.appearance), points=points, closed=True)
    raise ValueError("Outline/inline requires circles, rectangles, or closed polylines.")


class MotionKind(str, Enum):
    RAPID = "rapid"
    LINEAR = "linear"
    ARC_CW = "arc_cw"
    ARC_CCW = "arc_ccw"
    DWELL = "dwell"


@dataclass(frozen=True)
class ToolPoint:
    """Controller-neutral point: +Z is clearance and -Z is cut depth."""

    x: float
    y: float
    z: float


@dataclass(frozen=True)
class Motion:
    kind: MotionKind
    end: ToolPoint
    feed: float | None = None
    center: Point | None = None
    dwell_seconds: float | None = None


@dataclass
class ToolpathBlock:
    operation_id: str
    tool_station: int
    motions: list[Motion]
    label: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class GeneratedToolpath:
    blocks: list[ToolpathBlock] = field(default_factory=list)

    @property
    def motions(self) -> list[Motion]:
        return [motion for block in self.blocks for motion in block.motions]


@dataclass(frozen=True)
class _PathEdge:
    start: ToolPoint
    motion: Motion

    @property
    def end(self) -> ToolPoint:
        return self.motion.end


def apply_curve_feed_schedule(
    blocks: list[ToolpathBlock],
    operation: RouterOffsetOperation,
    cutter_diameter: float,
    nominal_feed: float,
) -> list[ToolpathBlock]:
    """Apply conservative cutter-center feeds to tight wall-contact curves.

    The approved recipe feed remains a hard ceiling.  Clockwise/CCW motion and
    cut direction identify whether the finished wall is toward or away from a
    curve center.  Only the away case needs a reduction:

        centerline feed = wall feed * Rcenter / (Rcenter + cutter radius)

    True arcs use their exact radius.  Segmented curves use a local three-point
    circumradius, then a forward/backward distance pass avoids abrupt F-word
    changes.  Plunges, ramps, helices, rapids, and vertical tab moves are left
    untouched.
    """
    if operation.curve_feed_mode == "off":
        return blocks
    if operation.curve_feed_mode != "wall_compensation":
        raise ValueError("Curve-feed mode must be off or wall compensation.")
    if not 0 < operation.curve_feed_min_percent <= 100:
        raise ValueError("Minimum curve feed must be above 0% and no more than 100%.")
    if operation.curve_feed_transition_distance <= 0:
        raise ValueError("Curve-feed transition distance must be positive.")
    if cutter_diameter <= 0 or nominal_feed <= 0:
        return blocks

    radius = cutter_diameter / 2.0
    floor_feed = nominal_feed * operation.curve_feed_min_percent / 100.0
    output: list[ToolpathBlock] = []
    for block in blocks:
        scheduled, adjusted, minimum = _schedule_block_curve_feeds(
            block, operation.cut_direction, radius, nominal_feed,
            floor_feed, operation.curve_feed_transition_distance,
        )
        if adjusted:
            scheduled.metadata = {
                **scheduled.metadata,
                "curve_feed_mode": operation.curve_feed_mode,
                "curve_feed_adjusted_motions": adjusted,
                "curve_feed_minimum": minimum,
                "curve_feed_recipe_ceiling": nominal_feed,
            }
        output.append(scheduled)
    return output


def _schedule_block_curve_feeds(
    block: ToolpathBlock,
    cut_direction: str,
    cutter_radius: float,
    nominal_feed: float,
    floor_feed: float,
    transition_distance: float,
) -> tuple[ToolpathBlock, int, float]:
    motions = list(block.motions)
    if len(motions) < 3:
        return block, 0, nominal_feed
    starts: list[ToolPoint | None] = []
    previous: ToolPoint | None = None
    for motion in motions:
        starts.append(previous)
        previous = motion.end

    targets: list[float | None] = [None] * len(motions)
    lengths: list[float] = [0.0] * len(motions)
    eligible: list[bool] = [False] * len(motions)
    wall_side = "right" if cut_direction == "climb" else "left"

    for index, motion in enumerate(motions):
        start = starts[index]
        if start is None:
            continue
        lengths[index] = _motion_length(start, motion)
        horizontal_cut = (
            motion.kind in {MotionKind.LINEAR, MotionKind.ARC_CW, MotionKind.ARC_CCW}
            and motion.feed is not None
            and start.z < -TOLERANCE
            and abs(start.z - motion.end.z) <= TOLERANCE
        )
        if not horizontal_cut:
            continue
        eligible[index] = True
        targets[index] = min(nominal_feed, float(motion.feed))
        if motion.kind in {MotionKind.ARC_CW, MotionKind.ARC_CCW} and motion.center is not None:
            center_side = "right" if motion.kind == MotionKind.ARC_CW else "left"
            if center_side != wall_side:
                center_radius = math.hypot(start.x - motion.center.x, start.y - motion.center.y)
                # A block may deliberately carry a lower feed (for example a
                # 205 IPM finish after 350 IPM roughing). Curve compensation
                # may reduce that value, but must never raise it back toward
                # the roughing recipe ceiling.
                targets[index] = min(
                    float(targets[index]),
                    _wall_compensated_feed(center_radius, cutter_radius, nominal_feed, floor_feed),
                )

    # DXF splines frequently arrive as short linear chords.  Estimate local
    # centerline curvature at each all-linear vertex so they receive the same
    # protection as native arcs.
    for index in range(1, len(motions) - 1):
        if not eligible[index] or not eligible[index + 1]:
            continue
        current = motions[index]
        following = motions[index + 1]
        start = starts[index]
        if (
            current.kind != MotionKind.LINEAR or following.kind != MotionKind.LINEAR
            or start is None
        ):
            continue
        turn = _turn_direction(start, current.end, following.end)
        if turn is None or turn == wall_side:
            continue
        center_radius = _circumradius(start, current.end, following.end)
        if center_radius is None:
            continue
        corner_feed = _wall_compensated_feed(
            center_radius, cutter_radius, nominal_feed, floor_feed,
        )
        targets[index] = min(float(targets[index]), corner_feed)
        targets[index + 1] = min(float(targets[index + 1]), corner_feed)

    # Distance-based forward/backward smoothing.  The transition distance is
    # the travel required to move through the complete nominal-to-floor range.
    max_change_per_unit = max(TOLERANCE, (nominal_feed - floor_feed) / transition_distance)
    active = [index for index, value in enumerate(targets) if value is not None]
    for first, second in zip(active, active[1:]):
        if second != first + 1:
            continue
        allowed = max_change_per_unit * max(lengths[second], TOLERANCE)
        targets[second] = min(float(targets[second]), float(targets[first]) + allowed)
    for first, second in zip(reversed(active[1:]), reversed(active[:-1])):
        if first != second + 1:
            continue
        allowed = max_change_per_unit * max(lengths[first], TOLERANCE)
        targets[second] = min(float(targets[second]), float(targets[first]) + allowed)

    adjusted = 0
    minimum = nominal_feed
    for index, target in enumerate(targets):
        if target is None:
            continue
        original = motions[index].feed
        motion_ceiling = nominal_feed if original is None else min(nominal_feed, float(original))
        # The roughing recipe's curve-feed floor is not permission to speed up
        # a deliberately slower finish block.
        motion_floor = min(floor_feed, motion_ceiling)
        target = max(motion_floor, min(motion_ceiling, target))
        if original is not None and target < float(original) - TOLERANCE:
            motions[index] = replace(motions[index], feed=target)
            adjusted += 1
            minimum = min(minimum, target)
    return replace(block, motions=motions, metadata=dict(block.metadata)), adjusted, minimum


def _wall_compensated_feed(
    center_radius: float,
    cutter_radius: float,
    nominal_feed: float,
    floor_feed: float,
) -> float:
    if center_radius <= TOLERANCE:
        return floor_feed
    return max(floor_feed, min(nominal_feed, nominal_feed * center_radius / (center_radius + cutter_radius)))


def _turn_direction(first: ToolPoint, vertex: ToolPoint, last: ToolPoint) -> str | None:
    cross = (
        (vertex.x - first.x) * (last.y - vertex.y)
        - (vertex.y - first.y) * (last.x - vertex.x)
    )
    scale = max(
        math.hypot(vertex.x - first.x, vertex.y - first.y),
        math.hypot(last.x - vertex.x, last.y - vertex.y),
        1.0,
    )
    if abs(cross) <= TOLERANCE * scale:
        return None
    return "left" if cross > 0 else "right"


def _circumradius(first: ToolPoint, middle: ToolPoint, last: ToolPoint) -> float | None:
    a = math.hypot(middle.x - last.x, middle.y - last.y)
    b = math.hypot(first.x - last.x, first.y - last.y)
    c = math.hypot(first.x - middle.x, first.y - middle.y)
    twice_area = abs(
        (middle.x - first.x) * (last.y - first.y)
        - (middle.y - first.y) * (last.x - first.x)
    )
    if min(a, b, c, twice_area) <= TOLERANCE:
        return None
    return a * b * c / (2.0 * twice_area)


def _motion_length(start: ToolPoint, motion: Motion) -> float:
    if motion.kind in {MotionKind.ARC_CW, MotionKind.ARC_CCW} and motion.center is not None:
        radius = math.hypot(start.x - motion.center.x, start.y - motion.center.y)
        if radius > TOLERANCE:
            start_angle = math.atan2(start.y - motion.center.y, start.x - motion.center.x)
            end_angle = math.atan2(motion.end.y - motion.center.y, motion.end.x - motion.center.x)
            sweep = end_angle - start_angle
            if motion.kind == MotionKind.ARC_CW:
                while sweep >= 0:
                    sweep -= math.tau
            else:
                while sweep <= 0:
                    sweep += math.tau
            planar = abs(sweep) * radius
            return math.hypot(planar, motion.end.z - start.z)
    return math.sqrt(
        (motion.end.x - start.x) ** 2
        + (motion.end.y - start.y) ** 2
        + (motion.end.z - start.z) ** 2
    )


def combine_common_line_pairs(
    blocks: list[ToolpathBlock],
    operation: RouterOffsetOperation,
    plunge_feed: float,
) -> list[ToolpathBlock]:
    """Pair compatible closed profiles that share an exact reversed line.

    This first implementation intentionally handles non-overlapping pairs.
    Each pair becomes one Euler trail: shared edge, remainder of part A, then
    remainder of part B.  It therefore cuts a matched edge once and performs
    one plunge/retract for two profiles.
    """
    if operation.common_line_mode == "off":
        return blocks
    if operation.common_line_mode != "paired_rectangles":
        raise ValueError("Common-line mode must be off or paired rectangles.")
    if operation.common_line_tolerance <= 0:
        raise ValueError("Common-line tolerance must be positive.")

    output: list[ToolpathBlock] = []
    consumed: set[int] = set()
    for first_index, first in enumerate(blocks):
        if first_index in consumed:
            continue
        combined = None
        partner_index = None
        if _common_line_block_candidate(first):
            for second_index in range(first_index + 1, len(blocks)):
                if second_index in consumed:
                    continue
                second = blocks[second_index]
                if not _common_line_blocks_compatible(first, second, operation.common_line_tolerance):
                    continue
                combined = _combine_common_line_block_pair(
                    first, second, plunge_feed, operation.common_line_tolerance,
                )
                if combined is not None:
                    partner_index = second_index
                    break
        if combined is None:
            output.append(first)
            continue
        consumed.add(partner_index)
        output.append(combined)
    return output


def _common_line_block_candidate(block: ToolpathBlock) -> bool:
    return (
        block.metadata.get("side") == OffsetSide.OUTSIDE.value
        and block.metadata.get("role") in {"rough", "finish"}
        and block.metadata.get("entry_mode_actual", block.metadata.get("entry_mode")) == "plunge"
        and not block.metadata.get("finished_tab_width")
        and bool(block.metadata.get("entity_id"))
    )


def _common_line_blocks_compatible(
    first: ToolpathBlock,
    second: ToolpathBlock,
    tolerance: float,
) -> bool:
    return (
        _common_line_block_candidate(second)
        and first.tool_station == second.tool_station
        and first.metadata.get("entity_id") != second.metadata.get("entity_id")
        and first.metadata.get("role") == second.metadata.get("role")
        and abs(float(first.metadata.get("depth", 0)) - float(second.metadata.get("depth", 0))) <= tolerance
        and abs(float(first.metadata.get("offset_distance", 0)) - float(second.metadata.get("offset_distance", 0))) <= tolerance
    )


def _closed_constant_depth_edges(block: ToolpathBlock, tolerance: float) -> list[_PathEdge] | None:
    if len(block.motions) < 5 or block.motions[0].kind != MotionKind.RAPID or block.motions[-1].kind != MotionKind.RAPID:
        return None
    depth = -abs(float(block.metadata.get("depth", 0)))
    edges: list[_PathEdge] = []
    previous = block.motions[0].end
    for motion in block.motions[1:-1]:
        if (
            motion.kind in {MotionKind.LINEAR, MotionKind.ARC_CW, MotionKind.ARC_CCW}
            and abs(previous.z - depth) <= tolerance
            and abs(motion.end.z - depth) <= tolerance
            and math.hypot(motion.end.x - previous.x, motion.end.y - previous.y) > tolerance
        ):
            edges.append(_PathEdge(previous, motion))
        elif edges:
            return None
        previous = motion.end
    if len(edges) < 4:
        return None
    if _point_distance(edges[0].start, edges[-1].end) > tolerance:
        return None
    return edges


def _combine_common_line_block_pair(
    first: ToolpathBlock,
    second: ToolpathBlock,
    plunge_feed: float,
    tolerance: float,
) -> ToolpathBlock | None:
    first_edges = _closed_constant_depth_edges(first, tolerance)
    second_edges = _closed_constant_depth_edges(second, tolerance)
    if first_edges is None or second_edges is None:
        return None
    matches: list[tuple[int, int]] = []
    for first_index, first_edge in enumerate(first_edges):
        if first_edge.motion.kind != MotionKind.LINEAR:
            continue
        for second_index, second_edge in enumerate(second_edges):
            if second_edge.motion.kind != MotionKind.LINEAR:
                continue
            if (
                _point_distance(first_edge.start, second_edge.end) <= tolerance
                and _point_distance(first_edge.end, second_edge.start) <= tolerance
            ):
                matches.append((first_index, second_index))
                break
    if not matches:
        return None
    first_indices = {value[0] for value in matches}
    second_indices = {value[1] for value in matches}
    if not _cyclic_contiguous(first_indices, len(first_edges)) or not _cyclic_contiguous(second_indices, len(second_edges)):
        return None

    shared = _cyclic_run(first_edges, first_indices)
    # The second run must be the reverse traversal of the complete first run.
    second_shared = _cyclic_run(second_edges, second_indices)
    if (
        _point_distance(shared[0].start, second_shared[-1].end) > tolerance
        or _point_distance(shared[-1].end, second_shared[0].start) > tolerance
    ):
        return None
    first_rest = _cycle_without_run(first_edges, first_indices)
    second_rest = _cycle_without_run(second_edges, second_indices)
    if not first_rest or not second_rest:
        return None

    # Orient the shared chain U->V, then select the remaining cycles V->U and
    # U->V.  This forms a continuous Euler trail with no air-cut connector.
    u, v = shared[0].start, shared[-1].end
    if _point_distance(first_rest[0].start, v) > tolerance:
        return None
    if _point_distance(first_rest[-1].end, u) > tolerance:
        return None
    if _point_distance(second_rest[0].start, u) > tolerance:
        return None
    if _point_distance(second_rest[-1].end, v) > tolerance:
        return None

    depth = -abs(float(first.metadata.get("depth", 0)))
    safe_z = first.motions[0].end.z
    motions = [
        Motion(MotionKind.RAPID, ToolPoint(u.x, u.y, safe_z)),
        Motion(MotionKind.LINEAR, ToolPoint(u.x, u.y, depth), plunge_feed),
    ]
    motions.extend(edge.motion for edge in shared)
    motions.extend(edge.motion for edge in first_rest)
    motions.extend(edge.motion for edge in second_rest)
    motions.append(Motion(MotionKind.RAPID, ToolPoint(v.x, v.y, safe_z)))
    shared_length = sum(_motion_length(edge.start, edge.motion) for edge in shared)
    entity_ids = [str(first.metadata["entity_id"]), str(second.metadata["entity_id"])]
    metadata = {
        **first.metadata,
        "entity_ids": entity_ids,
        "entity_id": entity_ids[0],
        "role": "common_line_pair",
        "source_role": first.metadata.get("role"),
        "common_line_length": shared_length,
        "common_line_edge_count": len(shared),
        "common_line_lifts_saved": 1,
    }
    return ToolpathBlock(
        first.operation_id,
        first.tool_station,
        motions,
        f"Common-line pair at depth {abs(depth):g}",
        metadata,
    )


def _cyclic_contiguous(indices: set[int], count: int) -> bool:
    if not indices or len(indices) >= count:
        return False
    starts = [index for index in indices if (index - 1) % count not in indices]
    return len(starts) == 1


def _cyclic_run(edges: list[_PathEdge], indices: set[int]) -> list[_PathEdge]:
    count = len(edges)
    start = next(index for index in indices if (index - 1) % count not in indices)
    result = []
    index = start
    while index in indices:
        result.append(edges[index])
        index = (index + 1) % count
    return result


def _cycle_without_run(edges: list[_PathEdge], indices: set[int]) -> list[_PathEdge]:
    count = len(edges)
    last = next(index for index in indices if (index + 1) % count not in indices)
    result = []
    index = (last + 1) % count
    while index not in indices:
        result.append(edges[index])
        index = (index + 1) % count
    return result


def _point_distance(first: ToolPoint, second: ToolPoint) -> float:
    return math.sqrt(
        (first.x - second.x) ** 2
        + (first.y - second.y) ** 2
        + (first.z - second.z) ** 2
    )


def depth_passes(target_depth: float, stepdown: float | None) -> list[float]:
    if target_depth <= 0:
        raise ValueError("Target depth must be positive.")
    if stepdown is None:
        return [target_depth]
    if stepdown <= 0:
        raise ValueError("Stepdown must be positive.")
    count = math.ceil(target_depth / stepdown - TOLERANCE)
    return [min(target_depth, stepdown * index) for index in range(1, count + 1)]


def island_fill_depth_schedule(operation: IslandFillOperation) -> list[tuple[float, bool]]:
    """Return equalized roughing depths and flag an optional bottom-cleanup pass.

    Island fills benefit from equal axial loading instead of a very shallow
    remainder pass.  Explicit increments remain available when the operator
    needs an exact pass-by-pass schedule.
    """
    if operation.target_depth <= 0:
        raise ValueError("Island-fill final depth must be positive.")
    cleanup = operation.bottom_finish_depth if operation.bottom_finish_pass else 0.0
    if cleanup < 0 or cleanup >= operation.target_depth - TOLERANCE:
        if operation.bottom_finish_pass:
            raise ValueError("Bottom cleanup depth must be positive and smaller than final depth.")
        cleanup = 0.0
    rough_depth = operation.target_depth - cleanup
    if rough_depth <= TOLERANCE:
        rough_depths: list[float] = []
    elif operation.pass_mode == "custom_increments":
        if not operation.pass_depths or any(value <= 0 for value in operation.pass_depths):
            raise ValueError("Custom island-fill pass increments must all be positive.")
        total = sum(operation.pass_depths)
        if abs(total - rough_depth) > max(TOLERANCE, rough_depth * 1e-6):
            raise ValueError(
                f"Custom pass increments total {total:g}; they must total the roughing depth {rough_depth:g}."
            )
        running = 0.0
        rough_depths = []
        for increment in operation.pass_depths:
            running += increment
            rough_depths.append(min(rough_depth, running))
    else:
        if operation.pass_mode == "equal_count":
            if operation.pass_count is None or operation.pass_count < 1:
                raise ValueError("Island-fill equal pass count must be at least one.")
            count = operation.pass_count
        elif operation.pass_mode == "maximum_stepdown":
            if operation.stepdown is None:
                count = 1
            elif operation.stepdown <= 0:
                raise ValueError("Island-fill maximum stepdown must be positive.")
            else:
                count = max(1, math.ceil(rough_depth / operation.stepdown - TOLERANCE))
        else:
            raise ValueError("Island-fill pass mode must be maximum stepdown, equal count, or custom increments.")
        rough_depths = [rough_depth * index / count for index in range(1, count + 1)]
    schedule = [(depth, False) for depth in rough_depths]
    if operation.bottom_finish_pass:
        schedule.append((operation.target_depth, True))
    return schedule


def drill_peck_depths(operation: DrillCirclesOperation) -> list[float]:
    """Return the exact cumulative depths used by a drilling cycle."""
    if operation.peck_mode == "none":
        return [operation.target_depth]
    if operation.peck_definition == "count":
        if operation.peck_count is None or operation.peck_count < 1:
            raise ValueError("Peck count must be at least 1 when drilling by equal count.")
        return [operation.target_depth * index / operation.peck_count for index in range(1, operation.peck_count + 1)]
    if operation.peck_definition != "max_depth":
        raise ValueError("Peck definition must be maximum depth or equal count.")
    return depth_passes(operation.target_depth, operation.peck_depth)


def generate_drill_circles(project: CamProject, operation: DrillCirclesOperation) -> GeneratedToolpath:
    tool = _required_tool(project, operation.tool_station, operation=operation)
    entities = _entities_by_id(project)
    circles = [_required_entity(entities, entity_id, Circle) for entity_id in operation.entity_ids]
    surface_feed = operation.surface_feed_override
    if surface_feed is None:
        surface_feed = float(tool.metadata.get("surface_feed", tool.plunge_rate or 1.0))
    plunge_feed = tool.plunge_rate
    if plunge_feed is None or plunge_feed <= 0:
        raise ValueError(f"Tool {tool.station} requires a positive plunge rate.")

    blocks = []
    if operation.peck_mode not in {"none", "chip_break", "full_clear"}:
        raise ValueError("Drill cycle must be straight plunge, chip break, or full chip clear.")
    if operation.peck_mode != "none":
        if operation.peck_depth is not None and operation.peck_depth <= 0:
            raise ValueError("Peck depth must be positive when supplied.")
        if operation.peck_definition not in {"max_depth", "count"}:
            raise ValueError("Peck definition must be maximum depth or equal count.")
        if operation.peck_definition == "count" and (operation.peck_count is None or operation.peck_count < 1):
            raise ValueError("Peck count must be at least 1 when drilling by equal count.")
    if operation.retract_height <= 0 or operation.reentry_clearance < 0:
        raise ValueError("Peck retract height must be positive and re-entry clearance cannot be negative.")
    if operation.dwell_seconds < 0 or operation.top_dwell_seconds < 0:
        raise ValueError("Dwell times cannot be negative.")
    if operation.surface_feed_override is not None and operation.surface_feed_override <= 0:
        raise ValueError("Surface approach feed must be positive when supplied.")
    if operation.retract_feed_override is not None and operation.retract_feed_override <= 0:
        raise ValueError("Retract feed must be positive when supplied.")
    retract_feed = operation.retract_feed_override or plunge_feed
    peck_depths = drill_peck_depths(operation)
    for circle in circles:
        x, y = circle.center.x, circle.center.y
        motions = [
            Motion(MotionKind.RAPID, ToolPoint(x, y, operation.safe_z)),
            Motion(MotionKind.LINEAR, ToolPoint(x, y, 0.0), surface_feed),
        ]
        for index, depth in enumerate(peck_depths):
            if index and operation.peck_mode == "full_clear":
                previous_depth = peck_depths[index - 1]
                reentry_depth = max(0.0, previous_depth - operation.reentry_clearance)
                if reentry_depth > TOLERANCE:
                    motions.append(Motion(MotionKind.LINEAR, ToolPoint(x, y, -reentry_depth), plunge_feed))
            motions.append(Motion(MotionKind.LINEAR, ToolPoint(x, y, -depth), plunge_feed))
            if operation.dwell_seconds:
                motions.append(Motion(MotionKind.DWELL, ToolPoint(x, y, -depth), dwell_seconds=operation.dwell_seconds))
            if index < len(peck_depths) - 1:
                retract_z = (
                    operation.retract_height
                    if operation.peck_mode == "full_clear"
                    else -max(0.0, depth - operation.retract_height)
                )
                retract_kind = MotionKind.LINEAR if operation.controlled_retract else MotionKind.RAPID
                motions.append(Motion(retract_kind, ToolPoint(x, y, retract_z), retract_feed if operation.controlled_retract else None))
                if operation.top_dwell_seconds:
                    motions.append(Motion(MotionKind.DWELL, ToolPoint(x, y, retract_z), dwell_seconds=operation.top_dwell_seconds))
        if operation.return_to_surface or len(peck_depths) == 1:
            # Withdrawal is its own process move. In particular, a straight
            # no-peck hole must not silently reuse the slower surface-entry
            # feed when the recipe provides a dedicated retract feed.
            motions.append(Motion(MotionKind.LINEAR, ToolPoint(x, y, 0.0), retract_feed))
        motions.append(Motion(MotionKind.RAPID, ToolPoint(x, y, operation.safe_z)))
        blocks.append(ToolpathBlock(
            operation.id, tool.station, motions, f"Drill {circle.id}",
            {"entity_id": circle.id, "role": "drill", "depth": operation.target_depth},
        ))
    return GeneratedToolpath(blocks)


def generate_drag_engraving(project: CamProject, operation: EngraveOperation) -> GeneratedToolpath:
    """Trace source geometry with a spring-loaded drag tip and spindle off."""
    tool = _required_tool(project, operation.tool_station, operation=operation, allow_spindle_disabled=True)
    if tool.kind.value not in {"engraver", "other"}:
        raise ValueError(f"T{tool.station} must be defined as an engraver tool.")
    if tool.spindle_mode != "disabled" or tool.spindle_rpm is not None:
        raise ValueError(f"T{tool.station} drag engraving requires the spindle to be disabled with no RPM.")
    if tool.feed_rate is None or tool.feed_rate <= 0:
        raise ValueError(f"T{tool.station} requires a positive engraving feed.")
    if tool.plunge_rate is None or tool.plunge_rate <= 0:
        raise ValueError(f"T{tool.station} requires a positive engagement feed.")
    if operation.target_depth <= 0:
        raise ValueError("Drag-engraver compression depth must be positive.")
    if operation.approach_height <= 0 or operation.safe_z <= operation.approach_height:
        raise ValueError("Safe Z must be above the positive engraving approach height.")
    entities = _entities_by_id(project)
    selected = [entities.get(entity_id) for entity_id in operation.entity_ids]
    if not selected or any(entity is None for entity in selected):
        raise ValueError("Engraving operation has missing or empty geometry.")

    blocks: list[ToolpathBlock] = []
    for entity in selected:
        if isinstance(entity, Circle):
            closed = True
            start = Point(entity.center.x + entity.radius, entity.center.y)
            opposite = Point(entity.center.x - entity.radius, entity.center.y)
            trace = [
                Motion(MotionKind.ARC_CCW, ToolPoint(opposite.x, opposite.y, -operation.target_depth), tool.feed_rate, entity.center),
                Motion(MotionKind.ARC_CCW, ToolPoint(start.x, start.y, -operation.target_depth), tool.feed_rate, entity.center),
            ]
        else:
            if isinstance(entity, Line):
                points, closed = [entity.start, entity.end], False
            elif isinstance(entity, Rectangle):
                points, closed = _rectangle_points(entity), True
            elif isinstance(entity, Polyline) and len(entity.points) >= 2:
                points, closed = list(entity.points), entity.closed
            else:
                raise ValueError("Engraving supports lines, circles, rectangles, and polylines.")
            if operation.reverse_open_paths and not closed:
                points.reverse()
            start = points[0]
            trace_points = points[1:] + ([points[0]] if closed else [])
            trace = [
                Motion(MotionKind.LINEAR, ToolPoint(point.x, point.y, -operation.target_depth), tool.feed_rate)
                for point in trace_points
            ]
        motions = [
            Motion(MotionKind.RAPID, ToolPoint(start.x, start.y, operation.safe_z)),
            Motion(MotionKind.RAPID, ToolPoint(start.x, start.y, operation.approach_height)),
            Motion(MotionKind.LINEAR, ToolPoint(start.x, start.y, -operation.target_depth), tool.plunge_rate),
            *trace,
            Motion(MotionKind.LINEAR, ToolPoint(start.x if isinstance(entity, Circle) else trace[-1].end.x if trace else start.x, start.y if isinstance(entity, Circle) else trace[-1].end.y if trace else start.y, operation.approach_height), tool.plunge_rate),
            Motion(MotionKind.RAPID, ToolPoint(start.x if isinstance(entity, Circle) else trace[-1].end.x if trace else start.x, start.y if isinstance(entity, Circle) else trace[-1].end.y if trace else start.y, operation.safe_z)),
        ]
        blocks.append(ToolpathBlock(
            operation.id, tool.station, motions, f"Drag engrave {entity.id}",
            {"entity_id": entity.id, "side": "engrave", "spindle": "off", "compression_depth": operation.target_depth, "closed": closed},
        ))
    return GeneratedToolpath(blocks)


def generate_thread_mill(project: CamProject, operation: ThreadMillOperation) -> GeneratedToolpath:
    """Generate internal UN helical interpolation without controller G-code assumptions."""
    tool = _required_tool(project, operation.tool_station, operation=operation)
    if tool.kind.value != "thread_mill":
        raise ValueError(f"T{tool.station} must be defined as a thread_mill tool.")
    if tool.feed_rate is None or tool.feed_rate <= 0 or tool.plunge_rate is None or tool.plunge_rate <= 0:
        raise ValueError(f"T{tool.station} requires positive cutting and plunge feeds.")
    if not operation.cutter_verified:
        raise ValueError("Verify that the selected cutter supports the thread pitch and 60 degree UN form.")
    if operation.radial_passes < 1 or operation.spring_passes < 0:
        raise ValueError("Thread milling requires at least one radial pass and a nonnegative spring-pass count.")
    if operation.handedness not in {"right", "left"} or operation.cut_direction not in {"climb", "conventional"}:
        raise ValueError("Thread handedness or cut direction is invalid.")

    standard = thread_standard(operation.thread_designation)
    dimensions = standard.in_units(project.units)
    qualified_tpi = {
        int(value.strip())
        for value in str(tool.metadata.get("thread_tpi", "")).split(",")
        if value.strip().isdigit()
    }
    if qualified_tpi and standard.tpi not in qualified_tpi:
        raise ValueError(f"T{tool.station} catalog qualification does not include {standard.tpi} TPI.")
    thread_form = str(tool.metadata.get("thread_form", "")).casefold()
    if thread_form and ("60" not in thread_form or "un" not in thread_form):
        raise ValueError(f"T{tool.station} is not cataloged for a UN 60 degree thread form.")
    if operation.pilot_hole_depth is None or operation.pilot_hole_depth + TOLERANCE < operation.target_depth + operation.bottom_clearance:
        raise ValueError("Verified pilot-hole depth must cover thread depth plus bottom clearance.")
    if operation.target_depth + TOLERANCE < dimensions.pitch:
        raise ValueError("Thread depth must contain at least one full pitch.")
    if tool.cutting_edge_length is not None and operation.target_depth > tool.cutting_edge_length + TOLERANCE:
        raise ValueError(f"Thread depth exceeds T{tool.station} cutting-edge length.")
    pitch_diameter = (
        (dimensions.pitch_diameter_min + dimensions.pitch_diameter_max) / 2.0
        + operation.pitch_diameter_adjustment
    )
    if not dimensions.pitch_diameter_min - TOLERANCE <= pitch_diameter <= dimensions.pitch_diameter_max + TOLERANCE:
        raise ValueError("Adjusted pitch diameter is outside the qualified Class 2B range.")
    if tool.diameter >= dimensions.minor_diameter_min - TOLERANCE:
        raise ValueError("Thread mill diameter must be smaller than the minimum qualified pilot-hole diameter.")
    final_radius = (pitch_diameter - tool.diameter) / 2.0
    if final_radius <= TOLERANCE:
        raise ValueError("Thread mill is too large for the requested pitch diameter.")

    entities = _entities_by_id(project)
    circles = [_required_entity(entities, entity_id, Circle) for entity_id in operation.entity_ids]
    blocks: list[ToolpathBlock] = []
    upward = (operation.handedness == "right") == (operation.cut_direction == "climb")
    counterclockwise = operation.cut_direction == "climb"
    arc_kind = MotionKind.ARC_CCW if counterclockwise else MotionKind.ARC_CW
    angular_direction = 1.0 if counterclockwise else -1.0

    for circle in circles:
        hole_diameter = circle.radius * 2.0
        if not dimensions.minor_diameter_min - TOLERANCE <= hole_diameter <= dimensions.minor_diameter_max + TOLERANCE:
            raise ValueError(
                f"Circle {circle.id} diameter {hole_diameter:g} is outside the "
                f"{operation.thread_designation} pilot range "
                f"{dimensions.minor_diameter_min:g}-{dimensions.minor_diameter_max:g} {project.units}."
            )
        clearance_radius = circle.radius - tool.diameter / 2.0
        if clearance_radius <= TOLERANCE:
            raise ValueError(f"T{tool.station} cannot enter pilot circle {circle.id}.")
        if final_radius < clearance_radius - TOLERANCE:
            raise ValueError("Calculated thread path does not reach beyond the existing pilot-hole wall.")

        radial_radii = [
            clearance_radius + (final_radius - clearance_radius) * index / operation.radial_passes
            for index in range(1, operation.radial_passes + 1)
        ]
        radial_radii.extend([final_radius] * operation.spring_passes)
        for pass_index, radius in enumerate(radial_radii, start=1):
            centerline_diameter = radius * 2.0
            # Internal circular feed correction: the cutter center travels a
            # smaller circumference than the cutting edge at pitch diameter.
            helix_feed = tool.feed_rate * centerline_diameter / pitch_diameter
            start_z, end_z = (-operation.target_depth, 0.0) if upward else (0.0, -operation.target_depth)
            start_angle = 0.0
            radial = Point(math.cos(start_angle), math.sin(start_angle))
            tangent = Point(-math.sin(start_angle), math.cos(start_angle))
            lead_center = Point(
                circle.center.x + radial.x * radius / 2.0,
                circle.center.y + radial.y * radius / 2.0,
            )
            lead_start = Point(
                lead_center.x - angular_direction * tangent.x * radius / 2.0,
                lead_center.y - angular_direction * tangent.y * radius / 2.0,
            )
            path_start = Point(circle.center.x + radius, circle.center.y)
            motions = [
                Motion(MotionKind.RAPID, ToolPoint(circle.center.x, circle.center.y, operation.safe_z)),
                Motion(MotionKind.LINEAR, ToolPoint(circle.center.x, circle.center.y, start_z), tool.plunge_rate),
                Motion(MotionKind.LINEAR, ToolPoint(lead_start.x, lead_start.y, start_z), tool.plunge_rate),
                Motion(arc_kind, ToolPoint(path_start.x, path_start.y, start_z), helix_feed, lead_center),
            ]

            current_angle = start_angle
            current_z = start_z
            remaining = operation.target_depth
            z_sign = 1.0 if upward else -1.0
            while remaining > TOLERANCE:
                z_change = min(dimensions.pitch / 4.0, remaining)
                angle_change = angular_direction * 2.0 * math.pi * z_change / dimensions.pitch
                current_angle += angle_change
                current_z += z_sign * z_change
                remaining -= z_change
                endpoint = ToolPoint(
                    circle.center.x + radius * math.cos(current_angle),
                    circle.center.y + radius * math.sin(current_angle),
                    current_z,
                )
                motions.append(Motion(arc_kind, endpoint, helix_feed, circle.center))

            radial = Point(math.cos(current_angle), math.sin(current_angle))
            tangent = Point(-math.sin(current_angle), math.cos(current_angle))
            exit_center = Point(
                circle.center.x + radial.x * radius / 2.0,
                circle.center.y + radial.y * radius / 2.0,
            )
            exit_point = ToolPoint(
                exit_center.x + angular_direction * tangent.x * radius / 2.0,
                exit_center.y + angular_direction * tangent.y * radius / 2.0,
                end_z,
            )
            motions.extend(
                [
                    Motion(arc_kind, exit_point, helix_feed, exit_center),
                    Motion(MotionKind.LINEAR, ToolPoint(circle.center.x, circle.center.y, end_z), tool.plunge_rate),
                    Motion(MotionKind.RAPID, ToolPoint(circle.center.x, circle.center.y, operation.safe_z)),
                ]
            )
            blocks.append(
                ToolpathBlock(
                    operation.id,
                    tool.station,
                    motions,
                    f"{operation.thread_designation} {circle.id} pass {pass_index}/{len(radial_radii)}",
                    {
                        "entity_id": circle.id,
                        "role": "thread_mill",
                        "thread": operation.thread_designation,
                        "pitch": dimensions.pitch,
                        "pitch_diameter": pitch_diameter,
                        "pilot_diameter": hole_diameter,
                        "handedness": operation.handedness,
                        "direction": operation.cut_direction,
                        "centerline_feed": helix_feed,
                    },
                )
            )
    return GeneratedToolpath(blocks)


def offset_open_polyline(points: list[Point], distance: float, side: OffsetSide) -> list[Point]:
    if len(points) < 2:
        raise ValueError("An open offset needs at least two points.")
    if side == OffsetSide.ON:
        return list(points)
    if side not in {OffsetSide.LEFT, OffsetSide.RIGHT}:
        raise ValueError("Open contours use left, right, or on-contour offset sides.")
    if distance < 0:
        raise ValueError("Offset distance cannot be negative.")

    shifted = []
    for start, end in zip(points, points[1:]):
        dx, dy = end.x - start.x, end.y - start.y
        length = math.hypot(dx, dy)
        if length <= TOLERANCE:
            raise ValueError("Open contour contains a zero-length segment.")
        if side == OffsetSide.LEFT:
            nx, ny = -dy / length, dx / length
        else:
            nx, ny = dy / length, -dx / length
        shifted.append(
            (
                Point(start.x + nx * distance, start.y + ny * distance),
                Point(end.x + nx * distance, end.y + ny * distance),
            )
        )

    result = [shifted[0][0]]
    for previous, following in zip(shifted, shifted[1:]):
        intersection = _line_intersection(previous[0], previous[1], following[0], following[1])
        if intersection is None:
            intersection = Point(
                (previous[1].x + following[0].x) / 2,
                (previous[1].y + following[0].y) / 2,
            )
        result.append(intersection)
    result.append(shifted[-1][1])
    return result


def generate_open_router_offset(project: CamProject, operation: RouterOffsetOperation) -> GeneratedToolpath:
    if operation.tab_mode == "aligned":
        raise ValueError("Aligned Rows / Columns requires closed Outside cuts, not Cut Line.")
    tool = _required_tool(project, operation.tool_station, operation=operation)
    if tool.feed_rate is None or tool.feed_rate <= 0:
        raise ValueError(f"Tool {tool.station} requires a positive cutting feed rate.")
    if tool.plunge_rate is None or tool.plunge_rate <= 0:
        raise ValueError(f"Tool {tool.station} requires a positive plunge rate.")
    if operation.side not in {OffsetSide.LEFT, OffsetSide.RIGHT, OffsetSide.ON}:
        raise ValueError("Cut Line uses Center, Left, or Right compensation.")
    if operation.entry_mode not in {"plunge", "ramp"}:
        raise ValueError("Cut Line supports plunge or ramp entry.")
    if any(value <= 0 for value in operation.pass_depths):
        raise ValueError("Explicit pass increments must be positive.")
    entities = _entities_by_id(project)
    if operation.open_chain_mode == "connected":
        from .open_chains import assemble_open_chains
        source_chains, ignored_entity_ids, simplified_entity_ids = assemble_open_chains(
            entities, operation.entity_ids, 1e-7 * (25.4 if project.units == "mm" else 1.0))
    elif operation.open_chain_mode == "source":
        source_chains, ignored_entity_ids, simplified_entity_ids = _open_contour_chains(entities, operation.entity_ids)
    else:
        raise ValueError("Unknown Cut Line chain-direction policy.")
    distance = 0.0 if operation.side == OffsetSide.ON else tool.diameter / 2 + operation.allowance
    if operation.cut_direction not in {"climb", "conventional"}:
        raise ValueError("Cut direction must be climb or conventional.")

    if operation.pass_depths:
        planned_depths = []
        cumulative = 0.0
        for increment in operation.pass_depths:
            cumulative += increment
            if cumulative > operation.target_depth + TOLERANCE:
                raise ValueError("Explicit pass increments exceed final depth.")
            planned_depths.append(min(cumulative, operation.target_depth))
        if planned_depths[-1] < operation.target_depth - TOLERANCE:
            planned_depths.append(operation.target_depth)
    else:
        planned_depths = depth_passes(operation.target_depth, operation.stepdown)

    blocks = []
    chain_count = len(source_chains)
    for chain_index, (source_points, source_entity_ids) in enumerate(source_chains, start=1):
        # Offset before applying requested traversal direction. This retains
        # Left/Right relative to each source instead of silently exchanging
        # compensation sides when conventional traversal reverses the path.
        offset_points = offset_open_polyline(source_points, distance, operation.side)
        if operation.cut_direction == "conventional":
            offset_points.reverse()

        for depth in planned_depths:
            first = offset_points[0]
            second = offset_points[1]
            dx, dy = second.x - first.x, second.y - first.y
            segment_length = math.hypot(dx, dy)
            if segment_length <= TOLERANCE:
                raise ValueError("Cut Line begins with a zero-length segment.")
            lead_start = Point(
                first.x - dx / segment_length * operation.lead_in,
                first.y - dy / segment_length * operation.lead_in,
            )
            motions = [Motion(MotionKind.RAPID, ToolPoint(lead_start.x, lead_start.y, operation.safe_z))]
            ramp_distance = 0.0
            if operation.entry_mode == "ramp":
                motions.append(Motion(MotionKind.LINEAR, ToolPoint(lead_start.x, lead_start.y, 0.0), tool.plunge_rate))
                if operation.lead_in > TOLERANCE:
                    motions.append(Motion(MotionKind.LINEAR, ToolPoint(first.x, first.y, -depth), tool.plunge_rate))
                    remaining = offset_points[1:]
                else:
                    # An open contour has no closing lap that can recut a ramped
                    # entry. Ramping over its complete first segment is especially
                    # dangerous for a single-segment sheet cutoff: the cutter would
                    # skim progressively deeper for the full height and reach final
                    # depth only at the far end. Use a bounded, approximately
                    # five-degree entry instead, back-cut that short tapered span at
                    # full depth, then traverse the complete source path at depth.
                    total_length = sum(a.distance_to(b) for a, b in zip(offset_points, offset_points[1:]))
                    preferred_ramp = max(tool.diameter * 2.0, depth / math.tan(math.radians(5.0)))
                    ramp_distance = min(total_length, preferred_ramp)
                    if ramp_distance <= TOLERANCE:
                        raise ValueError("Cut Line is too short for ramp entry.")
                    ramp_path = _contour_points_between(offset_points, 0.0, ramp_distance)
                    traveled = 0.0
                    previous = ramp_path[0]
                    for point in ramp_path[1:]:
                        traveled += previous.distance_to(point)
                        z = -depth * min(1.0, traveled / ramp_distance)
                        motions.append(Motion(MotionKind.LINEAR, ToolPoint(point.x, point.y, z), tool.plunge_rate))
                        previous = point
                    motions.extend(
                        Motion(MotionKind.LINEAR, ToolPoint(point.x, point.y, -depth), tool.feed_rate)
                        for point in reversed(ramp_path[:-1])
                    )
                    remaining = offset_points[1:]
            else:
                motions.append(Motion(MotionKind.LINEAR, ToolPoint(lead_start.x, lead_start.y, -depth), tool.plunge_rate))
                if operation.lead_in > TOLERANCE:
                    motions.append(Motion(MotionKind.LINEAR, ToolPoint(first.x, first.y, -depth), tool.feed_rate))
                remaining = offset_points[1:]
            motions.extend(
                Motion(MotionKind.LINEAR, ToolPoint(point.x, point.y, -depth), tool.feed_rate)
                for point in remaining
            )
            last = offset_points[-1]
            if operation.lead_out > TOLERANCE:
                previous = offset_points[-2]
                out_dx, out_dy = last.x - previous.x, last.y - previous.y
                out_length = math.hypot(out_dx, out_dy)
                last = Point(
                    last.x + out_dx / out_length * operation.lead_out,
                    last.y + out_dy / out_length * operation.lead_out,
                )
                motions.append(Motion(MotionKind.LINEAR, ToolPoint(last.x, last.y, -depth), tool.feed_rate))
            motions.append(Motion(MotionKind.RAPID, ToolPoint(last.x, last.y, operation.safe_z)))
            blocks.append(
                ToolpathBlock(
                    operation.id,
                    tool.station,
                    motions,
                    f"Open {operation.side.value} offset path {chain_index}/{chain_count} at depth {depth:g}",
                    {
                        "depth": depth,
                        "offset_distance": distance,
                        "entity_ids": list(source_entity_ids),
                        "operation_entity_ids": list(operation.entity_ids),
                        "ignored_entity_ids": list(ignored_entity_ids),
                        "simplified_entity_ids": list(simplified_entity_ids),
                        "chain_index": chain_index,
                        "chain_count": chain_count,
                        "side": operation.side.value,
                        "role": "cut_line",
                        "open_chain_mode": operation.open_chain_mode,
                        "chain_start": [source_points[0].x, source_points[0].y],
                        "chain_end": [source_points[-1].x, source_points[-1].y],
                        "entry_mode": operation.entry_mode,
                        "ramp_distance": ramp_distance,
                    },
                )
            )
    return GeneratedToolpath(blocks)


def router_offset_uses_open_geometry(project: CamProject, operation: RouterOffsetOperation) -> bool:
    """Return True when every referenced source is an open line/contour."""
    entities = _entities_by_id(project)
    selected = [entities.get(entity_id) for entity_id in operation.entity_ids]
    return bool(selected) and all(
        isinstance(entity, Line) or isinstance(entity, Polyline) and not entity.closed
        for entity in selected
    )


def partial_entry_through_pairs(
    project: CamProject,
    operation: RouterOffsetOperation,
    tool_diameter: float,
) -> dict[str, set[str]]:
    """Match partial-depth contours to corresponding later through contours.

    With automatic waste-side entry, a shallow entry may cross the matching
    profile that a later operation removes completely. That profile is related
    process geometry, not a neighboring part. Pairing is deliberately strict:
    later operation, through-cut intent, same tool station, same compensation
    side, near-identical center, and closely matching extents. Same-layer
    pairing is allowed only for an operation explicitly identified by its
    saved recipe as a shelf/recess process; generic partial cuts retain the
    conservative layer separation rule.
    """
    if (
        operation.cut_intent != "partial"
        or operation.entry_side != "automatic_waste"
        or tool_diameter <= TOLERANCE
    ):
        return {}
    try:
        operation_index = next(
            index for index, candidate in enumerate(project.operations)
            if candidate.id == operation.id
        )
    except StopIteration:
        return {}
    entities = _entities_by_id(project)
    future_contours: list[tuple[RouterOffsetOperation, object]] = []
    for candidate_operation in project.operations[operation_index + 1:]:
        if (
            not candidate_operation.enabled
            or type(candidate_operation) is not RouterOffsetOperation
            or candidate_operation.cut_intent != "through"
            or candidate_operation.tool_station != operation.tool_station
            or candidate_operation.target_depth <= operation.target_depth + TOLERANCE
        ):
            continue
        for entity_id in candidate_operation.entity_ids:
            candidate = entities.get(entity_id)
            if candidate is not None:
                future_contours.append((candidate_operation, candidate))
    if not future_contours:
        return {}

    recipe_metadata = operation.recipe_metadata if isinstance(operation.recipe_metadata, dict) else {}
    process_roles = recipe_metadata.get("process_roles", ())
    if isinstance(process_roles, str):
        process_roles = (process_roles,)
    allow_same_layer_pairing = "shelf_recess" in {
        str(role).strip().casefold() for role in process_roles
    }

    center_tolerance = tool_diameter / 2.0 + unit_scale("in", project.units) * 0.01
    extent_tolerance = tool_diameter * 2.0
    scored: list[tuple[float, str, str]] = []
    for source_id in operation.entity_ids:
        source = entities.get(source_id)
        source_bounds = entity_bounds(source) if source is not None else None
        if source is None or source_bounds is None:
            continue
        source_side = operation.entity_sides.get(source_id, operation.side.value)
        source_center = (
            (source_bounds[0] + source_bounds[2]) / 2.0,
            (source_bounds[1] + source_bounds[3]) / 2.0,
        )
        source_size = (
            source_bounds[2] - source_bounds[0],
            source_bounds[3] - source_bounds[1],
        )
        for future_operation, candidate in future_contours:
            if candidate.id == source_id:
                continue
            if candidate.layer == source.layer and not allow_same_layer_pairing:
                continue
            if future_operation.entity_sides.get(candidate.id, future_operation.side.value) != source_side:
                continue
            candidate_bounds = entity_bounds(candidate)
            if candidate_bounds is None:
                continue
            candidate_center = (
                (candidate_bounds[0] + candidate_bounds[2]) / 2.0,
                (candidate_bounds[1] + candidate_bounds[3]) / 2.0,
            )
            candidate_size = (
                candidate_bounds[2] - candidate_bounds[0],
                candidate_bounds[3] - candidate_bounds[1],
            )
            center_delta = max(
                abs(source_center[0] - candidate_center[0]),
                abs(source_center[1] - candidate_center[1]),
            )
            extent_delta = max(
                abs(source_size[0] - candidate_size[0]),
                abs(source_size[1] - candidate_size[1]),
            )
            if center_delta <= center_tolerance and extent_delta <= extent_tolerance:
                scored.append((center_delta + extent_delta, source_id, candidate.id))

    result: dict[str, set[str]] = {}
    paired_sources: set[str] = set()
    paired_through: set[str] = set()
    for _score, source_id, through_id in sorted(scored):
        if source_id in paired_sources or through_id in paired_through:
            continue
        paired_sources.add(source_id)
        paired_through.add(through_id)
        result[source_id] = {through_id}
    return result


def generate_closed_router_offset(project: CamProject, operation: RouterOffsetOperation) -> GeneratedToolpath:
    """Generate male/outside or female/inside paths for closed 2D entities."""
    from .profile_second_tool import validate_retention
    validate_retention(project, operation)
    from .model import TabReleaseOperation
    if isinstance(operation, TabReleaseOperation):
        from .full_release import generate_release_stage
        return generate_release_stage(project, operation)
    if not isinstance(operation.tab_release_plan, dict):
        raise ValueError("Invalid staged tab-cleanup plan; clear or re-plan it before generating.")
    if operation.side not in {OffsetSide.INSIDE, OffsetSide.OUTSIDE, OffsetSide.ON}:
        raise ValueError("Closed contours use inside, outside, or on-contour offsets.")
    tool = _required_tool(project, operation.tool_station, operation=operation)
    if tool.feed_rate is None or tool.feed_rate <= 0:
        raise ValueError(f"Tool {tool.station} requires a positive cutting feed rate.")
    if tool.plunge_rate is None or tool.plunge_rate <= 0:
        raise ValueError(f"Tool {tool.station} requires a positive plunge rate.")
    entities = _entities_by_id(project)
    selected = [entities.get(entity_id) for entity_id in operation.entity_ids]
    if not selected:
        raise ValueError("Closed offset operation has no geometry.")
    later_through_pairs = partial_entry_through_pairs(
        project, operation, tool.diameter,
    )
    from .geometry_relationships import coincident_process_copies, noncutting_bent_source_ids
    process_copies = coincident_process_copies(project)
    bent_references = noncutting_bent_source_ids(project)
    if operation.cut_direction not in {"climb", "conventional"}:
        raise ValueError("Cut direction must be climb or conventional.")
    if operation.entry_mode not in {"plunge", "ramp", "helical"}:
        raise ValueError("Entry mode must be plunge, ramp, or helical.")
    if operation.entry_side not in {"automatic_waste", "inside", "outside"}:
        raise ValueError("Entry side must be automatic waste, inside, or outside.")
    if operation.helix_fallback not in {"waste_plunge", "strict_error"}:
        raise ValueError("Helical entry fallback must be waste plunge or strict error.")
    nonnegative_values = (
        operation.lead_in, operation.lead_out, operation.onion_skin,
        operation.finish_axial_stock, operation.tab_width, operation.tab_height,
        operation.female_tab_min_perimeter, operation.tab_cleanup_min_perimeter,
        operation.helix_diameter, operation.helix_min_diameter,
        operation.helix_pitch, operation.helix_ramp_angle,
        operation.helix_clearance, operation.helix_finish_revolutions,
    )
    if any(not math.isfinite(float(value)) or value < 0 for value in nonnegative_values):
        raise ValueError("Lead, helix, onion-skin, tab, and female-perimeter values must be finite and cannot be negative.")
    if operation.finishing_pass and operation.finish_axial_stock >= operation.target_depth:
        raise ValueError("Axial stock to leave must be smaller than target depth.")
    if operation.finish_feed_override is not None and operation.finish_feed_override <= 0:
        raise ValueError("Finishing feed must be positive.")
    if operation.finish_spindle_rpm_override is not None and operation.finish_spindle_rpm_override <= 0:
        raise ValueError("Finishing RPM must be positive.")
    if operation.helix_feed_override is not None and operation.helix_feed_override <= 0:
        raise ValueError("Helical feed must be positive.")
    if operation.entry_mode == "helical" and (operation.helix_diameter <= 0 or operation.helix_pitch <= 0):
        raise ValueError("Helical entry requires a positive helix diameter and pitch per revolution.")
    if operation.helix_min_diameter > operation.helix_diameter + TOLERANCE:
        raise ValueError("Minimum helix diameter cannot exceed the preferred helix diameter.")
    if operation.helix_ramp_angle >= 90:
        raise ValueError("Helix ramp angle must be less than 90 degrees.")
    if operation.tab_mode not in {"even", "minimum_and_spacing", "minimum_avoid_corners", "support_aware", "corners_and_spacing", "manual", "aligned"}:
        raise ValueError("Choose a supported tab placement strategy.")
    if operation.tab_cleanup_order not in {"after_each_part", "end_of_operation"}:
        raise ValueError("Choose a supported tab cleanup order.")
    if operation.contour_order not in {"inside_first", "part_by_part", "source_order"}:
        raise ValueError("Choose a supported contour order.")
    if operation.curve_feed_mode not in {"off", "wall_compensation"}:
        raise ValueError("Curve-feed mode must be off or wall compensation.")
    if not 0 < operation.curve_feed_min_percent <= 100 or operation.curve_feed_transition_distance <= 0:
        raise ValueError("Curve feed requires a 0-100% minimum and a positive transition distance.")
    if operation.common_line_mode not in {"off", "paired_rectangles"}:
        raise ValueError("Common-line mode must be off or paired rectangles.")
    if operation.common_line_tolerance <= 0:
        raise ValueError("Common-line tolerance must be positive.")
    if operation.tab_mode in {"minimum_and_spacing", "minimum_avoid_corners", "support_aware", "corners_and_spacing", "aligned"} and operation.tab_spacing <= 0:
        raise ValueError("Maximum tab spacing must be positive for the selected strategy.")
    if operation.tab_mode == "corners_and_spacing" and operation.corner_tab_offset <= 0:
        raise ValueError("Corner-tab offset must be positive for the corner strategy.")
    if any(value <= 0 for value in operation.pass_depths):
        raise ValueError("Explicit pass increments must be positive.")
    if operation.onion_skin >= operation.target_depth and operation.onion_skin > 0:
        raise ValueError("Onion skin must be smaller than target depth.")
    tabs_enabled = operation.tab_count > 0 or operation.tab_mode in {"minimum_and_spacing", "minimum_avoid_corners", "support_aware", "corners_and_spacing", "manual", "aligned"}
    if operation.tab_count < 0 or tabs_enabled and (operation.tab_width <= 0 or operation.tab_height <= 0 or operation.tab_height >= operation.tab_reference_depth):
        raise ValueError("Tabs require a positive count/width and a height below target depth.")
    if operation.common_line_mode != "off" and (
        operation.side != OffsetSide.OUTSIDE
        or abs(operation.allowance) > TOLERANCE
        or operation.entry_mode != "plunge"
        or operation.lead_in > TOLERANCE
        or operation.lead_out > TOLERANCE
        or tabs_enabled
    ):
        raise ValueError(
            "Paired common-line cutting currently requires an outside cut with plunge entry, "
            "zero allowance/leads, and no retaining tabs."
        )
    finish_reserve = operation.finish_axial_stock if operation.finishing_pass else 0.0
    rough_spindle_rpm = tool.spindle_rpm
    finish_spindle_rpm = operation.finish_spindle_rpm_override or rough_spindle_rpm
    rough_reserve = max(operation.onion_skin, finish_reserve)
    rough_target = operation.target_depth - rough_reserve
    if operation.pass_depths:
        planned_depths = []
        cumulative = 0.0
        for increment in operation.pass_depths:
            cumulative += increment
            if cumulative > rough_target + TOLERANCE:
                raise ValueError("Explicit pass increments exceed the roughing depth above the reserved bottom stock.")
            planned_depths.append(min(cumulative, rough_target))
        if not planned_depths or planned_depths[-1] < rough_target - TOLERANCE:
            planned_depths.append(rough_target)
    else:
        planned_depths = depth_passes(rough_target, operation.stepdown)
    # Legacy onion-skin behavior makes a full-depth rough pass before the
    # optional wall finish.  When axial stock is explicitly reserved, the one
    # finishing pass removes both the bottom and wall allowance instead.
    if operation.onion_skin and finish_reserve <= TOLERANCE and (not planned_depths or abs(planned_depths[-1] - operation.target_depth) > TOLERANCE):
        planned_depths.append(operation.target_depth)

    planned_entities = []
    backtrack_counts = {}
    for entity in selected:
        if entity is None:
            raise ValueError("Closed offset operation references missing geometry.")
        try:
            entity_side = OffsetSide(operation.entity_sides.get(entity.id, operation.side.value))
        except ValueError as exc:
            raise ValueError(f"Invalid cut side stored for contour {entity.id}.") from exc
        if entity_side not in {OffsetSide.INSIDE, OffsetSide.OUTSIDE, OffsetSide.ON}:
            raise ValueError("Closed contours use inside, outside, or on-contour offsets.")
        if isinstance(entity, Polyline) and entity.closed and entity_side != OffsetSide.ON:
            points, count = remove_micro_backtracks(entity.points, closed=True,
                tolerance=MICRO_BACKTRACK_INCHES * unit_scale('in', project.units))
            if count:
                # Normalize only a working copy before BOTH rounded and miter
                # compensation. The document is changed only by explicit repair.
                entity = replace(entity, points=points)
                backtrack_counts[entity.id] = count
        planned_entities.append((entity, entity_side))

    # Safe release order follows containment depth, not compensation side.
    # In a letter-within-a-box job both a letter opening and the sheet box can
    # be male/outside cuts, even though the opening must be cut first and the
    # box last.  The Inspect planner may store one job-specific manual order in
    # operation metadata; recipes never carry those geometry IDs.
    profile_entities = [entity for entity, _side in planned_entities]
    safe_entities = order_profile_entities(profile_entities, operation.contour_order)
    manual_ids = operation.manual_contour_order
    entity_ids = {entity.id for entity in profile_entities}
    if (
        isinstance(manual_ids, list)
        and len(manual_ids) == len(entity_ids)
        and len(set(manual_ids)) == len(manual_ids)
        and set(manual_ids) == entity_ids
    ):
        ordered_entities = [next(entity for entity in profile_entities if entity.id == entity_id) for entity_id in manual_ids]
    else:
        ordered_entities = safe_entities
    entity_order = {entity.id: index for index, entity in enumerate(ordered_entities)}
    containment_depths = profile_containment_depths(profile_entities)
    planned_entities.sort(key=lambda item: entity_order[item[0].id])
    from .aligned_tabs import prepare_aligned_tabs
    operation = prepare_aligned_tabs(operation, profile_entities)

    blocks = []
    deferred_cleanup_blocks = []
    for entity, entity_side in planned_entities:
        entity_operation = operation if entity_side == operation.side else replace(operation, side=entity_side)
        # Small female contours are normally waste cleanouts. Large female
        # contours can release substantial slugs, so they inherit the requested
        # hold-down policy once their source perimeter reaches the threshold.
        perimeter = entity_perimeter(entity)
        tabs_requested = operation.tab_count > 0 or operation.tab_mode in {"minimum_and_spacing", "minimum_avoid_corners", "support_aware", "corners_and_spacing", "manual", "aligned"}
        entity_has_tabs = (
            tabs_requested
            and (
                entity_side != OffsetSide.INSIDE
                or perimeter + TOLERANCE >= operation.female_tab_min_perimeter
            )
        )
        if not entity_has_tabs:
            entity_operation = replace(
                entity_operation, tab_count=0, tab_mode="even", remove_tabs_after=False,
            )
        else:
            # The operator-entered width describes the finished bridge left in
            # the material. A cutter at full depth removes one radius from each
            # end of the raised centerline span, so compensate by one complete
            # tool diameter. This also makes the later cleanup traverse the
            # entire compensated span.
            entity_operation = replace(
                entity_operation, tab_width=operation.tab_width + tool.diameter,
            )
        distance = 0.0 if entity_side == OffsetSide.ON else tool.diameter / 2 + operation.allowance
        if distance < 0:
            raise ValueError("Tool radius plus allowance cannot be negative.")
        finish_distance = 0.0 if entity_side == OffsetSide.ON else tool.diameter / 2
        has_tabs = entity_has_tabs
        pass_plan = [(depth, "rough", distance, tool.feed_rate) for depth in planned_depths]
        if operation.finishing_pass:
            finish_feed = operation.finish_feed_override or tool.feed_rate
            if finish_reserve > TOLERANCE:
                pass_plan.append((operation.target_depth, "finish", finish_distance, finish_feed))
            elif pass_plan and abs(pass_plan[-1][0] - operation.target_depth) <= TOLERANCE:
                pass_plan.append((operation.target_depth, "finish", finish_distance, finish_feed))
        for depth, pass_role, pass_distance, path_feed in pass_plan:
                if isinstance(entity, Circle):
                    geometries = [("circle", _offset_circle(entity, pass_distance, entity_side))]
                elif isinstance(entity, Rectangle):
                    if entity_side == OffsetSide.OUTSIDE and pass_distance > TOLERANCE and not has_tabs:
                        geometries = [("rounded_rectangle", (entity, pass_distance))]
                    else:
                        geometries = [("polygon", polygon) for polygon in _offset_closed_polygons(_rectangle_points(entity), pass_distance, entity_side)]
                elif isinstance(entity, Polyline) and entity.closed:
                    imported_rectangle = _axis_aligned_rectangle(entity.points)
                    if imported_rectangle is not None and entity_side == OffsetSide.OUTSIDE and pass_distance > TOLERANCE and not has_tabs:
                        geometries = [("rounded_rectangle", (imported_rectangle, pass_distance))]
                    elif entity_side == OffsetSide.OUTSIDE and pass_distance > TOLERANCE and not has_tabs:
                        geometries = [("rounded_polygon", (entity.points, pass_distance))]
                    else:
                        geometries = [("polygon", polygon) for polygon in _offset_closed_polygons(entity.points, pass_distance, entity_side)]
                else:
                    raise ValueError("Closed cuts require circles, rectangles, or closed merged contours.")
                # Compensation can split one large source into a main loop and
                # tiny secondary loops. Only these secondary, shorter-than-one-
                # tab loops may legitimately have no tab cleanup. Never exempt
                # the main contour or a missing tab on a tab-capable loop.
                fragment_lengths = [
                    sum(a.distance_to(b) for a, b in zip(shape, [*shape[1:], shape[0]]))
                    if kind == "polygon" and shape else 0.0
                    for kind, shape in geometries
                ]
                main_fragment = max(range(len(geometries)), key=fragment_lengths.__getitem__) if geometries else -1
                for path_index, (geometry_kind, geometry) in enumerate(geometries):
                    motions, entry_adjustment = _smoothed_profile_entry_motions(
                        geometry_kind,
                        geometry,
                        depth,
                        operation.safe_z,
                        tool.plunge_rate,
                        path_feed,
                        entity_operation,
                        entity.id,
                        [
                            candidate for candidate in project.entities
                            if candidate.id not in later_through_pairs.get(entity.id, set())
                            and candidate.id not in process_copies.get(entity.id, set())
                            and candidate.id not in bent_references
                        ],
                        tool.diameter / 2.0,
                        unit_scale("in", project.units),
                    )
                    if later_through_pairs.get(entity.id):
                        motions, shelf_adjustment = _prefer_shelf_waste_entry(
                            motions, entity_operation, entity,
                            [entities[i] for i in later_through_pairs[entity.id]],
                            [candidate for candidate in project.entities
                             if candidate.id not in later_through_pairs[entity.id]
                             and candidate.id not in process_copies.get(entity.id, set())
                             and candidate.id not in bent_references],
                            tool.diameter / 2, tool.plunge_rate,
                        )
                        if shelf_adjustment:
                            entry_adjustment = {**(entry_adjustment or {}), **shelf_adjustment}
                    actual_entry_mode = (
                        str(entry_adjustment["entry_mode_actual"])
                        if entry_adjustment and entry_adjustment.get("entry_mode_actual")
                        else _actual_profile_entry_mode(
                            motions, operation.entry_mode, tool.plunge_rate, path_feed,
                        )
                    )
                    blocks.append(
                        ToolpathBlock(
                            operation.id,
                            tool.station,
                            motions,
                            f"Closed {entity_side.value} {pass_role} cut at depth {depth:g}",
                            {
                                "depth": depth,
                                "offset_distance": pass_distance,
                                "side": entity_side.value,
                                "entity_id": entity.id,
                                "contour_order_index": entity_order[entity.id],
                                "containment_depth": containment_depths[entity.id],
                                "path_index": path_index,
                                "role": pass_role,
                                "programmed_feed": path_feed,
                                "spindle_rpm": finish_spindle_rpm if pass_role == "finish" else rough_spindle_rpm,
                                "radial_stock_to_leave": operation.allowance if pass_role == "rough" else 0.0,
                                "axial_stock_to_leave": rough_reserve if pass_role == "rough" else 0.0,
                                "cut_direction": operation.cut_direction,
                                "entry_mode": operation.entry_mode,
                                "entry_mode_actual": actual_entry_mode,
                                **({"micro_backtracks_removed": backtrack_counts[entity.id]}
                                   if entity.id in backtrack_counts else {}),
                                **(entry_adjustment or {}),
                                "finished_tab_width": operation.tab_width if has_tabs else 0.0,
                                "raised_tab_span": entity_operation.tab_width if has_tabs else 0.0,
                            },
                        )
                    )
                    final_role = "finish" if operation.finishing_pass else "rough"
                    if (
                        has_tabs and operation.remove_tabs_after
                        and perimeter + TOLERANCE >= operation.tab_cleanup_min_perimeter
                        and geometry_kind in {"polygon", "circle"}
                        and abs(depth - operation.target_depth) <= TOLERANCE and pass_role == final_role
                    ):
                        # Tab cleanup is a release pass, not another copy of the
                        # current profile pass. Always use the operation's exact
                        # final target so a through-cut includes its breakthrough
                        # even when a calculated pass depth differs by tolerance.
                        cleanup_depth = operation.target_depth
                        if (
                            len(geometries) > 1 and path_index != main_fragment
                            and geometry_kind == "polygon"
                            and fragment_lengths[path_index] <= entity_operation.tab_width + TOLERANCE
                            and not _raised_profile_tab_groups(motions, entity_operation)
                        ):
                            # Keep the actual profile motions and all collision
                            # checks. Skip only a nonexistent release pass, and
                            # disclose the generated fragment for source review.
                            blocks[-1].metadata.update({
                                "tab_cleanup_skipped": "tab_free_tiny_fragment",
                                "fragment_perimeter": fragment_lengths[path_index],
                                "fragment_focus": [
                                    (min(p.x for p in geometry) + max(p.x for p in geometry)) / 2,
                                    (min(p.y for p in geometry) + max(p.y for p in geometry)) / 2,
                                ],
                            })
                            continue
                        cleanup = _tab_cleanup_motions_from_profile(
                            motions, cleanup_depth, operation.safe_z,
                            tool.plunge_rate, path_feed, entity_operation,
                        )
                        cleanup_block = ToolpathBlock(
                            operation.id,
                            tool.station,
                            cleanup,
                            f"Closed {entity_side.value} tab cleanup at depth {cleanup_depth:g}",
                            {
                                "depth": cleanup_depth, "offset_distance": pass_distance, "side": entity_side.value,
                                "entity_id": entity.id, "path_index": path_index, "role": "tab_cleanup",
                                "contour_order_index": entity_order[entity.id],
                                "containment_depth": containment_depths[entity.id],
                                "cut_direction": operation.cut_direction, "entry_mode": "plunge",
                                "cut_intent": operation.cut_intent, "breakthrough": operation.breakthrough,
                                "finished_tab_width": operation.tab_width,
                                "raised_tab_span": entity_operation.tab_width,
                                "spindle_rpm": (
                                    finish_spindle_rpm
                                    if operation.finishing_pass
                                    else rough_spindle_rpm
                                ),
                            },
                        )
                        if operation.tab_cleanup_order == "end_of_operation":
                            deferred_cleanup_blocks.append(cleanup_block)
                        else:
                            blocks.append(cleanup_block)
                if (
                    entity_side == OffsetSide.INSIDE
                    and isinstance(entity, Polyline)
                    and abs(pass_distance - tool.diameter / 2) <= TOLERANCE
                ):
                    slot = _narrow_keyhole_centerline(
                        entity.points, tool.diameter,
                        [geometry for kind, geometry in geometries if kind == "polygon"],
                    )
                    if slot is not None:
                        start, end = slot
                        slot_motions = [
                            Motion(MotionKind.RAPID, ToolPoint(start.x, start.y, operation.safe_z)),
                            Motion(MotionKind.LINEAR, ToolPoint(start.x, start.y, -depth), tool.plunge_rate),
                            Motion(MotionKind.LINEAR, ToolPoint(end.x, end.y, -depth), path_feed),
                            Motion(MotionKind.RAPID, ToolPoint(end.x, end.y, operation.safe_z)),
                        ]
                        blocks.append(ToolpathBlock(
                            operation.id, tool.station, slot_motions,
                            f"Inside keyhole stem recovery at depth {depth:g}",
                            {
                                "depth": depth, "offset_distance": pass_distance,
                                "side": entity_side.value, "entity_id": entity.id,
                                "contour_order_index": entity_order[entity.id],
                                "containment_depth": containment_depths[entity.id],
                                "path_index": len(geometries), "role": "narrow_slot",
                                "cut_direction": operation.cut_direction, "entry_mode": "plunge",
                                "entry_mode_actual": "plunge",
                                "spindle_rpm": finish_spindle_rpm if pass_role == "finish" else rough_spindle_rpm,
                            },
                        ))
    blocks.extend(deferred_cleanup_blocks)
    if operation.tab_release_plan:
        from .tab_release import apply_tab_release_plan
        blocks = apply_tab_release_plan(project, operation, blocks)
    blocks = combine_common_line_pairs(blocks, operation, tool.plunge_rate)
    blocks = apply_curve_feed_schedule(
        blocks, operation, tool.diameter, tool.feed_rate,
    )
    from .cleared_travel import link_closed_profile_depths
    blocks = link_closed_profile_depths(project, operation, blocks)
    result = GeneratedToolpath(blocks)
    from .hybrid_profile import verify_finish_tabs
    verify_finish_tabs(operation,result)
    return result


def generate_rectangular_island_fill(project: CamProject, operation: IslandFillOperation) -> GeneratedToolpath:
    from .profile_second_tool import validate_retention
    validate_retention(project,operation)
    from .calculation_cache import reuse_cleanout_path
    return reuse_cleanout_path(project, operation, False,
        lambda: _generate_rectangular_island_fill(project, operation))


def _generate_rectangular_island_fill(project: CamProject, operation: IslandFillOperation) -> GeneratedToolpath:
    from .depth_intent import resolve_fill_depth
    operation = resolve_fill_depth(project, operation)
    if operation.strategy not in {FillStrategy.CONCENTRIC, FillStrategy.SPEED_CLEANOUT}:
        raise ValueError("Only Concentric and Speed Cleanout fill strategies are implemented.")
    tool = _required_tool(project, operation.tool_station, operation=operation)
    if tool.feed_rate is None or tool.feed_rate <= 0:
        raise ValueError(f"Tool {tool.station} requires a positive cutting feed rate.")
    if tool.plunge_rate is None or tool.plunge_rate <= 0:
        raise ValueError(f"Tool {tool.station} requires a positive plunge rate.")
    stepover = operation.stepover if operation.stepover is not None else tool.diameter / 2
    if stepover <= 0 or stepover > tool.diameter:
        raise ValueError("Stepover must be positive and no greater than tool diameter.")
    if operation.cut_direction not in {"climb", "conventional", "bidirectional"}:
        raise ValueError("Island-fill direction must be climb, conventional, or bidirectional.")
    if operation.entry_mode not in {"plunge", "ramp", "helical"}:
        raise ValueError("Island-fill entry mode must be plunge, ramp, or helical.")
    if operation.helix_fallback not in {"ramp", "plunge", "error"}:
        raise ValueError("Island-fill helical fallback must be ramp, plunge, or error.")
    if min(operation.ramp_length, operation.helix_diameter, operation.helix_min_diameter, operation.helix_pitch, operation.helix_ramp_angle, operation.helix_clearance, operation.helix_finish_revolutions, operation.dwell_seconds) < 0:
        raise ValueError("Island-fill entry dimensions and dwell cannot be negative.")
    if operation.entry_mode == "helical" and (operation.helix_diameter <= 0 or operation.helix_pitch <= 0):
        raise ValueError("Island-fill helical entry requires a positive diameter and pitch per revolution.")
    if operation.helix_feed_override is not None and operation.helix_feed_override <= 0:
        raise ValueError("Island-fill helical feed must be positive.")
    if operation.entry_mode == "helical" and operation.ramp_length <= 0:
        raise ValueError("Island-fill helical entry requires a positive follow-on ramp length for concentric coverage loops.")
    if operation.helix_min_diameter > operation.helix_diameter + TOLERANCE:
        raise ValueError("Minimum island-fill helix diameter cannot exceed the preferred diameter.")
    if operation.helix_ramp_angle >= 90:
        raise ValueError("Island-fill helix ramp angle must be less than 90 degrees.")
    if operation.sliver_max_path_length < 0 or operation.sliver_link_distance < 0:
        raise ValueError("Island-fill short-path cleanup dimensions cannot be negative.")
    if operation.sliver_cleanup and operation.sliver_max_path_length <= 0:
        raise ValueError("Island-fill short-path cleanup requires a positive path-length threshold.")
    if operation.sliver_link_distance > tool.diameter + TOLERANCE:
        raise ValueError("Island-fill short-path linking cannot exceed the selected cutter diameter.")
    if min(operation.stay_down_max_distance, operation.stay_down_max_engagement) < 0:
        raise ValueError("Keep Tool Down distance and engagement cannot be negative.")
    if not 0 < operation.stay_down_feed_percent <= 100:
        raise ValueError("Keep Tool Down link feed must be greater than 0% and no more than 100%.")
    if operation.keep_tool_down and operation.stay_down_max_distance <= 0:
        raise ValueError("Keep Tool Down requires a positive maximum link distance.")
    if operation.keep_tool_down and not 0 < operation.stay_down_max_engagement <= tool.diameter + TOLERANCE:
        raise ValueError("Keep Tool Down engagement must be positive and no greater than the cutter diameter.")
    if operation.cut_direction == "bidirectional" and operation.stay_down_max_engagement > tool.diameter + TOLERANCE:
        raise ValueError("Bidirectional cleanout link engagement cannot exceed the cutter diameter.")
    if tool.cutting_edge_length is not None and operation.target_depth > tool.cutting_edge_length + TOLERANCE:
        raise ValueError(
            f"Island-fill depth {operation.target_depth:g} exceeds T{tool.station}'s "
            f"{tool.cutting_edge_length:g} cutting-edge length."
        )
    if min(operation.speed_max_full_depth_engagement, operation.speed_link_distance) < 0:
        raise ValueError("Speed Cleanout engagement and link distances cannot be negative.")
    if not 0 < operation.speed_center_feed_percent <= 100:
        raise ValueError("Speed Cleanout center feed must be greater than 0% and no more than 100%.")
    if operation.speed_fallback not in {"depth_step", "error"}:
        raise ValueError("Speed Cleanout fallback must use normal depth passes or stop with an error.")
    if operation.strategy is FillStrategy.SPEED_CLEANOUT:
        if operation.speed_max_full_depth_engagement <= 0:
            raise ValueError("Speed Cleanout requires a positive maximum full-depth engagement.")
        if operation.speed_max_full_depth_engagement > tool.diameter + TOLERANCE:
            raise ValueError("Speed Cleanout full-depth engagement cannot exceed the cutter diameter.")
        if operation.speed_link_distance <= 0:
            raise ValueError("Speed Cleanout requires a positive cleared-entry link distance.")
        if operation.speed_link_distance > tool.diameter + TOLERANCE:
            raise ValueError("Speed Cleanout cleared-entry linking cannot exceed the cutter diameter.")
    entities = _entities_by_id(project)
    selected_entities = []
    seen_ids: set[str] = set()
    for entity_id in [*operation.entity_ids, *operation.island_entity_ids]:
        if entity_id in seen_ids:
            continue
        entity = entities.get(entity_id)
        if entity is None:
            raise ValueError(f"Island fill references missing geometry {entity_id}.")
        selected_entities.append(entity)
        seen_ids.add(entity_id)
    regions = classify_island_fill_regions(selected_entities)
    classified_outer_ids = {outer.id for outer, _islands in regions}
    classified_island_ids = {island.id for _outer, islands in regions for island in islands}
    provided_outer_ids = set(operation.entity_ids)
    provided_island_ids = set(operation.island_entity_ids)
    if classified_outer_ids != provided_outer_ids or classified_island_ids != provided_island_ids:
        outside_islands = classified_outer_ids.intersection(provided_island_ids)
        if outside_islands:
            example = next(iter(outside_islands))
            raise ValueError(
                f"Selected island {example} is not completely inside the supplied outer fill boundary. "
                "Treat it as a separate fill region or select its containing boundary."
            )
        raise ValueError(
            "Island-fill boundary roles do not match the contour nesting. "
            "Recreate the operation so outer regions and protected islands can be classified again."
        )
    blocks: list[ToolpathBlock] = []
    for region_index, (outer_entity, island_entities) in enumerate(regions, start=1):
        region_operation = replace(
            operation,
            entity_ids=[outer_entity.id],
            island_entity_ids=[entity.id for entity in island_entities],
        )
        try:
            from .calculation_cache import reuse_fill_region
            generated = reuse_fill_region(project, region_operation, tool, stepover,
                lambda: _generate_single_island_fill(project, region_operation, tool, stepover))
        except ValueError as exc:
            from .object_identity import object_identity_label
            raise ValueError(f"{object_identity_label(project, outer_entity)} (cleanout region {region_index}/{len(regions)}, T{tool.station}): {exc}") from exc
        region_entity_ids = [outer_entity.id, *(entity.id for entity in island_entities)]
        for block in generated.blocks:
            block.metadata["region_index"] = region_index
            block.metadata["region_count"] = len(regions)
            block.metadata["outer_entity_id"] = outer_entity.id
            # Keep an explicit source-to-motion relationship.  Output
            # completeness checks must be able to prove that every selected
            # boundary contributed to this region, including protected
            # islands which intentionally produce no cutting loop of their
            # own.
            block.metadata["entity_id"] = outer_entity.id
            block.metadata["entity_ids"] = list(region_entity_ids)
        blocks.extend(generated.blocks)
    return GeneratedToolpath(blocks)


def _generate_single_island_fill(project, operation, tool, stepover) -> GeneratedToolpath:
    schedule = island_fill_depth_schedule(operation)
    entities = _entities_by_id(project)
    outer_entity = entities.get(operation.entity_ids[0])
    island_entities = [entities.get(entity_id) for entity_id in operation.island_entity_ids]
    accessible_paths = _accessible_region_paths(
        _closed_entity_points(outer_entity),
        [_closed_entity_points(entity) for entity in island_entities],
        tool.diameter / 2.0,
    )
    if operation.strategy is FillStrategy.SPEED_CLEANOUT or operation.profile_retention.get('workflow') == 'waste_then_tabbed_finish':
        return _generate_general_island_fill(project, operation, tool, stepover, outer_entity, island_entities)
    rectangular_baseline = (
        isinstance(outer_entity, Rectangle)
        and len(island_entities) <= 1
        and all(isinstance(entity, Rectangle) for entity in island_entities)
    )
    if not rectangular_baseline:
        return _generate_general_island_fill(project, operation, tool, stepover, outer_entity, island_entities)
    outer = _required_entity(entities, operation.entity_ids[0], Rectangle)
    islands = [_required_entity(entities, entity_id, Rectangle) for entity_id in operation.island_entity_ids]
    if len(islands) > 1:
        raise ValueError("The baseline rectangular fill engine supports zero or one island.")

    radius = tool.diameter / 2
    outer_box = _inset_box(_rectangle_box(outer), radius)
    if not _valid_box(outer_box):
        raise ValueError("Outer rectangle is too small for the selected tool.")

    if islands:
        island_source = _rectangle_box(islands[0])
        protected = _expand_box(island_source, radius)
        if not _box_contains(outer_box, protected):
            raise ValueError("Island does not leave enough clearance for the selected tool.")
        bulk_loops = _island_bulk_loops(outer_box, island_source, radius, stepover)
        cleanup_offsets = [radius + stepover, radius]
    else:
        island_source = None
        bulk_loops = _center_out_loops(outer_box, stepover)
        cleanup_offsets = []

    regular_loops, sliver_loops = _partition_island_fill_loops(
        bulk_loops,
        operation.sliver_max_path_length if operation.sliver_cleanup else 0.0,
    )
    if operation.profile_retention.get('workflow') == 'waste_then_tabbed_finish':
        # Concentric loops are produced boundary-inward. Clear interior waste
        # first, then approach the protected walls; never alter cut direction.
        regular_loops = list(reversed(regular_loops))

    blocks = []
    helix_cache: dict = {}
    for depth, bottom_cleanup in schedule:
        cut_feed = (
            operation.bottom_finish_feed_override
            if bottom_cleanup and operation.bottom_finish_feed_override is not None
            else tool.feed_rate
        )
        if cut_feed is None or cut_feed <= 0:
            raise ValueError("Bottom cleanup feed must be positive when specified.")
        if regular_loops:
            link_stats: dict = {}
            motions = _island_fill_loop_block_motions(
                regular_loops, depth, operation.safe_z, tool.plunge_rate, cut_feed, operation,
                accessible_paths, helix_cache, tool.diameter / 2.0, link_stats,
            )
            blocks.append(
                ToolpathBlock(
                    operation.id,
                    tool.station,
                    motions,
                    f"{'Bottom cleanup' if bottom_cleanup else 'Island fill'} at depth {depth:g}",
                    {
                        "depth": depth, "stepover": stepover, "role": "bulk",
                        "bottom_cleanup": bottom_cleanup, "cut_direction": operation.cut_direction,
                        "entry_mode": operation.entry_mode,
                        "keep_tool_down": operation.keep_tool_down,
                        **link_stats,
                    },
                )
            )
        if operation.corner_tags and stepover > tool.diameter / 2 + TOLERANCE and len(regular_loops) > 1:
            motions = _rectangular_corner_tag_motions(
                regular_loops, depth, operation.safe_z, tool.plunge_rate, cut_feed,
            )
            blocks.append(ToolpathBlock(
                operation.id, tool.station, motions,
                f"Corner coverage at depth {depth:g}",
                {
                    "depth": depth, "stepover": stepover, "role": "corner_tags",
                    "bottom_cleanup": bottom_cleanup,
                },
            ))
        if island_source is not None:
            motions = _rounded_island_cleanup_motions(
                island_source,
                cleanup_offsets,
                depth,
                operation.safe_z,
                tool.plunge_rate,
                cut_feed,
                operation.cut_direction,
                operation.entry_mode,
                operation.dwell_seconds,
                operation,
                accessible_paths,
                helix_cache,
            )
            blocks.append(
                ToolpathBlock(
                    operation.id,
                    tool.station,
                    motions,
                    f"Island cleanup at depth {depth:g}",
                    {
                        "depth": depth, "stepover": stepover, "role": "island_cleanup",
                        "bottom_cleanup": bottom_cleanup, "cut_direction": operation.cut_direction,
                    },
                )
            )
    if sliver_loops:
        motions, linked_count, plunge_count = _island_fill_sliver_cleanup_motions(
            sliver_loops, operation.target_depth, operation.safe_z,
            tool.plunge_rate, tool.feed_rate, operation, accessible_paths,
        )
        blocks.append(ToolpathBlock(
            operation.id, tool.station, motions,
            f"Short-path final cleanup ({len(sliver_loops)} loops) at depth {operation.target_depth:g}",
            {
                "depth": operation.target_depth, "stepover": stepover, "role": "sliver_cleanup",
                "loop_count": len(sliver_loops), "linked_count": linked_count,
                "plunge_count": plunge_count,
                "max_path_length": operation.sliver_max_path_length,
                "max_link_distance": operation.sliver_link_distance,
                "cut_direction": operation.cut_direction, "entry_mode": "full_depth_cleanup",
            },
        ))
    return GeneratedToolpath(blocks)


def classify_island_fill_regions(entities) -> list[tuple[object, list[object]]]:
    """Group closed selected contours into independent fills by nesting parity.

    Depth 0/2/4 contours are fill boundaries. Their immediate children are
    protected islands. This allows one Island Fill operation to cover several
    disjoint pockets, including holes nested inside protected islands.
    """
    records: list[tuple[object, list[tuple[int, int]], float, tuple[int, int, int, int]]] = []
    for entity in entities:
        path = _region_clipper_path(_closed_entity_points(entity))
        xs = [point[0] for point in path]
        ys = [point[1] for point in path]
        records.append((entity, path, abs(float(pyclipper.Area(path))), (min(xs), min(ys), max(xs), max(ys))))
    if not records:
        raise ValueError("Island fill requires at least one closed boundary.")

    parents: list[int | None] = [None] * len(records)
    for child_index, (_child_entity, child_path, child_area, child_bounds) in enumerate(records):
        candidates: list[tuple[float, int]] = []
        for outer_index, (_outer_entity, outer_path, outer_area, outer_bounds) in enumerate(records):
            if outer_index == child_index or outer_area <= child_area + 1.0:
                continue
            if not (
                outer_bounds[0] <= child_bounds[0] and outer_bounds[1] <= child_bounds[1]
                and outer_bounds[2] >= child_bounds[2] and outer_bounds[3] >= child_bounds[3]
            ):
                continue
            if not _clipper_difference([child_path], [outer_path]):
                candidates.append((outer_area, outer_index))
        if candidates:
            parents[child_index] = min(candidates)[1]

    depths: list[int] = []
    for index in range(len(records)):
        depth = 0
        parent = parents[index]
        while parent is not None:
            depth += 1
            if depth > len(records):
                raise ValueError("Island-fill contour nesting could not be resolved.")
            parent = parents[parent]
        depths.append(depth)

    top_level = [index for index, depth in enumerate(depths) if depth == 0]
    for position, first_index in enumerate(top_level):
        _first_entity, first_path, _first_area, _first_bounds = records[first_index]
        for second_index in top_level[position + 1:]:
            _second_entity, second_path, _second_area, _second_bounds = records[second_index]
            if _clipper_intersection([first_path], [second_path]):
                raise ValueError(
                    "Two selected outer fill boundaries overlap without one containing the other. "
                    "Repair or separate those contours before generating the fill."
                )

    regions: list[tuple[object, list[object]]] = []
    for outer_index, depth in enumerate(depths):
        if depth % 2:
            continue
        islands = [
            records[index][0]
            for index, parent in enumerate(parents)
            if parent == outer_index
        ]
        regions.append((records[outer_index][0], islands))
    return regions


def _generate_general_island_fill(project, operation, tool, stepover, outer_entity, island_entities) -> GeneratedToolpath:
    outer_points = _closed_entity_points(outer_entity)
    islands = [_closed_entity_points(entity) for entity in island_entities]
    if operation.strategy is FillStrategy.SPEED_CLEANOUT:
        return _generate_speed_island_fill(operation, tool, stepover, outer_points, islands, project)
    # EnRoute calls these extra paths "corner tags": they preserve full cutter
    # coverage when overlap is below 50%.  Arbitrary polygon topology makes a
    # local tag fragile, so the general engine safely tightens only the
    # corner-sensitive concentric spacing to 50% cutter diameter.
    effective_stepover = min(stepover, tool.diameter / 2.0) if operation.corner_tags else stepover
    loops = _concentric_region_loops(outer_points, islands, tool.diameter / 2.0, effective_stepover)
    if not loops:
        raise ValueError("The selected region is too small for the tool or is fully occupied by islands.")
    accessible_paths = _accessible_region_paths(outer_points, islands, tool.diameter / 2.0)
    regular_loops, sliver_loops = _partition_island_fill_loops(
        loops,
        operation.sliver_max_path_length if operation.sliver_cleanup else 0.0,
    )
    sliver_loops = _nonredundant_fill_cleanup_loops(
        regular_loops, sliver_loops, tool.diameter / 2.0,
    )
    blocks: list[ToolpathBlock] = []
    helix_cache: dict = {}
    for depth, bottom_cleanup in island_fill_depth_schedule(operation):
        cut_feed = (
            operation.bottom_finish_feed_override
            if bottom_cleanup and operation.bottom_finish_feed_override is not None
            else tool.feed_rate
        )
        if cut_feed is None or cut_feed <= 0:
            raise ValueError("Bottom cleanup feed must be positive when specified.")
        if regular_loops:
            link_stats: dict = {}
            motions = _island_fill_loop_block_motions(
                regular_loops, depth, operation.safe_z, tool.plunge_rate, cut_feed, operation,
                accessible_paths, helix_cache, tool.diameter / 2.0, link_stats,
            )
            blocks.append(
                ToolpathBlock(
                    operation.id,
                    tool.station,
                    motions,
                    f"Concentric region fill ({len(regular_loops)} loops) at depth {depth:g}",
                    {
                        "depth": depth, "stepover": stepover, "effective_stepover": effective_stepover,
                        "role": "region_fill", "loop_count": len(regular_loops),
                        "bottom_cleanup": bottom_cleanup, "corner_tags": operation.corner_tags,
                        "cut_direction": operation.cut_direction, "entry_mode": operation.entry_mode,
                        "keep_tool_down": operation.keep_tool_down,
                        **link_stats,
                    },
                )
            )
    if sliver_loops:
        motions, linked_count, plunge_count = _island_fill_sliver_cleanup_motions(
            sliver_loops, operation.target_depth, operation.safe_z,
            tool.plunge_rate, tool.feed_rate, operation, accessible_paths,
        )
        blocks.append(ToolpathBlock(
            operation.id, tool.station, motions,
            f"Short-path final cleanup ({len(sliver_loops)} loops) at depth {operation.target_depth:g}",
            {
                "depth": operation.target_depth, "stepover": stepover,
                "effective_stepover": effective_stepover, "role": "sliver_cleanup",
                "loop_count": len(sliver_loops), "linked_count": linked_count,
                "plunge_count": plunge_count,
                "max_path_length": operation.sliver_max_path_length,
                "max_link_distance": operation.sliver_link_distance,
                "corner_tags": operation.corner_tags, "cut_direction": operation.cut_direction,
                "entry_mode": "full_depth_cleanup",
            },
        ))
    return GeneratedToolpath(blocks)


def _nonredundant_fill_cleanup_loops(regular_loops, cleanup_loops, cutter_radius):
    """Omit only cleanup loops with an entirely covered cutter sweep.

    No area/length allowance is used. Retained regular loops run at every
    scheduled depth; retained cleanup loops run at final depth. An empty
    stroke is not evidence of coverage, so preserve unprovable paths.
    """
    if not cleanup_loops:
        return []
    covered = _stroke_region_loops(regular_loops, cutter_radius)
    retained = []
    for loop in cleanup_loops:
        sweep = _stroke_region_loops([loop], cutter_radius)
        if not sweep or _clipper_difference(sweep, covered):
            retained.append(loop)
            if sweep:
                covered = _clipper_union([*covered, *sweep])
    return retained


def _generate_speed_island_fill(operation, tool, stepover, outer_points, islands, project=None) -> GeneratedToolpath:
    """Depth-step wall tracks, then safely clear the narrow center at final depth."""
    radius = tool.diameter / 2.0
    accessible_paths = _accessible_region_paths(outer_points, islands, radius)
    effective_stepover = min(stepover, radius) if operation.corner_tags else stepover
    levels = _speed_cleanout_loop_levels(accessible_paths, effective_stepover)
    if not levels:
        raise ValueError("The selected Speed Cleanout region is too small for the cutter.")
    primary_loops = levels[0]
    center_loops = [loop for level in levels[1:] for loop in level]

    outer_path = _region_clipper_path(outer_points)
    island_paths = [_region_clipper_path(points) for points in islands]
    design_region = _clipper_difference([outer_path], island_paths)
    # Do not classify square-corner material that the round cutter can never
    # reach as center engagement. The proof applies to the complete region
    # reachable by this cutter, not to impossible nominal design corners.
    machinable_region = _clipper_intersection(
        design_region, _offset_region_paths(accessible_paths, radius),
    )
    # Subtract the actual sweep directly. Intersecting it with the target first
    # is redundant for A - B, and near-coincident integer boundaries can create
    # orphan negative slivers which corrupt the subsequent erosion proof.
    primary_sweep = _stroke_region_loops(primary_loops, radius)
    residual = _clipper_difference(machinable_region, primary_sweep)
    residual_core = _offset_region_paths(
        residual, -operation.speed_max_full_depth_engagement / 2.0,
    ) if residual else []
    originally_wide = bool(residual_core)
    # Rough locally wider corners at normal depths, not every narrow band on
    # the same letter. The complete residual is checked again after promotion.
    if islands and residual_core and operation.speed_fallback == "depth_step":
        promoted, retained = [], []
        for loop in center_loops:
            if _clipper_intersection(_stroke_region_loops([loop], radius), residual_core):
                promoted.append(loop)
            else:
                retained.append(loop)
        if promoted:
            primary_loops = [*primary_loops, *promoted]
            center_loops = retained
            primary_sweep = _stroke_region_loops(primary_loops, radius)
            residual = _clipper_difference(machinable_region, primary_sweep)
            residual_core = _offset_region_paths(residual, -operation.speed_max_full_depth_engagement / 2.0) if residual else []
    # Trace one offset from the outer wall, clipped to cutter-accessible space.
    # Offsetting both walls together collapses a half-inch band into hundreds
    # of microscopic closed polygons. Do not discard their coverage: retain
    # every old loop whose complete stroke is not covered by the new traces.
    center_paths = []
    if not residual_core and center_loops:
        offset = _offset_region_paths([outer_path], -(radius + effective_stepover))
        for path in offset:
            clip = pyclipper.Pyclipper()
            clip.AddPath([*path, path[0]], pyclipper.PT_SUBJECT, False)
            clip.AddPaths(accessible_paths, pyclipper.PT_CLIP, True)
            tree = clip.Execute2(pyclipper.CT_INTERSECTION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
            for trace in pyclipper.OpenPathsFromPolyTree(tree):
                if len(trace) >= 2:
                    points = [Point(x / _REGION_SCALE, y / _REGION_SCALE) for x, y in trace]
                    sweep = _speed_trace_sweep(points, radius)
                    if _clipper_intersection(sweep, residual):
                        center_paths.append(points)
        covered = _clipper_union([*primary_sweep, *[path for points in center_paths for path in _speed_trace_sweep(points, radius)]])
        retained = []
        for loop in center_loops:
            sweep = _stroke_region_loops([loop], radius)
            if not sweep or _clipper_difference(sweep, covered):
                retained.append(loop)
                covered = _clipper_union([*covered, *sweep])
        center_paths.extend([*loop, loop[0]] for loop in retained)
    full_depth_safe = not residual_core
    unsafe_reason = ""
    center_motions = None
    linked_count = 0
    plunge_count = 0
    if residual and not center_loops:
        full_depth_safe = False
        unsafe_reason = (
            "the initially wider residual has no separate center path after roughing"
            if originally_wide else "no continuous center path was found for the remaining material"
        )
    elif residual_core:
        unsafe_reason = (
            "the material left between the wall tracks is wider than the configured "
            f"{operation.speed_max_full_depth_engagement:g} full-depth engagement limit"
        )
    elif center_loops:
        center_feed = tool.feed_rate * operation.speed_center_feed_percent / 100.0
        center_result = _speed_cleanout_trace_motions(
            primary_loops, center_paths, operation.target_depth, operation.safe_z,
            tool.plunge_rate, center_feed, operation, accessible_paths,
        )
        if center_result is None:
            full_depth_safe = False
            unsafe_reason = (
                "one or more center cleanup paths cannot be reached from an already-cleared wall "
                f"within the {operation.speed_link_distance:g} link limit"
            )
        else:
            center_motions, linked_count, plunge_count = center_result

    if not full_depth_safe and operation.speed_fallback == "error":
        raise ValueError(
            f"Speed Cleanout stopped because {unsafe_reason}. Increase the engagement/link limit only "
            "after a shop test, or choose the automatic normal-depth fallback."
        )

    if not full_depth_safe:
        fallback_loops = [loop for level in levels for loop in level]
        fallback_blocks: list[ToolpathBlock] = []
        helix_cache: dict = {}
        for depth, bottom_cleanup in island_fill_depth_schedule(operation):
            cut_feed = (
                operation.bottom_finish_feed_override
                if bottom_cleanup and operation.bottom_finish_feed_override is not None
                else tool.feed_rate
            )
            link_stats: dict = {}
            motions = _island_fill_loop_block_motions(
                fallback_loops, depth, operation.safe_z, tool.plunge_rate, cut_feed, operation,
                accessible_paths, helix_cache, tool.diameter / 2.0, link_stats,
            )
            fallback_blocks.append(ToolpathBlock(
                operation.id, tool.station, motions,
                f"Speed Cleanout safety fallback at depth {depth:g}",
                {
                    "depth": depth, "stepover": stepover, "role": "speed_center_fallback",
                    "loop_count": len(fallback_loops), "bottom_cleanup": bottom_cleanup,
                    "fallback_reason": unsafe_reason, "full_depth": False,
                    "cut_direction": operation.cut_direction, "entry_mode": operation.entry_mode,
                    "keep_tool_down": operation.keep_tool_down,
                    **link_stats,
                },
            ))
        return GeneratedToolpath(fallback_blocks)

    blocks: list[ToolpathBlock] = []
    helix_cache: dict = {}
    schedule = island_fill_depth_schedule(operation)
    for depth, bottom_cleanup in schedule:
        cut_feed = (
            operation.bottom_finish_feed_override
            if bottom_cleanup and operation.bottom_finish_feed_override is not None
            else tool.feed_rate
        )
        link_stats: dict = {}
        motions = _island_fill_loop_block_motions(
            primary_loops, depth, operation.safe_z, tool.plunge_rate, cut_feed, operation,
            accessible_paths, helix_cache, tool.diameter / 2.0, link_stats,
        )
        blocks.append(ToolpathBlock(
            operation.id, tool.station, motions,
            f"Speed Cleanout wall tracks at depth {depth:g}",
            {
                "depth": depth, "stepover": stepover, "role": "speed_primary",
                "loop_count": len(primary_loops), "bottom_cleanup": bottom_cleanup,
                "cut_direction": operation.cut_direction, "entry_mode": operation.entry_mode,
                "keep_tool_down": operation.keep_tool_down,
                **link_stats,
            },
        ))
    if project is not None:
        blocks = _speed_wall_depth_routes(project, operation, tool, primary_loops, accessible_paths, blocks)
    if full_depth_safe and center_motions:
        blocks.append(ToolpathBlock(
            operation.id, tool.station, center_motions,
            f"Speed Cleanout center cleanup at final depth {operation.target_depth:g}",
            {
                "depth": operation.target_depth, "stepover": stepover,
                "role": "speed_center_cleanup", "loop_count": len(center_paths),
                "linked_count": linked_count, "plunge_count": plunge_count,
                "full_depth": True, "residual_safe": True,
                "max_full_depth_engagement": operation.speed_max_full_depth_engagement,
                "center_feed_percent": operation.speed_center_feed_percent,
                "max_link_distance": operation.speed_link_distance,
                "cut_direction": operation.cut_direction, "entry_mode": "cleared_wall_link",
            },
        ))
    return GeneratedToolpath(blocks)


def _speed_wall_depth_routes(project, operation, tool, loops, accessible, original):
    """Compare closed-wall depth-first travel without any new lateral connector.

    Only a positively known partial-depth, plunge-entry pocket qualifies. Each
    closed loop reaches every scheduled depth before moving at full Safe Z to
    another loop. The next depth starts at the exact closed seam, so its plunge
    removes only the ordinary next stepdown. No fixture/part clearance is guessed.
    """
    thickness = project.metadata.get("job_setup", {}).get("material_thickness", 0)
    if (not operation.keep_tool_down or operation.entry_mode != "plunge"
            or operation.dwell_seconds > TOLERANCE or operation.cut_intent == "through"
            or operation.cut_direction not in {"climb", "conventional"}
            or not isinstance(thickness, (float, int)) or not math.isfinite(thickness)
            or operation.target_depth >= thickness - TOLERANCE):
        return original
    schedule = island_fill_depth_schedule(operation)
    if len(schedule) < 2:
        return original
    prepared = _prepared_island_fill_loops(loops, operation.cut_direction, accessible)
    remaining = set(range(len(prepared)))
    current = None
    candidate = []
    while remaining:
        if current is None:
            i, vertex = min(remaining), 0
        else:
            _, i, vertex = min((current.distance_to(p), i, j)
                               for i in remaining for j, p in enumerate(prepared[i]))
        remaining.remove(i)
        points = prepared[i][vertex:] + prepared[i][:vertex]
        start = points[0]
        motions = [Motion(MotionKind.RAPID, ToolPoint(start.x, start.y, operation.safe_z))]
        for depth, bottom in schedule:
            feed = (operation.bottom_finish_feed_override
                    if bottom and operation.bottom_finish_feed_override is not None else tool.feed_rate)
            motions.append(Motion(MotionKind.LINEAR, ToolPoint(start.x, start.y, -depth), tool.plunge_rate))
            motions.extend(Motion(MotionKind.LINEAR, ToolPoint(p.x, p.y, -depth), feed)
                           for p in [*points[1:], start])
        motions.append(Motion(MotionKind.RAPID, ToolPoint(start.x, start.y, operation.safe_z)))
        candidate.append(ToolpathBlock(operation.id, tool.station, motions,
            f"Speed Cleanout closed wall through depth {schedule[-1][0]:g}",
            {"role": "speed_primary", "depth": schedule[-1][0],
             "linked_depths": [d for d, _ in schedule], "depth_links_saved": len(schedule)-1,
             "wall_depth_route": True, "loop_count": 1, "plunge_count": 1,
             "linked_count": 0, "keep_tool_down": True,
             "cut_direction": operation.cut_direction, "entry_mode": "plunge"}))
        current = start
    # Include approach from the same first seam and departure to the original
    # final seam, rather than optimizing an isolated block at the cost of its
    # neighbours. This return is a full-clearance rapid, not a below-stock link.
    if not candidate or not original:
        return original
    first = original[0].motions[0]
    last = original[-1].motions[-1]
    candidate[0].motions.insert(0, first)
    candidate[-1].motions.append(last)
    from .optimization import estimate_cycle_metrics
    def cost(blocks):
        return estimate_cycle_metrics(project, [(operation, GeneratedToolpath(blocks))]).estimated_seconds
    if cost(candidate) + TOLERANCE >= cost(original):
        return original
    # Keep all walls of this pocket in one spindle-engagement block, just as
    # ordinary fill already keeps multiple safe-Z restarts inside one block.
    # Full-clearance travel between separate loops remains verbatim.
    metadata = dict(candidate[0].metadata)
    metadata.update(loop_count=len(candidate), plunge_count=len(candidate),
                    depth_links_saved=sum(b.metadata['depth_links_saved'] for b in candidate),
                    stepover=original[0].metadata.get('stepover'))
    return [replace(candidate[0], motions=[m for b in candidate for m in b.motions], metadata=metadata)]


def _speed_trace_sweep(points, radius):
    path = [(round(p.x * _REGION_SCALE), round(p.y * _REGION_SCALE)) for p in points]
    offset = pyclipper.PyclipperOffset(8.0, 0.001 * _REGION_SCALE)
    offset.AddPath(path, pyclipper.JT_ROUND, pyclipper.ET_OPENROUND)
    return offset.Execute(radius * _REGION_SCALE)


def _speed_cleanout_trace_motions(primary_loops, traces, depth, safe_z, plunge_feed, cut_feed, operation, accessible_paths):
    """Route open center traces; every restart enters a depth-cleared wall."""
    maximum = operation.speed_link_distance
    spacing = max(0.01, maximum / 2.0)
    walls = {}
    for loop in primary_loops:
        for _, _, p in _sample_closed_fill_loop(loop, spacing):
            walls.setdefault((math.floor(p.x / maximum), math.floor(p.y / maximum)), []).append(p)
    def nearby(p):
        x, y = math.floor(p.x / maximum), math.floor(p.y / maximum)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                yield from walls.get((x + dx, y + dy), ())

    prepared = []
    for trace in traces:
        closed = trace[0].distance_to(trace[-1]) < TOLERANCE
        points = trace[:-1] if closed else trace
        if operation.cut_direction == "conventional":
            points = list(reversed(points))
        samples = _sample_closed_fill_loop(points, spacing) if closed else [(0,0.,points[0]),(len(points)-2,1.,points[-1])]
        candidates = []
        for index, fraction, p in samples:
            for wall in nearby(p):
                distance = wall.distance_to(p)
                if distance <= maximum + TOLERANCE:
                    candidates.append((distance, index, fraction, p, wall))
        chosen = next((value for value in sorted(candidates, key=lambda candidate: candidate[0])
                       if _fill_cleanup_link_is_safe(value[3], value[4], accessible_paths)), None)
        if chosen is None:
            return None
        prepared.append((points, closed, chosen))

    remaining = set(range(len(prepared)))
    motions = []
    links = plunges = 0
    current = None
    while remaining:
        # Stay down only to a reachable endpoint (or closed-loop seam).
        choices = []
        if current is not None:
            for index in sorted(remaining):
                points, closed, entry = prepared[index]
                samples = [entry[1:4]] if closed else [(0,0.,points[0]),(len(points)-2,1.,points[-1])]
                for segment, fraction, p in samples:
                    distance = current.distance_to(p)
                    if distance <= maximum + TOLERANCE:
                        choices.append((distance, index, segment, fraction, p))
        link = next((choice for choice in sorted(choices, key=lambda candidate: (candidate[0], candidate[1]))
                     if _fill_cleanup_link_is_safe(current, choice[4], accessible_paths)), None)
        if link is not None:
            _, index, segment, fraction, p = link
            links += 1
        else:
            if current is not None:
                motions.append(Motion(MotionKind.RAPID, ToolPoint(current.x, current.y, safe_z)))
            index = min(remaining, key=lambda i: (current.distance_to(prepared[i][2][4]) if current else 0., i))
            _, segment, fraction, p, wall = prepared[index][2]
            motions.append(Motion(MotionKind.RAPID, ToolPoint(wall.x, wall.y, safe_z)))
            motions.append(Motion(MotionKind.LINEAR, ToolPoint(wall.x, wall.y, -depth), plunge_feed))
            plunges += 1
            if operation.dwell_seconds > TOLERANCE:
                motions.append(Motion(MotionKind.DWELL, ToolPoint(wall.x, wall.y, -depth),
                                      dwell_seconds=operation.dwell_seconds))
        motions.append(Motion(MotionKind.LINEAR, ToolPoint(p.x, p.y, -depth), cut_feed))
        points, closed, _ = prepared[index]
        remaining.remove(index)
        if closed:
            sequence = _fill_loop_sequence_from_sample(points, segment, fraction)
        else:
            sequence = points if segment == 0 and fraction == 0 else list(reversed(points))
        motions.extend(Motion(MotionKind.LINEAR, ToolPoint(point.x, point.y, -depth), cut_feed)
                       for point in sequence[1:])
        current = sequence[-1]
    if current is not None:
        motions.append(Motion(MotionKind.RAPID, ToolPoint(current.x, current.y, safe_z)))
    return motions, links, plunges


def _rest_region_loops(outer, islands, previous_radius, new_radius, cutter_radius, stepover, ignore_stock=0.0, *, owner=None, source_context=None, cleared_factory=None):
    from .calculation_cache import reuse_rest_region_loops
    return reuse_rest_region_loops(outer, islands, previous_radius, new_radius, cutter_radius,
        stepover, ignore_stock, lambda: _calculate_rest_region_loops(
            outer, islands, previous_radius, new_radius, cutter_radius, stepover, ignore_stock,
            cleared_factory=cleared_factory), owner=owner, source_context=source_context)


def _calculate_rest_region_loops(outer, islands, previous_radius, new_radius, cutter_radius, stepover, ignore_stock=0.0, *, cleared_factory=None):
    outer_path = _region_clipper_path(outer)
    island_paths = [_region_clipper_path(points) for points in islands]
    design_region = _clipper_difference([outer_path], island_paths)
    previous_access = _accessible_region_paths(outer, islands, previous_radius)
    previous_swept = _clipper_intersection(design_region, _offset_region_paths(previous_access, previous_radius))
    remaining_material = _clipper_difference(design_region, previous_swept)
    if ignore_stock > TOLERANCE:
        cores = _offset_region_paths(remaining_material, -ignore_stock / 2.0)
        remaining_material = _offset_region_paths(cores, ignore_stock / 2.0)
    new_access = _accessible_region_paths(outer, islands, new_radius)
    rest_influence = _offset_region_paths(remaining_material, cutter_radius)
    loops = _offset_region_loops(_clipper_intersection(new_access, rest_influence), stepover)
    if cleared_factory is not None:
        from .rest_path_filter import filter_loops
        loops = filter_loops(loops, design_region, cutter_radius, ignore_stock, cleared_factory)
    return loops


def generate_rest_machining(project: CamProject, operation: RestMachiningOperation) -> GeneratedToolpath:
    from .profile_second_tool import validate_retention
    validate_retention(project,operation)
    from .calculation_cache import reuse_cleanout_path
    return reuse_cleanout_path(project, operation, False,
        lambda: _generate_rest_machining(project, operation))


def _generate_rest_machining(project: CamProject, operation: RestMachiningOperation) -> GeneratedToolpath:
    """Clear cutter-center regions newly reachable relative to a prior fill."""
    tool = _required_tool(project, operation.tool_station, operation=operation)
    if tool.feed_rate is None or tool.feed_rate <= 0 or tool.plunge_rate is None or tool.plunge_rate <= 0:
        raise ValueError(f"T{tool.station} requires positive cutting and plunge feeds.")
    source = next((item for item in project.operations if item.id == operation.source_operation_id), None)
    if not isinstance(source, IslandFillOperation):
        raise ValueError("Rest machining source must be an existing island-fill operation.")
    from .depth_intent import resolve_fill_depth
    source = resolve_fill_depth(project, source)
    if not source.enabled:
        raise ValueError("Rest machining source operation is disabled.")
    previous_tool = _required_tool(project, source.tool_station, operation=source)
    if tool.diameter >= previous_tool.diameter - TOLERANCE:
        raise ValueError("Rest machining tool must be smaller than the source-operation tool.")
    if operation.target_depth > source.target_depth + TOLERANCE:
        raise ValueError("Rest machining depth cannot exceed the cleared source-operation depth.")
    if operation.stock_to_leave < 0 or operation.ignore_stock_less_than < 0:
        raise ValueError("Stock-to-leave and ignore-stock thresholds cannot be negative.")
    if tool.cutting_edge_length is not None and operation.target_depth > tool.cutting_edge_length + TOLERANCE:
        raise ValueError(f"Rest depth exceeds T{tool.station}'s cutting-edge length.")
    stepover = operation.stepover if operation.stepover is not None else tool.diameter / 3.0
    if stepover <= 0 or stepover > tool.diameter:
        raise ValueError("Rest stepover must be positive and no greater than the rest tool diameter.")
    effective_new_radius = tool.diameter / 2.0 + operation.stock_to_leave
    previous_radius = previous_tool.diameter / 2.0
    if effective_new_radius >= previous_radius - operation.ignore_stock_less_than - TOLERANCE:
        raise ValueError("No significant rest region remains after stock-to-leave and ignore-stock settings.")

    if operation.entity_ids != source.entity_ids or operation.island_entity_ids != source.island_entity_ids:
        raise ValueError("Rest operation geometry no longer matches its source operation.")
    entities = _entities_by_id(project)
    selected = []
    for entity_id in dict.fromkeys([*operation.entity_ids, *operation.island_entity_ids]):
        if entity_id not in entities:
            raise ValueError(f"Rest machining references missing geometry {entity_id}.")
        selected.append(entities[entity_id])
    regions = classify_island_fill_regions(selected)
    if ({outer.id for outer, _ in regions} != set(operation.entity_ids)
            or {e.id for _, islands in regions for e in islands} != set(operation.island_entity_ids)):
        raise ValueError("Rest source boundary roles no longer match contour nesting; recreate the source.")
    blocks: list[ToolpathBlock] = []
    no_work_entity_ids = []
    for region_index, (outer_entity, island_entities) in enumerate(regions, 1):
        region_source = replace(source, entity_ids=[outer_entity.id],
                                island_entity_ids=[e.id for e in island_entities])
        source_stepover = source.stepover if source.stepover is not None else previous_tool.diameter / 2
        source_context = {
            'operation': region_source.to_dict(), 'tool': previous_tool.to_dict(),
            'entities': [e.to_dict() for e in (outer_entity, *island_entities)],
            'units': project.units, 'rest_depth': operation.target_depth,
            'stock_thickness': project.metadata.get('job_setup', {}).get('material_thickness', 0),
            'cycle_optimization': project.metadata.get('cycle_optimization', {}),
        }
        def cleared_factory():
            from .calculation_cache import reuse_fill_region
            from .rest_path_filter import source_clearance
            generated = reuse_fill_region(project, region_source, previous_tool, source_stepover,
                lambda: _generate_single_island_fill(project, region_source, previous_tool, source_stepover))
            return source_clearance(generated, operation.target_depth, previous_radius)
        loops = _rest_region_loops(_closed_entity_points(outer_entity),
            [_closed_entity_points(e) for e in island_entities], previous_radius,
            effective_new_radius, tool.diameter / 2.0, stepover, operation.ignore_stock_less_than,
            owner=operation.id, source_context=source_context, cleared_factory=cleared_factory)
        if not loops:
            no_work_entity_ids.append(outer_entity.id)
        for depth in depth_passes(operation.target_depth, operation.stepdown):
            for loop_index, loop in enumerate(loops, start=1):
                motions = _linear_loop_block_motions(
                    [[*loop, loop[0]]], depth, operation.safe_z, tool.plunge_rate, tool.feed_rate,
                )
                blocks.append(ToolpathBlock(
                    operation.id,
                    tool.station,
                    motions,
                    f"Rest region {loop_index}/{len(loops)} at depth {depth:g}",
                    {
                        "depth": depth,
                        "role": "rest_machining",
                        "source_operation_id": source.id,
                        "previous_tool_diameter": previous_tool.diameter,
                        "rest_tool_diameter": tool.diameter,
                        "stock_to_leave": operation.stock_to_leave,
                        "ignore_stock_less_than": operation.ignore_stock_less_than,
                        "region_index": region_index, "region_count": len(regions),
                        "outer_entity_id": outer_entity.id, "entity_id": outer_entity.id,
                        "entity_ids": [outer_entity.id, *(e.id for e in island_entities)],
                    },
                ))
    if not blocks:
        raise ValueError("No rest material remains reachable above the selected ignore-stock threshold. "
                         "No rest cuts were generated; disable this unnecessary rest operation or review its tool/threshold settings.")
    if no_work_entity_ids:
        # Explicit no-work attribution, not fictitious motion. Completeness
        # independently rederives this list from current inputs before using it.
        blocks[0].metadata['rest_no_work_entity_ids'] = no_work_entity_ids
    return GeneratedToolpath(blocks)


def generate_profile_rest_machining(project: CamProject, operation: ProfileRestMachiningOperation) -> GeneratedToolpath:
    """Cut only profile-centerline regions newly reachable by a smaller cutter."""
    from .profile_second_tool import validate_retention
    validate_retention(project, operation)
    tool = _required_tool(project, operation.tool_station, operation=operation)
    if tool.feed_rate is None or tool.feed_rate <= 0 or tool.plunge_rate is None or tool.plunge_rate <= 0:
        raise ValueError(f"T{tool.station} requires positive cutting and plunge feeds.")
    source = next((item for item in project.operations if item.id == operation.source_operation_id), None)
    if not isinstance(source, RouterOffsetOperation) or router_offset_uses_open_geometry(project, source):
        raise ValueError("Profile rest machining requires an existing closed router-offset source operation.")
    if source.tab_mode == "aligned":
        raise ValueError("Profile rest machining is not yet supported for Aligned Rows / Columns tabs; it could cut the shared bridges.")
    if not source.enabled:
        raise ValueError("Profile rest machining source operation is disabled.")
    if source.remove_tabs_after:
        raise ValueError("Disable tab cleanup on the source profile; release tabs from the dependent profile-rest operation instead.")
    dependency = profile_rest_dependency_status(project, operation)
    if not dependency.current:
        raise ValueError(dependency.message)
    previous_tool = _required_tool(project, source.tool_station, operation=source)
    if tool.diameter >= previous_tool.diameter - TOLERANCE:
        raise ValueError("Profile-rest tool must be smaller than the source-operation tool.")
    if operation.target_depth > source.target_depth + TOLERANCE:
        raise ValueError("Profile-rest depth cannot exceed the source profile depth.")
    if operation.stock_to_leave < 0 or operation.ignore_stock_less_than < 0:
        raise ValueError("Profile-rest stock thresholds cannot be negative.")
    effective_rest_radius = tool.diameter / 2.0 + operation.stock_to_leave
    previous_radius = previous_tool.diameter / 2.0
    if effective_rest_radius >= previous_radius - operation.ignore_stock_less_than - TOLERANCE:
        raise ValueError("No significant profile-rest region remains with this cutter and threshold.")
    subset = bool(operation.profile_retention and source.profile_retention
                  and operation.entity_ids and set(operation.entity_ids) <= set(source.entity_ids))
    if operation.entity_ids != source.entity_ids and not subset:
        raise ValueError("Profile-rest geometry no longer matches its source profile.")

    common = {
        "stepdown": source.target_depth,
        "pass_depths": [],
        "onion_skin": 0.0,
        "finishing_pass": False,
        "tab_count": 0,
        "tab_mode": "even",
        "remove_tabs_after": False,
        "lead_in": 0.0,
        "lead_out": 0.0,
        "entry_mode": "plunge",
        "allowance": 0.0 if source.finishing_pass else source.allowance,
    }
    primary_plan = replace(source, **common)
    rest_plan = replace(
        source,
        **{
            **common,
            "tool_station": operation.tool_station,
            "entity_ids": list(operation.entity_ids),
            "allowance": operation.stock_to_leave,
            "feed_rate_override": operation.feed_rate_override,
            "plunge_rate_override": operation.plunge_rate_override,
            "spindle_rpm_override": operation.spindle_rpm_override,
        },
    )
    primary_paths = _profile_centerline_paths(generate_closed_router_offset(project, primary_plan))
    rest_paths = _profile_centerline_paths(generate_closed_router_offset(project, rest_plan))
    threshold = previous_radius - effective_rest_radius + operation.ignore_stock_less_than
    if operation.inherit_pass_schedule:
        depths = _profile_source_depths(source)
    else:
        depths = depth_passes(operation.target_depth, operation.stepdown)
    has_tabs = source.tab_count > 0 or source.tab_mode in {"minimum_and_spacing", "minimum_avoid_corners", "support_aware", "corners_and_spacing", "manual", "aligned"}
    if operation.remove_tabs_after and not has_tabs:
        raise ValueError("Profile-rest tab cleanup requires tabs on the source profile.")

    blocks: list[ToolpathBlock] = []
    cleanup_paths: list[tuple[str, int, list[Point], RouterOffsetOperation]] = []
    for key, rest_points in rest_paths.items():
        entity_id, path_index = key
        comparison = [points for (candidate_id, _index), points in primary_paths.items() if candidate_id == entity_id]
        if not comparison:
            continue
        try:
            entity_side = OffsetSide(source.entity_sides.get(entity_id, source.side.value))
        except ValueError as exc:
            raise ValueError(f"Invalid source cut side for contour {entity_id}.") from exc
        source_for_entity = source if entity_side == source.side else replace(source, side=entity_side)
        segments = _profile_rest_distance_groups(
            rest_points,
            comparison,
            threshold,
            tool.diameter,
            source_for_entity if operation.preserve_tabs else None,
        )
        if not segments:
            continue
        cleanup_paths.append((entity_id, path_index, rest_points, source_for_entity))
        for depth in depths:
            for group_index, distances in enumerate(segments, start=1):
                motions = _profile_rest_group_motions(
                    rest_points,
                    distances,
                    depth,
                    operation.safe_z,
                    tool.plunge_rate,
                    tool.feed_rate,
                    source_for_entity if operation.preserve_tabs else None,
                )
                blocks.append(ToolpathBlock(
                    operation.id,
                    tool.station,
                    motions,
                    f"Profile rest {entity_side.value} region {group_index}/{len(segments)} at depth {depth:g}",
                    {
                        "depth": depth, "role": "profile_rest", "source_operation_id": source.id,
                        "entity_id": entity_id, "path_index": path_index, "side": entity_side.value,
                        "previous_tool_diameter": previous_tool.diameter, "rest_tool_diameter": tool.diameter,
                        "stock_to_leave": operation.stock_to_leave,
                        "ignore_stock_less_than": operation.ignore_stock_less_than,
                        "tabs_preserved": bool(operation.preserve_tabs and has_tabs),
                    },
                ))
    if not blocks:
        raise ValueError("No meaningful profile-rest material is reachable with the selected smaller tool.")
    if operation.remove_tabs_after:
        for entity_id, path_index, rest_points, source_for_entity in cleanup_paths:
            cleanup = _tab_cleanup_motions(
                _without_duplicate_closure(rest_points), operation.target_depth, operation.safe_z,
                tool.plunge_rate, tool.feed_rate, source_for_entity,
            )
            blocks.append(ToolpathBlock(
                operation.id,
                tool.station,
                cleanup,
                f"Profile rest tab cleanup for {entity_id}",
                {
                    "depth": operation.target_depth, "role": "tab_cleanup", "source_operation_id": source.id,
                    "entity_id": entity_id, "path_index": path_index, "side": source_for_entity.side.value,
                },
            ))
    return GeneratedToolpath(blocks)


def _profile_source_depths(source: RouterOffsetOperation) -> list[float]:
    rough_target = source.target_depth - source.onion_skin if source.onion_skin else source.target_depth
    if source.pass_depths:
        depths, cumulative = [], 0.0
        for increment in source.pass_depths:
            cumulative += increment
            depths.append(min(cumulative, rough_target))
        if not depths or depths[-1] < rough_target - TOLERANCE:
            depths.append(rough_target)
    else:
        depths = depth_passes(rough_target, source.stepdown)
    if source.onion_skin and (not depths or depths[-1] < source.target_depth - TOLERANCE):
        depths.append(source.target_depth)
    return depths


def _profile_centerline_paths(path: GeneratedToolpath) -> dict[tuple[str, int], list[Point]]:
    result = {}
    for block in path.blocks:
        key = (str(block.metadata.get("entity_id", "")), int(block.metadata.get("path_index", 0)))
        points = _motion_centerline_points(block)
        if len(points) >= 3:
            result[key] = points
    return result


def _motion_centerline_points(block: ToolpathBlock) -> list[Point]:
    depth = float(block.metadata.get("depth", 0.0))
    points: list[Point] = []
    previous = None
    for motion in block.motions:
        end = motion.end
        if motion.kind == MotionKind.RAPID:
            previous = end
            continue
        if previous is None:
            previous = end
            continue
        at_depth = abs(end.z + depth) <= 1e-7
        same_xy = math.hypot(end.x - previous.x, end.y - previous.y) <= TOLERANCE
        if at_depth and not same_xy:
            start = Point(previous.x, previous.y)
            if not points or points[-1].distance_to(start) > 1e-7:
                points.append(start)
            if motion.kind in {MotionKind.ARC_CW, MotionKind.ARC_CCW} and motion.center is not None:
                center = motion.center
                start_angle = math.atan2(start.y - center.y, start.x - center.x)
                end_angle = math.atan2(end.y - center.y, end.x - center.x)
                delta = end_angle - start_angle
                if motion.kind == MotionKind.ARC_CW:
                    while delta >= 0:
                        delta -= 2 * math.pi
                else:
                    while delta <= 0:
                        delta += 2 * math.pi
                radius = start.distance_to(center)
                steps = max(2, math.ceil(abs(delta) / math.radians(5)))
                points.extend(Point(center.x + radius * math.cos(start_angle + delta * index / steps), center.y + radius * math.sin(start_angle + delta * index / steps)) for index in range(1, steps + 1))
            else:
                points.append(Point(end.x, end.y))
        previous = end
    return _without_duplicate_closure(points)


def _without_duplicate_closure(points: list[Point]) -> list[Point]:
    result = list(points)
    if len(result) > 1 and result[0].distance_to(result[-1]) <= 1e-7:
        result.pop()
    return result


def _distance_to_closed_paths(point: Point, paths: list[list[Point]]) -> float:
    return min(
        _point_segment_distance(point, start, end)
        for path in paths
        for start, end in zip(path, path[1:] + path[:1])
    )


def _point_segment_distance(point: Point, start: Point, end: Point) -> float:
    dx, dy = end.x - start.x, end.y - start.y
    length_squared = dx * dx + dy * dy
    if length_squared <= TOLERANCE:
        return point.distance_to(start)
    ratio = max(0.0, min(1.0, ((point.x - start.x) * dx + (point.y - start.y) * dy) / length_squared))
    projection = Point(start.x + ratio * dx, start.y + ratio * dy)
    return point.distance_to(projection)


def _profile_rest_distance_groups(rest_points, primary_paths, threshold, tool_diameter, tab_source):
    from .contour_index import ContourIndex
    indexes = [ContourIndex(path, threshold + 1e-7) for path in primary_paths]
    sequence = [*rest_points, rest_points[0]]
    lengths = [start.distance_to(end) for start, end in zip(sequence, sequence[1:])]
    total = sum(lengths)
    spacing = max(tool_diameter / 4.0, total / 4000.0, 0.001)
    distances = {0.0, total}
    traveled = 0.0
    for length in lengths:
        count = max(1, math.ceil(length / spacing))
        distances.update(traveled + length * index / count for index in range(count + 1))
        traveled += length
    if tab_source is not None and (tab_source.tab_count > 0 or tab_source.tab_mode in {"minimum_and_spacing", "minimum_avoid_corners", "support_aware", "corners_and_spacing", "manual", "aligned"}):
        for start, end in _tab_intervals(sequence, tab_source):
            distances.update((start, end))
    ordered = sorted(distances)
    groups, current = [], []
    for start, end in zip(ordered, ordered[1:]):
        midpoint = _point_at_contour_distance(sequence, (start + end) / 2.0)
        limit = threshold + 1e-7
        # Exact same distance predicate, with conservative local edge lookup.
        # This is rest-material classification, never a collision exemption.
        needed = not any(_point_segment_distance(midpoint, a, b) <= limit
            for index in indexes for a, b in index.near_edges(
                (midpoint.x, midpoint.y, midpoint.x, midpoint.y), limit))
        if needed:
            if not current:
                current = [start]
            current.append(end)
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _profile_rest_group_motions(rest_points, distances, depth, safe_z, plunge_feed, cut_feed, tab_source):
    sequence = [*rest_points, rest_points[0]]
    tab_intervals = []
    if tab_source is not None and (tab_source.tab_count > 0 or tab_source.tab_mode in {"minimum_and_spacing", "minimum_avoid_corners", "support_aware", "corners_and_spacing", "manual", "aligned"}) and depth > tab_source.tab_floor_depth + TOLERANCE:
        tab_intervals = _tab_intervals(sequence, tab_source)
    start = _point_at_contour_distance(sequence, distances[0])
    first_midpoint = (distances[0] + distances[1]) / 2.0
    first_z = -(tab_source.tab_floor_depth) if tab_source is not None and any(a <= first_midpoint <= b for a, b in tab_intervals) else -depth
    motions = [
        Motion(MotionKind.RAPID, ToolPoint(start.x, start.y, safe_z)),
        Motion(MotionKind.LINEAR, ToolPoint(start.x, start.y, first_z), plunge_feed),
    ]
    for start_distance, end_distance in zip(distances, distances[1:]):
        midpoint = (start_distance + end_distance) / 2.0
        desired_z = -(tab_source.tab_floor_depth) if tab_source is not None and any(a <= midpoint <= b for a, b in tab_intervals) else -depth
        current = motions[-1].end
        if abs(current.z - desired_z) > TOLERANCE:
            motions.append(Motion(MotionKind.LINEAR, ToolPoint(current.x, current.y, desired_z), plunge_feed if desired_z < current.z else cut_feed))
        end = _point_at_contour_distance(sequence, end_distance)
        motions.append(Motion(MotionKind.LINEAR, ToolPoint(end.x, end.y, desired_z), cut_feed))
    end = motions[-1].end
    motions.append(Motion(MotionKind.RAPID, ToolPoint(end.x, end.y, safe_z)))
    return motions


def _closed_entity_points(entity) -> list[Point]:
    if isinstance(entity, Rectangle):
        return _rectangle_points(entity)
    if isinstance(entity, Polyline) and entity.closed:
        return _clean_polygon_points(entity.points)
    if isinstance(entity, Circle) and entity.radius > TOLERANCE:
        segments = 96
        return [
            Point(
                entity.center.x + entity.radius * math.cos(2.0 * math.pi * index / segments),
                entity.center.y + entity.radius * math.sin(2.0 * math.pi * index / segments),
            )
            for index in range(segments)
        ]
    raise ValueError("Island fill boundaries must be circles, rectangles, or closed polylines.")


_REGION_SCALE = 1_000_000.0


def _region_clipper_path(points):
    path = [(round(point.x * _REGION_SCALE), round(point.y * _REGION_SCALE)) for point in _clean_polygon_points(points)]
    if len(path) < 3:
        raise ValueError("Region boundary has too few distinct points.")
    if not pyclipper.Orientation(path):
        path.reverse()
    return path


def _offset_region_paths(paths, amount):
    if not paths:
        return []
    offsetter = pyclipper.PyclipperOffset(8.0, 0.01 * _REGION_SCALE)
    offsetter.AddPaths(paths, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
    return offsetter.Execute(amount * _REGION_SCALE)


def _clipper_difference(subject_paths, clip_paths):
    if not subject_paths:
        return []
    clipper = pyclipper.Pyclipper()
    clipper.AddPaths(subject_paths, pyclipper.PT_SUBJECT, True)
    if clip_paths:
        clipper.AddPaths(clip_paths, pyclipper.PT_CLIP, True)
    return clipper.Execute(pyclipper.CT_DIFFERENCE, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)


def _clipper_intersection(subject_paths, clip_paths):
    if not subject_paths or not clip_paths:
        return []
    clipper = pyclipper.Pyclipper()
    clipper.AddPaths(subject_paths, pyclipper.PT_SUBJECT, True)
    clipper.AddPaths(clip_paths, pyclipper.PT_CLIP, True)
    return clipper.Execute(pyclipper.CT_INTERSECTION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)


def _clipper_union(paths):
    if not paths:
        return []
    clipper = pyclipper.Pyclipper()
    clipper.AddPaths(paths, pyclipper.PT_SUBJECT, True)
    return clipper.Execute(pyclipper.CT_UNION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)


def _stroke_region_loops(loops, radius):
    """Return the cutter sweep produced by following closed centerline loops."""
    if not loops or radius <= 0:
        return []
    strokes = []
    for loop in loops:
        path = _region_clipper_path(loop)
        offsetter = pyclipper.PyclipperOffset(8.0, 0.01 * _REGION_SCALE)
        offsetter.AddPath(path, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDLINE)
        strokes.extend(offsetter.Execute(radius * _REGION_SCALE))
    return _clipper_union(strokes)


def _accessible_region_paths(outer_points, island_points, cutter_radius):
    outer_path = _region_clipper_path(outer_points)
    island_paths = [_region_clipper_path(points) for points in island_points]
    for path in island_paths:
        if any(pyclipper.PointInPolygon(point, outer_path) == 0 for point in path):
            raise ValueError("Every island must be completely inside the outer fill boundary.")
    accessible_outer = _offset_region_paths([outer_path], -cutter_radius)
    protected_islands = _offset_region_paths(island_paths, cutter_radius)
    return _clipper_difference(accessible_outer, protected_islands)


def _offset_region_loops(region_paths, stepover):
    loops: list[list[Point]] = []
    for iteration in range(10000):
        current = region_paths if iteration == 0 else _offset_region_paths(region_paths, -iteration * stepover)
        if not current:
            break
        for path in current:
            points = _clean_polygon_points([Point(x / _REGION_SCALE, y / _REGION_SCALE) for x, y in path])
            if len(points) >= 3:
                loops.append(points)
    else:
        raise ValueError("Region loop generation did not converge.")
    return loops


def _speed_cleanout_loop_levels(region_paths, stepover):
    """Keep erosion generations separate without changing legacy fills."""
    levels: list[list[list[Point]]] = []
    for iteration in range(10000):
        current = region_paths if iteration == 0 else _offset_region_paths(region_paths, -iteration * stepover)
        if not current:
            break
        level = []
        for path in current:
            points = _clean_polygon_points([Point(x / _REGION_SCALE, y / _REGION_SCALE) for x, y in path])
            if len(points) >= 3:
                level.append(points)
        if level:
            levels.append(level)
    else:
        raise ValueError("Speed Cleanout centerline generation did not converge.")
    return levels


def _concentric_region_loops(outer_points, island_points, tool_radius: float, stepover: float) -> list[list[Point]]:
    scale = 1_000_000.0

    def clipper_path(points):
        path = [(round(point.x * scale), round(point.y * scale)) for point in _clean_polygon_points(points)]
        if len(path) < 3:
            raise ValueError("Region boundary has too few distinct points.")
        if not pyclipper.Orientation(path):
            path.reverse()
        return path

    outer_path = clipper_path(outer_points)
    island_paths = [clipper_path(points) for points in island_points]
    for path in island_paths:
        if any(pyclipper.PointInPolygon(point, outer_path) == 0 for point in path):
            raise ValueError("Every island must be completely inside the outer fill boundary.")

    def offset_paths(paths, amount):
        offsetter = pyclipper.PyclipperOffset(8.0, 0.01 * scale)
        offsetter.AddPaths(paths, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
        return offsetter.Execute(amount * scale)

    accessible_outer = offset_paths([outer_path], -tool_radius)
    if not accessible_outer:
        return []
    protected_islands = offset_paths(island_paths, tool_radius) if island_paths else []
    clipper = pyclipper.Pyclipper()
    clipper.AddPaths(accessible_outer, pyclipper.PT_SUBJECT, True)
    if protected_islands:
        clipper.AddPaths(protected_islands, pyclipper.PT_CLIP, True)
    region_paths = clipper.Execute(
        pyclipper.CT_DIFFERENCE,
        pyclipper.PFT_NONZERO,
        pyclipper.PFT_NONZERO,
    )
    loops: list[list[Point]] = []
    for iteration in range(10000):
        distance = iteration * stepover
        current = region_paths if iteration == 0 else offset_paths(region_paths, -distance)
        if not current:
            break
        for path in current:
            points = _clean_polygon_points([Point(x / scale, y / scale) for x, y in path])
            if len(points) >= 3:
                loops.append(points)
    else:
        raise ValueError("Concentric region fill did not converge.")
    return loops


def _required_tool(project: CamProject, station: int, *, operation=None, allow_spindle_disabled: bool = False):
    tool = effective_tool(project, operation) if operation is not None else project.tool_library.by_station(station)
    if tool is None:
        raise ValueError(f"Tool station {station} is empty.")
    if tool.diameter <= 0:
        raise ValueError(f"Tool {station} requires a positive diameter.")
    if tool.spindle_mode == "disabled" and not allow_spindle_disabled:
        raise ValueError(f"Tool station {station} is configured as spindle-disabled and cannot run a rotary toolpath.")
    if tool.spindle_mode == "required" and tool.spindle_rpm is None:
        raise ValueError(f"Tool station {station} requires a spindle RPM.")
    if tool.spindle_rpm is not None and (
        tool.min_rpm is not None and tool.spindle_rpm < tool.min_rpm
        or tool.max_rpm is not None and tool.spindle_rpm > tool.max_rpm
    ):
        raise ValueError(f"Tool station {station} RPM is outside its safe tool range.")
    return tool


def _entities_by_id(project: CamProject) -> dict[str, object]:
    return {entity.id: entity for entity in project.entities}


def _required_entity(entities: dict[str, object], entity_id: str, entity_type):
    entity = entities.get(entity_id)
    if not isinstance(entity, entity_type):
        raise ValueError(f"Entity {entity_id!r} must be a {entity_type.__name__}.")
    return entity


def _ordered_line_points(lines: list[Line]) -> list[Point]:
    if not lines:
        raise ValueError("Open offset operation has no line geometry.")
    points = [lines[0].start, lines[0].end]
    for line in lines[1:]:
        if points[-1].distance_to(line.start) <= 1e-7:
            points.append(line.end)
        elif points[-1].distance_to(line.end) <= 1e-7:
            points.append(line.start)
        else:
            raise ValueError("Open offset line entities must be connected and ordered.")
    return points


def _clean_open_source_points(points: list[Point], tolerance: float = 1e-7) -> tuple[list[Point], bool]:
    """Remove repeated adjacent points without changing an open source's direction."""
    cleaned: list[Point] = []
    removed_zero_segment = False
    for point in points:
        if cleaned and cleaned[-1].distance_to(point) <= tolerance:
            removed_zero_segment = True
            continue
        cleaned.append(point)
    return cleaned, removed_zero_segment


def _open_contour_chains(
    entities: dict[str, object],
    entity_ids: list[str],
    tolerance: float = 1e-7,
) -> tuple[list[tuple[list[Point], list[str]]], list[str], list[str]]:
    """Build independent, direction-preserving open chains.

    Disconnected sources deliberately remain separate paths. Connected Line
    and open-Polyline sources may be reordered, but never reversed: reversing
    source direction after compensation would silently exchange Left and
    Right. A connected component whose stored directions do not form one
    unbranched open chain is therefore rejected instead of guessed.

    Returns ``(chains, ignored_ids, simplified_ids)``. A zero-length source is
    ignored; repeated adjacent points inside an otherwise valid polyline are
    removed and disclosed through ``simplified_ids``.
    """
    if not entity_ids:
        raise ValueError("Cut Line has no source geometry.")
    chunks: list[tuple[int, list[Point], str]] = []
    ignored_ids: list[str] = []
    simplified_ids: list[str] = []
    seen_ids: set[str] = set()
    for source_index, entity_id in enumerate(entity_ids):
        if entity_id in seen_ids:
            raise ValueError(f"Cut Line references source geometry {entity_id} more than once.")
        seen_ids.add(entity_id)
        entity = entities.get(entity_id)
        if isinstance(entity, Line):
            points = [entity.start, entity.end]
        elif isinstance(entity, Polyline) and not entity.closed:
            points = list(entity.points)
        else:
            raise ValueError("Cut Line requires lines or merged open contours.")
        points, simplified = _clean_open_source_points(points, tolerance)
        if simplified:
            simplified_ids.append(entity_id)
        if len(points) < 2:
            ignored_ids.append(entity_id)
            continue
        chunks.append((source_index, points, entity_id))

    if not chunks:
        raise ValueError(
            "Cut Line selection contains only zero-length geometry. Delete or repair the point remnants, "
            "then select at least one real line."
        )

    def endpoints_touch(first: tuple[int, list[Point], str], second: tuple[int, list[Point], str]) -> bool:
        return any(
            a.distance_to(b) <= tolerance
            for a in (first[1][0], first[1][-1])
            for b in (second[1][0], second[1][-1])
        )

    # Find undirected connected components first. This separates genuinely
    # independent cutoffs from one connected component whose directions or
    # branching are invalid.
    remaining = set(range(len(chunks)))
    components: list[list[int]] = []
    while remaining:
        seed = min(remaining, key=lambda index: chunks[index][0])
        remaining.remove(seed)
        component = [seed]
        pending = [seed]
        while pending:
            current = pending.pop()
            joined = [
                candidate for candidate in tuple(remaining)
                if endpoints_touch(chunks[current], chunks[candidate])
            ]
            for candidate in joined:
                remaining.remove(candidate)
                component.append(candidate)
                pending.append(candidate)
        components.append(component)
    components.sort(key=lambda component: min(chunks[index][0] for index in component))

    chains: list[tuple[list[Point], list[str]]] = []
    for component_number, component in enumerate(components, start=1):
        if len(component) == 1:
            _index, points, entity_id = chunks[component[0]]
            chains.append((list(points), [entity_id]))
            continue

        incoming: dict[int, list[int]] = {index: [] for index in component}
        outgoing: dict[int, list[int]] = {index: [] for index in component}
        for first in component:
            for second in component:
                if first == second:
                    continue
                if chunks[first][1][-1].distance_to(chunks[second][1][0]) <= tolerance:
                    outgoing[first].append(second)
                    incoming[second].append(first)

        starts = [index for index in component if not incoming[index]]
        ends = [index for index in component if not outgoing[index]]
        invalid = (
            len(starts) != 1
            or len(ends) != 1
            or any(len(incoming[index]) > 1 or len(outgoing[index]) > 1 for index in component)
        )
        if invalid:
            labels = ", ".join(
                chunks[index][2] for index in sorted(component, key=lambda item: chunks[item][0])
            )
            raise ValueError(
                f"Cut Line component {component_number} is connected but its source directions branch or conflict "
                f"({labels}). Join/reverse that component explicitly; separate disconnected cut lines are allowed."
            )

        ordered: list[int] = []
        current = starts[0]
        while current not in ordered:
            ordered.append(current)
            if not outgoing[current]:
                break
            current = outgoing[current][0]
        if len(ordered) != len(component) or ordered[-1] != ends[0]:
            raise ValueError(
                f"Cut Line component {component_number} does not form one unbranched open path. "
                "Join or repair that component before cutting."
            )

        points = list(chunks[ordered[0]][1])
        source_ids = [chunks[ordered[0]][2]]
        for index in ordered[1:]:
            following = chunks[index][1]
            if points[-1].distance_to(following[0]) > tolerance:
                raise ValueError(f"Cut Line component {component_number} contains an unresolved gap.")
            points.extend(following[1:])
            source_ids.append(chunks[index][2])
        chains.append((points, source_ids))
    return chains, ignored_ids, simplified_ids


def _open_contour_points(entities: dict[str, object], entity_ids: list[str]) -> list[Point]:
    """Compatibility helper requiring exactly one valid open chain."""
    chains, _ignored_ids, _simplified_ids = _open_contour_chains(entities, entity_ids)
    if len(chains) != 1:
        raise ValueError("Open offset selection contains more than one disconnected path.")
    return chains[0][0]


def _line_intersection(a1: Point, a2: Point, b1: Point, b2: Point) -> Point | None:
    dax, day = a2.x - a1.x, a2.y - a1.y
    dbx, dby = b2.x - b1.x, b2.y - b1.y
    denominator = dax * dby - day * dbx
    if abs(denominator) <= TOLERANCE:
        return None
    t = ((b1.x - a1.x) * dby - (b1.y - a1.y) * dbx) / denominator
    return Point(a1.x + t * dax, a1.y + t * day)


def _rectangle_points(rectangle: Rectangle) -> list[Point]:
    min_x, min_y, max_x, max_y = _rectangle_box(rectangle)
    return [Point(min_x, min_y), Point(max_x, min_y), Point(max_x, max_y), Point(min_x, max_y)]


def _offset_closed_polygon(points: list[Point], distance: float, side: OffsetSide) -> list[Point]:
    paths = _offset_closed_polygons(points, distance, side)
    if len(paths) != 1:
        raise ValueError("This offset splits into multiple contours; use it as a toolpath rather than editable outline geometry.")
    return paths[0]


def _offset_closed_polygons(points: list[Point], distance: float, side: OffsetSide) -> list[list[Point]]:
    points = _clean_polygon_points(points)
    if len(points) < 3:
        raise ValueError("Closed contour requires at least three points.")
    if side == OffsetSide.ON or distance == 0:
        return [list(points)]
    area = sum(
        point.x * following.y - following.x * point.y
        for point, following in zip(points, points[1:] + points[:1])
    ) / 2
    if abs(area) <= TOLERANCE:
        raise ValueError("Closed contour has zero area.")
    scale = 1_000_000.0
    path = [(round(point.x * scale), round(point.y * scale)) for point in points]
    if not pyclipper.Orientation(path):
        path.reverse()
    offsetter = pyclipper.PyclipperOffset(8.0, 0.01 * scale)
    offsetter.AddPath(path, pyclipper.JT_MITER, pyclipper.ET_CLOSEDPOLYGON)
    delta = distance * scale * (1 if side == OffsetSide.OUTSIDE else -1)
    solutions = offsetter.Execute(delta)
    results = [
        _clean_polygon_points([Point(x / scale, y / scale) for x, y in solution])
        for solution in solutions
        if len(solution) >= 3
    ]
    if not results:
        raise ValueError("Inside offset collapses the selected contour at this tool diameter or allowance.")
    return results


def _narrow_keyhole_centerline(
    points: list[Point], cutter_diameter: float, offset_polygons: list[list[Point]],
) -> tuple[Point, Point] | None:
    """Recover a cutter-width keyhole stem discarded by polygon erosion.

    Polygon offsets represent areas, so a neck exactly one cutter diameter
    wide collapses to a line and disappears. This recognizes only an elongated
    contour with a cutter-width flat end cap and returns that missing medial
    segment when the generated offset does not already reach the cap.
    """
    points = _clean_polygon_points(points)
    if len(points) < 5 or cutter_diameter <= TOLERANCE or not offset_polygons:
        return None
    radius = cutter_diameter / 2
    cap_tolerance = max(cutter_diameter * 0.05, 5e-5)
    extreme_tolerance = max(cutter_diameter * 0.02, 1e-6)
    segments = list(zip(points, points[1:] + points[:1]))

    for segment_index, (first, second) in enumerate(segments):
        cap_width = first.distance_to(second)
        if not cutter_diameter - cap_tolerance <= cap_width <= cutter_diameter + cap_tolerance:
            continue
        midpoint = Point((first.x + second.x) / 2, (first.y + second.y) / 2)
        cap_axis = Point((second.x - first.x) / cap_width, (second.y - first.y) / cap_width)
        stem_axis = Point(-cap_axis.y, cap_axis.x)

        def ranges(axis: Point) -> tuple[float, float, float]:
            values = [
                (point.x - midpoint.x) * axis.x + (point.y - midpoint.y) * axis.y
                for point in points
            ]
            return min(values), max(values), max(values) - min(values)

        min_major, max_major, major_span = ranges(stem_axis)
        min_minor, max_minor, minor_span = ranges(cap_axis)
        if major_span < minor_span * 1.35:
            continue
        if abs(max_major) <= extreme_tolerance:
            direction = stem_axis
        elif abs(min_major) <= extreme_tolerance:
            direction = Point(-stem_axis.x, -stem_axis.y)
            min_major, max_major = -max_major, -min_major
        else:
            continue
        # A genuine collapsed keyhole stem has two straight, parallel walls
        # leaving the cutter-width end cap in the same direction.  Sampling a
        # large rounded letter such as D often produces individual arc chords
        # close to one cutter diameter; treating one of those chords as an end
        # cap creates a destructive medial line across the letter counter.
        previous = points[segment_index - 1]
        following = points[(segment_index + 2) % len(points)]
        first_wall = Point(previous.x - first.x, previous.y - first.y)
        second_wall = Point(following.x - second.x, following.y - second.y)
        first_wall_length = math.hypot(first_wall.x, first_wall.y)
        second_wall_length = math.hypot(second_wall.x, second_wall.y)
        if min(first_wall_length, second_wall_length) < cutter_diameter * 0.75:
            continue
        first_alignment = (
            first_wall.x * stem_axis.x + first_wall.y * stem_axis.y
        ) / first_wall_length
        second_alignment = (
            second_wall.x * stem_axis.x + second_wall.y * stem_axis.y
        ) / second_wall_length
        if (
            abs(first_alignment) < math.cos(math.radians(12))
            or abs(second_alignment) < math.cos(math.radians(12))
            or first_alignment * second_alignment <= 0
        ):
            continue
        bulb_radius = minor_span / 2
        bulb_major = min_major + bulb_radius
        start_major = bulb_major + max(0.0, bulb_radius - radius)
        end_major = -radius
        start = Point(
            midpoint.x + direction.x * start_major,
            midpoint.y + direction.y * start_major,
        )
        end = Point(
            midpoint.x + direction.x * end_major,
            midpoint.y + direction.y * end_major,
        )
        if start.distance_to(end) <= TOLERANCE:
            continue
        # Always make the medial pass for a cutter-width stem. Polygon erosion
        # can either discard it or leave orientation-dependent microscopic
        # islands; neither is a reliable representation of the intended slot.
        return start, end
    return None


def _polygon_has_self_intersection(points: list[Point]) -> bool:
    def orientation(a: Point, b: Point, c: Point) -> float:
        return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)

    def intersects(a: Point, b: Point, c: Point, d: Point) -> bool:
        first = orientation(a, b, c)
        second = orientation(a, b, d)
        third = orientation(c, d, a)
        fourth = orientation(c, d, b)
        return first * second < -TOLERANCE and third * fourth < -TOLERANCE

    count = len(points)
    for first_index in range(count):
        a, b = points[first_index], points[(first_index + 1) % count]
        for second_index in range(first_index + 1, count):
            if second_index in {first_index, (first_index + 1) % count} or (second_index + 1) % count == first_index:
                continue
            c, d = points[second_index], points[(second_index + 1) % count]
            if intersects(a, b, c, d):
                return True
    return False


def _clean_polygon_points(points: list[Point]) -> list[Point]:
    cleaned = []
    for point in points:
        if not cleaned or cleaned[-1].distance_to(point) > TOLERANCE:
            cleaned.append(point)
    if len(cleaned) > 1 and cleaned[0].distance_to(cleaned[-1]) <= TOLERANCE:
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
            if abs(cross) <= TOLERANCE and first[0] * second[0] + first[1] * second[1] >= 0:
                changed = True
                continue
            result.append(point)
        cleaned = result
    return cleaned


def _is_convex_polygon(points: list[Point]) -> bool:
    signs = set()
    for index, point in enumerate(points):
        following = points[(index + 1) % len(points)]
        after = points[(index + 2) % len(points)]
        cross = (following.x - point.x) * (after.y - following.y) - (following.y - point.y) * (
            after.x - following.x
        )
        if abs(cross) > TOLERANCE:
            signs.add(cross > 0)
    return len(signs) == 1


def _offset_circle(circle: Circle, distance: float, side: OffsetSide) -> tuple[Point, float]:
    if side == OffsetSide.OUTSIDE:
        radius = circle.radius + distance
    elif side == OffsetSide.INSIDE:
        radius = circle.radius - distance
    else:
        radius = circle.radius
    if radius <= TOLERANCE:
        raise ValueError("Inside offset is too large for the selected circle.")
    return circle.center, radius


def _prepare_router_polygon(points: list[Point], operation: RouterOffsetOperation) -> list[Point]:
    points = _clean_polygon_points(points)
    area = sum(a.x * b.y - b.x * a.y for a, b in zip(points, points[1:] + points[:1])) / 2
    # The shop's EnRoute/controller convention is CW for an outside climb cut
    # and CCW for an inside climb cut with its clockwise router spindle.
    climb_ccw = operation.side == OffsetSide.INSIDE
    desired_ccw = climb_ccw if operation.cut_direction == "climb" else not climb_ccw
    if (area > 0) != desired_ccw:
        points.reverse()
    start_index = operation.start_index % len(points)
    return points[start_index:] + points[:start_index]


def _entry_wants_inside(operation: RouterOffsetOperation) -> bool:
    if operation.entry_side == "inside":
        return True
    if operation.entry_side == "outside":
        return False
    return operation.side != OffsetSide.OUTSIDE


def _point_in_polygon(point: Point, polygon: list[Point]) -> bool:
    inside = False
    for first, second in zip(polygon, polygon[1:] + polygon[:1]):
        dx, dy = second.x - first.x, second.y - first.y
        cross = (point.x - first.x) * dy - (point.y - first.y) * dx
        if abs(cross) <= 1e-8 and min(first.x, second.x) - 1e-8 <= point.x <= max(first.x, second.x) + 1e-8 and min(first.y, second.y) - 1e-8 <= point.y <= max(first.y, second.y) + 1e-8:
            return True
        if (first.y > point.y) != (second.y > point.y):
            crossing_x = first.x + (point.y - first.y) * dx / dy
            if crossing_x > point.x:
                inside = not inside
    return inside


def _distance_to_closed_path(point: Point, polygon: list[Point]) -> float:
    distance = math.inf
    for first, second in zip(polygon, [*polygon[1:], polygon[0]]):
        dx, dy = second.x - first.x, second.y - first.y
        length_squared = dx * dx + dy * dy
        if length_squared <= TOLERANCE:
            candidate = point.distance_to(first)
        else:
            fraction = max(0.0, min(1.0, ((point.x - first.x) * dx + (point.y - first.y) * dy) / length_squared))
            candidate = point.distance_to(Point(first.x + dx * fraction, first.y + dy * fraction))
        distance = min(distance, candidate)
    return distance


def _profile_entry_fallback_metadata(
    motions: list[Motion],
    actual_mode: str,
) -> dict | None:
    """Describe a requested helix that legitimately emitted another entry."""
    if actual_mode not in {"waste_plunge_fallback", "plunge_on_contour_fallback"}:
        return None
    plunge = motions[0].end if motions else None
    contour_start = _profile_entry_start(motions, actual_mode)
    details = {
        "entry_fallback": True,
        "entry_mode_actual": actual_mode,
        "entry_fallback_reason_code": "helix_does_not_fit",
        "entry_fallback_reason": (
            "No cutter-center helix at or above the configured minimum diameter "
            "fit on the permitted waste side of this contour."
        ),
        "entry_adjustment_kind": (
            "waste_plunge" if actual_mode == "waste_plunge_fallback" else "direct_plunge"
        ),
    }
    if plunge is not None:
        details["entry_plunge_location"] = (plunge.x, plunge.y)
    if contour_start is not None:
        details["entry_effective_start"] = contour_start
    return details


def _prefer_shelf_waste_entry(motions, operation, source, through_entities, obstacles, cutter_radius, link_feed):
    """Keep a partial-profile helix out of its paired finished boundary.

    Only translate the existing orbit along its waste normal. Its diameter,
    pitch and feed stay intact; the original profile follows a verified short
    radial connection. If no bounded, clear translation exists, retain the
    valid shelf entry and disclose that it still crosses the paired boundary.
    """
    helix = _helix_circle(motions) if operation.entry_mode == "helical" else None
    if helix is None:
        return motions, None
    center, radius = helix
    start = Point(motions[0].end.x, motions[0].end.y)
    if radius <= TOLERANCE:
        return motions, None
    nx, ny = (center.x-start.x)/radius, (center.y-start.y)/radius
    stop = 2
    while stop < len(motions) and motions[stop].center is not None and motions[stop].center.distance_to(center)<1e-7:
        stop += 1

    def shifted(distance):
        prefix = [replace(m, end=replace(m.end, x=m.end.x+nx*distance, y=m.end.y+ny*distance),
                          center=Point(m.center.x+nx*distance,m.center.y+ny*distance) if m.center else None)
                  for m in motions[:stop]]
        return [*prefix, Motion(MotionKind.LINEAR, motions[stop-1].end, link_feed), *motions[stop:]]

    def clear_boundary(candidate):
        orbit = _helix_circle(candidate)
        for boundary in through_entities:
            if _profile_entry_self_interference(candidate,'helical',boundary,cutter_radius) is not None:
                return False
            probe=Point(orbit[0].x+orbit[1],orbit[0].y)
            inside = (probe.distance_to(boundary.center)<boundary.radius if isinstance(boundary,Circle)
                      else _point_in_polygon(probe, [a for a,_ in _entry_entity_segments(boundary)]))
            if inside != (operation.side == OffsetSide.INSIDE):
                return False
        return True

    if clear_boundary(motions):
        return motions, None
    limit=4*cutter_radius+operation.helix_diameter
    low=0.0
    refine=True
    for step in range(1,33):
        high=limit*step/32
        candidate=shifted(high)
        if not clear_boundary(candidate):
            low=high
            continue
        # Refine the nearest clear orbit, then check its full sweep/link
        # against the protected shelf face and every unrelated contour.
        for _ in range(14 if refine else 0):
            middle=(low+high)/2
            if clear_boundary(shifted(middle)):high=middle
            else:low=middle
        candidate=shifted(high)
        refine=False
        if _profile_entry_interference(candidate,'helical',source.id,obstacles,cutter_radius,protect_source=True) is not None:
            continue
        a=Point(candidate[stop-1].end.x,candidate[stop-1].end.y)
        b=Point(motions[stop-1].end.x,motions[stop-1].end.y)
        link=[Motion(MotionKind.RAPID,ToolPoint(a.x,a.y,0)),Motion(MotionKind.LINEAR,ToolPoint(b.x,b.y,-1),link_feed)]
        if _profile_entry_self_interference(link,'plunge',source,cutter_radius) is not None:
            continue
        if _entry_segments_interference([(a,b)],source.id,obstacles,cutter_radius) is not None:
            continue
        return candidate, {
            'entry_adjusted':True,'entry_mode_actual':'helical',
            'entry_adjustment_kind':'shelf_waste_shift','entry_adjustment_distance':high,
            'entry_adjustment_reason':'Moved the helix into waste beyond the paired through-cut boundary; the checked connection enters the shelf.',
            'entry_effective_start':(candidate[0].end.x,candidate[0].end.y),
        }
    if operation.helix_fallback == 'waste_plunge':
        # The already-verified profile start is tangent to the protected face.
        # Omit the shelf-marking orbit, never the contour or its tab schedule.
        fallback = [motions[0], Motion(MotionKind.LINEAR, motions[stop-1].end, link_feed), *motions[stop:]]
        return fallback, {
            'entry_adjusted':True,'entry_fallback':True,
            'entry_mode_actual':'plunge_on_contour_fallback',
            'entry_adjustment_kind':'shelf_plunge',
            'entry_fallback_reason':'No bounded clear waste-side shelf helix fit; plunge on the verified profile instead.',
            'entry_effective_start':(start.x,start.y),
        }
    return motions, {'entry_shelf_boundary_unresolved':True}


def _smoothed_profile_entry_motions(
    geometry_kind, geometry, depth, safe_z, plunge_feed, cut_feed, operation,
    source_entity_id, all_entities, cutter_radius, one_inch,
) -> tuple[list[Motion], dict | None]:
    """Prefer a relocated full-size helix before any permitted reduction.

    Probe with a temporary no-shrink operation. Plunge is allowed only as a
    search sentinel here, never as an override of the saved fallback policy.
    A full-size result must pass the same cutter-sweep check as every entry.
    """
    if operation.entry_mode == "helical":
        full_size = replace(operation, helix_min_diameter=operation.helix_diameter,
                            helix_fallback="waste_plunge")
        motions, details = _recover_profile_entry_motions(
            geometry_kind, geometry, depth, safe_z, plunge_feed, cut_feed, full_size,
            source_entity_id, all_entities, cutter_radius, one_inch, prefer_helix=True,
        )
        actual = _actual_profile_entry_mode(motions, "helical", plunge_feed, cut_feed)
        if actual == "helical" and _profile_entry_interference(
            motions, actual, source_entity_id, all_entities, cutter_radius,
            protect_source=operation.side in {OffsetSide.INSIDE, OffsetSide.OUTSIDE},
        ) is None:
            return motions, details
        # With shrink disabled and plunge permitted, this already is the
        # complete search/fallback result. Do not repeat it on tiny holes.
        if (operation.helix_min_diameter >= operation.helix_diameter - TOLERANCE
                and operation.helix_fallback == "waste_plunge"):
            return motions, details
    return _recover_profile_entry_motions(
        geometry_kind, geometry, depth, safe_z, plunge_feed, cut_feed, operation,
        source_entity_id, all_entities, cutter_radius, one_inch,
    )


def _recover_profile_entry_motions(
    geometry_kind: str,
    geometry,
    depth: float,
    safe_z: float,
    plunge_feed: float,
    cut_feed: float,
    operation: RouterOffsetOperation,
    source_entity_id: str,
    all_entities,
    cutter_radius: float,
    one_inch: float,
    *, prefer_helix: bool = False,
) -> tuple[list[Motion], dict | None]:
    """Generate a profile and recover automatically from an unsafe entry.

    The requested start remains authoritative unless the cutter-width sweep of
    its helix or lead reaches another contour. Recovery probes four
    deterministic alternatives: exact one-inch contour advances for
    polygon/circle paths, or subsequent vertices for exact rounded paths. If a
    polygon still has no safe helix, a short ramp along the actual profile is
    used and then recut at full depth. The operation itself is not mutated;
    the chosen per-contour adjustment is recorded on the block.
    """
    prepared_polygon = _prepare_router_polygon(geometry, operation) if geometry_kind == "polygon" else None

    def generate(attempt: int, requested_advance: float | None = None) -> tuple[list[Motion], dict]:
        advance = max(one_inch, TOLERANCE) * attempt if requested_advance is None else requested_advance
        details: dict = {"entry_adjustment_attempt": attempt}
        if geometry_kind == "circle":
            motions = _circle_cut_motions(
                geometry, depth, safe_z, plunge_feed, cut_feed, operation,
                entry_advance=advance,
            )
            details.update(entry_adjustment_distance=advance, entry_adjustment_kind="contour_distance")
        elif geometry_kind == "polygon":
            assert prepared_polygon is not None
            polygon = _reroot_closed_points(prepared_polygon, advance)
            motions = _closed_polygon_cut_motions(
                polygon, depth, safe_z, plunge_feed, cut_feed, operation,
            )
            details.update(entry_adjustment_distance=advance, entry_adjustment_kind="contour_distance")
        elif geometry_kind == "rounded_rectangle":
            candidate_operation = replace(operation, start_index=operation.start_index + attempt)
            motions = _rounded_rectangle_cut_motions(
                geometry[0], geometry[1], depth, safe_z,
                plunge_feed, cut_feed, candidate_operation,
            )
            details.update(entry_adjustment_vertices=attempt, entry_adjustment_kind="vertex")
        elif geometry_kind == "rounded_polygon":
            candidate_operation = replace(operation, start_index=operation.start_index + attempt)
            motions = _rounded_polygon_cut_motions(
                geometry[0], geometry[1], depth, safe_z,
                plunge_feed, cut_feed, candidate_operation,
            )
            details.update(entry_adjustment_vertices=attempt, entry_adjustment_kind="vertex")
        else:
            raise ValueError(f"Unsupported closed-profile geometry kind: {geometry_kind}.")
        return motions, details

    original_motions, _details = generate(0)
    original_actual_mode = _actual_profile_entry_mode(
        original_motions, operation.entry_mode, plunge_feed, cut_feed,
    )
    if (
        operation.entry_mode not in {"helical", "ramp", "plunge"}
        or operation.entry_mode != "helical" and operation.lead_in <= TOLERANCE
    ):
        return original_motions, None
    original_collision = _profile_entry_interference(
        original_motions, original_actual_mode,
        source_entity_id, all_entities, cutter_radius,
        protect_source=operation.side in {OffsetSide.INSIDE, OffsetSide.OUTSIDE},
    )
    search_for_fit = prefer_helix and original_actual_mode != "helical"
    if original_collision is None and not search_for_fit:
        return original_motions, _profile_entry_fallback_metadata(
            original_motions, original_actual_mode,
        )

    candidate_attempts: list[tuple[int, float | None, int | None]] = [
        (attempt, None, None) for attempt in range(1, 5)
    ]
    if prepared_polygon is not None:
        # Prefer actual straight-edge interiors to another corner when an
        # entry needs relocation. A backwards tangent at a re-entrant vertex
        # can run through this same letter even with no neighboring objects.
        interior_candidates = []
        advance = 0.0
        for index, (a, b) in enumerate(zip(prepared_polygon, [*prepared_polygon[1:], prepared_polygon[0]])):
            length = a.distance_to(b)
            if length > max(2 * operation.lead_in, 4 * cutter_radius):
                interior_candidates.append((length, advance + length / 2, index))
            advance += length
        interior_candidates.sort(key=lambda value: (-value[0], value[2]))
        if search_for_fit or original_collision and original_collision[0] == source_entity_id:
            candidate_attempts = [(20 + i, distance, None) for i, (_length, distance, _index) in enumerate(interior_candidates[:12])] + candidate_attempts
        # A long straight edge can remain unsafe for every one-inch probe
        # while the very next edge has ample waste space. Include the actual
        # contour vertices after the ordinary downstream probes so safety is
        # not dependent on the overall size of the part. Cap complex outlines
        # to eight vertices here; the normal generator already searches each
        # segment for local waste-side helix fit on every candidate.
        cumulative = 0.0
        following = [*prepared_polygon[1:], prepared_polygon[0]]
        for vertex_position, (first, second) in enumerate(
            zip(prepared_polygon, following), start=1,
        ):
            cumulative += first.distance_to(second)
            if vertex_position > 8 or cumulative <= TOLERANCE:
                break
            candidate_attempts.append((4 + vertex_position, cumulative, vertex_position))

    seen_starts = {_profile_entry_start(original_motions, original_actual_mode)}
    if search_for_fit and prepared_polygon is not None:
        # The polygon generator already exhausted every edge's midpoint and
        # quarter points at this diameter. Re-rooting a failed probe would
        # repeat that whole search, especially costly on dense small contours.
        candidate_attempts = []
    if geometry_kind == "rounded_polygon":
        # A compensated imported outline can have more than four genuinely
        # different start vertices. Test each source vertex once before
        # concluding that its requested helix has no safe placement.
        candidate_attempts = [
            (attempt, None, attempt)
            for attempt in range(1, max(4, min(len(geometry[0]) - 1, 8)) + 1)
        ]
    for attempt, requested_advance, vertex_position in candidate_attempts:
        candidate_motions, details = generate(attempt, requested_advance)
        if vertex_position is not None and geometry_kind == "polygon":
            details.pop("entry_adjustment_distance", None)
            details.update(
                entry_adjustment_kind="vertex",
                entry_adjustment_vertices=vertex_position,
            )
        candidate_actual_mode = _actual_profile_entry_mode(
            candidate_motions, operation.entry_mode, plunge_feed, cut_feed,
        )
        if prefer_helix and candidate_actual_mode != "helical":
            continue
        candidate_start = _profile_entry_start(candidate_motions, candidate_actual_mode)
        if candidate_start in seen_starts:
            continue
        seen_starts.add(candidate_start)
        if _profile_entry_interference(
            candidate_motions, candidate_actual_mode,
            source_entity_id, all_entities, cutter_radius,
            protect_source=operation.side in {OffsetSide.INSIDE, OffsetSide.OUTSIDE},
        ) is not None:
            continue
        entry_segment = _linear_entry_segment(candidate_motions)
        details.update(
            entry_adjusted=True,
            entry_mode_actual=candidate_actual_mode,
            entry_effective_start=(
                (entry_segment[1].x, entry_segment[1].y)
                if candidate_actual_mode in {"ramp", "plunge", "waste_plunge_fallback"} and entry_segment
                else candidate_start
            ),
        )
        if original_collision is not None:
            details.update(
                entry_original_interference_entity_id=original_collision[0],
                entry_original_interference_point=(original_collision[1].x, original_collision[1].y),
            )
        elif search_for_fit:
            details["entry_adjustment_reason"] = "Moved to preserve the requested full-size helix."
        fallback_details = _profile_entry_fallback_metadata(
            candidate_motions, candidate_actual_mode,
        )
        if fallback_details is not None:
            # Preserve relocation as the primary adjustment while recording
            # the actual entry method and why the requested helix was not used.
            fallback_kind = fallback_details.pop("entry_adjustment_kind")
            details.update(fallback_details)
            details["entry_fallback_kind"] = fallback_kind
        return candidate_motions, details

    if original_collision is None:
        return original_motions, _profile_entry_fallback_metadata(original_motions, original_actual_mode)

    omit_linear_lead = operation.entry_mode in {"plunge", "ramp"} and operation.lead_in > TOLERANCE
    omit_unsafe_helix = operation.entry_mode == "helical" and operation.helix_fallback == "waste_plunge"
    if original_collision[0] == source_entity_id and (omit_linear_lead or omit_unsafe_helix):
        # No tested tangent lead fits. Omit the unsafe excursion, not the
        # contour: plunge on the compensated path (or ramp along that path).
        # Keep depths, cutter, tabs, direction and the requested feed rates.
        fallback_operation = replace(operation, lead_in=0.0,
            entry_mode="plunge" if omit_unsafe_helix else operation.entry_mode)
        if prepared_polygon is not None:
            lengths = [a.distance_to(b) for a,b in zip(prepared_polygon,[*prepared_polygon[1:],prepared_polygon[0]])]
            longest = max(range(len(lengths)), key=lambda i:lengths[i])
            polygon = _reroot_closed_points(prepared_polygon, sum(lengths[:longest])+lengths[longest]/2)
            fallback_motions = _closed_polygon_cut_motions(polygon, depth, safe_z, plunge_feed, cut_feed, fallback_operation)
        else:
            fallback_motions, _ = _smoothed_profile_entry_motions(
                geometry_kind, geometry, depth, safe_z, plunge_feed, cut_feed,
                fallback_operation, source_entity_id, all_entities, cutter_radius, one_inch)
        actual = "contour_ramp_fallback" if operation.entry_mode == "ramp" else "plunge_on_contour_fallback"
        return fallback_motions, {
            "entry_adjusted": True, "entry_fallback": True,
            "entry_adjustment_kind": "unsafe_helix_omitted" if omit_unsafe_helix else "unsafe_lead_omitted", "entry_mode_actual": actual,
            "entry_fallback_reason_code": "entry_gouges_source",
            "entry_fallback_reason": "No tested entry stayed clear of this contour; enter on the compensated profile instead.",
            "entry_original_interference_entity_id": source_entity_id,
            "entry_effective_start": (fallback_motions[0].end.x, fallback_motions[0].end.y),
        }

    active_tabs = (
        (operation.tab_count > 0 or operation.tab_mode in {"minimum_and_spacing", "minimum_avoid_corners", "support_aware", "corners_and_spacing", "manual", "aligned"})
        and depth > operation.tab_floor_depth + TOLERANCE
    )
    if (
        operation.entry_mode == "helical"
        and operation.helix_fallback == "waste_plunge"
        and geometry_kind == "polygon"
        and not active_tabs
    ):
        assert prepared_polygon is not None
        # Lead-in is not an editable helical-entry setting.  Do not let a
        # stale, hidden linear-lead value silently control this recovery move.
        ramp_distance = one_inch * 0.5
        fallback_motions = _closed_polygon_contour_ramp_motions(
            prepared_polygon, ramp_distance, depth, safe_z, plunge_feed, cut_feed, operation,
        )
        return fallback_motions, {
            "entry_adjusted": True,
            "entry_fallback": True,
            "entry_adjustment_attempt": 5,
            "entry_adjustment_kind": "contour_ramp",
            "entry_adjustment_distance": min(
                ramp_distance,
                sum(first.distance_to(second) for first, second in zip(
                    prepared_polygon, [*prepared_polygon[1:], prepared_polygon[0]],
                )),
            ),
            "entry_mode_actual": "contour_ramp_fallback",
            "entry_original_mode_actual": original_actual_mode,
            "entry_fallback_reason_code": "helix_interferes",
            "entry_fallback_reason": (
                "Every tested helical entry location conflicted with neighboring geometry."
            ),
            "entry_original_interference_entity_id": original_collision[0],
            "entry_original_interference_point": (original_collision[1].x, original_collision[1].y),
            "entry_effective_start": (prepared_polygon[0].x, prepared_polygon[0].y),
        }
    if (
        operation.entry_mode == "helical"
        and operation.helix_fallback == "waste_plunge"
        and geometry_kind == "rounded_rectangle"
        and not active_tabs
    ):
        # A compensated rectangle is an exact sequence of straight edges and
        # corner arcs. If no helix location clears its neighboring geometry,
        # descend directly at the profile start; this adds no XY entry sweep
        # and retains the exact rounded cutting contour.
        fallback_operation = replace(operation, entry_mode="plunge", lead_in=0.0)
        fallback_motions = _rounded_rectangle_cut_motions(
            geometry[0], geometry[1], depth, safe_z,
            plunge_feed, cut_feed, fallback_operation,
        )
        return fallback_motions, {
            "entry_adjusted": True,
            "entry_fallback": True,
            "entry_adjustment_attempt": 5,
            "entry_adjustment_kind": "direct_plunge",
            "entry_mode_actual": "plunge_on_contour_fallback",
            "entry_original_mode_actual": original_actual_mode,
            "entry_fallback_reason_code": "helix_interferes",
            "entry_fallback_reason": (
                "Every tested helical entry location conflicted with neighboring geometry."
            ),
            "entry_original_interference_entity_id": original_collision[0],
            "entry_original_interference_point": (original_collision[1].x, original_collision[1].y),
            "entry_effective_start": (fallback_motions[0].end.x, fallback_motions[0].end.y),
        }
    if omit_unsafe_helix:
        # Tabbed/rounded profiles cannot use the contour-ramp recovery above.
        # Remove only the initial orbit, preserving the exact remaining path
        # (including raised tab spans and their cleanup coordinates).
        fallback = _checked_helix_to_plunge(
            original_motions, source_entity_id, all_entities, cutter_radius,
            plunge_feed, protect_source=operation.side in {OffsetSide.INSIDE, OffsetSide.OUTSIDE},
        )
        if fallback is not None:
            return fallback, {
                "entry_adjusted": True, "entry_fallback": True,
                "entry_adjustment_kind": "checked_neighbor_plunge",
                "entry_mode_actual": "plunge_on_contour_fallback",
                "entry_fallback_reason_code": "helix_interferes",
                "entry_fallback_reason": "No tested helix cleared neighboring geometry; plunge at the collision-checked profile start, preserving tabs.",
                "entry_original_interference_entity_id": original_collision[0],
                "entry_effective_start": (fallback[0].end.x, fallback[0].end.y),
            }
    # Preserve the requested entry so normal interference preflight reports a
    # true blocking error with both affected geometry objects.
    return original_motions, None


def _profile_plunge_interference(point, source_entity_id, all_entities, cutter_radius, *, protect_source=True):
    """Check the cutter disk at a vertical entry; ordinary tangency is allowed."""
    limit = cutter_radius - max(1e-7, cutter_radius * 1e-5)
    for entity in all_entities:
        if entity.id == source_entity_id and not protect_source:
            continue
        bounds = entity_bounds(entity)
        if bounds is None or not _boxes_overlap_with_tolerance(
            (point.x-cutter_radius, point.y-cutter_radius, point.x+cutter_radius, point.y+cutter_radius), bounds,
        ):
            continue
        if isinstance(entity, Circle):
            gap = abs(point.distance_to(entity.center) - entity.radius)
        else:
            gap = min((_entry_segment_distance(point, point, a, b)[0]
                       for a, b in _entry_entity_segments(entity)), default=math.inf)
        if gap < limit:
            return entity.id, point
    return None


def _checked_helix_to_plunge(motions, source_entity_id, all_entities, cutter_radius, plunge_feed, *, protect_source=True):
    helix = _helix_circle(motions)
    if helix is None:
        return None
    stop = 2
    while (stop < len(motions) and motions[stop].kind in {MotionKind.ARC_CW, MotionKind.ARC_CCW}
           and motions[stop].center is not None and motions[stop].center.distance_to(helix[0]) < TOLERANCE):
        stop += 1
    landing = motions[stop-1].end
    point = Point(landing.x, landing.y)
    # Only replace a complete orbit ending at the original profile start.
    # Never infer a new connection across the part or rewrite tabbed motion.
    if (stop >= len(motions) or landing.z >= 0
            or point.distance_to(Point(motions[0].end.x, motions[0].end.y)) > TOLERANCE
            or _profile_plunge_interference(point, source_entity_id, all_entities, cutter_radius,
                                           protect_source=protect_source) is not None):
        return None
    return [replace(motions[0], end=ToolPoint(point.x, point.y, motions[0].end.z)),
            Motion(MotionKind.LINEAR, landing, plunge_feed), *motions[stop:]]


def _reroot_closed_points(points: list[Point], advance: float) -> list[Point]:
    """Start a closed polyline an exact distance farther along its direction."""
    cleaned = _clean_polygon_points(points)
    following = [*cleaned[1:], cleaned[0]]
    lengths = [first.distance_to(second) for first, second in zip(cleaned, following)]
    perimeter = sum(lengths)
    if perimeter <= TOLERANCE:
        return cleaned
    target = advance % perimeter
    if target <= TOLERANCE or perimeter - target <= TOLERANCE:
        return cleaned
    traveled = 0.0
    for index, (first, second, length) in enumerate(zip(cleaned, following, lengths)):
        if traveled + length + TOLERANCE < target:
            traveled += length
            continue
        fraction = max(0.0, min(1.0, (target - traveled) / length))
        entry = Point(
            first.x + (second.x - first.x) * fraction,
            first.y + (second.y - first.y) * fraction,
        )
        next_index = (index + 1) % len(cleaned)
        rotated = cleaned[next_index:] + cleaned[:next_index]
        if entry.distance_to(second) <= TOLERANCE:
            return rotated
        return [entry, *rotated]
    return cleaned


def _linear_entry_segment(motions: list[Motion]) -> tuple[Point, Point] | None:
    current = None
    for motion in motions:
        end = Point(motion.end.x, motion.end.y)
        if current is not None and motion.kind != MotionKind.RAPID and current.distance_to(end) > TOLERANCE:
            return current, end
        current = end
    return None


def _linear_entry_contour_start(motions: list[Motion]) -> tuple[float, float] | None:
    segment = _linear_entry_segment(motions)
    return None if segment is None else (round(segment[1].x, 8), round(segment[1].y, 8))


def _actual_profile_entry_mode(
    motions: list[Motion],
    requested_mode: str,
    plunge_feed: float,
    cut_feed: float,
) -> str:
    """Describe the entry that was actually emitted, including helix fallbacks."""
    if requested_mode != "helical":
        return requested_mode
    if _helix_circle(motions) is not None:
        return "helical"
    # A direct fallback plunges on the profile, so the initial XY point occurs
    # again when the closed contour returns to its start. A waste-side plunge
    # begins away from the profile and links to it only after reaching depth.
    if motions:
        initial = Point(motions[0].end.x, motions[0].end.y)
        if any(
            Point(motion.end.x, motion.end.y).distance_to(initial) <= TOLERANCE
            for motion in motions[2:]
        ):
            return "plunge_on_contour_fallback"
        return "waste_plunge_fallback"
    return "plunge_on_contour_fallback"


def _profile_entry_start(motions: list[Motion], actual_mode: str) -> tuple[float, float] | None:
    if not motions:
        return None
    if actual_mode in {"helical", "plunge_on_contour_fallback"}:
        return (round(motions[0].end.x, 8), round(motions[0].end.y, 8))
    return _linear_entry_contour_start(motions)


def _helical_entry_segments(motions: list[Motion]) -> list[tuple[Point, Point]]:
    """Flatten the initial same-center helical arc run for clearance checks."""
    first_arc = next(
        (
            index for index, motion in enumerate(motions)
            if index > 0
            and motion.kind in {MotionKind.ARC_CW, MotionKind.ARC_CCW}
            and motion.center is not None
            and min(motions[index - 1].end.z, motion.end.z) < -TOLERANCE
        ),
        None,
    )
    if first_arc is None:
        return []
    center = motions[first_arc].center
    if center is None:
        return []
    points = [Point(motions[first_arc - 1].end.x, motions[first_arc - 1].end.y)]
    for motion in motions[first_arc:]:
        if (
            motion.kind not in {MotionKind.ARC_CW, MotionKind.ARC_CCW}
            or motion.center is None
            or motion.center.distance_to(center) > 1e-7
        ):
            break
        points.extend(_arc_path_points(
            points[-1], motion.kind, Point(motion.end.x, motion.end.y), center,
            maximum_step=math.pi / 36,
        )[1:])
    return [
        (first, second) for first, second in zip(points, points[1:])
        if first.distance_to(second) > TOLERANCE
    ]


def _profile_entry_interference(
    motions: list[Motion],
    actual_mode: str,
    source_entity_id: str,
    all_entities,
    cutter_radius: float,
    *, protect_source: bool = False,
) -> tuple[str, Point] | None:
    if protect_source:
        source = next((e for e in all_entities if e.id == source_entity_id), None)
        hit = _profile_entry_self_interference(motions, actual_mode, source, cutter_radius)
        if hit is not None:
            return source_entity_id, hit
    if actual_mode == "helical":
        return _helical_entry_interference(
            motions, source_entity_id, all_entities, cutter_radius,
        )
    elif actual_mode == "plunge_on_contour_fallback":
        # There is no XY entry excursion: the cutter descends at the profile
        # and its following move is the intended contour itself.
        segments = []
    else:
        segment = _linear_entry_segment(motions)
        segments = [] if segment is None else [segment]
    return _entry_segments_interference(
        segments, source_entity_id, all_entities, cutter_radius,
    )


def _profile_entry_self_interference(motions, actual_mode, source, cutter_radius):
    """Allow normal cutter tangency, but never an entry through its own part."""
    if source is None or actual_mode in {"plunge_on_contour_fallback", "contour_ramp_fallback"}:
        return None
    tolerance = max(1e-7, cutter_radius * 1e-5)
    limit = cutter_radius - tolerance
    helix = _helix_circle(motions) if actual_mode == "helical" else None
    segment = None if helix else _linear_entry_segment(motions)
    if helix is None and segment is None:
        return None
    if isinstance(source, Circle):
        # Use the real circle: inscribed polygon chords falsely report gouges
        # on a correctly tangent inside cut.
        if helix:
            center, radius = helix
            distance = center.distance_to(source.center)
            gap = max(distance-radius-source.radius, abs(radius-source.radius)-distance, 0.0)
            return Point(center.x, center.y) if gap < limit else None
        gap, _tool_point, point = _circle_path_segment_distance(source.center, source.radius, *segment)
        return point if gap < limit else None
    for a, b in _entry_entity_segments(source):
        gap, tool_point, contour_point = (_circle_path_segment_distance(*helix, a, b)
            if helix else _entry_segment_distance(*segment, a, b))
        if gap < limit:
            return Point((tool_point.x+contour_point.x)/2, (tool_point.y+contour_point.y)/2)
    return None


def _corner_avoiding_entry_segments(points, clearance):
    """Prefer edge interiors away from sharp concave turns; retain ties/order."""
    area = sum(a.x*b.y-b.x*a.y for a,b in zip(points,[*points[1:],points[0]]))
    corners = []
    for i, point in enumerate(points):
        a,b = points[i-1], points[(i+1)%len(points)]
        ux,uy,vx,vy = point.x-a.x,point.y-a.y,b.x-point.x,b.y-point.y
        lengths = math.hypot(ux,uy)*math.hypot(vx,vy)
        if lengths > TOLERANCE and (ux*vy-uy*vx)*area < 0 and (ux*vx+uy*vy)/lengths < math.cos(math.pi/6):
            corners.append(point)
    if not corners:
        return range(len(points))
    def cramped(i):
        a,b=points[i],points[(i+1)%len(points)]
        midpoint=Point((a.x+b.x)/2,(a.y+b.y)/2)
        return min(midpoint.distance_to(c) for c in corners) < clearance
    return sorted(range(len(points)), key=lambda i:(cramped(i),i))


def _helical_entry_interference(
    motions: list[Motion],
    source_entity_id: str,
    all_entities,
    cutter_radius: float,
) -> tuple[str, Point] | None:
    """Check the XY annulus swept by a helix without flattening every turn."""
    helix = _helix_circle(motions)
    if helix is None:
        return None
    center, radius = helix
    entry_bounds = (
        center.x - radius - cutter_radius,
        center.y - radius - cutter_radius,
        center.x + radius + cutter_radius,
        center.y + radius + cutter_radius,
    )
    tolerance = max(1e-7, cutter_radius * 1e-5)
    for entity in all_entities:
        if entity.id == source_entity_id:
            continue
        bounds = entity_bounds(entity)
        if bounds is None or not _boxes_overlap_with_tolerance(entry_bounds, bounds):
            continue
        for contour_start, contour_end in _entry_entity_segments(entity):
            gap, tool_point, contour_point = _circle_path_segment_distance(
                center, radius, contour_start, contour_end,
            )
            if gap <= cutter_radius + tolerance:
                return entity.id, Point(
                    (tool_point.x + contour_point.x) / 2,
                    (tool_point.y + contour_point.y) / 2,
                )
    return None


def _helix_circle(motions: list[Motion]) -> tuple[Point, float] | None:
    # Generated helices always begin after the rapid and Z0 positioning move.
    # Later profile arcs at full depth (rounded letters/corners) are cutting
    # geometry and must never be mistaken for the entry helix.
    if len(motions) > 2:
        motion = motions[2]
        if (
            motion.kind in {MotionKind.ARC_CW, MotionKind.ARC_CCW}
            and motion.center is not None
            and abs(motions[1].end.z) <= TOLERANCE
            and motion.end.z < -TOLERANCE
        ):
            previous = Point(motions[1].end.x, motions[1].end.y)
            return motion.center, previous.distance_to(motion.center)
    return None


def _circle_path_segment_distance(
    center: Point,
    radius: float,
    start: Point,
    end: Point,
) -> tuple[float, Point, Point]:
    """Return the shortest distance between a circle path and a line segment."""
    dx, dy = end.x - start.x, end.y - start.y
    length_squared = dx * dx + dy * dy
    if length_squared <= TOLERANCE:
        contour = start
    else:
        projection = max(0.0, min(1.0, (
            (center.x - start.x) * dx + (center.y - start.y) * dy
        ) / length_squared))
        closest = Point(start.x + dx * projection, start.y + dy * projection)
        minimum_radius = center.distance_to(closest)
        start_radius = center.distance_to(start)
        end_radius = center.distance_to(end)
        maximum_radius = max(start_radius, end_radius)
        if minimum_radius <= radius <= maximum_radius:
            # Solve the segment/circle intersection for an exact focus point.
            ox, oy = start.x - center.x, start.y - center.y
            a = length_squared
            b = 2.0 * (ox * dx + oy * dy)
            c = ox * ox + oy * oy - radius * radius
            discriminant = max(0.0, b * b - 4.0 * a * c)
            roots = [
                value for value in (
                    (-b - math.sqrt(discriminant)) / (2.0 * a),
                    (-b + math.sqrt(discriminant)) / (2.0 * a),
                )
                if -TOLERANCE <= value <= 1.0 + TOLERANCE
            ]
            fraction = max(0.0, min(1.0, roots[0])) if roots else projection
            contour = Point(start.x + dx * fraction, start.y + dy * fraction)
        elif radius < minimum_radius:
            contour = closest
        else:
            contour = start if start_radius >= end_radius else end
    radial_distance = center.distance_to(contour)
    if radial_distance <= TOLERANCE:
        tool_point = Point(center.x + radius, center.y)
    else:
        tool_point = Point(
            center.x + (contour.x - center.x) * radius / radial_distance,
            center.y + (contour.y - center.y) * radius / radial_distance,
        )
    return tool_point.distance_to(contour), tool_point, contour


def _linear_entry_interference(
    motions: list[Motion],
    source_entity_id: str,
    all_entities,
    cutter_radius: float,
) -> tuple[str, Point] | None:
    segment = _linear_entry_segment(motions)
    return _entry_segments_interference(
        [] if segment is None else [segment], source_entity_id, all_entities, cutter_radius,
    )


def _entry_segments_interference(
    segments: list[tuple[Point, Point]],
    source_entity_id: str,
    all_entities,
    cutter_radius: float,
) -> tuple[str, Point] | None:
    if not segments:
        return None
    entry_bounds = (
        min(point.x for segment in segments for point in segment) - cutter_radius,
        min(point.y for segment in segments for point in segment) - cutter_radius,
        max(point.x for segment in segments for point in segment) + cutter_radius,
        max(point.y for segment in segments for point in segment) + cutter_radius,
    )
    for entity in all_entities:
        if entity.id == source_entity_id:
            continue
        bounds = entity_bounds(entity)
        if bounds is None or not _boxes_overlap_with_tolerance(entry_bounds, bounds):
            continue
        for tool_start, tool_end in segments:
            for contour_start, contour_end in _entry_entity_segments(entity):
                distance, tool_point, contour_point = _entry_segment_distance(
                    tool_start, tool_end, contour_start, contour_end,
                )
                if distance <= cutter_radius + max(1e-7, cutter_radius * 1e-5):
                    return entity.id, Point(
                        (tool_point.x + contour_point.x) / 2,
                        (tool_point.y + contour_point.y) / 2,
                    )
    return None


def _boxes_overlap_with_tolerance(first, second) -> bool:
    return not (
        first[2] < second[0] or first[0] > second[2]
        or first[3] < second[1] or first[1] > second[3]
    )


def _entry_entity_segments(entity) -> list[tuple[Point, Point]]:
    if isinstance(entity, Circle):
        points = [
            Point(
                entity.center.x + entity.radius * math.cos(2 * math.pi * index / 96),
                entity.center.y + entity.radius * math.sin(2 * math.pi * index / 96),
            )
            for index in range(96)
        ]
        return list(zip(points, [*points[1:], points[0]]))
    if isinstance(entity, Rectangle):
        points = _rectangle_points(entity)
        return list(zip(points, [*points[1:], points[0]]))
    if isinstance(entity, Line):
        return [(entity.start, entity.end)]
    if isinstance(entity, Polyline) and len(entity.points) >= 2:
        result = list(zip(entity.points, entity.points[1:]))
        if entity.closed:
            result.append((entity.points[-1], entity.points[0]))
        return result
    return []


def _entry_segment_distance(
    first_start: Point,
    first_end: Point,
    second_start: Point,
    second_end: Point,
) -> tuple[float, Point, Point]:
    ux, uy = first_end.x - first_start.x, first_end.y - first_start.y
    vx, vy = second_end.x - second_start.x, second_end.y - second_start.y
    wx, wy = first_start.x - second_start.x, first_start.y - second_start.y
    a, b, c = ux * ux + uy * uy, ux * vx + uy * vy, vx * vx + vy * vy
    d, e = ux * wx + uy * wy, vx * wx + vy * wy
    denominator = a * c - b * b
    if a <= 1e-20 and c <= 1e-20:
        return first_start.distance_to(second_start), first_start, second_start
    if a <= 1e-20:
        first_fraction, second_fraction = 0.0, max(0.0, min(1.0, e / c))
    elif c <= 1e-20:
        second_fraction, first_fraction = 0.0, max(0.0, min(1.0, -d / a))
    else:
        first_fraction = max(0.0, min(1.0, (b * e - c * d) / denominator)) if abs(denominator) > 1e-20 else 0.0
        second_fraction = (b * first_fraction + e) / c
        if second_fraction < 0.0:
            second_fraction = 0.0
            first_fraction = max(0.0, min(1.0, -d / a))
        elif second_fraction > 1.0:
            second_fraction = 1.0
            first_fraction = max(0.0, min(1.0, (b - d) / a))
    first_point = Point(first_start.x + ux * first_fraction, first_start.y + uy * first_fraction)
    second_point = Point(second_start.x + vx * second_fraction, second_start.y + vy * second_fraction)
    return first_point.distance_to(second_point), first_point, second_point


def _arc_path_points(start: Point, kind: MotionKind, end: Point, center: Point, maximum_step: float = math.pi / 18) -> list[Point]:
    start_angle = math.atan2(start.y - center.y, start.x - center.x)
    end_angle = math.atan2(end.y - center.y, end.x - center.x)
    sweep = (end_angle - start_angle) % (2 * math.pi) if kind == MotionKind.ARC_CCW else -((start_angle - end_angle) % (2 * math.pi))
    if abs(sweep) <= TOLERANCE:
        sweep = 2 * math.pi if kind == MotionKind.ARC_CCW else -2 * math.pi
    radius = start.distance_to(center)
    count = max(2, math.ceil(abs(sweep) / maximum_step))
    return [
        Point(center.x + radius * math.cos(start_angle + sweep * index / count), center.y + radius * math.sin(start_angle + sweep * index / count))
        for index in range(count + 1)
    ]


def _helical_entry_motions(
    start: Point,
    tangent: Point,
    boundary: list[Point],
    depth: float,
    safe_z: float,
    plunge_feed: float,
    operation: RouterOffsetOperation,
) -> list[Motion]:
    helix_feed = operation.helix_feed_override or plunge_feed
    wants_inside = _entry_wants_inside(operation)
    left = Point(-tangent.y, tangent.x)
    selected = None
    # The stored diameter is a preferred maximum. Small holes and keyhole
    # bulbs often cannot accept that full orbit, but rejecting a mixed batch is
    # unnecessarily disruptive. Reduce the cutter-center orbit per contour,
    # while retaining a 25% floor so a nominal helix cannot silently become an
    # effectively stationary plunge.
    diameter_scales = (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.25)
    radius = operation.helix_diameter / 2.0
    for scale in diameter_scales:
        candidate_radius = radius * scale
        if candidate_radius * 2.0 < operation.helix_min_diameter - TOLERANCE:
            continue
        candidates = (
            (Point(start.x + left.x * candidate_radius, start.y + left.y * candidate_radius), MotionKind.ARC_CCW),
            (Point(start.x - left.x * candidate_radius, start.y - left.y * candidate_radius), MotionKind.ARC_CW),
        )
        for center, kind in candidates:
            radial_angle = math.atan2(start.y - center.y, start.x - center.x)
            direction = 1.0 if kind == MotionKind.ARC_CCW else -1.0
            samples = [
                Point(center.x + candidate_radius * math.cos(radial_angle + direction * 2 * math.pi * index / 72), center.y + candidate_radius * math.sin(radial_angle + direction * 2 * math.pi * index / 72))
                for index in range(1, 72)
            ]
            if all(
                _point_in_polygon(point, boundary) == wants_inside
                and (operation.helix_clearance <= TOLERANCE
                     or _distance_to_closed_path(point, boundary) >= operation.helix_clearance - TOLERANCE)
                for point in samples
            ):
                selected = center, kind, radial_angle, direction, candidate_radius
                break
        if selected is not None:
            break
    if selected is None:
        if operation.helix_fallback == "waste_plunge":
            # Preserve helical entry on contours where it fits, but do not let
            # one tiny cleanout reject an otherwise valid grouped operation.
            # The plunge is nudged onto the requested waste side and feeds back
            # to the contour so it does not mark the finished wall directly.
            fallback_radius = max(operation.helix_diameter * 0.025, TOLERANCE * 100)
            for normal_sign in (1.0, -1.0):
                center = Point(
                    start.x + left.x * fallback_radius * normal_sign,
                    start.y + left.y * fallback_radius * normal_sign,
                )
                samples = [
                    Point(
                        center.x + (start.x - center.x) * index / 8,
                        center.y + (start.y - center.y) * index / 8,
                    )
                    for index in range(8)
                ]
                if all(_point_in_polygon(point, boundary) == wants_inside for point in samples):
                    return [
                        Motion(MotionKind.RAPID, ToolPoint(center.x, center.y, safe_z)),
                        Motion(MotionKind.LINEAR, ToolPoint(center.x, center.y, -depth), plunge_feed),
                        Motion(MotionKind.LINEAR, ToolPoint(start.x, start.y, -depth), plunge_feed),
                    ]
            return [
                Motion(MotionKind.RAPID, ToolPoint(start.x, start.y, safe_z)),
                Motion(MotionKind.LINEAR, ToolPoint(start.x, start.y, -depth), plunge_feed),
            ]
        side = "inside" if wants_inside else "outside"
        raise ValueError(
            f"The preferred {operation.helix_diameter:g} diameter helical entry cannot be safely reduced to fit on the {side} of this contour. "
            "Choose another start vertex/quadrant or use a smaller cutter."
        )
    center, kind, radial_angle, direction, radius = selected
    helix_feed = _size_adjusted_helix_feed(helix_feed, radius, operation.helix_diameter)
    effective_pitch = _effective_helix_pitch(radius, operation.helix_pitch, operation.helix_ramp_angle)
    if effective_pitch <= TOLERANCE:
        raise ValueError("Helical entry requires a positive pitch or ramp-angle limit.")
    revolutions = max(1, math.ceil(depth / effective_pitch - TOLERANCE))
    quarter_count = revolutions * 4
    motions = [
        Motion(MotionKind.RAPID, ToolPoint(start.x, start.y, safe_z)),
        Motion(MotionKind.LINEAR, ToolPoint(start.x, start.y, 0.0), plunge_feed),
    ]
    for index in range(1, quarter_count + 1):
        angle = radial_angle + direction * (math.pi / 2) * index
        point = Point(center.x + radius * math.cos(angle), center.y + radius * math.sin(angle))
        motions.append(Motion(kind, ToolPoint(point.x, point.y, -depth * index / quarter_count), helix_feed, center))
    for index in range(1, operation.helix_finish_revolutions * 4 + 1):
        angle = radial_angle + direction * (math.pi / 2) * index
        point = Point(center.x + radius * math.cos(angle), center.y + radius * math.sin(angle))
        motions.append(Motion(kind, ToolPoint(point.x, point.y, -depth), helix_feed, center))
    return motions


def _closed_polygon_cut_motions(points, depth, safe_z, plunge_feed, cut_feed, operation: RouterOffsetOperation):
    if operation.tab_mode == "aligned":
        # A full-depth entry (and the final plunge before retract) must be in
        # an unprotected gap, regardless of source start vertex/direction.
        from .aligned_tabs import aligned_intervals
        sequence = [*points, points[0]]
        intervals = aligned_intervals(sequence, operation)
        total = sum(a.distance_to(b) for a, b in zip(sequence, sequence[1:]))
        if intervals[0][0] <= TOLERANCE or intervals[-1][1] >= total - TOLERANCE:
            gaps = [(a[1], b[0]) for a, b in zip(intervals, intervals[1:])]
            gaps += [(intervals[-1][1], total), (0.0, intervals[0][0])]
            start, end = max(gaps, key=lambda gap: gap[1] - gap[0])
            points = _reroot_closed_points(points, (start + end) / 2)
    first, second = points[0], points[1]
    dx, dy = second.x - first.x, second.y - first.y
    length = math.hypot(dx, dy)
    ux, uy = dx / length, dy / length
    lead_start = Point(first.x - ux * operation.lead_in, first.y - uy * operation.lead_in)
    if operation.entry_mode == "helical":
        entry_error = None
        entry_candidates = []
        # Search every viable contour location for the requested helix before
        # allowing the per-contour plunge fallback.  Passing the fallback
        # policy into each probe used to let the first cramped segment return
        # a successful plunge and prematurely end the search, even when a
        # later segment had ample room for a true helix.
        search_operation = replace(operation, helix_fallback="strict_error")
        # Treat the selected start segment as the first preference, then search
        # the rest of a complex contour (notably a keyhole bulb) for safe waste
        # space before rejecting the operation.
        for segment_index in _corner_avoiding_entry_segments(points, operation.helix_diameter * 1.5):
            segment_start = points[segment_index]
            segment_end = points[(segment_index + 1) % len(points)]
            segment_dx = segment_end.x - segment_start.x
            segment_dy = segment_end.y - segment_start.y
            segment_length = math.hypot(segment_dx, segment_dy)
            if segment_length <= TOLERANCE:
                continue
            segment_tangent = Point(segment_dx / segment_length, segment_dy / segment_length)
            for fraction in (0.5, 0.25, 0.75):
                entry = Point(segment_start.x + segment_dx * fraction, segment_start.y + segment_dy * fraction)
                entry_candidates.append((entry, segment_tangent, segment_index))
                try:
                    motions = _helical_entry_motions(
                        entry, segment_tangent, points, depth, safe_z,
                        plunge_feed, search_operation,
                    )
                    sequence = [entry, *points[segment_index + 1:], *points[:segment_index + 1], entry]
                    break
                except ValueError as exc:
                    entry_error = exc
            else:
                continue
            break
        else:
            if operation.helix_fallback != "waste_plunge" or not entry_candidates:
                raise entry_error or ValueError("Helical entry does not fit on the selected starting segment.")
            # No legitimate helix fits anywhere on this contour.  Only now is
            # the configured fallback allowed, at the preferred first probe.
            entry, segment_tangent, segment_index = entry_candidates[0]
            motions = _helical_entry_motions(
                entry, segment_tangent, points, depth, safe_z,
                plunge_feed, operation,
            )
            sequence = [entry, *points[segment_index + 1:], *points[:segment_index + 1], entry]
    elif operation.entry_mode == "ramp":
        motions = [Motion(MotionKind.RAPID, ToolPoint(lead_start.x, lead_start.y, safe_z))]
        motions.append(Motion(MotionKind.LINEAR, ToolPoint(lead_start.x, lead_start.y, 0.0), plunge_feed))
        if operation.lead_in > TOLERANCE:
            motions.append(Motion(MotionKind.LINEAR, ToolPoint(first.x, first.y, -depth), plunge_feed))
            sequence = [first, *points[1:], first]
        else:
            motions.append(Motion(MotionKind.LINEAR, ToolPoint(second.x, second.y, -depth), plunge_feed))
            sequence = [second, *points[2:], first, second]
    else:
        motions = [Motion(MotionKind.RAPID, ToolPoint(lead_start.x, lead_start.y, safe_z))]
        motions.append(Motion(MotionKind.LINEAR, ToolPoint(lead_start.x, lead_start.y, -depth), plunge_feed))
        if operation.lead_in > TOLERANCE:
            motions.append(Motion(MotionKind.LINEAR, ToolPoint(first.x, first.y, -depth), cut_feed))
        sequence = [first, *points[1:], first]

    has_tabs = operation.tab_count > 0 or operation.tab_mode in {"minimum_and_spacing", "minimum_avoid_corners", "support_aware", "corners_and_spacing", "manual", "aligned"}
    # A retaining tab is a fixed floor measured from the operation's final
    # depth. Respect that floor on every pass that would cut below it. If tabs
    # are deferred until only the numerically final pass, a near-full
    # penultimate pass (for example 0.127 followed by 0.12711) has already
    # removed the material that the final pass is supposed to leave behind.
    tab_floor_depth = operation.tab_floor_depth
    if has_tabs and depth > tab_floor_depth + TOLERANCE:
        _append_tabbed_path(motions, sequence, depth, operation, cut_feed, plunge_feed)
    else:
        motions.extend(Motion(MotionKind.LINEAR, ToolPoint(point.x, point.y, -depth), cut_feed) for point in sequence[1:])

    current = motions[-1].end
    if abs(current.z + depth) > TOLERANCE:
        motions.append(Motion(MotionKind.LINEAR, ToolPoint(current.x, current.y, -depth), plunge_feed))
        current = motions[-1].end
    if operation.lead_out > TOLERANCE:
        out_dx, out_dy = sequence[1].x - sequence[0].x, sequence[1].y - sequence[0].y
        out_length = math.hypot(out_dx, out_dy)
        lead_end = ToolPoint(current.x + out_dx / out_length * operation.lead_out, current.y + out_dy / out_length * operation.lead_out, -depth)
        motions.append(Motion(MotionKind.LINEAR, lead_end, cut_feed))
        current = lead_end
    motions.append(Motion(MotionKind.RAPID, ToolPoint(current.x, current.y, safe_z)))
    return motions


def _closed_polygon_contour_ramp_motions(
    points: list[Point],
    requested_ramp_distance: float,
    depth: float,
    safe_z: float,
    plunge_feed: float,
    cut_feed: float,
    operation: RouterOffsetOperation,
) -> list[Motion]:
    """Enter along the real profile, then recut the ramp span at full depth.

    This is the final automatic recovery for a helix whose cutter sweep cannot
    clear nearby geometry. It intentionally has no off-contour entry segment.
    Tabbed final passes are excluded by the caller because descending through a
    future raised span would remove its hold-down bridge.
    """
    cleaned = _clean_polygon_points(points)
    sequence = [*cleaned, cleaned[0]]
    perimeter = sum(first.distance_to(second) for first, second in zip(sequence, sequence[1:]))
    if perimeter <= TOLERANCE:
        raise ValueError("Cannot create an on-contour entry for a zero-length profile.")
    ramp_distance = min(max(requested_ramp_distance, TOLERANCE), perimeter)
    ramp_path = _contour_points_between(sequence, 0.0, ramp_distance)
    remainder = _contour_points_between(sequence, ramp_distance, perimeter)

    start = ramp_path[0]
    motions = [
        Motion(MotionKind.RAPID, ToolPoint(start.x, start.y, safe_z)),
        Motion(MotionKind.LINEAR, ToolPoint(start.x, start.y, 0.0), plunge_feed),
    ]
    traveled = 0.0
    for first, second in zip(ramp_path, ramp_path[1:]):
        traveled += first.distance_to(second)
        ramp_z = -depth * min(1.0, traveled / ramp_distance)
        motions.append(Motion(
            MotionKind.LINEAR,
            ToolPoint(second.x, second.y, ramp_z),
            plunge_feed,
        ))

    # Complete the remaining contour at depth, return to the original start,
    # and recut the descending portion so every point reaches the target depth.
    motions.extend(
        Motion(MotionKind.LINEAR, ToolPoint(point.x, point.y, -depth), cut_feed)
        for point in remainder[1:]
    )
    motions.extend(
        Motion(MotionKind.LINEAR, ToolPoint(point.x, point.y, -depth), cut_feed)
        for point in ramp_path[1:]
    )
    current = motions[-1].end
    if operation.lead_out > TOLERANCE and len(ramp_path) > 1:
        previous = ramp_path[-2]
        dx, dy = current.x - previous.x, current.y - previous.y
        length = math.hypot(dx, dy)
        if length > TOLERANCE:
            current = ToolPoint(
                current.x + dx / length * operation.lead_out,
                current.y + dy / length * operation.lead_out,
                -depth,
            )
            motions.append(Motion(MotionKind.LINEAR, current, cut_feed))
    motions.append(Motion(MotionKind.RAPID, ToolPoint(current.x, current.y, safe_z)))
    return motions


def _tab_intervals(sequence, operation: RouterOffsetOperation) -> list[tuple[float, float]]:
    if operation.tab_mode == "support_aware":
        from .support_tabs import planned_intervals
        return list(planned_intervals(tuple((p.x,p.y) for p in sequence),
            operation.tab_width,operation.tab_count,operation.tab_spacing))
    if operation.tab_mode == "minimum_avoid_corners":
        from .smooth_tabs import planned_intervals
        return list(planned_intervals(tuple((p.x,p.y) for p in sequence),
            operation.tab_width,operation.tab_count,operation.tab_spacing))
    if operation.tab_mode == "aligned":
        from .aligned_tabs import aligned_intervals
        return aligned_intervals(sequence, operation)
    lengths = [a.distance_to(b) for a, b in zip(sequence, sequence[1:])]
    total = sum(lengths)
    if total <= TOLERANCE:
        raise ValueError("Cannot place tabs on a zero-length contour.")
    corner_positions = []
    traveled = 0.0
    for length in lengths:
        corner_positions.append(traveled)
        traveled += length
    return _tab_intervals_for_length(total, operation, corner_positions)


def _tab_intervals_for_length(
    total: float,
    operation: RouterOffsetOperation,
    corner_positions: list[float] | None = None,
) -> list[tuple[float, float]]:
    """Place non-overlapping tabs while preserving a full-depth gap between them."""
    if operation.tab_mode == "aligned":
        raise ValueError("Aligned tabs require the complete XY contour, not perimeter-only placement.")
    if total <= TOLERANCE:
        raise ValueError("Cannot place tabs on a zero-length contour.")
    if operation.tab_mode == "manual":
        centers = sorted({round((position % 1.0) * total, 10) for position in operation.manual_tab_positions})
    elif operation.tab_mode == "corners_and_spacing":
        centers = []
        for position in corner_positions or ():
            centers.extend(((position - operation.corner_tab_offset) % total, (position + operation.corner_tab_offset) % total))
        if not centers:
            requested = max(1, operation.tab_count, math.ceil(total / operation.tab_spacing))
            centers = [(index + 0.5) * total / requested for index in range(requested)]
        centers = sorted(centers)
        expanded = []
        for index, start in enumerate(centers):
            end = centers[(index + 1) % len(centers)] + (total if index == len(centers) - 1 else 0.0)
            expanded.append(start)
            gap = end - start
            additions = max(0, math.ceil(gap / operation.tab_spacing) - 1)
            expanded.extend((start + gap * step / (additions + 1)) % total for step in range(1, additions + 1))
        centers = sorted({round(center, 10) for center in expanded})
    elif operation.tab_mode in {"minimum_and_spacing", "minimum_avoid_corners", "support_aware"}:
        requested = max(4, operation.tab_count, math.ceil(total / operation.tab_spacing))
        spacing = total / requested
        centers = [(index + 0.5) * spacing for index in range(requested)]
    else:
        if operation.tab_count <= 0:
            return []
        spacing = total / operation.tab_count
        centers = [(index + 0.5) * spacing for index in range(operation.tab_count)]
    # A tab must be followed by at least one tab-width of full-depth cutting.
    # On a contour too small for the requested minimum, reduce the effective
    # count instead of blocking the entire grouped operation.
    minimum_center_spacing = operation.tab_width * 2.0
    maximum_count = max(1, int(total // minimum_center_spacing)) if minimum_center_spacing > TOLERANCE else len(centers)
    if operation.tab_mode in {"even", "minimum_and_spacing", "minimum_avoid_corners", "support_aware"} and len(centers) > maximum_count:
        centers = [(index + 0.5) * total / maximum_count for index in range(maximum_count)]
    else:
        centers = _prune_tab_centers(centers, total, minimum_center_spacing)
    if not centers or operation.tab_width >= total - TOLERANCE:
        return []
    intervals = []
    for center in centers:
        start, end = center - operation.tab_width / 2, center + operation.tab_width / 2
        if start < 0:
            intervals.extend(((0.0, end), (total + start, total)))
        elif end > total:
            intervals.extend(((start, total), (0.0, end - total)))
        else:
            intervals.append((start, end))
    intervals.sort()
    return intervals


def _prune_tab_centers(centers: list[float], total: float, minimum_spacing: float) -> list[float]:
    centers = sorted({round(center % total, 10) for center in centers})
    if minimum_spacing <= TOLERANCE:
        return centers
    while len(centers) > 1:
        gaps = [
            (centers[(index + 1) % len(centers)] - center) % total
            for index, center in enumerate(centers)
        ]
        smallest = min(gaps)
        if smallest + TOLERANCE >= minimum_spacing:
            break
        index = gaps.index(smallest)
        remove_index = (index + 1) % len(centers)
        centers.pop(remove_index)
    return centers


def _append_tabbed_path(motions, sequence, depth, operation: RouterOffsetOperation, cut_feed, plunge_feed) -> None:
    lengths = [a.distance_to(b) for a, b in zip(sequence, sequence[1:])]
    intervals = _tab_intervals(sequence, operation)
    tab_z = -(operation.tab_floor_depth)
    traveled = 0.0
    for start, end, segment_length in zip(sequence, sequence[1:], lengths):
        boundaries = [0.0, segment_length]
        for interval_start, interval_end in intervals:
            for boundary in (interval_start - traveled, interval_end - traveled):
                if TOLERANCE < boundary < segment_length - TOLERANCE:
                    boundaries.append(boundary)
        boundaries = sorted(set(boundaries))
        for local_start, local_end in zip(boundaries, boundaries[1:]):
            midpoint = traveled + (local_start + local_end) / 2
            desired_z = tab_z if any(a <= midpoint <= b for a, b in intervals) else -depth
            current = motions[-1].end
            if abs(current.z - desired_z) > TOLERANCE:
                vertical_feed = plunge_feed if desired_z < current.z else cut_feed
                motions.append(Motion(MotionKind.LINEAR, ToolPoint(current.x, current.y, desired_z), vertical_feed))
            ratio = local_end / segment_length
            point = Point(start.x + (end.x - start.x) * ratio, start.y + (end.y - start.y) * ratio)
            motions.append(Motion(MotionKind.LINEAR, ToolPoint(point.x, point.y, desired_z), cut_feed))
        traveled += segment_length


def _point_at_contour_distance(sequence, distance: float) -> Point:
    traveled = 0.0
    for start, end in zip(sequence, sequence[1:]):
        length = start.distance_to(end)
        if distance <= traveled + length + TOLERANCE:
            ratio = 0.0 if length <= TOLERANCE else max(0.0, min(1.0, (distance - traveled) / length))
            return Point(start.x + (end.x - start.x) * ratio, start.y + (end.y - start.y) * ratio)
        traveled += length
    return sequence[-1]


def _contour_points_between(sequence, start_distance: float, end_distance: float) -> list[Point]:
    points = [_point_at_contour_distance(sequence, start_distance)]
    traveled = 0.0
    for _start, end in zip(sequence, sequence[1:]):
        traveled += _start.distance_to(end)
        if start_distance + TOLERANCE < traveled < end_distance - TOLERANCE:
            points.append(end)
    last = _point_at_contour_distance(sequence, end_distance)
    if points[-1].distance_to(last) > TOLERANCE:
        points.append(last)
    return points


def _tab_cleanup_motions(points, depth, safe_z, plunge_feed, cut_feed, operation: RouterOffsetOperation):
    sequence = [*points, points[0]]
    intervals = _tab_intervals(sequence, operation)
    motions = []
    for start_distance, end_distance in intervals:
        path = _contour_points_between(sequence, start_distance, end_distance)
        start, end = path[0], path[-1]
        motions.append(Motion(MotionKind.RAPID, ToolPoint(start.x, start.y, safe_z)))
        motions.append(Motion(MotionKind.LINEAR, ToolPoint(start.x, start.y, -depth), plunge_feed))
        motions.extend(Motion(MotionKind.LINEAR, ToolPoint(point.x, point.y, -depth), cut_feed) for point in path[1:])
        motions.append(Motion(MotionKind.RAPID, ToolPoint(end.x, end.y, safe_z)))
    return motions


def _raised_profile_tab_groups(profile_motions, operation):
    """Read raised spans from actual motion, never inferred tab intent."""
    tab_z = -(operation.tab_floor_depth)
    groups: list[tuple[ToolPoint, list[Motion]]] = []
    active: list[Motion] | None = None
    current: ToolPoint | None = None
    for motion in profile_motions:
        raised_span = (
            current is not None
            and motion.kind in {MotionKind.LINEAR, MotionKind.ARC_CW, MotionKind.ARC_CCW}
            and math.hypot(motion.end.x - current.x, motion.end.y - current.y) > TOLERANCE
            and abs(current.z - tab_z) <= TOLERANCE
            and abs(motion.end.z - tab_z) <= TOLERANCE
        )
        if raised_span:
            if active is None:
                active = []
                groups.append((current, active))
            active.append(motion)
        else:
            active = None
        current = motion.end
    return groups


def _tab_cleanup_motions_from_profile(
    profile_motions: list[Motion],
    depth: float,
    safe_z: float,
    plunge_feed: float,
    cut_feed: float,
    operation: RouterOffsetOperation,
) -> list[Motion]:
    """Cut the exact XY spans raised by a completed tabbed profile pass.

    Recomputing tab intervals from the source polygon is unsafe because ramp
    and helical entry may rotate the effective contour start away from vertex
    zero. Extracting spans from the generated profile also preserves its cut
    direction, corner traversal, manual positions, and compensated width.
    """
    groups = _raised_profile_tab_groups(profile_motions, operation)
    if not groups:
        raise ValueError("The final profile pass contains no raised tab spans to clean up.")

    cleanup: list[Motion] = []
    for start, segments in groups:
        cleanup.append(Motion(MotionKind.RAPID, ToolPoint(start.x, start.y, safe_z)))
        cleanup.append(Motion(MotionKind.LINEAR, ToolPoint(start.x, start.y, -depth), plunge_feed))
        for source in segments:
            cleanup.append(Motion(
                source.kind,
                ToolPoint(source.end.x, source.end.y, -depth),
                cut_feed,
                source.center,
            ))
        end = segments[-1].end
        cleanup.append(Motion(MotionKind.RAPID, ToolPoint(end.x, end.y, safe_z)))
    return cleanup


def _circle_cut_motions(
    circle_geometry,
    depth,
    safe_z,
    plunge_feed,
    cut_feed,
    operation: RouterOffsetOperation,
    *,
    entry_advance: float = 0.0,
):
    center, radius = circle_geometry
    # EnRoute starts a default circle at its bottom and emits four unambiguous
    # quarter-arcs rather than relying on controller handling of semicircles.
    angle = -math.pi / 2 + (operation.start_index % 4) * math.pi / 2
    start_point = Point(center.x + radius * math.cos(angle), center.y + radius * math.sin(angle))
    clockwise = (
        operation.side in {OffsetSide.OUTSIDE, OffsetSide.ON} and operation.cut_direction == "climb"
    ) or (
        operation.side == OffsetSide.INSIDE and operation.cut_direction == "conventional"
    )
    if entry_advance > TOLERANCE:
        angle += (-1.0 if clockwise else 1.0) * entry_advance / radius
        start_point = Point(center.x + radius * math.cos(angle), center.y + radius * math.sin(angle))
    tangent = Point(math.sin(angle), -math.cos(angle)) if clockwise else Point(-math.sin(angle), math.cos(angle))
    lead_start = Point(start_point.x - tangent.x * operation.lead_in, start_point.y - tangent.y * operation.lead_in)
    arc_kind = MotionKind.ARC_CW if clockwise else MotionKind.ARC_CCW
    angular_step = -math.pi / 2 if clockwise else math.pi / 2
    quarter_points = [
        Point(
            center.x + radius * math.cos(angle + angular_step * index),
            center.y + radius * math.sin(angle + angular_step * index),
        )
        for index in range(1, 5)
    ]
    if operation.entry_mode == "helical":
        boundary = [
            Point(center.x + radius * math.cos(2 * math.pi * index / 72), center.y + radius * math.sin(2 * math.pi * index / 72))
            for index in range(72)
        ]
        motions = _helical_entry_motions(start_point, tangent, boundary, depth, safe_z, plunge_feed, operation)
    elif operation.entry_mode == "ramp":
        motions = [Motion(MotionKind.RAPID, ToolPoint(lead_start.x, lead_start.y, safe_z))]
        motions.append(Motion(MotionKind.LINEAR, ToolPoint(lead_start.x, lead_start.y, 0.0), plunge_feed))
        if operation.lead_in > TOLERANCE:
            motions.append(Motion(MotionKind.LINEAR, ToolPoint(start_point.x, start_point.y, -depth), plunge_feed))
        else:
            motions.extend(
                Motion(arc_kind, ToolPoint(point.x, point.y, -depth * index / 4), plunge_feed, center)
                for index, point in enumerate(quarter_points, 1)
            )
    else:
        motions = [Motion(MotionKind.RAPID, ToolPoint(lead_start.x, lead_start.y, safe_z))]
        motions.append(Motion(MotionKind.LINEAR, ToolPoint(lead_start.x, lead_start.y, -depth), plunge_feed))
        if operation.lead_in > TOLERANCE:
            motions.append(Motion(MotionKind.LINEAR, ToolPoint(start_point.x, start_point.y, -depth), cut_feed))
    has_tabs = operation.tab_count > 0 or operation.tab_mode in {"minimum_and_spacing", "minimum_avoid_corners", "support_aware", "corners_and_spacing", "manual", "aligned"}
    tab_floor_depth = operation.tab_floor_depth
    if has_tabs and depth > tab_floor_depth + TOLERANCE:
        _append_tabbed_circle(
            motions, center, radius, angle, clockwise, depth, operation, cut_feed, plunge_feed,
        )
    else:
        motions.extend(
            Motion(arc_kind, ToolPoint(point.x, point.y, -depth), cut_feed, center)
            for point in quarter_points
        )
    current = motions[-1].end
    if abs(current.z + depth) > TOLERANCE:
        motions.append(Motion(MotionKind.LINEAR, ToolPoint(current.x, current.y, -depth), plunge_feed))
        current = motions[-1].end
    if operation.lead_out > TOLERANCE:
        current = ToolPoint(current.x + tangent.x * operation.lead_out, current.y + tangent.y * operation.lead_out, -depth)
        motions.append(Motion(MotionKind.LINEAR, current, cut_feed))
    motions.append(Motion(MotionKind.RAPID, ToolPoint(current.x, current.y, safe_z)))
    return motions


def _append_tabbed_circle(
    motions,
    center: Point,
    radius: float,
    start_angle: float,
    clockwise: bool,
    depth: float,
    operation: RouterOffsetOperation,
    cut_feed: float,
    plunge_feed: float,
) -> None:
    total = 2.0 * math.pi * radius
    intervals = _tab_intervals_for_length(total, operation)
    if not intervals:
        direction = -1.0 if clockwise else 1.0
        arc_kind = MotionKind.ARC_CW if clockwise else MotionKind.ARC_CCW
        for index in range(1, 5):
            angle = start_angle + direction * math.pi / 2 * index
            point = Point(center.x + radius * math.cos(angle), center.y + radius * math.sin(angle))
            motions.append(Motion(arc_kind, ToolPoint(point.x, point.y, -depth), cut_feed, center))
        return
    boundaries = sorted({0.0, total, *(value for interval in intervals for value in interval)})
    direction = -1.0 if clockwise else 1.0
    arc_kind = MotionKind.ARC_CW if clockwise else MotionKind.ARC_CCW
    tab_z = -(operation.tab_floor_depth)
    for segment_start, segment_end in zip(boundaries, boundaries[1:]):
        midpoint = (segment_start + segment_end) / 2.0
        desired_z = tab_z if any(first <= midpoint <= second for first, second in intervals) else -depth
        current = motions[-1].end
        if abs(current.z - desired_z) > TOLERANCE:
            vertical_feed = plunge_feed if desired_z < current.z else cut_feed
            motions.append(Motion(MotionKind.LINEAR, ToolPoint(current.x, current.y, desired_z), vertical_feed))
        angle = start_angle + direction * segment_end / radius
        point = Point(center.x + radius * math.cos(angle), center.y + radius * math.sin(angle))
        motions.append(Motion(arc_kind, ToolPoint(point.x, point.y, desired_z), cut_feed, center))


def _rounded_rectangle_cut_motions(
    rectangle: Rectangle,
    offset: float,
    depth: float,
    safe_z: float,
    plunge_feed: float,
    cut_feed: float,
    operation: RouterOffsetOperation,
) -> list[Motion]:
    """Route a male rectangle with true radius arcs about its source corners."""
    min_x, min_y, max_x, max_y = _rectangle_box(rectangle)
    mid_x, mid_y = (min_x + max_x) / 2, (min_y + max_y) / 2
    nodes = [
        Point(mid_x, min_y - offset),
        Point(min_x, min_y - offset),
        Point(min_x - offset, min_y),
        Point(min_x - offset, mid_y),
        Point(min_x - offset, max_y),
        Point(min_x, max_y + offset),
        Point(mid_x, max_y + offset),
        Point(max_x, max_y + offset),
        Point(max_x + offset, max_y),
        Point(max_x + offset, mid_y),
        Point(max_x + offset, min_y),
        Point(max_x, min_y - offset),
    ]
    centers = {
        1: Point(min_x, min_y),
        4: Point(min_x, max_y),
        7: Point(max_x, max_y),
        10: Point(max_x, min_y),
    }
    clockwise = operation.cut_direction == "climb"
    start_node = (operation.start_index % 4) * 3
    segments: list[tuple[MotionKind, Point, Point | None]] = []
    if clockwise:
        for step in range(len(nodes)):
            index = (start_node + step) % len(nodes)
            segments.append((
                MotionKind.ARC_CW if index in centers else MotionKind.LINEAR,
                nodes[(index + 1) % len(nodes)],
                centers.get(index),
            ))
    else:
        for step in range(len(nodes)):
            segment_index = (start_node - 1 - step) % len(nodes)
            segments.append((
                MotionKind.ARC_CCW if segment_index in centers else MotionKind.LINEAR,
                nodes[segment_index],
                centers.get(segment_index),
            ))
    return _closed_contour_cut_motions(
        nodes[start_node], segments, depth, safe_z, plunge_feed, cut_feed, operation,
    )


def _rounded_polygon_cut_motions(
    source_points: list[Point],
    offset: float,
    depth: float,
    safe_z: float,
    plunge_feed: float,
    cut_feed: float,
    operation: RouterOffsetOperation,
) -> list[Motion]:
    """Build an exact round-join outside offset for a simple DXF polygon."""
    points = _clean_polygon_points(source_points)
    area = sum(a.x * b.y - b.x * a.y for a, b in zip(points, points[1:] + points[:1])) / 2
    if area < 0:
        points.reverse()
    count = len(points)
    normals: list[Point] = []
    for start, end in zip(points, points[1:] + points[:1]):
        dx, dy = end.x - start.x, end.y - start.y
        length = math.hypot(dx, dy)
        if length <= TOLERANCE:
            raise ValueError("Closed contour contains a zero-length segment.")
        normals.append(Point(dy / length, -dx / length))

    entries: list[Point] = []
    exits: list[Point] = []
    rounded: list[bool] = []
    for index, vertex in enumerate(points):
        previous_index = (index - 1) % count
        previous = points[previous_index]
        following = points[(index + 1) % count]
        cross = (
            (vertex.x - previous.x) * (following.y - vertex.y)
            - (vertex.y - previous.y) * (following.x - vertex.x)
        )
        previous_normal, next_normal = normals[previous_index], normals[index]
        previous_end = Point(vertex.x + previous_normal.x * offset, vertex.y + previous_normal.y * offset)
        next_start = Point(vertex.x + next_normal.x * offset, vertex.y + next_normal.y * offset)
        if cross > TOLERANCE:
            entries.append(previous_end)
            exits.append(next_start)
            rounded.append(True)
        else:
            previous_start = Point(previous.x + previous_normal.x * offset, previous.y + previous_normal.y * offset)
            next_end = Point(following.x + next_normal.x * offset, following.y + next_normal.y * offset)
            intersection = _line_intersection(previous_start, previous_end, next_start, next_end)
            join = intersection or Point(
                (previous_end.x + next_start.x) / 2,
                (previous_end.y + next_start.y) / 2,
            )
            entries.append(join)
            exits.append(join)
            rounded.append(False)

    start_index = operation.start_index % count
    start = exits[start_index]
    forward: list[tuple[MotionKind, Point, Point | None]] = []
    for step in range(1, count + 1):
        index = (start_index + step) % count
        if exits[(index - 1) % count].distance_to(entries[index]) > TOLERANCE:
            forward.append((MotionKind.LINEAR, entries[index], None))
        if rounded[index]:
            forward.append((MotionKind.ARC_CCW, exits[index], points[index]))
    if operation.cut_direction == "climb":
        endpoints = [start] + [segment[1] for segment in forward]
        segments = []
        for reverse_index in range(len(forward) - 1, -1, -1):
            kind, _end, center = forward[reverse_index]
            if kind == MotionKind.ARC_CCW:
                kind = MotionKind.ARC_CW
            segments.append((kind, endpoints[reverse_index], center))
    else:
        segments = forward
    if not _rounded_polygon_centerline_is_clear(start, segments, points, offset):
        # Closely spaced concave vertices (commonly a source fillet smaller
        # than the cutter radius) can make adjacent analytical round joins
        # overlap.  The individual joins look plausible, but their combined
        # cutter sweep can enter the protected source shape.  Fall back to the
        # topology-resolved polygon offset rather than emitting that overcut.
        robust_paths = _offset_closed_polygons(points, offset, OffsetSide.OUTSIDE)
        if len(robust_paths) != 1:
            raise ValueError(
                "The topology-safe outside offset separates into multiple paths; "
                "inspect or simplify the source contour before machining."
            )
        return _closed_polygon_cut_motions(
            robust_paths[0], depth, safe_z, plunge_feed, cut_feed, operation,
        )
    return _closed_contour_cut_motions(
        start, segments, depth, safe_z, plunge_feed, cut_feed, operation,
    )


def _rounded_polygon_centerline_is_clear(
    start: Point,
    segments: list[tuple[MotionKind, Point, Point | None]],
    source_points: list[Point],
    offset: float,
) -> bool:
    """Verify that a proposed outside centerline keeps the cutter off the part."""
    if offset <= TOLERANCE:
        return True
    clearance_tolerance = max(1e-6, offset * 1e-4)
    from .contour_index import ContourIndex
    index = ContourIndex(source_points, offset)
    linear_step = max(offset / 8.0, TOLERANCE * 100)
    current = start
    for kind, end, center in segments:
        if kind in {MotionKind.ARC_CW, MotionKind.ARC_CCW} and center is not None:
            samples = _arc_path_points(
                current, kind, end, center, maximum_step=math.pi / 72,
            )
        else:
            length = current.distance_to(end)
            count = max(1, math.ceil(length / linear_step))
            samples = [
                Point(
                    current.x + (end.x - current.x) * index / count,
                    current.y + (end.y - current.y) * index / count,
                )
                for index in range(count + 1)
            ]
        if any(
            index.contains(point)
            or index.closer_than(point, offset - clearance_tolerance)
            for point in samples
        ):
            return False
        current = end
    return True


def _closed_contour_cut_motions(
    start: Point,
    segments: list[tuple[MotionKind, Point, Point | None]],
    depth: float,
    safe_z: float,
    plunge_feed: float,
    cut_feed: float,
    operation: RouterOffsetOperation,
) -> list[Motion]:
    if not segments:
        raise ValueError("Closed contour contains no cutting segments.")
    first_kind, first_end, first_center = segments[0]
    if first_kind == MotionKind.LINEAR:
        dx, dy = first_end.x - start.x, first_end.y - start.y
        length = math.hypot(dx, dy)
        tangent = Point(dx / length, dy / length)
    else:
        assert first_center is not None
        rx, ry = start.x - first_center.x, start.y - first_center.y
        radius = math.hypot(rx, ry)
        tangent = Point(ry / radius, -rx / radius) if first_kind == MotionKind.ARC_CW else Point(-ry / radius, rx / radius)
    lead_start = Point(start.x - tangent.x * operation.lead_in, start.y - tangent.y * operation.lead_in)
    remaining = segments
    recut_ramp_segment = False
    if operation.entry_mode == "helical":
        boundary = [start]
        cursor = start
        for kind, end, center in segments:
            if kind in {MotionKind.ARC_CW, MotionKind.ARC_CCW} and center is not None:
                boundary.extend(_arc_path_points(cursor, kind, end, center)[1:])
            else:
                boundary.append(end)
            cursor = end
        motions = _helical_entry_motions(start, tangent, boundary, depth, safe_z, plunge_feed, operation)
    elif operation.entry_mode == "ramp":
        motions = [Motion(MotionKind.RAPID, ToolPoint(lead_start.x, lead_start.y, safe_z))]
        motions.append(Motion(MotionKind.LINEAR, ToolPoint(lead_start.x, lead_start.y, 0.0), plunge_feed))
        if operation.lead_in > TOLERANCE:
            motions.append(Motion(MotionKind.LINEAR, ToolPoint(start.x, start.y, -depth), plunge_feed))
        else:
            motions.append(Motion(first_kind, ToolPoint(first_end.x, first_end.y, -depth), plunge_feed, first_center))
            remaining = segments[1:]
            recut_ramp_segment = True
    else:
        motions = [Motion(MotionKind.RAPID, ToolPoint(lead_start.x, lead_start.y, safe_z))]
        motions.append(Motion(MotionKind.LINEAR, ToolPoint(lead_start.x, lead_start.y, -depth), plunge_feed))
        if operation.lead_in > TOLERANCE:
            motions.append(Motion(MotionKind.LINEAR, ToolPoint(start.x, start.y, -depth), cut_feed))
    motions.extend(
        Motion(kind, ToolPoint(end.x, end.y, -depth), cut_feed, center)
        for kind, end, center in remaining
    )
    if recut_ramp_segment:
        # The first segment was used to descend.  Complete the closed loop and
        # traverse that segment once more at full depth so a rounded rectangle
        # or rounded polygon does not leave a shallow skim at its start.
        motions.append(Motion(
            first_kind,
            ToolPoint(first_end.x, first_end.y, -depth),
            cut_feed,
            first_center,
        ))
    current = motions[-1].end
    if operation.lead_out > TOLERANCE:
        current = ToolPoint(
            current.x + tangent.x * operation.lead_out,
            current.y + tangent.y * operation.lead_out,
            -depth,
        )
        motions.append(Motion(MotionKind.LINEAR, current, cut_feed))
    motions.append(Motion(MotionKind.RAPID, ToolPoint(current.x, current.y, safe_z)))
    return motions


def _rectangle_box(rectangle: Rectangle) -> tuple[float, float, float, float]:
    return (
        min(rectangle.corner_a.x, rectangle.corner_b.x),
        min(rectangle.corner_a.y, rectangle.corner_b.y),
        max(rectangle.corner_a.x, rectangle.corner_b.x),
        max(rectangle.corner_a.y, rectangle.corner_b.y),
    )


def _axis_aligned_rectangle(points: list[Point]) -> Rectangle | None:
    """Recognize a four-corner DXF polyline box without changing its identity."""
    cleaned = _clean_polygon_points(points)
    if len(cleaned) != 4:
        return None
    min_x = min(point.x for point in cleaned)
    min_y = min(point.y for point in cleaned)
    max_x = max(point.x for point in cleaned)
    max_y = max(point.y for point in cleaned)
    expected = {(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)}
    actual = {(point.x, point.y) for point in cleaned}
    if actual != expected or max_x - min_x <= TOLERANCE or max_y - min_y <= TOLERANCE:
        return None
    return Rectangle(corner_a=Point(min_x, min_y), corner_b=Point(max_x, max_y))


def _inset_box(box, amount):
    return box[0] + amount, box[1] + amount, box[2] - amount, box[3] - amount


def _expand_box(box, amount):
    return box[0] - amount, box[1] - amount, box[2] + amount, box[3] + amount


def _valid_box(box) -> bool:
    return box[0] <= box[2] + TOLERANCE and box[1] <= box[3] + TOLERANCE


def _box_contains(outer, inner) -> bool:
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


def _box_loop(box) -> list[Point]:
    min_x, min_y, max_x, max_y = box
    return [
        Point(min_x, min_y),
        Point(max_x, min_y),
        Point(max_x, max_y),
        Point(min_x, max_y),
        Point(min_x, min_y),
    ]


def _center_out_loops(outer_box, stepover: float) -> list[list[Point]]:
    loops = []
    inset = 0.0
    while True:
        box = _inset_box(outer_box, inset)
        if not _valid_box(box):
            break
        loops.append(_box_loop(box))
        inset += stepover
    return list(reversed(loops))


def _island_bulk_loops(outer_box, island_source, radius: float, stepover: float) -> list[list[Point]]:
    loops = []
    distance = radius + 2 * stepover
    previous = None
    for _ in range(100000):
        expanded = _expand_box(island_source, distance)
        box = (
            max(outer_box[0], expanded[0]),
            max(outer_box[1], expanded[1]),
            min(outer_box[2], expanded[2]),
            min(outer_box[3], expanded[3]),
        )
        if previous != box:
            loops.append(_box_loop(box))
            previous = box
        if all(abs(box[index] - outer_box[index]) <= TOLERANCE for index in range(4)):
            break
        distance += stepover
    else:
        raise ValueError("Island fill loop generation did not converge.")
    return loops


def _linear_loop_block_motions(loops, depth, safe_z, plunge_feed, cut_feed) -> list[Motion]:
    first = loops[0][0]
    motions = [
        Motion(MotionKind.RAPID, ToolPoint(first.x, first.y, safe_z)),
        Motion(MotionKind.LINEAR, ToolPoint(first.x, first.y, -depth), plunge_feed),
    ]
    for loop in loops:
        start = loop[0]
        current = motions[-1].end
        if abs(current.x - start.x) > TOLERANCE or abs(current.y - start.y) > TOLERANCE:
            motions.append(Motion(MotionKind.LINEAR, ToolPoint(start.x, start.y, -depth), cut_feed))
        motions.extend(Motion(MotionKind.LINEAR, ToolPoint(point.x, point.y, -depth), cut_feed) for point in loop[1:])
    last = motions[-1].end
    motions.append(Motion(MotionKind.RAPID, ToolPoint(last.x, last.y, safe_z)))
    return motions


def _point_in_fill_region(point: Point, accessible_paths) -> bool:
    """Return whether a cutter-center point is inside a clipped fill region."""
    location = (round(point.x * _REGION_SCALE), round(point.y * _REGION_SCALE))
    winding = 0
    for path in accessible_paths:
        relation = pyclipper.PointInPolygon(location, path)
        if relation == -1:
            return True
        if relation:
            winding += 1 if pyclipper.Orientation(path) else -1
    return winding > 0


def _select_fill_helix(
    candidates, accessible_paths, preferred_diameter: float,
    minimum_diameter: float = 0.0, wall_clearance: float = 0.0, clearance_cache=None,
):
    """Find the largest requested helix using an eroded cutter-center region.

    Eroding the accessible region by the orbit radius turns the expensive
    72-point-per-candidate sweep into a point-in-region lookup.  The eroded
    paths are shared by every loop and depth in the same fill region.
    """
    diameter_scales = (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.25)
    clearance_cache = clearance_cache if clearance_cache is not None else {}
    for scale in diameter_scales:
        radius = preferred_diameter * scale / 2.0
        if radius * 2.0 < minimum_diameter - TOLERANCE:
            continue
        cache_key = (round(radius, 12), round(wall_clearance, 12))
        safe_centers = clearance_cache.get(cache_key)
        if safe_centers is None:
            safe_centers = _offset_region_paths(accessible_paths, -(radius + wall_clearance))
            clearance_cache[cache_key] = safe_centers
        if not safe_centers:
            continue
        for start, tangent, payload in candidates:
            left = Point(-tangent.y, tangent.x)
            for sign, kind in ((1.0, MotionKind.ARC_CCW), (-1.0, MotionKind.ARC_CW)):
                center = Point(start.x + left.x * radius * sign, start.y + left.y * radius * sign)
                if _point_in_fill_region(center, safe_centers):
                    return start, center, kind, radius, payload
    return None


def _size_adjusted_helix_feed(requested_feed: float, radius: float, preferred_diameter: float) -> float:
    """Do not increase orbital speed when fitting a smaller cutter-center orbit.

    Scale tangential feed linearly with diameter. A quarter-size orbit receives
    a quarter of the requested feed; full-size helices and plunge feeds stay
    unchanged. Apply at generation so preview, time estimation and CNC agree.
    """
    return requested_feed * min(1.0, (2.0 * radius) / preferred_diameter)


def _effective_helix_pitch(radius: float, pitch: float, ramp_angle: float) -> float:
    if ramp_angle <= TOLERANCE:
        return pitch
    angle_pitch = 2.0 * math.pi * radius * math.tan(math.radians(ramp_angle))
    return min(pitch, angle_pitch) if pitch > TOLERANCE else angle_pitch


def _fill_helix_motions(selection, depth, safe_z, plunge_feed, operation) -> list[Motion]:
    start, center, kind, radius, _payload = selection
    start_angle = math.atan2(start.y - center.y, start.x - center.x)
    direction = 1.0 if kind == MotionKind.ARC_CCW else -1.0
    helix_feed = operation.helix_feed_override or plunge_feed
    helix_feed = _size_adjusted_helix_feed(helix_feed, radius, operation.helix_diameter)
    pitch = _effective_helix_pitch(radius, operation.helix_pitch, operation.helix_ramp_angle)
    if pitch <= TOLERANCE:
        raise ValueError("Helical entry requires a positive pitch or ramp-angle limit.")
    revolutions = max(1, math.ceil(depth / pitch - TOLERANCE))
    quarter_count = revolutions * 4
    motions = [
        Motion(MotionKind.RAPID, ToolPoint(start.x, start.y, safe_z)),
        Motion(MotionKind.LINEAR, ToolPoint(start.x, start.y, 0.0), plunge_feed),
    ]
    for index in range(1, quarter_count + 1):
        angle = start_angle + direction * math.pi * index / 2.0
        point = Point(center.x + radius * math.cos(angle), center.y + radius * math.sin(angle))
        motions.append(Motion(
            kind,
            ToolPoint(point.x, point.y, -depth * index / quarter_count),
            helix_feed,
            center,
        ))
    for index in range(1, operation.helix_finish_revolutions * 4 + 1):
        angle = start_angle + direction * math.pi * index / 2.0
        point = Point(center.x + radius * math.cos(angle), center.y + radius * math.sin(angle))
        motions.append(Motion(kind, ToolPoint(point.x, point.y, -depth), helix_feed, center))
    return motions


def _fill_loop_helix_selection(
    points: list[Point], accessible_paths, preferred_diameter: float,
    minimum_diameter: float = 0.0, wall_clearance: float = 0.0, helix_cache=None,
):
    helix_cache = helix_cache if helix_cache is not None else {}
    signature = (
        len(points),
        round(min(point.x for point in points), 8), round(min(point.y for point in points), 8),
        round(max(point.x for point in points), 8), round(max(point.y for point in points), 8),
        round(preferred_diameter, 8),
        round(minimum_diameter, 8), round(wall_clearance, 8),
    )
    selection_cache = helix_cache.setdefault("selections", {})
    if signature in selection_cache:
        return selection_cache[signature]
    candidates = []
    segments = []
    for segment_index, (first, second) in enumerate(zip(points, [*points[1:], points[0]])):
        dx, dy = second.x - first.x, second.y - first.y
        length = math.hypot(dx, dy)
        if length <= TOLERANCE:
            continue
        tangent = Point(dx / length, dy / length)
        segments.append((length, segment_index, first, dx, dy, tangent))
    # Midpoints cover the whole contour. Quarter points add options on only
    # the longest spans, avoiding thousands of redundant candidates on dense
    # DXF polylines.
    for _length, segment_index, first, dx, dy, tangent in segments:
        start = Point(first.x + dx * 0.5, first.y + dy * 0.5)
        candidates.append((start, tangent, (segment_index, 0.5)))
    for _length, segment_index, first, dx, dy, tangent in sorted(segments, reverse=True)[:24]:
        for fraction in (0.25, 0.75):
            start = Point(first.x + dx * fraction, first.y + dy * fraction)
            candidates.append((start, tangent, (segment_index, fraction)))
    selection = _select_fill_helix(
        candidates,
        accessible_paths,
        preferred_diameter,
        minimum_diameter,
        wall_clearance,
        helix_cache.setdefault("clearance_regions", {}),
    )
    selection_cache[signature] = selection
    return selection


def _fill_loop_path_length(raw_loop) -> float:
    points = _clean_polygon_points(raw_loop)
    if len(points) < 3:
        return 0.0
    sequence = [*points, points[0]]
    return sum(first.distance_to(second) for first, second in zip(sequence, sequence[1:]))


def _fill_loop_signature(points: list[Point]):
    """Return a direction/start-independent signature for a clipped fill loop."""
    return frozenset((round(point.x, 6), round(point.y, 6)) for point in points)


def _prepared_island_fill_loops(loops, cut_direction: str, accessible_paths=None) -> list[list[Point]]:
    """Orient fill loops while retaining climb cuts on actual pocket walls.

    Bidirectional cleanout is intended for interior material-removal passes. A
    boundary loop still gets the normal climb direction so the faster strategy
    does not silently turn the final wall pass into a conventional finish.
    Non-boundary loops alternate conventional/climb, beginning with
    conventional immediately after the climb wall track.
    """
    boundary_signatures = {
        _fill_loop_signature([
            Point(x / _REGION_SCALE, y / _REGION_SCALE) for x, y in path
        ])
        for path in ((accessible_paths or []) if cut_direction == "bidirectional" else [])
    }
    prepared: list[list[Point]] = []
    interior_index = 0
    for loop_index, raw_loop in enumerate(loops):
        points = _clean_polygon_points(raw_loop)
        if len(points) < 3:
            continue
        reverse = cut_direction == "conventional"
        if cut_direction == "bidirectional":
            boundary_loop = loop_index == 0 or _fill_loop_signature(points) in boundary_signatures
            if not boundary_loop:
                reverse = interior_index % 2 == 0
                interior_index += 1
        if reverse:
            points.reverse()
        prepared.append(points)
    return prepared


def _partition_island_fill_loops(loops, maximum_short_length: float):
    """Separate residual short loops without changing legacy fill behavior."""
    regular = []
    short = []
    for loop in loops:
        length = _fill_loop_path_length(loop)
        if length <= TOLERANCE:
            continue
        if maximum_short_length > TOLERANCE and length <= maximum_short_length + TOLERANCE:
            short.append(loop)
        else:
            regular.append(loop)
    return regular, short


def _fill_cleanup_link_is_safe(start: Point, end: Point, accessible_paths) -> bool:
    """Verify the entire cutter-center connector remains in this fill region."""
    distance = start.distance_to(end)
    sample_count = max(4, min(48, int(math.ceil(distance / 0.01))))
    for index in range(sample_count + 1):
        fraction = index / sample_count
        point = Point(
            start.x + (end.x - start.x) * fraction,
            start.y + (end.y - start.y) * fraction,
        )
        if not _point_in_fill_region(point, accessible_paths):
            return False
    return True


def _stroke_open_fill_link(start: Point, end: Point, radius: float):
    if start.distance_to(end) <= TOLERANCE:
        return []
    path = [
        (round(start.x * _REGION_SCALE), round(start.y * _REGION_SCALE)),
        (round(end.x * _REGION_SCALE), round(end.y * _REGION_SCALE)),
    ]
    offsetter = pyclipper.PyclipperOffset(8.0, 0.01 * _REGION_SCALE)
    offsetter.AddPath(path, pyclipper.JT_ROUND, pyclipper.ET_OPENROUND)
    return offsetter.Execute(radius * _REGION_SCALE)


def _fill_stay_down_link_is_safe(
    start: Point,
    end: Point,
    accessible_paths,
    cleared_sweep,
    cutter_radius: float,
    maximum_engagement: float,
) -> bool:
    """Prove a same-depth link stays legal and only nibbles a narrow strip."""
    if not _fill_cleanup_link_is_safe(start, end, accessible_paths):
        return False
    connector_sweep = _stroke_open_fill_link(start, end, cutter_radius)
    if not connector_sweep:
        return True
    uncleared = _clipper_difference(connector_sweep, cleared_sweep)
    if not uncleared:
        return True
    # Eroding by the allowed one-sided radial engagement removes a safe thin
    # crescent/strip.  A surviving core proves the link crosses too much new
    # material and must use a retract instead.
    return not _offset_region_paths(uncleared, -maximum_engagement)


def _sample_closed_fill_loop(points: list[Point], spacing: float):
    """Return bounded-spacing samples with enough data to start on a segment."""
    samples = []
    for segment_index, (first, second) in enumerate(zip(points, [*points[1:], points[0]])):
        length = first.distance_to(second)
        divisions = max(1, int(math.ceil(length / max(spacing, 0.01))))
        for index in range(divisions):
            fraction = index / divisions
            samples.append((
                segment_index, fraction,
                Point(
                    first.x + (second.x - first.x) * fraction,
                    first.y + (second.y - first.y) * fraction,
                ),
            ))
    return samples


def _fill_loop_sequence_from_sample(points: list[Point], segment_index: int, fraction: float):
    first = points[segment_index]
    second_index = (segment_index + 1) % len(points)
    second = points[second_index]
    entry = Point(
        first.x + (second.x - first.x) * fraction,
        first.y + (second.y - first.y) * fraction,
    )
    if fraction <= TOLERANCE:
        rotated = [*points[segment_index:], *points[:segment_index]]
        return [*rotated, rotated[0]]
    rotated = [entry, *points[second_index:], *points[:second_index], entry]
    return rotated


def _speed_cleanout_center_motions(
    primary_loops, center_loops, depth, safe_z, plunge_feed, cut_feed,
    operation: IslandFillOperation, accessible_paths,
):
    """Enter every full-depth center chain from a previously cleared wall path."""
    prepared = []
    for raw_loop in center_loops:
        points = _clean_polygon_points(raw_loop)
        if len(points) < 3:
            continue
        if operation.cut_direction == "conventional":
            points.reverse()
        prepared.append(points)
    if not prepared:
        return [], 0, 0

    maximum_link = operation.speed_link_distance
    spacing = max(0.01, maximum_link / 2.0)
    wall_buckets: dict[tuple[int, int], list[Point]] = {}
    for raw_loop in primary_loops:
        wall_points = _clean_polygon_points(raw_loop)
        for _segment_index, _fraction, point in _sample_closed_fill_loop(wall_points, spacing):
            key = (math.floor(point.x / maximum_link), math.floor(point.y / maximum_link))
            wall_buckets.setdefault(key, []).append(point)

    loop_samples = [_sample_closed_fill_loop(points, spacing) for points in prepared]

    def nearby(bucket, point):
        cell_x = math.floor(point.x / maximum_link)
        cell_y = math.floor(point.y / maximum_link)
        for offset_x in (-1, 0, 1):
            for offset_y in (-1, 0, 1):
                yield from bucket.get((cell_x + offset_x, cell_y + offset_y), ())

    wall_entries = []
    for samples in loop_samples:
        candidates = []
        for segment_index, fraction, center_point in samples:
            for wall_point in nearby(wall_buckets, center_point):
                distance = wall_point.distance_to(center_point)
                if distance <= maximum_link + TOLERANCE:
                    candidates.append((distance, wall_point, segment_index, fraction, center_point))
        chosen = next((candidate for candidate in sorted(candidates, key=lambda value: value[0])
                       if _fill_cleanup_link_is_safe(candidate[1], candidate[4], accessible_paths)), None)
        if chosen is None:
            return None
        wall_entries.append(chosen)

    sample_buckets: dict[tuple[int, int], list[tuple[int, int, float, Point]]] = {}
    for loop_index, samples in enumerate(loop_samples):
        for segment_index, fraction, point in samples:
            key = (math.floor(point.x / maximum_link), math.floor(point.y / maximum_link))
            sample_buckets.setdefault(key, []).append((loop_index, segment_index, fraction, point))

    def nearest_neighbor(current: Point, remaining: set[int]):
        candidates = []
        for loop_index, segment_index, fraction, point in nearby(sample_buckets, current):
            if loop_index not in remaining:
                continue
            distance = current.distance_to(point)
            if distance <= maximum_link + TOLERANCE:
                candidates.append((distance, loop_index, segment_index, fraction, point))
        return next((candidate for candidate in sorted(candidates, key=lambda value: value[0])
                     if _fill_cleanup_link_is_safe(current, candidate[4], accessible_paths)), None)

    remaining = set(range(len(prepared)))
    motions: list[Motion] = []
    linked_count = 0
    plunge_count = 0
    while remaining:
        loop_index = min(remaining)
        remaining.remove(loop_index)
        _distance, wall_entry, segment_index, fraction, center_entry = wall_entries[loop_index]
        motions.append(Motion(MotionKind.RAPID, ToolPoint(wall_entry.x, wall_entry.y, safe_z)))
        motions.append(Motion(MotionKind.LINEAR, ToolPoint(wall_entry.x, wall_entry.y, -depth), plunge_feed))
        plunge_count += 1
        if operation.dwell_seconds > TOLERANCE:
            motions.append(Motion(
                MotionKind.DWELL, ToolPoint(wall_entry.x, wall_entry.y, -depth),
                dwell_seconds=operation.dwell_seconds,
            ))
        if wall_entry.distance_to(center_entry) > TOLERANCE:
            motions.append(Motion(MotionKind.LINEAR, ToolPoint(center_entry.x, center_entry.y, -depth), cut_feed))

        while True:
            sequence = _fill_loop_sequence_from_sample(prepared[loop_index], segment_index, fraction)
            motions.extend(
                Motion(MotionKind.LINEAR, ToolPoint(point.x, point.y, -depth), cut_feed)
                for point in sequence[1:]
            )
            current = sequence[-1]
            neighbor = nearest_neighbor(current, remaining)
            if neighbor is None:
                motions.append(Motion(MotionKind.RAPID, ToolPoint(current.x, current.y, safe_z)))
                break
            _distance, loop_index, segment_index, fraction, entry = neighbor
            remaining.remove(loop_index)
            if current.distance_to(entry) > TOLERANCE:
                motions.append(Motion(MotionKind.LINEAR, ToolPoint(entry.x, entry.y, -depth), cut_feed))
            linked_count += 1
    return motions, linked_count, plunge_count


def _island_fill_sliver_cleanup_motions(
    loops, depth, safe_z, plunge_feed, cut_feed, operation: IslandFillOperation, accessible_paths,
) -> tuple[list[Motion], int, int]:
    """Cut short residual loops once at final depth and safely chain neighbors.

    Linking is deliberately limited to one classified fill region, to the
    operator's short distance cap, and to connector segments whose complete
    cutter-center path remains inside the offset-accessible region.
    """
    prepared: list[list[Point]] = []
    for raw_loop in loops:
        points = _clean_polygon_points(raw_loop)
        if len(points) < 3:
            continue
        if operation.cut_direction == "conventional":
            points.reverse()
        prepared.append(points)
    if not prepared:
        raise ValueError("Island-fill short-path cleanup produced no usable loops.")

    maximum_link = operation.sliver_link_distance
    buckets: dict[tuple[int, int], list[tuple[int, int, Point]]] = {}
    if maximum_link > TOLERANCE:
        for loop_index, points in enumerate(prepared):
            for vertex_index, point in enumerate(points):
                key = (math.floor(point.x / maximum_link), math.floor(point.y / maximum_link))
                buckets.setdefault(key, []).append((loop_index, vertex_index, point))

    def nearest_safe_neighbor(current: Point, remaining: set[int]):
        if maximum_link <= TOLERANCE:
            return None
        cell_x = math.floor(current.x / maximum_link)
        cell_y = math.floor(current.y / maximum_link)
        candidates = []
        for offset_x in (-1, 0, 1):
            for offset_y in (-1, 0, 1):
                for loop_index, vertex_index, point in buckets.get((cell_x + offset_x, cell_y + offset_y), ()):
                    if loop_index not in remaining:
                        continue
                    distance = current.distance_to(point)
                    if distance <= maximum_link + TOLERANCE:
                        candidates.append((distance, loop_index, vertex_index, point))
        for distance, loop_index, vertex_index, point in sorted(candidates, key=lambda value: value[:3]):
            if _fill_cleanup_link_is_safe(current, point, accessible_paths):
                return distance, loop_index, vertex_index, point
        return None

    remaining = set(range(len(prepared)))
    motions: list[Motion] = []
    linked_count = 0
    plunge_count = 0
    current = None
    while remaining:
        # A disconnected restart is still at Safe Z. Choose its nearest
        # existing vertex rather than jumping back to polygon list order.
        if current is None:
            loop_index, vertex_index = min(remaining), 0
        else:
            _, loop_index, vertex_index = min(
                (current.distance_to(point), index, vertex)
                for index in remaining
                for vertex, point in enumerate(prepared[index])
            )
        remaining.remove(loop_index)
        points = prepared[loop_index]
        points = [*points[vertex_index:], *points[:vertex_index]]
        start = points[0]
        motions.append(Motion(MotionKind.RAPID, ToolPoint(start.x, start.y, safe_z)))
        motions.append(Motion(MotionKind.LINEAR, ToolPoint(start.x, start.y, -depth), plunge_feed))
        plunge_count += 1
        if operation.dwell_seconds > TOLERANCE:
            motions.append(Motion(
                MotionKind.DWELL, ToolPoint(start.x, start.y, -depth),
                dwell_seconds=operation.dwell_seconds,
            ))

        while True:
            sequence = [*points, points[0]]
            motions.extend(
                Motion(MotionKind.LINEAR, ToolPoint(point.x, point.y, -depth), cut_feed)
                for point in sequence[1:]
            )
            current = points[0]
            neighbor = nearest_safe_neighbor(current, remaining)
            if neighbor is None:
                motions.append(Motion(MotionKind.RAPID, ToolPoint(current.x, current.y, safe_z)))
                break
            distance, loop_index, vertex_index, entry = neighbor
            remaining.remove(loop_index)
            points = prepared[loop_index]
            points = [*points[vertex_index:], *points[:vertex_index]]
            if distance > TOLERANCE:
                motions.append(Motion(MotionKind.LINEAR, ToolPoint(entry.x, entry.y, -depth), cut_feed))
            linked_count += 1
    return motions, linked_count, plunge_count


def _island_fill_loop_block_motions(
    loops, depth, safe_z, plunge_feed, cut_feed, operation: IslandFillOperation,
    accessible_paths=None, helix_cache=None, tool_radius: float = 0.0, link_stats: dict | None = None,
) -> list[Motion]:
    """Cut fill loops with a deliberate entry and a complete full-depth lap."""
    if operation.keep_tool_down or operation.cut_direction == "bidirectional":
        link_operation = operation
        if operation.cut_direction == "bidirectional" and not operation.keep_tool_down:
            # Both-ways cutting is useful only when it can shorten the links.
            # Derive conservative limits from the actual cutter/stepover, then
            # pass every connector through the normal swept-area verifier.
            cutter_diameter = tool_radius * 2.0
            programmed_stepover = operation.stepover or tool_radius
            link_operation = replace(
                operation,
                keep_tool_down=True,
                stay_down_max_distance=max(operation.stay_down_max_distance, cutter_diameter),
                stay_down_max_engagement=max(
                    operation.stay_down_max_engagement,
                    min(programmed_stepover, cutter_diameter),
                ),
            )
        return _island_fill_stay_down_motions(
            loops, depth, safe_z, plunge_feed, cut_feed, link_operation,
            accessible_paths or [], helix_cache, tool_radius, link_stats,
        )
    motions: list[Motion] = []
    helical_entry_used = False
    for points in _prepared_island_fill_loops(loops, operation.cut_direction, accessible_paths):
        sequence = [*points, points[0]]
        perimeter = sum(first.distance_to(second) for first, second in zip(sequence, sequence[1:]))
        if perimeter <= TOLERANCE:
            continue
        start = points[0]
        actual_entry_mode = operation.entry_mode
        if operation.entry_mode == "helical":
            # A concentric pocket can contain hundreds of coverage loops. A
            # full helix on every one creates tens of thousands of arc motions
            # and freezes the Tk preview. Use one true helix per depth block;
            # subsequent re-entries use the configured non-helical fallback.
            selection = None if helical_entry_used else _fill_loop_helix_selection(
                points, accessible_paths or [], operation.helix_diameter,
                operation.helix_min_diameter, operation.helix_clearance, helix_cache,
            )
            if selection is not None:
                helical_entry_used = True
                segment_index, fraction = selection[-1]
                first = points[segment_index]
                second = points[(segment_index + 1) % len(points)]
                entry = Point(
                    first.x + (second.x - first.x) * fraction,
                    first.y + (second.y - first.y) * fraction,
                )
                sequence = [entry, *points[segment_index + 1:], *points[:segment_index + 1], entry]
                motions.extend(_fill_helix_motions(
                    selection, depth, safe_z, plunge_feed, operation,
                ))
                if operation.dwell_seconds > TOLERANCE:
                    motions.append(Motion(MotionKind.DWELL, motions[-1].end, dwell_seconds=operation.dwell_seconds))
                motions.extend(
                    Motion(MotionKind.LINEAR, ToolPoint(point.x, point.y, -depth), cut_feed)
                    for point in sequence[1:]
                )
                end = motions[-1].end
                motions.append(Motion(MotionKind.RAPID, ToolPoint(end.x, end.y, safe_z)))
                continue
            if operation.helix_fallback == "error" and not helical_entry_used:
                raise ValueError(
                    f"The preferred {operation.helix_diameter:g} diameter island-fill helix cannot fit inside the cutter-accessible region. "
                    "Reduce the helix diameter or choose a linear-ramp/plunge fallback."
                )
            actual_entry_mode = "ramp" if helical_entry_used else operation.helix_fallback
        motions.append(Motion(MotionKind.RAPID, ToolPoint(start.x, start.y, safe_z)))
        if actual_entry_mode == "ramp":
            motions.append(Motion(MotionKind.LINEAR, ToolPoint(start.x, start.y, 0.0), plunge_feed))
            first_segment = points[0].distance_to(points[1])
            ramp_distance = min(perimeter, operation.ramp_length or first_segment)
            if ramp_distance <= TOLERANCE:
                raise ValueError("Island-fill ramp length must be positive.")
            ramp_path = _contour_points_between(sequence, 0.0, ramp_distance)
            traveled = 0.0
            previous = ramp_path[0]
            for point in ramp_path[1:]:
                traveled += previous.distance_to(point)
                z = -depth * min(1.0, traveled / ramp_distance)
                motions.append(Motion(MotionKind.LINEAR, ToolPoint(point.x, point.y, z), plunge_feed))
                previous = point
            if operation.dwell_seconds > TOLERANCE:
                end = motions[-1].end
                motions.append(Motion(MotionKind.DWELL, end, dwell_seconds=operation.dwell_seconds))
            # Re-cut the complete contour at depth.  This prevents the entry
            # ramp itself from leaving a tapered strip on the pocket floor.
            remainder = _contour_points_between(sequence, ramp_distance, perimeter)
            initial = _contour_points_between(sequence, 0.0, ramp_distance)
            for point in [*remainder[1:], *initial[1:]]:
                motions.append(Motion(MotionKind.LINEAR, ToolPoint(point.x, point.y, -depth), cut_feed))
        else:
            motions.append(Motion(MotionKind.LINEAR, ToolPoint(start.x, start.y, -depth), plunge_feed))
            if operation.dwell_seconds > TOLERANCE:
                motions.append(Motion(
                    MotionKind.DWELL, ToolPoint(start.x, start.y, -depth),
                    dwell_seconds=operation.dwell_seconds,
                ))
            motions.extend(
                Motion(MotionKind.LINEAR, ToolPoint(point.x, point.y, -depth), cut_feed)
                for point in sequence[1:]
            )
        end = motions[-1].end
        motions.append(Motion(MotionKind.RAPID, ToolPoint(end.x, end.y, safe_z)))
    if not motions:
        raise ValueError("Island fill produced no usable clearing loops.")
    return motions


def _island_fill_stay_down_motions(
    loops, depth, safe_z, plunge_feed, cut_feed, operation: IslandFillOperation,
    accessible_paths, helix_cache, tool_radius: float, link_stats: dict | None,
) -> list[Motion]:
    """Route remaining loops using verified links and cleared-contour retraces.

    A spatial index bounds connector queries. Reordering never reverses an
    oriented loop, crosses regions, or inherits clearance from another depth.
    When no legal link exists, use the ordinary safe-Z entry path.
    """
    if tool_radius <= TOLERANCE:
        raise ValueError("Keep Tool Down requires a positive cutter radius.")
    prepared = _prepared_island_fill_loops(loops, operation.cut_direction, accessible_paths)
    if not prepared:
        raise ValueError("Island fill produced no usable clearing loops.")

    motions: list[Motion] = []
    cleared_sweep = []
    current: Point | None = None
    linked_count = 0
    plunge_count = 0
    link_segments: list[dict] = []
    helical_entry_used = False
    last_sequence = None
    sample_spacing = max(0.01, min(operation.stay_down_max_distance / 3.0, tool_radius / 2.0))

    remaining = set(range(len(prepared)))
    samples = [_sample_closed_fill_loop(points, sample_spacing) for points in prepared]
    cell_size = max(operation.stay_down_max_distance, 0.01)
    buckets = {}
    for loop_index, loop_samples in enumerate(samples):
        for segment_index, fraction, point in loop_samples:
            key = (math.floor(point.x / cell_size), math.floor(point.y / cell_size))
            buckets.setdefault(key, []).append((loop_index, segment_index, fraction, point))
    while remaining:
        best = None
        approach = []
        loop_index = min(remaining)
        if current is not None and cleared_sweep:
            cx, cy = math.floor(current.x / cell_size), math.floor(current.y / cell_size)
            candidates = sorted(
                (
                    (current.distance_to(point), candidate_loop, segment_index, fraction, point)
                    for bx in range(cx - 1, cx + 2) for by in range(cy - 1, cy + 2)
                    for candidate_loop, segment_index, fraction, point in buckets.get((bx, by), ())
                    if candidate_loop in remaining
                    if current.distance_to(point) <= operation.stay_down_max_distance + TOLERANCE
                ),
                key=lambda value: value[:4],
            )
            best = next((
                candidate for candidate in candidates
                if _fill_stay_down_link_is_safe(
                    current, candidate[4], accessible_paths, cleared_sweep,
                    tool_radius, operation.stay_down_max_engagement,
                )
            ), None)

        if best is None and current is not None and last_sequence:
            # Travel only on the just-cleared closed contour to reach a
            # useful connecting seam. Both directions retrace cut material.
            perimeter = sum(a.distance_to(b) for a, b in zip(last_sequence, last_sequence[1:]))
            traveled = 0.0
            bridge_candidates = []
            for a, b in zip(last_sequence, last_sequence[1:]):
                length = a.distance_to(b)
                divisions = max(1, math.ceil(length / sample_spacing))
                for j in range(divisions):
                    fraction = j / divisions
                    anchor = Point(a.x + (b.x - a.x) * fraction, a.y + (b.y - a.y) * fraction)
                    position = traveled + length * fraction
                    walk = min(position, perimeter - position)
                    if walk / cut_feed > (safe_z + depth) / plunge_feed:
                        continue
                    cx, cy = math.floor(anchor.x / cell_size), math.floor(anchor.y / cell_size)
                    for bx in range(cx - 1, cx + 2):
                        for by in range(cy - 1, cy + 2):
                            for candidate_loop, target_segment, target_fraction, point in buckets.get((bx, by), ()):
                                if candidate_loop not in remaining:
                                    continue
                                gap = anchor.distance_to(point)
                                if gap <= operation.stay_down_max_distance + TOLERANCE:
                                    bridge_candidates.append((walk + gap, candidate_loop, target_segment, target_fraction, point, anchor, position))
                traveled += length
            for candidate in sorted(bridge_candidates, key=lambda value: value[:4]):
                _cost, candidate_loop, target_segment, target_fraction, point, anchor, position = candidate
                if _fill_stay_down_link_is_safe(
                    anchor, point, accessible_paths, cleared_sweep, tool_radius, operation.stay_down_max_engagement,
                ):
                    if position <= perimeter - position:
                        approach = _contour_points_between(last_sequence, 0.0, position)
                    else:
                        approach = list(reversed(_contour_points_between(last_sequence, position, perimeter)))
                    # The distance-to-point reconstruction can differ by a few
                    # floating bits at a clipper half-grid tie. End at the exact
                    # anchor whose connector passed the clearance proof.
                    approach[-1] = anchor
                    best = (anchor.distance_to(point), candidate_loop, target_segment, target_fraction, point)
                    break

        if best is not None:
            loop_index = best[1]
        elif current is not None:
            _distance, loop_index, vertex = min(
                (current.distance_to(point), i, j)
                for i in remaining for j, point in enumerate(prepared[i])
            )
            prepared[loop_index] = [*prepared[loop_index][vertex:], *prepared[loop_index][:vertex]]
        points = prepared[loop_index]
        remaining.remove(loop_index)

        if best is not None:
            _distance, _loop_index, segment_index, fraction, entry = best
            for point in approach[1:]:
                motions.append(Motion(MotionKind.LINEAR, ToolPoint(point.x, point.y, -depth), cut_feed))
            if approach:
                current = approach[-1]
            sequence = _fill_loop_sequence_from_sample(points, segment_index, fraction)
            if current.distance_to(entry) > TOLERANCE:
                link_index = len(motions)
                link_feed = cut_feed * operation.stay_down_feed_percent / 100.0
                motions.append(Motion(MotionKind.LINEAR, ToolPoint(entry.x, entry.y, -depth), link_feed))
                link_segments.append({
                    "motion_index": link_index,
                    "start": (current.x, current.y), "end": (entry.x, entry.y),
                    "depth": depth,
                })
            motions.extend(
                Motion(MotionKind.LINEAR, ToolPoint(point.x, point.y, -depth), cut_feed)
                for point in sequence[1:]
            )
            linked_count += 1
            last_sequence = sequence
        else:
            if current is not None:
                motions.append(Motion(MotionKind.RAPID, ToolPoint(current.x, current.y, safe_z)))
            # ``points`` is already oriented for this particular pass. Re-enter
            # it as an ordinary climb loop so the recursive entry builder does
            # not reverse it again or re-enter stay-down mode.
            entry_operation = replace(operation, keep_tool_down=False, cut_direction="climb")
            if operation.entry_mode == "helical" and helical_entry_used:
                entry_operation = replace(entry_operation, entry_mode="ramp")
            fresh = _island_fill_loop_block_motions(
                [points], depth, safe_z, plunge_feed, cut_feed, entry_operation,
                accessible_paths, helix_cache, tool_radius,
            )
            if fresh and fresh[-1].kind is MotionKind.RAPID:
                fresh = fresh[:-1]
            motions.extend(fresh)
            if operation.entry_mode == "helical" and any(
                motion.kind in {MotionKind.ARC_CW, MotionKind.ARC_CCW}
                and motion.end.z < -TOLERANCE for motion in fresh
            ):
                helical_entry_used = True
            plunge_count += 1
            last_sequence = [*points, points[0]]
        current = Point(motions[-1].end.x, motions[-1].end.y)
        if last_sequence and current.distance_to(last_sequence[0]) > TOLERANCE:
            last_sequence = None
        cleared_sweep = _clipper_union([
            *cleared_sweep,
            *_stroke_region_loops([points], tool_radius),
        ])

    if current is not None:
        motions.append(Motion(MotionKind.RAPID, ToolPoint(current.x, current.y, safe_z)))
    if link_stats is not None:
        link_stats.update({
            "linked_count": linked_count,
            "plunge_count": plunge_count,
            "stay_down_links": link_segments,
            "bidirectional": operation.cut_direction == "bidirectional",
        })
    return motions


def _rectangular_corner_tag_motions(loops, depth, safe_z, plunge_feed, cut_feed) -> list[Motion]:
    """Add short corner-bisector cuts between adjacent rectangular loops."""
    motions: list[Motion] = []
    cleaned = [_clean_polygon_points(loop) for loop in loops]
    for inner, outer in zip(cleaned, cleaned[1:]):
        if len(inner) != 4 or len(outer) != 4:
            continue
        for start, end in zip(inner, outer):
            if start.distance_to(end) <= TOLERANCE:
                continue
            motions.extend((
                Motion(MotionKind.RAPID, ToolPoint(start.x, start.y, safe_z)),
                Motion(MotionKind.LINEAR, ToolPoint(start.x, start.y, -depth), plunge_feed),
                Motion(MotionKind.LINEAR, ToolPoint(end.x, end.y, -depth), cut_feed),
                Motion(MotionKind.RAPID, ToolPoint(end.x, end.y, safe_z)),
            ))
    if not motions:
        raise ValueError("Corner coverage was requested, but no rectangular corner spans were found.")
    return motions


def _rounded_island_cleanup_motions(
    source_box, offsets, depth, safe_z, plunge_feed, cut_feed,
    cut_direction="climb", entry_mode="plunge", dwell_seconds=0.0,
    operation: IslandFillOperation | None = None, accessible_paths=None, helix_cache=None,
):
    clockwise = cut_direction == "conventional"
    first_loop = _rounded_rectangle_loop(source_box, offsets[0], depth, cut_feed, clockwise)
    start = first_loop[0].end
    motions: list[Motion] = []
    actual_entry_mode = entry_mode
    if entry_mode == "helical" and operation is not None:
        first_cut = first_loop[1]
        if first_cut.center is not None:
            radial = Point(start.x - first_cut.center.x, start.y - first_cut.center.y)
            radius = math.hypot(radial.x, radial.y)
            if radius <= TOLERANCE:
                tangent = Point(1.0, 0.0)
            elif first_cut.kind == MotionKind.ARC_CCW:
                tangent = Point(-radial.y / radius, radial.x / radius)
            else:
                tangent = Point(radial.y / radius, -radial.x / radius)
        else:
            dx, dy = first_cut.end.x - start.x, first_cut.end.y - start.y
            length = math.hypot(dx, dy)
            tangent = Point(dx / length, dy / length) if length > TOLERANCE else Point(1.0, 0.0)
        selection = _select_fill_helix(
            [(Point(start.x, start.y), tangent, None)],
            accessible_paths or [],
            operation.helix_diameter,
            operation.helix_min_diameter,
            operation.helix_clearance,
            (helix_cache if helix_cache is not None else {}).setdefault("clearance_regions", {}),
        )
        if selection is not None:
            motions.extend(_fill_helix_motions(selection, depth, safe_z, plunge_feed, operation))
            if dwell_seconds > TOLERANCE:
                motions.append(Motion(MotionKind.DWELL, motions[-1].end, dwell_seconds=dwell_seconds))
            motions.extend(first_loop[1:])
        elif operation.helix_fallback == "error":
            raise ValueError(
                f"The preferred {operation.helix_diameter:g} diameter island-fill helix cannot fit beside a protected island. "
                "Reduce the helix diameter or choose a linear-ramp/plunge fallback."
            )
        else:
            actual_entry_mode = operation.helix_fallback
    if not motions:
        motions = [Motion(MotionKind.RAPID, ToolPoint(start.x, start.y, safe_z))]
    if actual_entry_mode == "ramp" and len(motions) == 1:
        motions.append(Motion(MotionKind.LINEAR, ToolPoint(start.x, start.y, 0.0), plunge_feed))
        first_cut = first_loop[1]
        motions.append(Motion(
            first_cut.kind,
            ToolPoint(first_cut.end.x, first_cut.end.y, -depth),
            plunge_feed,
            first_cut.center,
        ))
        if dwell_seconds > TOLERANCE:
            motions.append(Motion(MotionKind.DWELL, motions[-1].end, dwell_seconds=dwell_seconds))
        # Complete the loop at depth, then re-cut the ramped first segment so
        # the cleanup contour reaches full depth everywhere.
        motions.extend(first_loop[2:])
        motions.append(Motion(
            first_cut.kind,
            ToolPoint(first_cut.end.x, first_cut.end.y, -depth),
            cut_feed,
            first_cut.center,
        ))
    elif len(motions) == 1:
        motions.append(Motion(MotionKind.LINEAR, ToolPoint(start.x, start.y, -depth), plunge_feed))
        if dwell_seconds > TOLERANCE:
            motions.append(Motion(MotionKind.DWELL, motions[-1].end, dwell_seconds=dwell_seconds))
        motions.extend(first_loop[1:])
    for offset in offsets[1:]:
        loop = _rounded_rectangle_loop(source_box, offset, depth, cut_feed, clockwise)
        next_start = loop[0].end
        motions.append(Motion(MotionKind.LINEAR, next_start, cut_feed))
        motions.extend(loop[1:])
    last = motions[-1].end
    motions.append(Motion(MotionKind.RAPID, ToolPoint(last.x, last.y, safe_z)))
    return motions


def _rounded_rectangle_loop(source_box, offset, depth, feed, clockwise=False) -> list[Motion]:
    min_x, min_y, max_x, max_y = source_box
    z = -depth
    start = ToolPoint(min_x - offset, min_y, z)
    if clockwise:
        return [
            Motion(MotionKind.LINEAR, start, feed),
            Motion(MotionKind.LINEAR, ToolPoint(min_x - offset, max_y, z), feed),
            Motion(MotionKind.ARC_CW, ToolPoint(min_x, max_y + offset, z), feed, Point(min_x, max_y)),
            Motion(MotionKind.LINEAR, ToolPoint(max_x, max_y + offset, z), feed),
            Motion(MotionKind.ARC_CW, ToolPoint(max_x + offset, max_y, z), feed, Point(max_x, max_y)),
            Motion(MotionKind.LINEAR, ToolPoint(max_x + offset, min_y, z), feed),
            Motion(MotionKind.ARC_CW, ToolPoint(max_x, min_y - offset, z), feed, Point(max_x, min_y)),
            Motion(MotionKind.LINEAR, ToolPoint(min_x, min_y - offset, z), feed),
            Motion(MotionKind.ARC_CW, start, feed, Point(min_x, min_y)),
        ]
    return [
        Motion(MotionKind.LINEAR, start, feed),
        Motion(MotionKind.ARC_CCW, ToolPoint(min_x, min_y - offset, z), feed, Point(min_x, min_y)),
        Motion(MotionKind.LINEAR, ToolPoint(max_x, min_y - offset, z), feed),
        Motion(MotionKind.ARC_CCW, ToolPoint(max_x + offset, min_y, z), feed, Point(max_x, min_y)),
        Motion(MotionKind.LINEAR, ToolPoint(max_x + offset, max_y, z), feed),
        Motion(MotionKind.ARC_CCW, ToolPoint(max_x, max_y + offset, z), feed, Point(max_x, max_y)),
        Motion(MotionKind.LINEAR, ToolPoint(min_x, max_y + offset, z), feed),
        Motion(MotionKind.ARC_CCW, ToolPoint(min_x - offset, max_y, z), feed, Point(min_x, max_y)),
        Motion(MotionKind.LINEAR, start, feed),
    ]
