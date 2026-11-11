"""Conservative, measurable 2D cycle-time optimization.

The optimizer operates on controller-neutral motions after ordinary CAM
generation.  Every behavior is project opt-in, preserves operation ordering,
and records enough metadata for preflight and operator-facing comparisons.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import copy
import math
from typing import Sequence

from .model import (
    CamProject,
    DrillCirclesOperation,
    EngraveOperation,
    OffsetSide,
    Operation,
    Point,
    RouterOffsetOperation,
)
from .toolpath import GeneratedToolpath, Motion, MotionKind, ToolPoint, ToolpathBlock
from .topology import profile_containers
from .units import unit_scale


TOLERANCE = 1e-8


@dataclass(frozen=True)
class CycleOptimizationSettings:
    enabled: bool = False
    depth_linking: bool = True
    profile_ordering: bool = True
    profile_travel_strategy: str = "generated"
    profile_start_optimization: bool = True
    drill_ordering: bool = True
    engraving_ordering: bool = True
    minimum_retract: bool = True
    minimum_retract_height: float = 0.05
    minimum_retract_max_distance: float = 12.0
    motion_compaction: bool = True
    line_merge_tolerance: float = 0.00005
    arc_fitting: bool = True
    arc_fitting_tolerance: float = 0.001
    rapid_xy_ipm: float = 600.0
    rapid_z_ipm: float = 300.0
    tool_change_seconds: float = 8.0
    spindle_start_seconds: float = 2.0


@dataclass(frozen=True)
class CycleMetrics:
    estimated_seconds: float = 0.0
    cutting_seconds: float = 0.0
    rapid_seconds: float = 0.0
    dwell_seconds: float = 0.0
    tool_change_seconds: float = 0.0
    cutting_distance: float = 0.0
    rapid_distance: float = 0.0
    blocks: int = 0
    motions: int = 0
    plunges: int = 0
    retracts: int = 0
    tool_changes: int = 0
    depth_links: int = 0
    low_retract_links: int = 0
    reordered_blocks: int = 0
    compacted_motions: int = 0
    fitted_arcs: int = 0

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass(frozen=True)
class CycleComparison:
    baseline: CycleMetrics
    optimized: CycleMetrics

    @property
    def seconds_saved(self) -> float:
        return max(0.0, self.baseline.estimated_seconds - self.optimized.estimated_seconds)

    @property
    def percent_saved(self) -> float:
        if self.baseline.estimated_seconds <= TOLERANCE:
            return 0.0
        return self.seconds_saved / self.baseline.estimated_seconds * 100.0

    def to_dict(self) -> dict:
        return {
            "baseline": self.baseline.to_dict(),
            "optimized": self.optimized.to_dict(),
            "seconds_saved": self.seconds_saved,
            "percent_saved": self.percent_saved,
        }


@dataclass
class _ProfileRouteGroup:
    token: int
    phase: int
    entity_ids: tuple[str, ...]
    blocks: list[ToolpathBlock]


PRODUCTION_OPTIMIZATION_BEHAVIORS = {
    "enabled": True,
    "depth_linking": True,
    "profile_ordering": True,
    "profile_travel_strategy": "nearest_safe",
    "profile_start_optimization": True,
    "drill_ordering": True,
    "engraving_ordering": True,
    # These two remain advanced because they depend on complete fixture data
    # or controller qualification rather than toolpath topology alone.
    "minimum_retract": False,
    "motion_compaction": True,
    "arc_fitting": False,
}


def default_cycle_optimization_settings(units: str = "in") -> dict:
    factor = unit_scale("in", units)
    return {
        "enabled": False,
        "depth_linking": True,
        "profile_ordering": True,
        "profile_travel_strategy": "generated",
        "profile_start_optimization": True,
        "drill_ordering": True,
        "engraving_ordering": True,
        "minimum_retract": True,
        "minimum_retract_height": 0.05 * factor,
        "minimum_retract_max_distance": 12.0 * factor,
        "motion_compaction": True,
        "line_merge_tolerance": 0.00005 * factor,
        "arc_fitting": True,
        "arc_fitting_tolerance": 0.001 * factor,
        "rapid_xy_ipm": 600.0,
        "rapid_z_ipm": 300.0,
        "tool_change_seconds": 8.0,
        "spindle_start_seconds": 2.0,
    }


def production_cycle_optimization_settings(units: str = "in") -> dict:
    """Return the low-friction, topology-safe production motion policy."""
    values = default_cycle_optimization_settings(units)
    values.update(PRODUCTION_OPTIMIZATION_BEHAVIORS)
    return values


def cycle_optimization_settings(project: CamProject) -> CycleOptimizationSettings:
    values = default_cycle_optimization_settings(project.units)
    stored = project.metadata.get("cycle_optimization", {})
    if isinstance(stored, dict):
        values.update({key: value for key, value in stored.items() if key in values})
    return CycleOptimizationSettings(**values)


def cycle_optimization_mode(project: CamProject) -> str:
    """Classify the current project as production, original, or custom motion."""
    settings = cycle_optimization_settings(project)
    if not settings.enabled:
        return "original"
    if all(getattr(settings, key) == expected for key, expected in PRODUCTION_OPTIMIZATION_BEHAVIORS.items()):
        return "production"
    return "custom"


def validate_cycle_optimization_settings(settings: CycleOptimizationSettings) -> None:
    if settings.profile_travel_strategy not in {"generated", "nearest_safe", "y_serpentine", "small_edge_first"}:
        raise ValueError("Choose generated, nearest safe, Y-column serpentine, or small parts / edges first.")
    if settings.minimum_retract_height <= 0 or settings.minimum_retract_max_distance <= 0:
        raise ValueError("Minimum retract height and maximum link distance must be positive.")
    if settings.line_merge_tolerance < 0 or settings.arc_fitting_tolerance <= 0:
        raise ValueError("Line tolerance cannot be negative and arc-fitting tolerance must be positive.")
    if settings.rapid_xy_ipm <= 0 or settings.rapid_z_ipm <= 0:
        raise ValueError("Rapid XY and Z rates must be positive for cycle-time estimates.")
    if settings.tool_change_seconds < 0 or settings.spindle_start_seconds < 0:
        raise ValueError("Tool-change and spindle-start allowances cannot be negative.")


def optimize_generated_toolpath(
    project: CamProject,
    operation: Operation,
    generated: GeneratedToolpath,
) -> GeneratedToolpath:
    settings = cycle_optimization_settings(project)
    validate_cycle_optimization_settings(settings)
    if not settings.enabled or not generated.blocks:
        return generated
    if operation.profile_retention.get('workflow') == 'waste_then_tabbed_finish':
        # Keep the tested inner-to-outer clearing, full-safe-Z retracts and
        # tabbed finish motion intact. All independent collision audits remain.
        return generated
    from .model import TabReleaseOperation, DragKnifeOperation
    if isinstance(operation, DragKnifeOperation):
        # Tip compensation, swivel Z and alignment moves are inseparable.
        return generated
    if isinstance(operation, TabReleaseOperation) or any(
        isinstance(stage, TabReleaseOperation) and stage.enabled
        and operation.id in stage.depends_on_ids for stage in project.operations
    ):
        # A reviewed final-release contract freezes its prerequisite motions too.
        return generated
    # The reviewed span order and entry/bridge sweeps are authoritative. Generic
    # route/start/retract or arc fitting must not rewrite that reviewed plan.
    if isinstance(operation, RouterOffsetOperation) and operation.tab_release_plan:
        if (operation.tab_release_plan.get("mode") == "automatic_opposed"
                and settings.profile_ordering
                and settings.profile_travel_strategy == "small_edge_first"):
            # Automatic retain-two is a placement policy, not a manually
            # reviewed route. Reorder whole profile groups only: exact entry,
            # passes, retained spans and cleanup sequence remain unchanged.
            profiles = [b for b in generated.blocks if b.metadata.get("role") != "tab_cleanup"]
            cleanup = [b for b in generated.blocks if b.metadata.get("role") == "tab_cleanup"]
            ordered = _order_profile_groups(project, operation, profiles, False, "small_edge_first")
            stamp = generated.blocks[0].metadata.get("automatic_tab_cleanup")
            ordered = [replace(b, metadata={k:v for k,v in b.metadata.items() if k != "automatic_tab_cleanup"}) for b in ordered]
            if ordered and stamp:
                ordered[0] = replace(ordered[0], metadata={**ordered[0].metadata, "automatic_tab_cleanup":copy.deepcopy(stamp)})
            return GeneratedToolpath([*ordered,*cleanup])
        return generated

    blocks = [replace(block, motions=list(block.motions), metadata=dict(block.metadata)) for block in generated.blocks]
    if isinstance(operation, DrillCirclesOperation) and settings.drill_ordering:
        blocks = _nearest_order_blocks(blocks, allow_engrave_reverse=False)
    elif isinstance(operation, EngraveOperation) and settings.engraving_ordering:
        from .engrave_routing import order_engraving_blocks
        blocks = order_engraving_blocks(blocks)
    elif (
        isinstance(operation, RouterOffsetOperation)
        and settings.profile_ordering
    ):
        blocks = _order_profile_groups(
            project,
            operation,
            blocks,
            settings.profile_start_optimization,
            settings.profile_travel_strategy,
        )

    if isinstance(operation, RouterOffsetOperation) and settings.depth_linking:
        blocks = _link_profile_depths(blocks)
    if settings.minimum_retract:
        blocks = _apply_minimum_retracts(project, operation, blocks, settings)
    if settings.motion_compaction:
        blocks = [
            _compact_block(
                block,
                settings.line_merge_tolerance,
                settings.arc_fitting_tolerance if settings.arc_fitting else None,
            )
            for block in blocks
        ]
    return GeneratedToolpath(blocks)


def _block_entity_ids(block: ToolpathBlock, fallback: str) -> tuple[str, ...]:
    stored = block.metadata.get("entity_ids")
    if isinstance(stored, (list, tuple)):
        values = tuple(dict.fromkeys(str(value) for value in stored if str(value)))
    else:
        entity_id = str(block.metadata.get("entity_id", ""))
        values = (entity_id,) if entity_id else ()
    return values or (fallback,)


def _profile_route_groups(
    blocks: list[ToolpathBlock],
    operation: RouterOffsetOperation,
) -> list[_ProfileRouteGroup]:
    grouped: dict[tuple[int, tuple[str, ...]], _ProfileRouteGroup] = {}
    for block_index, block in enumerate(blocks):
        phase = int(
            operation.tab_cleanup_order == "end_of_operation"
            and block.metadata.get("role") == "tab_cleanup"
        )
        entity_ids = _block_entity_ids(block, f"__block_{block_index}")
        key = (phase, entity_ids)
        group = grouped.get(key)
        if group is None:
            group = _ProfileRouteGroup(len(grouped), phase, entity_ids, [])
            grouped[key] = group
        group.blocks.append(block)
    return list(grouped.values())


def _prepare_profile_group(
    group: _ProfileRouteGroup,
    current: ToolPoint,
    optimize_starts: bool,
) -> _ProfileRouteGroup:
    prepared: list[ToolpathBlock] = []
    for block in group.blocks:
        selected = _rotate_closed_block_near(block, current) if optimize_starts else block
        prepared.append(selected)
        current = _block_exit(selected)
    return _ProfileRouteGroup(group.token, group.phase, group.entity_ids, prepared)


def _profile_prerequisites(
    project: CamProject,
    operation: RouterOffsetOperation,
    groups: list[_ProfileRouteGroup],
) -> dict[int, set[int]]:
    prerequisites = {group.token: set() for group in groups}
    if operation.cut_intent == "partial":
        # A partial-depth contour cannot release a part, so compensation side
        # and geometric containment do not impose a machining sequence.
        return prerequisites

    entity_by_id = {entity.id: entity for entity in project.entities}
    profile_ids = {
        entity_id
        for group in groups
        for entity_id in group.entity_ids
        if entity_id in entity_by_id
    }
    try:
        containers = profile_containers([entity_by_id[entity_id] for entity_id in profile_ids])
    except ValueError:
        # Open Cut Line operations do not have a closed-profile containment
        # graph, so their generated source direction remains literal.
        return prerequisites

    group_for_entity = {
        (group.phase, entity_id): group.token
        for group in groups
        for entity_id in group.entity_ids
    }
    for phase in {group.phase for group in groups}:
        for child_id, parent_ids in containers.items():
            child_token = group_for_entity.get((phase, child_id))
            if child_token is None:
                continue
            for parent_id in parent_ids:
                parent_token = group_for_entity.get((phase, parent_id))
                if parent_token is not None and parent_token != child_token:
                    prerequisites[parent_token].add(child_token)
    return prerequisites


def _profile_route_score(current: ToolPoint, entry: ToolPoint, strategy: str) -> float:
    distance = _xy_distance(current, entry)
    if strategy == "nearest_safe":
        return distance
    # Softly favor progress from plate X0 toward positive X. A nearby choice
    # still wins, so this behaves like an up/down-Y serpentine rather than a
    # rigid raster scan with avoidable long moves.
    delta_x = entry.x - current.x
    return distance + abs(delta_x) * 0.35 + max(0.0, -delta_x) * 2.0


def _plan_profile_route(
    groups: list[_ProfileRouteGroup],
    prerequisites: dict[int, set[int]],
    optimize_starts: bool,
    strategy: str,
    priorities: dict[int, float] | None = None,
) -> list[_ProfileRouteGroup]:
    remaining = list(groups)
    result: list[_ProfileRouteGroup] = []
    current = ToolPoint(0.0, 0.0, 0.0)
    while remaining:
        phase = min(group.phase for group in remaining)
        phase_tokens = {group.token for group in remaining if group.phase == phase}
        eligible = [
            group for group in remaining
            if group.phase == phase
            and not (prerequisites.get(group.token, set()) & phase_tokens)
        ]
        if not eligible:
            # Never deadlock or drop motion if malformed geometry produces an
            # impossible graph. The generated order is the conservative exit.
            eligible = [next(group for group in remaining if group.phase == phase)]
        candidates = []
        for group in eligible:
            prepared = _prepare_profile_group(group, current, optimize_starts)
            candidates.append((
                (priorities.get(group.token, math.inf) if priorities is not None else 0,
                 _profile_route_score(current, _block_entry(prepared.blocks[0]), "nearest_safe" if priorities is not None else strategy)),
                group.token,
                prepared,
            ))
        _score, _token, selected = min(candidates, key=lambda item: (item[0], item[1]))
        result.append(selected)
        current = _block_exit(selected.blocks[-1])
        remaining = [group for group in remaining if group.token != selected.token]
    return result


def _profile_route_distance(groups: list[_ProfileRouteGroup]) -> float:
    current = ToolPoint(0.0, 0.0, 0.0)
    distance = 0.0
    for group in groups:
        for block in group.blocks:
            distance += _xy_distance(current, _block_entry(block))
            current = _block_exit(block)
    return distance


def _order_profile_groups(
    project: CamProject,
    operation: RouterOffsetOperation,
    blocks: list[ToolpathBlock],
    optimize_starts: bool,
    strategy: str,
) -> list[ToolpathBlock]:
    if not blocks:
        return blocks
    operation_ids = set(operation.entity_ids)
    manual = operation.manual_contour_order
    manual_is_active = (
        len(manual) == len(operation_ids)
        and len(set(manual)) == len(manual)
        and set(manual) == operation_ids
    )
    if strategy == "generated" or manual_is_active or operation.contour_order == "source_order":
        return _optimize_profile_starts_in_order(blocks) if optimize_starts else blocks

    groups = _profile_route_groups(blocks, operation)
    # Containment metadata is emitted by the closed-profile generator. Its
    # absence identifies an open Cut Line whose order/direction stays literal.
    if not any("containment_depth" in block.metadata for block in blocks):
        return _optimize_profile_starts_in_order(blocks) if optimize_starts else blocks
    prerequisites = _profile_prerequisites(project, operation, groups)
    priorities = None
    if strategy == "small_edge_first":
        from .edge_ordering import small_edge_priorities
        priorities = small_edge_priorities(project, groups)
        if not priorities or all(math.isinf(value) for value in priorities.values()):
            return _optimize_profile_starts_in_order(blocks) if optimize_starts else blocks
    selected = _plan_profile_route(groups, prerequisites, optimize_starts, strategy, priorities)
    before = _profile_route_distance(groups)
    after = _profile_route_distance(selected)
    original_positions = {group.token: index for index, group in enumerate(groups)}
    output: list[ToolpathBlock] = []
    for position, group in enumerate(selected):
        moved = original_positions[group.token] != position
        for block in group.blocks:
            metadata = {
                **block.metadata,
                "profile_travel_strategy": strategy,
                "profile_travel_distance_before": before,
                "profile_travel_distance_after": after,
            }
            if moved:
                metadata["travel_reordered"] = True
            output.append(replace(block, metadata=metadata))
    return output


def _optimize_profile_starts_in_order(blocks: list[ToolpathBlock]) -> list[ToolpathBlock]:
    """Rotate eligible closed paths toward the prior exit without reordering."""
    output: list[ToolpathBlock] = []
    current = ToolPoint(0.0, 0.0, 0.0)
    for block in blocks:
        selected = _rotate_closed_block_near(block, current)
        output.append(selected)
        current = _block_exit(selected)
    return output


def _nearest_order_blocks(blocks: list[ToolpathBlock], *, allow_engrave_reverse: bool) -> list[ToolpathBlock]:
    remaining = list(enumerate(blocks))
    ordered: list[ToolpathBlock] = []
    current = ToolPoint(0.0, 0.0, 0.0)
    while remaining:
        candidates = []
        for position, (source_index, block) in enumerate(remaining):
            options = [(block, False)]
            reversed_block = _reverse_open_engrave_block(block) if allow_engrave_reverse else None
            if reversed_block is not None:
                options.append((reversed_block, True))
            for option, reversed_path in options:
                candidates.append((
                    _xy_distance(current, _block_entry(option)), source_index, position,
                    option, reversed_path,
                ))
        _distance, source_index, position, selected, reversed_path = min(candidates, key=lambda item: (item[0], item[1]))
        if source_index != len(ordered) or reversed_path:
            selected = _mark_block(selected, "travel_reordered", True)
        if reversed_path:
            selected = _mark_block(selected, "path_reversed_for_travel", True)
        ordered.append(selected)
        current = _block_exit(selected)
        remaining.pop(position)
    return _two_opt_blocks(ordered)


def _two_opt_blocks(blocks: list[ToolpathBlock]) -> list[ToolpathBlock]:
    if len(blocks) < 4:
        return blocks
    # Reversing the order of a sub-route is only valid when each block returns
    # to its own XY entry (drill points and closed contours).  Open engraving
    # paths have direction-dependent exits and are handled by the greedy pass.
    if any(_xy_distance(_block_entry(block), _block_exit(block)) > TOLERANCE for block in blocks):
        return blocks
    route = list(blocks)
    improved = True
    while improved:
        improved = False
        for left in range(0, len(route) - 2):
            before = ToolPoint(0.0, 0.0, 0.0) if left == 0 else _block_exit(route[left - 1])
            for right in range(left + 1, len(route) - 1):
                after = _block_entry(route[right + 1])
                old = _xy_distance(before, _block_entry(route[left])) + _xy_distance(_block_exit(route[right]), after)
                new = _xy_distance(before, _block_entry(route[right])) + _xy_distance(_block_exit(route[left]), after)
                if new + TOLERANCE < old:
                    route[left:right + 1] = reversed(route[left:right + 1])
                    route[left:right + 1] = [_mark_block(block, "travel_reordered", True) for block in route[left:right + 1]]
                    improved = True
                    break
            if improved:
                break
    return route


def _reverse_open_engrave_block(block: ToolpathBlock) -> ToolpathBlock | None:
    motions = block.motions
    if len(motions) < 6 or block.metadata.get("closed") is not False:
        return None
    if motions[0].kind is not MotionKind.RAPID or motions[1].kind is not MotionKind.RAPID:
        return None
    if motions[2].kind is not MotionKind.LINEAR or motions[-2].kind is not MotionKind.LINEAR or motions[-1].kind is not MotionKind.RAPID:
        return None
    trace = motions[3:-2]
    if not trace or any(motion.kind is not MotionKind.LINEAR for motion in trace):
        return None
    original_start = motions[2].end
    vertices = [original_start, *(motion.end for motion in trace)]
    new_start = vertices[-1]
    safe_z = motions[0].end.z
    approach_z = motions[1].end.z
    compression_z = original_start.z
    trace_feed = trace[0].feed
    reversed_motions = [
        Motion(MotionKind.RAPID, ToolPoint(new_start.x, new_start.y, safe_z)),
        Motion(MotionKind.RAPID, ToolPoint(new_start.x, new_start.y, approach_z)),
        Motion(MotionKind.LINEAR, ToolPoint(new_start.x, new_start.y, compression_z), motions[2].feed),
    ]
    reversed_motions.extend(
        Motion(MotionKind.LINEAR, ToolPoint(point.x, point.y, compression_z), trace_feed)
        for point in reversed(vertices[:-1])
    )
    end = vertices[0]
    reversed_motions.extend((
        Motion(MotionKind.LINEAR, ToolPoint(end.x, end.y, approach_z), motions[-2].feed),
        Motion(MotionKind.RAPID, ToolPoint(end.x, end.y, safe_z)),
    ))
    return replace(block, motions=reversed_motions, metadata=dict(block.metadata))


def _rotate_closed_block_near(block: ToolpathBlock, target: ToolPoint) -> ToolpathBlock:
    motions = block.motions
    if (
        len(motions) < 6
        or motions[0].kind is not MotionKind.RAPID
        or motions[1].kind is not MotionKind.LINEAR
        or motions[-1].kind is not MotionKind.RAPID
        or str(block.metadata.get("entry_mode_actual", block.metadata.get("entry_mode", ""))) != "plunge"
        or float(block.metadata.get("finished_tab_width", 0.0) or 0.0) > TOLERANCE
    ):
        return block
    start = motions[1].end
    edges = motions[2:-1]
    if not edges or any(
        motion.kind not in {MotionKind.LINEAR, MotionKind.ARC_CW, MotionKind.ARC_CCW}
        or abs(motion.end.z - start.z) > TOLERANCE
        for motion in edges
    ):
        return block
    if _point_distance(edges[-1].end, start) > TOLERANCE:
        return block
    starts = [start, *(motion.end for motion in edges[:-1])]
    selected = min(range(len(starts)), key=lambda index: _xy_distance(target, starts[index]))
    if selected == 0:
        return block
    rotated = edges[selected:] + edges[:selected]
    new_start = starts[selected]
    new_motions = [
        Motion(MotionKind.RAPID, ToolPoint(new_start.x, new_start.y, motions[0].end.z)),
        Motion(MotionKind.LINEAR, ToolPoint(new_start.x, new_start.y, start.z), motions[1].feed),
        *rotated,
        Motion(MotionKind.RAPID, ToolPoint(new_start.x, new_start.y, motions[-1].end.z)),
    ]
    metadata = {**block.metadata, "start_point_optimized": True, "start_rotation_index": selected}
    return replace(block, motions=new_motions, metadata=metadata)


def _link_profile_depths(blocks: list[ToolpathBlock]) -> list[ToolpathBlock]:
    output: list[ToolpathBlock] = []
    for block in blocks:
        if output and _depth_link_compatible(output[-1], block):
            previous = output[-1]
            motions = [*previous.motions[:-1], *block.motions[1:]]
            metadata = dict(previous.metadata)
            metadata["depth_links_saved"] = int(metadata.get("depth_links_saved", 0)) + 1
            metadata["linked_depths"] = [
                *metadata.get("linked_depths", [previous.metadata.get("depth")]),
                block.metadata.get("depth"),
            ]
            metadata["depth"] = block.metadata.get("depth", metadata.get("depth"))
            output[-1] = replace(
                previous,
                motions=motions,
                label=f"{previous.label} through {block.metadata.get('depth', '')}",
                metadata=metadata,
            )
        else:
            output.append(block)
    return output


def _depth_link_compatible(first: ToolpathBlock, second: ToolpathBlock) -> bool:
    if (
        first.tool_station != second.tool_station
        or first.metadata.get("entity_id") != second.metadata.get("entity_id")
        or first.metadata.get("path_index") != second.metadata.get("path_index")
        or first.metadata.get("side") != second.metadata.get("side")
        or first.metadata.get("role") != second.metadata.get("role")
        or first.metadata.get("role") not in {"rough"}
        or float(first.metadata.get("finished_tab_width", 0.0) or 0.0) > TOLERANCE
        or float(second.metadata.get("finished_tab_width", 0.0) or 0.0) > TOLERANCE
        or len(first.motions) < 4 or len(second.motions) < 4
        or first.motions[-1].kind is not MotionKind.RAPID
        or second.motions[0].kind is not MotionKind.RAPID
        or second.motions[1].kind is not MotionKind.LINEAR
    ):
        return False
    first_depth = float(first.metadata.get("depth", 0.0) or 0.0)
    second_depth = float(second.metadata.get("depth", 0.0) or 0.0)
    if second_depth <= first_depth + TOLERANCE:
        return False
    first_end = first.motions[-2].end
    second_entry = second.motions[0].end
    second_plunge = second.motions[1].end
    return (
        _xy_distance(first_end, second_entry) <= TOLERANCE
        and _xy_distance(first_end, second_plunge) <= TOLERANCE
        and abs(first_end.z + first_depth) <= TOLERANCE
        and abs(second_plunge.z + second_depth) <= TOLERANCE
    )


def _apply_minimum_retracts(
    project: CamProject,
    operation: Operation,
    blocks: list[ToolpathBlock],
    settings: CycleOptimizationSettings,
) -> list[ToolpathBlock]:
    if len(blocks) < 2:
        return blocks
    output = [blocks[0]]
    for block in blocks[1:]:
        previous = output[-1]
        distance = _xy_distance(_block_exit(previous), _block_entry(block))
        travel_z = max(settings.minimum_retract_height, getattr(operation, "approach_height", 0.0))
        for zone in project.keepouts:
            if zone.enabled and _segment_intersects_polygon(_block_exit(previous), _block_entry(block), zone.points):
                travel_z = max(travel_z, zone.clearance_z)
        can_link = (
            previous.tool_station == block.tool_station
            # Part-by-part ordering can release the just-finished component.
            # Return to full Safe Z after its outside profile rather than
            # skimming a low link past a potentially shifted loose part.
            and not (
                isinstance(operation, RouterOffsetOperation)
                and operation.contour_order == "part_by_part"
                and previous.metadata.get("side") == OffsetSide.OUTSIDE.value
            )
            # A block-level RPM change must occur after a full safe retract and
            # router disengagement. Never convert that boundary into a
            # spindle-on low-retract link.
            and previous.metadata.get("spindle_rpm") == block.metadata.get("spindle_rpm")
            and 0 < distance <= settings.minimum_retract_max_distance + TOLERANCE
            and travel_z <= operation.safe_z + TOLERANCE
            and previous.motions and block.motions
            and previous.motions[-1].kind is MotionKind.RAPID
            and block.motions[0].kind is MotionKind.RAPID
        )
        if not can_link:
            output.append(block)
            continue
        previous_exit = previous.motions[-1].end
        block_entry = block.motions[0].end
        previous_motions = [
            *previous.motions[:-1],
            Motion(MotionKind.RAPID, ToolPoint(previous_exit.x, previous_exit.y, travel_z)),
        ]
        previous_metadata = {**previous.metadata, "low_retract_to_next": True, "low_retract_height": travel_z}
        output[-1] = replace(previous, motions=previous_motions, metadata=previous_metadata)
        block_motions = [
            Motion(MotionKind.RAPID, ToolPoint(block_entry.x, block_entry.y, travel_z)),
            *block.motions[1:],
        ]
        block_metadata = {**block.metadata, "low_retract_from_previous": True, "low_retract_height": travel_z}
        output.append(replace(block, motions=block_motions, metadata=block_metadata))
    return output


def _compact_block(block: ToolpathBlock, line_tolerance: float, arc_tolerance: float | None) -> ToolpathBlock:
    if block.metadata.get("cleared_lead_returns"):
        return block  # exact prior-segment proof uses immutable motion indices
    # These proofs refer to exact motion indices and earlier wall centerlines.
    # Re-fitting/merging them would invalidate the proof even if the display
    # looked unchanged. Preserve them until compaction can rebuild attribution.
    if block.metadata.get("stay_down_links") or block.metadata.get("role") in {"speed_primary", "speed_center_cleanup"}:
        return block
    original_count = len(block.motions)
    motions = _merge_collinear_motions(block.motions, line_tolerance)
    fitted_arcs = 0
    if arc_tolerance is not None:
        motions, fitted_arcs = _fit_linear_runs_to_arcs(motions, arc_tolerance)
    removed = original_count - len(motions)
    if removed <= 0 and fitted_arcs <= 0:
        return block
    metadata = {
        **block.metadata,
        "compacted_motions": removed,
        "fitted_arcs": fitted_arcs,
        "compaction_tolerance": arc_tolerance if arc_tolerance is not None else line_tolerance,
    }
    return replace(block, motions=motions, metadata=metadata)


def _merge_collinear_motions(motions: list[Motion], tolerance: float) -> list[Motion]:
    if len(motions) < 3:
        return motions
    output: list[Motion] = []
    current: ToolPoint | None = None
    for motion in motions:
        if (
            output and current is not None
            and output[-1].kind is MotionKind.LINEAR and motion.kind is MotionKind.LINEAR
            and output[-1].feed == motion.feed
        ):
            previous_start = _motion_start(output, len(output) - 1)
            middle = output[-1].end
            end = motion.end
            if previous_start is not None and _mergeable_collinear(previous_start, middle, end, tolerance):
                output[-1] = replace(motion)
                current = motion.end
                continue
        output.append(motion)
        current = motion.end
    return output


def _motion_start(motions: list[Motion], index: int) -> ToolPoint | None:
    return motions[index - 1].end if index > 0 else None


def _mergeable_collinear(first: ToolPoint, middle: ToolPoint, last: ToolPoint, tolerance: float) -> bool:
    if max(abs(first.z - middle.z), abs(middle.z - last.z)) > TOLERANCE:
        return False
    dx, dy = last.x - first.x, last.y - first.y
    length = math.hypot(dx, dy)
    if length <= TOLERANCE:
        return False
    deviation = abs((middle.x - first.x) * dy - (middle.y - first.y) * dx) / length
    dot = (middle.x - first.x) * dx + (middle.y - first.y) * dy
    return deviation <= max(tolerance, TOLERANCE) and -TOLERANCE <= dot <= length * length + TOLERANCE


def _fit_linear_runs_to_arcs(motions: list[Motion], tolerance: float) -> tuple[list[Motion], int]:
    if len(motions) < 4:
        return motions, 0
    output: list[Motion] = []
    fitted = 0
    index = 0
    current: ToolPoint | None = None
    while index < len(motions):
        motion = motions[index]
        if current is None or motion.kind is not MotionKind.LINEAR:
            output.append(motion)
            current = motion.end
            index += 1
            continue
        run = [current]
        end_index = index
        feed = motion.feed
        z = current.z
        while end_index < len(motions):
            candidate = motions[end_index]
            if candidate.kind is not MotionKind.LINEAR or candidate.feed != feed or abs(candidate.end.z - z) > TOLERANCE:
                break
            run.append(candidate.end)
            end_index += 1
        consumed = 0
        if len(run) >= 4:
            for count in range(len(run), 3, -1):
                fit = _fit_arc(run[:count], tolerance)
                if fit is not None:
                    kind, center = fit
                    output.append(Motion(kind, run[count - 1], feed, center))
                    current = run[count - 1]
                    consumed = count - 1
                    fitted += 1
                    break
        if consumed:
            index += consumed
            continue
        output.append(motion)
        current = motion.end
        index += 1
    return output, fitted


def _fit_arc(points: Sequence[ToolPoint], tolerance: float) -> tuple[MotionKind, Point] | None:
    middle = points[len(points) // 2]
    center = _circle_center(points[0], middle, points[-1])
    if center is None:
        return None
    radius = math.hypot(points[0].x - center.x, points[0].y - center.y)
    if radius <= tolerance:
        return None
    turns = []
    angles = [math.atan2(point.y - center.y, point.x - center.x) for point in points]
    for first, second, third in zip(points, points[1:], points[2:]):
        cross = (second.x - first.x) * (third.y - second.y) - (second.y - first.y) * (third.x - second.x)
        if abs(cross) > TOLERANCE:
            turns.append(1 if cross > 0 else -1)
    if not turns or any(value != turns[0] for value in turns):
        return None
    direction = turns[0]
    sweep = 0.0
    previous = angles[0]
    for angle in angles[1:]:
        delta = angle - previous
        if direction > 0:
            while delta <= 0:
                delta += math.tau
        else:
            while delta >= 0:
                delta -= math.tau
        if abs(delta) > math.pi + 1e-6:
            return None
        sweep += delta
        previous = angle
    if abs(sweep) > math.pi + 1e-6:
        return None
    samples = list(points)
    samples.extend(
        ToolPoint((first.x + second.x) / 2, (first.y + second.y) / 2, first.z)
        for first, second in zip(points, points[1:])
    )
    if max(abs(math.hypot(point.x - center.x, point.y - center.y) - radius) for point in samples) > tolerance:
        return None
    return (MotionKind.ARC_CCW if direction > 0 else MotionKind.ARC_CW), center


def _circle_center(first: ToolPoint, middle: ToolPoint, last: ToolPoint) -> Point | None:
    determinant = 2.0 * (
        first.x * (middle.y - last.y)
        + middle.x * (last.y - first.y)
        + last.x * (first.y - middle.y)
    )
    if abs(determinant) <= TOLERANCE:
        return None
    first_sq = first.x * first.x + first.y * first.y
    middle_sq = middle.x * middle.x + middle.y * middle.y
    last_sq = last.x * last.x + last.y * last.y
    x = (
        first_sq * (middle.y - last.y)
        + middle_sq * (last.y - first.y)
        + last_sq * (first.y - middle.y)
    ) / determinant
    y = (
        first_sq * (last.x - middle.x)
        + middle_sq * (first.x - last.x)
        + last_sq * (middle.x - first.x)
    ) / determinant
    return Point(x, y)


def estimate_cycle_metrics(
    project: CamProject,
    operation_paths: Sequence[tuple[Operation, GeneratedToolpath]],
    settings: CycleOptimizationSettings | None = None,
) -> CycleMetrics:
    settings = settings or cycle_optimization_settings(project)
    xy_rate = settings.rapid_xy_ipm / 60.0 * unit_scale("in", project.units)
    z_rate = settings.rapid_z_ipm / 60.0 * unit_scale("in", project.units)
    cutting_seconds = rapid_seconds = dwell_seconds = 0.0
    cutting_distance = rapid_distance = 0.0
    blocks = motions_count = plunges = retracts = 0
    depth_links = low_links = reordered = compacted = fitted = 0
    current: ToolPoint | None = None
    last_station: int | None = None
    tool_changes = 0
    spindle_starts = 0
    for operation, generated in operation_paths:
        if last_station is not None and operation.tool_station != last_station:
            tool_changes += 1
        if operation.tool_station != last_station:
            spindle_starts += 1
        last_station = operation.tool_station
        for block in generated.blocks:
            blocks += 1
            depth_links += int(block.metadata.get("depth_links_saved", 0) or 0)
            low_links += int(bool(block.metadata.get("low_retract_from_previous")))
            reordered += int(bool(block.metadata.get("travel_reordered")))
            compacted += int(block.metadata.get("compacted_motions", 0) or 0)
            fitted += int(block.metadata.get("fitted_arcs", 0) or 0)
            for motion in block.motions:
                motions_count += 1
                if motion.kind is MotionKind.DWELL:
                    dwell_seconds += float(motion.dwell_seconds or 0.0)
                    current = motion.end
                    continue
                if current is None:
                    # Plate output is local X0/Y0. Assume the router begins at
                    # that home XY and already at the first block's clearance
                    # height so the comparison includes home-to-first travel
                    # without inventing an unknown machine Z position.
                    current = ToolPoint(0.0, 0.0, motion.end.z)
                distance = _motion_length(current, motion)
                if current.z >= -TOLERANCE and motion.end.z < -TOLERANCE:
                    plunges += 1
                if current.z < -TOLERANCE and motion.end.z >= -TOLERANCE:
                    retracts += 1
                if motion.kind is MotionKind.RAPID:
                    xy = math.hypot(motion.end.x - current.x, motion.end.y - current.y)
                    z = abs(motion.end.z - current.z)
                    rapid_distance += distance
                    rapid_seconds += max(xy / xy_rate if xy else 0.0, z / z_rate if z else 0.0)
                else:
                    cutting_distance += distance
                    if motion.feed and motion.feed > 0:
                        # Controller-neutral motion feeds are project units per
                        # second (UI recipes display their values per minute).
                        cutting_seconds += distance / motion.feed
                current = motion.end
    tool_seconds = tool_changes * settings.tool_change_seconds + spindle_starts * settings.spindle_start_seconds
    total = cutting_seconds + rapid_seconds + dwell_seconds + tool_seconds
    return CycleMetrics(
        total, cutting_seconds, rapid_seconds, dwell_seconds, tool_seconds,
        cutting_distance, rapid_distance, blocks, motions_count, plunges, retracts,
        tool_changes, depth_links, low_links, reordered, compacted, fitted,
    )


def _motion_length(start: ToolPoint, motion: Motion) -> float:
    if motion.kind in {MotionKind.ARC_CW, MotionKind.ARC_CCW} and motion.center is not None:
        radius = math.hypot(start.x - motion.center.x, start.y - motion.center.y)
        start_angle = math.atan2(start.y - motion.center.y, start.x - motion.center.x)
        end_angle = math.atan2(motion.end.y - motion.center.y, motion.end.x - motion.center.x)
        sweep = end_angle - start_angle
        if motion.kind is MotionKind.ARC_CW:
            while sweep >= 0:
                sweep -= math.tau
        else:
            while sweep <= 0:
                sweep += math.tau
        return math.hypot(abs(sweep) * radius, motion.end.z - start.z)
    return math.sqrt(
        (motion.end.x - start.x) ** 2
        + (motion.end.y - start.y) ** 2
        + (motion.end.z - start.z) ** 2
    )


def _block_entry(block: ToolpathBlock) -> ToolPoint:
    return block.motions[0].end if block.motions else ToolPoint(0.0, 0.0, 0.0)


def _block_exit(block: ToolpathBlock) -> ToolPoint:
    return block.motions[-1].end if block.motions else ToolPoint(0.0, 0.0, 0.0)


def _mark_block(block: ToolpathBlock, key: str, value) -> ToolpathBlock:
    return replace(block, metadata={**block.metadata, key: value})


def _xy_distance(first: ToolPoint, second: ToolPoint) -> float:
    return math.hypot(second.x - first.x, second.y - first.y)


def _point_distance(first: ToolPoint, second: ToolPoint) -> float:
    return math.sqrt((second.x - first.x) ** 2 + (second.y - first.y) ** 2 + (second.z - first.z) ** 2)


def _segment_intersects_polygon(start: ToolPoint, end: ToolPoint, polygon: Sequence[Point]) -> bool:
    if _point_in_polygon(Point(start.x, start.y), polygon) or _point_in_polygon(Point(end.x, end.y), polygon):
        return True
    return any(
        _segments_intersect(
            Point(start.x, start.y), Point(end.x, end.y), first, second,
        )
        for first, second in zip(polygon, (*polygon[1:], polygon[0]))
    )


def _point_in_polygon(point: Point, polygon: Sequence[Point]) -> bool:
    inside = False
    for first, second in zip(polygon, (*polygon[1:], polygon[0])):
        if (first.y > point.y) != (second.y > point.y):
            x = (second.x - first.x) * (point.y - first.y) / (second.y - first.y) + first.x
            if point.x < x:
                inside = not inside
    return inside


def _segments_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    def orientation(first: Point, second: Point, third: Point) -> float:
        return (second.x - first.x) * (third.y - first.y) - (second.y - first.y) * (third.x - first.x)

    values = orientation(a, b, c), orientation(a, b, d), orientation(c, d, a), orientation(c, d, b)
    return values[0] * values[1] <= TOLERANCE and values[2] * values[3] <= TOLERANCE


def project_with_cycle_optimization(project: CamProject, enabled: bool) -> CamProject:
    clone = CamProject.from_dict(copy.deepcopy(project.to_dict()))
    settings = dict(clone.metadata.get("cycle_optimization", {}))
    settings["enabled"] = enabled
    clone.metadata["cycle_optimization"] = settings
    return clone
