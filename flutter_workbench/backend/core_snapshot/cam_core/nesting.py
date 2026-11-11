from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Callable

from .layout import drawing_bounds, entity_bounds
from .model import Circle, Entity, Line, Point, Polyline, Rectangle, TextEntity
from .plates import Plate
from .transform import transform_entity


_TOLERANCE = 1e-9


def _report_progress(
    callback: Callable[[str, float], None] | None,
    message: str,
    fraction: float,
) -> None:
    if callback is not None:
        callback(message, max(0.0, min(1.0, float(fraction))))


@dataclass(frozen=True)
class NestResult:
    entities: list[Entity]
    plates: list[Plate]
    assignments: dict[str, int]
    rotations: dict[str, float] = field(default_factory=dict)
    nested_group_count: int = 0
    common_line_pairs: tuple[tuple[str, str], ...] = ()
    interlocked_group_count: int = 0
    hole_nested_group_count: int = 0


@dataclass
class _Item:
    key: str
    members: list[Entity]
    bounds: tuple[float, float, float, float]
    member_rotations: dict[str, float] = field(default_factory=dict)
    # Number of original rigid parts represented by this item.  A letter and
    # all of its holes still count as one; accepted silhouette mates increase
    # the count as they grow into a reusable cluster.
    source_item_count: int = 1

    @property
    def width(self) -> float:
        return self.bounds[2] - self.bounds[0]

    @property
    def height(self) -> float:
        return self.bounds[3] - self.bounds[1]


@dataclass
class _FreeRectangle:
    x: float
    y: float
    width: float
    height: float


@dataclass
class _ShapeFootprint:
    members: list[Entity]
    bounds: tuple[float, float, float, float]
    contours: list[list[Point]]
    void_contours: list[list[Point]]
    mask_rows: list[int] = field(default_factory=list)


@dataclass
class _ShapePlateState:
    placed: list[_ShapeFootprint] = field(default_factory=list)
    occupied_rows: dict[int, int] = field(default_factory=dict)
    used_bounds: tuple[float, float, float, float] | None = None


def nest_entities_to_plates(
    entities: list[Entity],
    plates: list[Plate],
    spacing: float = 0.25,
    *,
    allow_rotation: bool = False,
    automatic_compounds: bool = True,
    entity_ids: set[str] | None = None,
    plate_numbers: set[int] | None = None,
    allow_new_plates: bool = True,
    common_line_tool_diameter: float | None = None,
    placement_strategy: str = "rectangular",
    use_holes: bool = False,
    rotation_step_degrees: float | None = None,
    maximum_rotation_degrees: float = 90.0,
    search_resolution: float | None = None,
    progress_callback: Callable[[str, float], None] | None = None,
) -> NestResult:
    """Best-fit bounding-box nest with compound-part protection.

    Explicit groups are always rigid. When ``automatic_compounds`` is enabled,
    ungrouped closed contours contained by one outer contour travel with that
    outer contour. A partial selection expands to the complete rigid item.
    Quarter-turn rotation is optional and all generated plates remain on the
    positive-X plate sequence.
    """
    _report_progress(progress_callback, "Preparing nesting geometry…", 0.0)
    if spacing < 0:
        raise ValueError("Nest spacing cannot be negative.")
    placement_strategy = str(placement_strategy or "rectangular").strip().casefold()
    if placement_strategy not in {"rectangular", "smart_pairs", "shape_aware"}:
        raise ValueError("Nesting strategy must be rectangular, smart_pairs, or shape_aware.")
    if rotation_step_degrees is not None and rotation_step_degrees <= 0:
        raise ValueError("Rotation increment must be greater than zero.")
    if maximum_rotation_degrees < 0 or maximum_rotation_degrees > 180:
        raise ValueError("Maximum nesting rotation must be between 0 and 180 degrees.")
    if search_resolution is not None and search_resolution <= 0:
        raise ValueError("Shape-aware placement resolution must be greater than zero.")
    if common_line_tool_diameter is not None:
        if common_line_tool_diameter <= 0:
            raise ValueError("Common-line nesting requires a positive cutter diameter.")
        # Two outside-profile cutter centerlines coincide when the source
        # edges are separated by one complete cutter diameter.
        spacing = common_line_tool_diameter
    if not plates:
        raise ValueError("At least one plate is required for nesting.")

    output_plates = sorted(plates, key=lambda plate: plate.number)
    eligible_numbers = set(plate_numbers or (plate.number for plate in output_plates))
    eligible_plates = [plate for plate in output_plates if plate.number in eligible_numbers]
    if not eligible_plates:
        raise ValueError("Choose at least one plate for nesting.")

    nestable_entities = [
        entity for entity in entities
        if isinstance(entity, (Line, Circle, Rectangle, Polyline))
        and not bool(entity.metadata.get("annotation_hidden", False))
    ]
    all_items = _compound_items(nestable_entities, automatic_compounds)
    requested_ids = None if entity_ids is None else set(entity_ids)
    items = [
        item for item in all_items
        if requested_ids is None or any(member.id in requested_ids for member in item.members)
    ]
    if not items:
        raise ValueError("No nestable geometry is in the selected scope.")
    nested_item_count = len(items)
    if placement_strategy in {"smart_pairs", "shape_aware"} and common_line_tool_diameter is not None:
        raise ValueError("Common-line nesting currently uses the fast rectangular engine.")
    if placement_strategy in {"smart_pairs", "shape_aware"} and use_holes and not automatic_compounds:
        raise ValueError("Hole nesting requires automatic outer/hole compound recognition.")
    if placement_strategy == "shape_aware":
        return _nest_shape_aware(
            entities,
            output_plates,
            eligible_plates,
            items,
            nested_item_count,
            spacing=spacing,
            allow_rotation=allow_rotation,
            use_holes=use_holes,
            rotation_step_degrees=rotation_step_degrees,
            maximum_rotation_degrees=maximum_rotation_degrees,
            search_resolution=(search_resolution if search_resolution is not None else max(0.05, spacing / 2)),
            requested_ids=requested_ids,
            allow_new_plates=allow_new_plates,
            automatic_compounds=automatic_compounds,
            progress_callback=progress_callback,
        )
    interlocked_count = 0
    hole_nested_count = 0
    layout_angles = None
    if placement_strategy == "smart_pairs":
        layout_angles = _rotation_angles(
            allow_rotation,
            rotation_step_degrees,
            maximum_rotation_degrees,
        )
        pair_resolution = search_resolution if search_resolution is not None else max(0.05, spacing / 2)
        items, interlocked_count, hole_nested_count = _pair_complementary_items(
            items,
            spacing=spacing,
            angles=layout_angles,
            use_holes=use_holes,
            resolution=pair_resolution,
            progress_callback=progress_callback,
        )
    if common_line_tool_diameter is not None:
        items = _pair_common_line_items(items, common_line_tool_diameter)
    nested_ids = {member.id for item in items for member in item.members}

    # Large and awkward parts establish the free-space structure first.
    items.sort(key=lambda item: (-max(item.width, item.height), -(item.width * item.height), item.key))
    free_by_plate = {
        plate.number: [_FreeRectangle(
            plate.x + spacing,
            plate.y + spacing,
            max(0.0, plate.width - spacing),
            max(0.0, plate.height - spacing),
        )]
        for plate in eligible_plates
    }

    # Selected-only nesting treats untouched geometry as occupied stock rather
    # than silently placing a selected part over it.
    if requested_ids is not None:
        for entity in entities:
            if entity.id in nested_ids:
                continue
            if not isinstance(entity, (Line, Circle, Rectangle, Polyline)):
                continue
            bounds = entity_bounds(entity)
            if bounds is None:
                continue
            for plate in eligible_plates:
                if _bounds_inside_plate(bounds, plate):
                    occupied = _FreeRectangle(
                        bounds[0] - spacing,
                        bounds[1] - spacing,
                        (bounds[2] - bounds[0]) + spacing * 2,
                        (bounds[3] - bounds[1]) + spacing * 2,
                    )
                    free_by_plate[plate.number] = _subtract_from_free_rectangles(
                        free_by_plate[plate.number], occupied,
                    )
                    break

    transformed: dict[str, Entity] = {}
    assignments: dict[str, int] = {}
    rotations: dict[str, float] = {}
    layout_progress_start = 0.35 if placement_strategy == "smart_pairs" else 0.0
    layout_progress_end = 0.90 if placement_strategy == "smart_pairs" else 1.0

    for item_index, item in enumerate(items, start=1):
        layout_fraction = (item_index - 1) / max(1, len(items))
        _report_progress(
            progress_callback,
            f"Placing part {item_index} of {len(items)} with the fast rectangular engine…",
            layout_progress_start + layout_fraction * (layout_progress_end - layout_progress_start),
        )
        placement = _best_placement(
            item, eligible_plates, free_by_plate, spacing, allow_rotation,
            angles=layout_angles,
        )
        if placement is None and allow_new_plates:
            template = eligible_plates[-1]
            next_x = max(plate.x + plate.width for plate in output_plates)
            new_plate = Plate(
                max(plate.number for plate in output_plates) + 1,
                next_x,
                0.0,
                template.width,
                template.height,
            )
            output_plates.append(new_plate)
            eligible_plates.append(new_plate)
            free_by_plate[new_plate.number] = [_FreeRectangle(
                new_plate.x + spacing,
                new_plate.y + spacing,
                max(0.0, new_plate.width - spacing),
                max(0.0, new_plate.height - spacing),
            )]
            placement = _best_placement(
                item, [new_plate], free_by_plate, spacing, allow_rotation,
                angles=layout_angles,
            )
        if placement is None:
            orientations = " in either orientation" if allow_rotation else ""
            raise ValueError(
                f"Nested item {item.width:g} × {item.height:g} does not fit{orientations} "
                "on the selected plate scope."
            )

        plate, free_index, angle, rotated_members, rotated_bounds = placement
        target_x = free_by_plate[plate.number][free_index].x
        target_y = free_by_plate[plate.number][free_index].y
        delta_x = target_x - rotated_bounds[0]
        delta_y = target_y - rotated_bounds[1]
        for entity in rotated_members:
            moved = transform_entity(entity, delta_x=delta_x, delta_y=delta_y)
            transformed[entity.id] = moved
            assignments[entity.id] = plate.number
            rotations[entity.id] = _normalized_rotation(
                item.member_rotations.get(entity.id, 0.0) + angle,
            )

        placed_width = rotated_bounds[2] - rotated_bounds[0]
        placed_height = rotated_bounds[3] - rotated_bounds[1]
        occupied = _FreeRectangle(target_x, target_y, placed_width + spacing, placed_height + spacing)
        chosen = free_by_plate[plate.number].pop(free_index)
        free_by_plate[plate.number].extend(_split_free_rectangle(chosen, occupied))
        free_by_plate[plate.number] = _prune_free_rectangles(free_by_plate[plate.number])

    if placement_strategy == "smart_pairs" and requested_ids is None:
        compact_resolution = (
            search_resolution if search_resolution is not None
            else max(0.05, spacing / 2)
        )
        transformed = _compact_smart_layout(
            items,
            transformed,
            assignments,
            rotations,
            output_plates,
            spacing=spacing,
            # Hole insertions have already been locked into rigid clusters.
            # Plate compaction only needs exterior silhouettes; ignoring stud
            # holes here is both faster and conservatively collision-safe.
            use_holes=False,
            resolution=compact_resolution,
            angles=layout_angles or (0.0,),
            progress_callback=progress_callback,
        )

    final_entities = [transformed.get(entity.id, entity) for entity in entities]
    common_line_pairs = (
        _common_line_candidate_pairs(final_entities, assignments, common_line_tool_diameter)
        if common_line_tool_diameter is not None else ()
    )
    _report_progress(progress_callback, "Finalizing nested geometry…", 1.0)
    return NestResult(
        final_entities,
        sorted(output_plates, key=lambda plate: plate.number),
        assignments,
        rotations,
        nested_item_count,
        common_line_pairs,
        interlocked_count,
        hole_nested_count,
    )


def _compact_smart_layout(
    items: list[_Item],
    transformed: dict[str, Entity],
    assignments: dict[str, int],
    rotations: dict[str, float],
    plates: list[Plate],
    *,
    spacing: float,
    use_holes: bool,
    resolution: float,
    angles: tuple[float, ...],
    progress_callback: Callable[[str, float], None] | None,
) -> dict[str, Entity]:
    """Repack each seeded plate with real cluster silhouettes.

    The fast rectangle solver establishes a guaranteed valid plate assignment
    and orientation.  This bounded pass keeps those decisions, but translates
    whole clusters using raster rejection plus exact contour clearance.  A
    plate is accepted only when every cluster fits and its used envelope is no
    worse than the rectangular seed. A final bounded rescue then tries to
    evacuate up to four clusters from later plates into verified silhouette
    gaps on earlier plates.
    """
    output = dict(transformed)
    # A plate-wide 0.125-inch grid is excessive for large lettering. Keep at
    # most roughly 64 cells across the largest cluster dimension; exact
    # contour clearance still validates every accepted location.
    largest_cluster_dimension = max(
        (max(item.width, item.height) for item in items),
        default=resolution,
    )
    resolution = max(resolution, largest_cluster_dimension / 64.0)
    plate_by_number = {plate.number: plate for plate in plates}
    items_by_plate: dict[int, list[_Item]] = {}
    for item in items:
        station = next(
            (assignments[member.id] for member in item.members if member.id in assignments),
            None,
        )
        if station is None or station not in plate_by_number:
            continue
        members = [output.get(member.id, member) for member in item.members]
        bounds = drawing_bounds(members)
        if bounds is None:
            continue
        items_by_plate.setdefault(station, []).append(_Item(
            item.key,
            members,
            bounds,
            dict(item.member_rotations),
            item.source_item_count,
        ))

    total = sum(len(values) for values in items_by_plate.values())
    completed = 0
    for plate_number in sorted(items_by_plate):
        plate = plate_by_number[plate_number]
        plate_items = items_by_plate[plate_number]
        plate_start_completed = completed
        seed_bounds = None
        for item in plate_items:
            seed_bounds = _union_bounds(seed_bounds, item.bounds)
        plate_items.sort(key=lambda item: (
            -_bounds_area(item.bounds),
            -max(item.width, item.height),
            item.key,
        ))
        state = _ShapePlateState()
        staged: dict[str, Entity] = {}
        failed = False
        for item in plate_items:
            _report_progress(
                progress_callback,
                f"Compacting silhouette cluster {completed + 1} of {total}…",
                0.90 + 0.085 * completed / max(1, total),
            )
            footprint = _shape_footprint(
                item.members, use_holes, resolution, raster_point_limit=128,
            )
            placement = _best_compaction_position(
                footprint,
                plate,
                state,
                spacing=spacing,
                resolution=resolution,
                use_holes=use_holes,
            )
            if placement is None:
                failed = True
                break
            target_x, target_y = placement
            delta_x = target_x - footprint.bounds[0]
            delta_y = target_y - footprint.bounds[1]
            moved_members = [
                transform_entity(entity, delta_x=delta_x, delta_y=delta_y)
                for entity in item.members
            ]
            moved = _translated_footprint(footprint, delta_x, delta_y)
            for entity in moved_members:
                staged[entity.id] = entity
            state.placed.append(moved)
            state.used_bounds = _union_bounds(state.used_bounds, moved.bounds)
            _add_absolute_mask(state, moved, plate, resolution)
            completed += 1

        if failed or state.used_bounds is None or seed_bounds is None:
            # The rectangle seed remains the safe fallback for this plate.
            completed = plate_start_completed + len(plate_items)
            continue
        seed_score = _plate_envelope_score(seed_bounds, plate)
        compact_score = _plate_envelope_score(state.used_bounds, plate)
        if compact_score <= seed_score:
            output.update(staged)

    output = _evacuate_sparse_later_plates(
        items,
        output,
        assignments,
        rotations,
        plates,
        spacing=spacing,
        use_holes=use_holes,
        resolution=resolution,
        angles=angles,
        progress_callback=progress_callback,
    )

    _report_progress(
        progress_callback,
        "Silhouette compaction complete; finalizing nested geometry…",
        0.99,
    )
    return output


def _evacuate_sparse_later_plates(
    items: list[_Item],
    transformed: dict[str, Entity],
    assignments: dict[str, int],
    rotations: dict[str, float],
    plates: list[Plate],
    *,
    spacing: float,
    use_holes: bool,
    resolution: float,
    angles: tuple[float, ...],
    progress_callback: Callable[[str, float], None] | None,
) -> dict[str, Entity]:
    """Try to empty lightly used later plates into real earlier-plate gaps.

    This is intentionally not another global nesting search. Only plates with
    four or fewer rigid clusters are considered, every source plate is moved
    atomically, and every accepted position passes the same exact contour and
    spacing check as normal silhouette compaction.
    """
    output = dict(transformed)
    plate_by_number = {plate.number: plate for plate in plates}
    original_areas = {
        item.key: _bounds_area(item.bounds)
        for item in items
    }
    items_by_plate: dict[int, list[_Item]] = {}
    for item in items:
        plate_number = next(
            (assignments[member.id] for member in item.members if member.id in assignments),
            None,
        )
        if plate_number is None or plate_number not in plate_by_number:
            continue
        members = [output.get(member.id, member) for member in item.members]
        bounds = drawing_bounds(members)
        if bounds is None:
            continue
        items_by_plate.setdefault(plate_number, []).append(_Item(
            item.key,
            members,
            bounds,
            dict(item.member_rotations),
            item.source_item_count,
        ))

    states: dict[int, _ShapePlateState] = {
        number: _ShapePlateState() for number in plate_by_number
    }
    for plate_number, plate_items in items_by_plate.items():
        plate = plate_by_number[plate_number]
        state = states[plate_number]
        for item in plate_items:
            footprint = _shape_footprint(
                item.members, use_holes, resolution, raster_point_limit=128,
            )
            state.placed.append(footprint)
            state.used_bounds = _union_bounds(state.used_bounds, footprint.bounds)
            _add_absolute_mask(state, footprint, plate, resolution)

    for source_number in sorted(items_by_plate, reverse=True):
        source_items = items_by_plate.get(source_number, [])
        target_numbers = sorted(
            number for number in plate_by_number if number < source_number
        )
        if not target_numbers or not 1 <= len(source_items) <= 4:
            continue
        _report_progress(
            progress_callback,
            f"Checking whether Plate {source_number} can be emptied into earlier silhouette gaps…",
            0.987,
        )
        trial_states = {
            number: _ShapePlateState(
                list(state.placed), dict(state.occupied_rows), state.used_bounds,
            )
            for number, state in states.items()
        }
        staged_entities: dict[str, Entity] = {}
        staged_assignments: dict[str, int] = {}
        staged_rotations: dict[str, float] = {}
        success = True
        accepted_count = 0
        # Hardest clusters go first so a small easy part cannot consume the
        # only opening needed by a larger one.
        for item in sorted(
            source_items,
            key=lambda candidate: (-_bounds_area(candidate.bounds), candidate.key),
        ):
            first_member = item.members[0]
            current_seed_angle = _normalized_rotation(
                rotations.get(first_member.id, 0.0)
                - item.member_rotations.get(first_member.id, 0.0)
            )
            rescue_deltas: list[float] = []
            for target_angle in (current_seed_angle, *angles):
                delta = _normalized_rotation(target_angle - current_seed_angle)
                if not any(math.isclose(delta, value, abs_tol=_TOLERANCE) for value in rescue_deltas):
                    rescue_deltas.append(delta)
                if len(rescue_deltas) >= 12:
                    break

            accepted = None
            for target_number in target_numbers:
                plate = plate_by_number[target_number]
                state = trial_states[target_number]
                for delta_angle in rescue_deltas:
                    rotated_members, rotated_bounds = _rotated_item(item, delta_angle)
                    oriented = _shape_footprint(
                        rotated_members, use_holes, resolution, raster_point_limit=128,
                    )
                    placement = _best_compaction_position(
                        oriented,
                        plate,
                        state,
                        spacing=spacing,
                        resolution=resolution,
                        use_holes=use_holes,
                    )
                    if placement is None:
                        continue
                    target_x, target_y = placement
                    delta_x = target_x - rotated_bounds[0]
                    delta_y = target_y - rotated_bounds[1]
                    moved_members = [
                        transform_entity(entity, delta_x=delta_x, delta_y=delta_y)
                        for entity in rotated_members
                    ]
                    moved = _translated_footprint(oriented, delta_x, delta_y)
                    accepted = (
                        target_number, delta_angle, moved_members, moved,
                    )
                    break
                if accepted is not None:
                    break
            if accepted is None:
                success = False
                break
            target_number, delta_angle, moved_members, moved = accepted
            target_state = trial_states[target_number]
            target_state.placed.append(moved)
            target_state.used_bounds = _union_bounds(target_state.used_bounds, moved.bounds)
            _add_absolute_mask(
                target_state, moved, plate_by_number[target_number], resolution,
            )
            for entity in moved_members:
                staged_entities[entity.id] = entity
                staged_assignments[entity.id] = target_number
                staged_rotations[entity.id] = _normalized_rotation(
                    rotations.get(entity.id, 0.0) + delta_angle,
                )
            accepted_count += 1

        if not success:
            merged_repack = None
            merged_target = None
            for target_number in target_numbers:
                merged_items = [
                    *items_by_plate.get(target_number, []),
                    *source_items,
                ]
                if len(merged_items) > 24:
                    continue
                _report_progress(
                    progress_callback,
                    (
                        f"Trying a bounded last-mile repack of Plate {target_number} "
                        f"with {len(source_items)} Plate {source_number} leftover(s)…"
                    ),
                    0.988,
                )
                merged_repack = _repack_clusters_on_plate(
                    merged_items,
                    plate_by_number[target_number],
                    rotations,
                    spacing=spacing,
                    use_holes=use_holes,
                    resolution=resolution,
                    angles=angles,
                    priority_areas=original_areas,
                )
                if merged_repack is not None:
                    merged_target = target_number
                    break
            if merged_repack is not None and merged_target is not None:
                repacked_entities, repacked_rotations, repacked_state, repacked_items = merged_repack
                output.update(repacked_entities)
                rotations.update(repacked_rotations)
                for entity_id in repacked_entities:
                    assignments[entity_id] = merged_target
                states[merged_target] = repacked_state
                states[source_number] = _ShapePlateState()
                items_by_plate[merged_target] = repacked_items
                items_by_plate[source_number] = []
                _report_progress(
                    progress_callback,
                    (
                        f"Plate {source_number} was emptied by a bounded last-mile "
                        f"repack of Plate {merged_target}."
                    ),
                    0.989,
                )
                continue
            _report_progress(
                progress_callback,
                (
                    f"Plate {source_number} rescue found room for {accepted_count} of "
                    f"{len(source_items)} cluster(s); keeping the safe two-plate layout."
                ),
                0.989,
            )
            continue
        output.update(staged_entities)
        assignments.update(staged_assignments)
        rotations.update(staged_rotations)
        states = trial_states
        states[source_number] = _ShapePlateState()
        items_by_plate[source_number] = []
        for item in source_items:
            target_number = staged_assignments[item.members[0].id]
            moved_members = [output[member.id] for member in item.members]
            moved_bounds = drawing_bounds(moved_members)
            assert moved_bounds is not None
            items_by_plate.setdefault(target_number, []).append(_Item(
                item.key,
                moved_members,
                moved_bounds,
                dict(item.member_rotations),
                item.source_item_count,
            ))
        _report_progress(
            progress_callback,
            f"Plate {source_number} was emptied into earlier verified silhouette gaps.",
            0.989,
        )
    return output


def _repack_clusters_on_plate(
    items: list[_Item],
    plate: Plate,
    rotations: dict[str, float],
    *,
    spacing: float,
    use_holes: bool,
    resolution: float,
    angles: tuple[float, ...],
    priority_areas: dict[str, float] | None = None,
) -> tuple[
    dict[str, Entity],
    dict[str, float],
    _ShapePlateState,
    list[_Item],
] | None:
    """Bottom-left silhouette repack used only for sparse-plate elimination."""
    state = _ShapePlateState()
    staged_entities: dict[str, Entity] = {}
    staged_rotations: dict[str, float] = {}
    staged_items: list[_Item] = []
    ordered = sorted(
        items,
        key=lambda item: (
            -(priority_areas or {}).get(item.key, _bounds_area(item.bounds)),
            item.key,
        ),
    )
    for item in ordered:
        first_member = item.members[0]
        current_seed_angle = _normalized_rotation(
            rotations.get(first_member.id, 0.0)
            - item.member_rotations.get(first_member.id, 0.0)
        )
        target_angles: list[float] = []
        # The broad, operator-selected angle search has already run during the
        # primary nest.  This rescue is deliberately cardinal: a greedy 45°
        # placement can look compact locally while blocking the final letter,
        # whereas 0/90/180/270 keeps the remaining free-space channels useful.
        for target_angle in (0.0, 180.0, 90.0, -90.0):
            normalized = _normalized_rotation(target_angle)
            if not any(
                math.isclose(normalized, value, abs_tol=_TOLERANCE)
                for value in target_angles
            ):
                target_angles.append(normalized)

        best = None
        for target_angle in target_angles:
            delta_angle = _normalized_rotation(target_angle - current_seed_angle)
            rotated_members, rotated_bounds = _rotated_item(item, delta_angle)
            oriented = _shape_footprint(
                rotated_members, use_holes, resolution, raster_point_limit=128,
            )
            placement = _best_compaction_position(
                oriented,
                plate,
                state,
                spacing=spacing,
                resolution=resolution,
                use_holes=use_holes,
            )
            if placement is None:
                continue
            target_x, target_y = placement
            delta_x = target_x - rotated_bounds[0]
            delta_y = target_y - rotated_bounds[1]
            moved = _translated_footprint(oriented, delta_x, delta_y)
            used_bounds = _union_bounds(state.used_bounds, moved.bounds)
            assert used_bounds is not None
            score = (
                *_plate_envelope_score(used_bounds, plate),
                abs(delta_angle),
            )
            if best is None or score < best[0]:
                moved_members = [
                    transform_entity(entity, delta_x=delta_x, delta_y=delta_y)
                    for entity in rotated_members
                ]
                best = (
                    score, delta_angle, moved_members, moved, used_bounds,
                )
        if best is None:
            return None
        _score, delta_angle, moved_members, moved, used_bounds = best
        state.placed.append(moved)
        state.used_bounds = used_bounds
        _add_absolute_mask(state, moved, plate, resolution)
        moved_bounds = drawing_bounds(moved_members)
        assert moved_bounds is not None
        staged_items.append(_Item(
            item.key,
            moved_members,
            moved_bounds,
            dict(item.member_rotations),
            item.source_item_count,
        ))
        for entity in moved_members:
            staged_entities[entity.id] = entity
            staged_rotations[entity.id] = _normalized_rotation(
                rotations.get(entity.id, 0.0) + delta_angle,
            )
    return staged_entities, staged_rotations, state, staged_items


def _plate_envelope_score(bounds, plate: Plate) -> tuple[float, float, float]:
    width = max(0.0, bounds[2] - plate.x)
    height = max(0.0, bounds[3] - plate.y)
    return width * height, height, width


def _best_compaction_position(
    footprint: _ShapeFootprint,
    plate: Plate,
    state: _ShapePlateState,
    *,
    spacing: float,
    resolution: float,
    use_holes: bool,
) -> tuple[float, float] | None:
    width = footprint.bounds[2] - footprint.bounds[0]
    height = footprint.bounds[3] - footprint.bounds[1]
    available_width = plate.width - spacing * 2 - width
    available_height = plate.height - spacing * 2 - height
    if available_width < -_TOLERANCE or available_height < -_TOLERANCE:
        return None
    max_grid_x = max(0, int(math.floor(max(0.0, available_width) / resolution + _TOLERANCE)))
    max_grid_y = max(0, int(math.floor(max(0.0, available_height) / resolution + _TOLERANCE)))
    special = _compaction_anchor_positions(
        footprint, state, plate, spacing, resolution, max_grid_x, max_grid_y,
    )
    if use_holes:
        special.extend(_hole_anchor_grid_positions(
            footprint, state, plate, spacing, resolution, max_grid_x, max_grid_y,
        ))
    seen: set[tuple[int, int]] = set()
    candidates = []
    examined = 0
    max_positions = min(100_000, (max_grid_x + 1) * (max_grid_y + 1))
    iterators = [
        iter(special),
        (
            (grid_x, grid_y)
            for grid_y in range(max_grid_y + 1)
            for grid_x in range(max_grid_x + 1)
        ),
    ]
    for iterator in iterators:
        for grid_x, grid_y in iterator:
            if (grid_x, grid_y) in seen:
                continue
            seen.add((grid_x, grid_y))
            examined += 1
            if examined > max_positions:
                break
            target_x = plate.x + spacing + grid_x * resolution
            target_y = plate.y + spacing + grid_y * resolution
            mask_grid_x = int(round((target_x - plate.x) / resolution))
            mask_grid_y = int(round((target_y - plate.y) / resolution))
            if _mask_collides(
                state, footprint.mask_rows, mask_grid_x, mask_grid_y,
            ):
                continue
            candidate = _translated_footprint(
                footprint,
                target_x - footprint.bounds[0],
                target_y - footprint.bounds[1],
            )
            if not all(_footprints_clear(candidate, other, spacing) for other in state.placed):
                continue
            used = _union_bounds(state.used_bounds, candidate.bounds)
            assert used is not None
            overlap = sum(
                _bounds_overlap_area(candidate.bounds, other.bounds)
                for other in state.placed
            )
            inside_void = use_holes and any(
                _footprint_inside_void(candidate, other) for other in state.placed
            )
            score = (
                *_plate_envelope_score(used, plate),
                0 if inside_void else 1,
                -overlap,
                grid_y,
                grid_x,
            )
            candidates.append((score, target_x, target_y))
            if len(candidates) >= 8:
                break
        if examined > max_positions or len(candidates) >= 8:
            break
    if not candidates:
        return None
    _score, target_x, target_y = min(candidates, key=lambda value: value[0])
    return target_x, target_y


def _compaction_anchor_positions(
    footprint: _ShapeFootprint,
    state: _ShapePlateState,
    plate: Plate,
    spacing: float,
    resolution: float,
    max_grid_x: int,
    max_grid_y: int,
) -> list[tuple[int, int]]:
    width = footprint.bounds[2] - footprint.bounds[0]
    height = footprint.bounds[3] - footprint.bounds[1]
    targets = [(plate.x + spacing, plate.y + spacing)]
    for other in state.placed:
        targets.extend((
            (other.bounds[2] + spacing, plate.y + spacing),
            (plate.x + spacing, other.bounds[3] + spacing),
            (other.bounds[2] + spacing, other.bounds[1]),
            (other.bounds[0], other.bounds[3] + spacing),
            (other.bounds[0] + (other.bounds[2] - other.bounds[0] - width) / 2, other.bounds[3] + spacing),
            (other.bounds[2] + spacing, other.bounds[1] + (other.bounds[3] - other.bounds[1] - height) / 2),
        ))
    result = []
    for target_x, target_y in targets:
        grid_x = int(round((target_x - plate.x - spacing) / resolution))
        grid_y = int(round((target_y - plate.y - spacing) / resolution))
        if 0 <= grid_x <= max_grid_x and 0 <= grid_y <= max_grid_y:
            result.append((grid_x, grid_y))
    return result


def _pair_complementary_items(
    items: list[_Item],
    *,
    spacing: float,
    angles: tuple[float, ...],
    use_holes: bool,
    resolution: float,
    progress_callback: Callable[[str, float], None] | None,
) -> tuple[list[_Item], int, int]:
    """Build a few useful silhouette clusters before fast plate packing.

    Low-resolution occupancy fingerprints rank likely mates.  Only the best
    few receive the raster-assisted, contour-verified local search.  An
    accepted mate remains real geometry and can be matched again, allowing
    E/E, T/T, and cavity/filler groups to grow beyond a single pair without an
    all-pairs exhaustive search.
    """
    if len(items) < 2:
        return items, 0, 0
    remaining = list(items)
    paired: list[_Item] = []
    pair_count = 0
    hole_count = 0
    initial_count = len(remaining)
    progress_stride = max(1, initial_count // 25)
    signature_cache: dict[str, dict[str, object]] = {}
    signature_template_cache: dict[object, dict[str, object]] = {}
    pair_pattern_cache: dict[tuple[object, ...], tuple[object, ...]] = {}
    quick_rank_cache: dict[tuple[object, object, float], object] = {}
    maximum_cluster_parts = 4

    def signature(item: _Item) -> dict[str, object]:
        cached = signature_cache.get(item.key)
        if cached is None:
            geometry_key = _translation_invariant_geometry_key(item)
            cached = signature_template_cache.get(geometry_key)
            if cached is None:
                cached = _pair_signature(
                    item, use_holes, resolution, geometry_key=geometry_key,
                )
                signature_template_cache[geometry_key] = cached
            signature_cache[item.key] = cached
        return cached

    # Classification is intentionally low resolution. It answers "block,
    # stick, obvious repeated cavity, or possible complement?" before any
    # detailed contour work is allowed to run.
    for index, item in enumerate(remaining, start=1):
        if index == 1 or index == initial_count or index % progress_stride == 0:
            _report_progress(
                progress_callback,
                f"Sorting silhouette {index} of {initial_count}…",
                0.02 + 0.08 * index / max(1, initial_count),
            )
        signature(item)
    shape_counts: dict[object, int] = {}
    for item in remaining:
        shape_key = signature(item)["shape_key"]
        shape_counts[shape_key] = shape_counts.get(shape_key, 0) + 1
    category_order = {"cavity": 0, "filler": 1, "block": 2, "stick": 3}
    remaining.sort(key=lambda item: (
        0 if shape_counts[signature(item)["shape_key"]] > 1 and signature(item)["category"] in {"cavity", "filler"} else 1,
        category_order[str(signature(item)["category"])],
        -_bounds_area(item.bounds),
        item.key,
    ))
    category_counts = {
        category: sum(1 for item in remaining if signature(item)["category"] == category)
        for category in ("cavity", "filler", "block", "stick")
    }
    _report_progress(
        progress_callback,
        (
            f"Sorted {category_counts['cavity']} cavity, {category_counts['filler']} filler, "
            f"{category_counts['block']} block, and {category_counts['stick']} stick shape(s)."
        ),
        0.10,
    )

    while remaining:
        first = remaining.pop(0)
        completed = initial_count - len(remaining) - 1
        report_this_shape = completed == 0 or completed % progress_stride == 0
        if report_this_shape:
            _report_progress(
                progress_callback,
                f"Pairing sorted shape {min(initial_count, completed + 1)} of {initial_count}…",
                0.10 + 0.20 * completed / max(1, initial_count),
            )
        ranked = []
        first_signature = signature(first)
        for index, second in enumerate(remaining):
            second_signature = signature(second)
            rank_key = (
                first_signature["geometry_key"],
                second_signature["geometry_key"],
                round(spacing, 9),
            )
            if rank_key not in quick_rank_cache:
                quick_rank_cache[rank_key] = _quick_pair_rank(
                    first_signature, second_signature, spacing,
                )
            rank = quick_rank_cache[rank_key]
            if rank is not None:
                ranked.append((rank, index, second, second_signature["shape_key"]))
        ranked.sort(key=lambda value: (value[0], value[2].key))

        # Repeated letters commonly produce dozens of identical candidates.
        # One representative of each silhouette is enough, but retaining the
        # best two different silhouettes lets a C/K/A-style cluster grow
        # without returning to an O(n^2) exact search.
        unique_ranked = []
        seen_shapes = set()
        for candidate in ranked:
            shape_key = candidate[3]
            if shape_key in seen_shapes:
                continue
            seen_shapes.add(shape_key)
            unique_ranked.append(candidate)
            candidate_limit = 2
            if len(unique_ranked) >= candidate_limit:
                break

        best = None
        for candidate_number, (_rank, index, second, _shape_key) in enumerate(unique_ranked, start=1):
            if report_this_shape:
                _report_progress(
                    progress_callback,
                    f"Testing recommended mate {candidate_number}/{len(unique_ranked)}…",
                    0.10 + 0.20 * (completed + candidate_number / max(1, len(unique_ranked))) / max(1, initial_count),
                )
            pair_kind = int(_rank[0])
            # When hole use is enabled, counters and other genuine internal
            # voids are usable clearance for every shortlisted relationship,
            # not only for the special case where one whole part fits inside
            # another.  The exact contour check still prevents material from
            # crossing an island wall.
            search_use_holes = bool(use_holes)
            angle_limit = 4 if pair_kind == 0 else 6
            if first.source_item_count > 1:
                angle_limit = min(angle_limit, 4)
            preferred_angle = float(_rank[-1])
            candidate_angles = _angles_with_preferred_first(
                angles[:angle_limit], preferred_angle,
            )
            first_geometry_key = first_signature["geometry_key"]
            second_geometry_key = signature(second)["geometry_key"]
            pattern_key = (
                first_geometry_key,
                second_geometry_key,
                round(spacing, 9),
                tuple(round(angle, 9) for angle in candidate_angles),
                search_use_holes,
            )
            pattern = pair_pattern_cache.get(pattern_key)
            if pattern is not None:
                result = _apply_pair_pattern(first, second, pattern)
            else:
                result = _best_local_pair(
                    first,
                    second,
                    spacing=spacing,
                    angles=candidate_angles,
                    use_holes=search_use_holes,
                    resolution=resolution,
                    max_positions=(
                        128 if first.source_item_count > 1
                        else 384 if pair_kind == 0
                        else 160
                    ),
                )
                if result is not None and first_geometry_key == second_geometry_key:
                    pair_pattern_cache[pattern_key] = _pair_pattern(first, result)
            if result is not None and (best is None or result[0] < best[0]):
                best = (*result, index, second)
                score = result[0]
                if score[0] == 0 or score[1] <= 0.75:
                    break

        if best is None:
            paired.append(first)
            continue
        _score, moved_second, combined_bounds, inside_hole, pair_angle, second_index, second = best
        remaining.pop(second_index)
        member_rotations = dict(first.member_rotations)
        member_rotations.update({
            entity.id: _normalized_rotation(second.member_rotations.get(entity.id, 0.0) + pair_angle)
            for entity in second.members
        })
        cluster = _Item(
            f"smart-cluster:{first.key}:{second.key}",
            [*first.members, *moved_second],
            combined_bounds,
            member_rotations,
            first.source_item_count + second.source_item_count,
        )
        pair_count += 1
        if inside_hole:
            hole_count += 1
        if cluster.source_item_count < maximum_cluster_parts and remaining:
            # Re-test the actual compound silhouette immediately.  This keeps
            # a successful E/E or K/C pair available for one or two more
            # high-value mates while still enforcing a hard cluster-size cap.
            remaining.insert(0, cluster)
        else:
            paired.append(cluster)

    _report_progress(
        progress_callback,
        (
            f"Fingerprint scan formed {pair_count} silhouette join(s) into "
            f"{len(paired)} rigid cluster(s); starting fast plate layout…"
        ),
        0.32,
    )
    return paired, pair_count, hole_count


def _pair_signature(
    item: _Item,
    use_holes: bool,
    resolution: float,
    *,
    geometry_key: object | None = None,
) -> dict[str, object]:
    # Signature rasterization is deliberately capped at roughly 32 cells on
    # the longest axis. It is a classifier, not the final clearance check.
    signature_resolution = max(resolution, max(item.width, item.height) / 32.0)
    footprint = _shape_footprint(
        item.members,
        use_holes,
        signature_resolution,
        raster_point_limit=64,
    )
    # Stud/drill holes belong to the rigid part but should not make two copies
    # of the same letter look like different exterior silhouettes.  Keep a
    # filled exterior for identity and fast complement ranking; actual hole
    # fit is ranked from void bounds and exact candidates still retain every
    # true counter when the operator enables hole nesting.
    exterior = (
        _shape_footprint(
            item.members,
            False,
            signature_resolution,
            raster_point_limit=64,
        )
        if use_holes and footprint.void_contours
        else footprint
    )
    bbox_area = max(_TOLERANCE, item.width * item.height)
    material_area = sum(bits.bit_count() for bits in exterior.mask_rows) * signature_resolution ** 2
    columns = max(1, int(math.ceil(item.width / signature_resolution)))
    external_void_fraction, largest_pocket_fraction = _external_void_features(
        exterior.mask_rows,
        columns,
    )
    aspect = min(item.width, item.height) / max(_TOLERANCE, max(item.width, item.height))
    fill_ratio = min(1.0, material_area / bbox_area)
    concave = any(
        isinstance(entity, Polyline) and entity.closed and _polyline_is_concave(entity.points)
        for entity in item.members
    )
    if aspect <= 0.28:
        category = "stick"
    elif concave and largest_pocket_fraction >= 0.15:
        category = "filler" if fill_ratio < 0.38 and largest_pocket_fraction >= 0.25 else "cavity"
    else:
        category = "block"
    return {
        "width": item.width,
        "height": item.height,
        "area": bbox_area,
        "fill_ratio": fill_ratio,
        "concave": concave,
        "category": category,
        "external_void_fraction": external_void_fraction,
        "largest_pocket_fraction": largest_pocket_fraction,
        "mask_rows": tuple(exterior.mask_rows),
        "mask_columns": columns,
        "mask_resolution": signature_resolution,
        "geometry_key": geometry_key if geometry_key is not None else _translation_invariant_geometry_key(item),
        "void_bounds": tuple(_path_bounds(path) for path in footprint.void_contours),
        "shape_key": (
            round(item.width / signature_resolution),
            round(item.height / signature_resolution),
            tuple(exterior.mask_rows),
            tuple(
                (
                    round((right - left) / signature_resolution),
                    round((top - bottom) / signature_resolution),
                )
                for left, bottom, right, top in (_path_bounds(path) for path in footprint.void_contours)
            ),
        ),
    }


def _quick_pair_rank(first: dict[str, object], second: dict[str, object], spacing: float):
    first_width = float(first["width"])
    first_height = float(first["height"])
    second_width = float(second["width"])
    second_height = float(second["height"])
    first_fill = float(first["fill_ratio"])
    second_fill = float(second["fill_ratio"])
    first_category = str(first["category"])
    second_category = str(second["category"])
    same_shape = first["shape_key"] == second["shape_key"]
    void_fit = _signature_hole_fit(first, second, spacing) or _signature_hole_fit(second, first, spacing)
    if not void_fit and same_shape and not (
        bool(first["concave"]) or bool(second["concave"])
    ):
        # Repeated circles and rectangles have no complementary recess. Their
        # optimal relationship is ordinary edge-to-edge packing, so do not
        # spend exact-search time trying to invent an interlock.
        return None
    if not void_fit and (first_category == "stick" or second_category == "stick"):
        return None
    # Dense rectangular parts cannot interlock; sending them to the local
    # solver only recreates the original performance problem.
    if not void_fit and first_fill > 0.90 and second_fill > 0.90:
        return None
    area_ratio = max(float(first["area"]), float(second["area"])) / max(
        _TOLERANCE, min(float(first["area"]), float(second["area"])),
    )
    if not void_fit and area_ratio > 6.0:
        return None
    complement = _fingerprint_complement_rank(first, second)
    if not void_fit and not same_shape and complement is None:
        return None
    if (
        not void_fit
        and not same_shape
        and first_category not in {"cavity", "filler"}
        and second_category not in {"cavity", "filler"}
        and (complement is None or complement[0] >= 0.94)
    ):
        return None
    dimension_mismatch = min(
        abs(first_width - second_width) + abs(first_height - second_height),
        abs(first_width - second_height) + abs(first_height - second_width),
    )
    pair_kind = 0 if same_shape else 1 if void_fit else 2
    complement_ratio = complement[0] if complement is not None else 1.0
    complement_contact = complement[1] if complement is not None else 0
    preferred_angle = complement[2] if complement is not None else 0.0
    pocket_balance = abs(
        float(first["largest_pocket_fraction"])
        - float(second["fill_ratio"])
    )
    return (
        pair_kind,
        complement_ratio,
        complement_contact,
        area_ratio,
        pocket_balance,
        dimension_mismatch,
        first_fill + second_fill,
        preferred_angle,
    )


def _angles_with_preferred_first(
    angles: tuple[float, ...], preferred: float,
) -> tuple[float, ...]:
    """Move a fingerprint-predicted angle to the front without expanding scope."""
    preferred_normalized = round(preferred % 360.0, 9)
    matching = [
        angle for angle in angles
        if round(angle % 360.0, 9) == preferred_normalized
    ]
    if not matching:
        return tuple(angles)
    chosen = matching[0]
    return (chosen, *(angle for angle in angles if angle != chosen))


def _fingerprint_complement_rank(
    first: dict[str, object], second: dict[str, object],
) -> tuple[float, int, float] | None:
    """Estimate whether two occupancy thumbnails can share an envelope.

    The calculation uses at most a 20-cell longest axis and bitwise row
    overlap.  It is deliberately approximate: it only ranks candidates; the
    selected candidates still pass full contour-clearance validation.
    """
    longest = max(
        float(first["width"]), float(first["height"]),
        float(second["width"]), float(second["height"]),
        _TOLERANCE,
    )
    cell = longest / 20.0
    first_rows, first_width = _resample_signature_mask(first, cell)
    second_rows, second_width = _resample_signature_mask(second, cell)
    first_height = len(first_rows)
    if not first_rows or not second_rows:
        return None
    baseline = min(
        (first_width + second_width) * max(first_height, len(second_rows)),
        max(first_width, second_width) * (first_height + len(second_rows)),
    )
    if baseline <= 0:
        return None
    best: tuple[float, int, float] | None = None
    for angle in (0.0, 180.0, 90.0, -90.0):
        moving_rows, moving_width = _rotate_mask_rows(
            second_rows, second_width, angle,
        )
        moving_height = len(moving_rows)
        for delta_y in range(-moving_height + 1, first_height):
            vertical_overlap = min(first_height, delta_y + moving_height) - max(0, delta_y)
            if vertical_overlap <= 0:
                continue
            for delta_x in range(-moving_width + 1, first_width):
                horizontal_overlap = min(first_width, delta_x + moving_width) - max(0, delta_x)
                if horizontal_overlap <= 0:
                    continue
                if _mask_rows_overlap(
                    first_rows, moving_rows, delta_x, delta_y,
                ):
                    continue
                envelope_width = max(first_width, delta_x + moving_width) - min(0, delta_x)
                envelope_height = max(first_height, delta_y + moving_height) - min(0, delta_y)
                ratio = envelope_width * envelope_height / baseline
                if ratio >= 0.985:
                    continue
                contact = _mask_contact_count(
                    first_rows, moving_rows, delta_x, delta_y,
                )
                candidate = (ratio, -contact, angle)
                if best is None or candidate < best:
                    best = candidate
    return best


def _resample_signature_mask(
    signature: dict[str, object], cell: float,
) -> tuple[tuple[int, ...], int]:
    source_rows = tuple(int(value) for value in signature["mask_rows"])
    source_columns = int(signature["mask_columns"])
    source_cell = float(signature["mask_resolution"])
    width = max(1, int(math.ceil(float(signature["width"]) / cell)))
    height = max(1, int(math.ceil(float(signature["height"]) / cell)))
    result = []
    for row in range(height):
        source_y = min(
            len(source_rows) - 1,
            max(0, int(((row + 0.5) * cell) / source_cell)),
        )
        bits = 0
        for column in range(width):
            source_x = min(
                source_columns - 1,
                max(0, int(((column + 0.5) * cell) / source_cell)),
            )
            if source_rows[source_y] & (1 << source_x):
                bits |= 1 << column
        result.append(bits)
    if not any(result):
        result = [(1 << width) - 1 for _ in range(height)]
    return tuple(result), width


def _rotate_mask_rows(
    rows: tuple[int, ...], width: int, angle: float,
) -> tuple[tuple[int, ...], int]:
    normalized = round(angle % 360.0, 9)
    occupied = {
        (column, row)
        for row, bits in enumerate(rows)
        for column in range(width)
        if bits & (1 << column)
    }
    height = len(rows)
    if normalized == 0.0:
        return rows, width
    if normalized == 180.0:
        transformed = {(width - 1 - x, height - 1 - y) for x, y in occupied}
        new_width, new_height = width, height
    elif normalized == 90.0:
        transformed = {(height - 1 - y, x) for x, y in occupied}
        new_width, new_height = height, width
    elif normalized == 270.0:
        transformed = {(y, width - 1 - x) for x, y in occupied}
        new_width, new_height = height, width
    else:
        return rows, width
    result = [0] * new_height
    for x, y in transformed:
        result[y] |= 1 << x
    return tuple(result), new_width


def _shifted_row_bits(bits: int, delta_x: int) -> int:
    return bits << delta_x if delta_x >= 0 else bits >> -delta_x


def _mask_rows_overlap(
    fixed: tuple[int, ...], moving: tuple[int, ...], delta_x: int, delta_y: int,
) -> bool:
    for moving_row, bits in enumerate(moving):
        fixed_row = moving_row + delta_y
        if 0 <= fixed_row < len(fixed) and fixed[fixed_row] & _shifted_row_bits(bits, delta_x):
            return True
    return False


def _mask_contact_count(
    fixed: tuple[int, ...], moving: tuple[int, ...], delta_x: int, delta_y: int,
) -> int:
    contact = 0
    for moving_row, bits in enumerate(moving):
        shifted = _shifted_row_bits(bits, delta_x)
        fixed_row = moving_row + delta_y
        if 0 <= fixed_row < len(fixed):
            contact += (fixed[fixed_row] & (shifted << 1)).bit_count()
            contact += (fixed[fixed_row] & (shifted >> 1)).bit_count()
        if 0 <= fixed_row - 1 < len(fixed):
            contact += (fixed[fixed_row - 1] & shifted).bit_count()
        if 0 <= fixed_row + 1 < len(fixed):
            contact += (fixed[fixed_row + 1] & shifted).bit_count()
    return contact


def _translation_invariant_geometry_key(item: _Item) -> tuple[object, ...]:
    scale = max(item.width, item.height, 1.0)
    quantum = max(1e-9, scale * 1e-8)

    def coordinate(value: float, origin: float) -> int:
        return int(round((value - origin) / quantum))

    members = []
    for entity in item.members:
        if isinstance(entity, Circle):
            members.append((
                "circle",
                coordinate(entity.center.x, item.bounds[0]),
                coordinate(entity.center.y, item.bounds[1]),
                int(round(entity.radius / quantum)),
            ))
        elif isinstance(entity, Rectangle):
            points = sorted((entity.corner_a, entity.corner_b), key=lambda point: (point.x, point.y))
            members.append((
                "rectangle",
                tuple(
                    (coordinate(point.x, item.bounds[0]), coordinate(point.y, item.bounds[1]))
                    for point in points
                ),
            ))
        elif isinstance(entity, Polyline):
            members.append((
                "polyline",
                bool(entity.closed),
                tuple(
                    (coordinate(point.x, item.bounds[0]), coordinate(point.y, item.bounds[1]))
                    for point in entity.points
                ),
            ))
        else:
            members.append((type(entity).__name__, repr(entity)))
    return round(item.width, 9), round(item.height, 9), tuple(members)


def _pair_pattern(first: _Item, result: tuple[object, ...]) -> tuple[object, ...]:
    score, moved_second, combined, inside_hole, angle = result
    moved_bounds = drawing_bounds(moved_second)
    if moved_bounds is None:
        raise ValueError("A paired nested item has no measurable geometry.")
    return (
        score,
        angle,
        moved_bounds[0] - first.bounds[0],
        moved_bounds[1] - first.bounds[1],
        (
            combined[0] - first.bounds[0],
            combined[1] - first.bounds[1],
            combined[2] - first.bounds[0],
            combined[3] - first.bounds[1],
        ),
        bool(inside_hole),
    )


def _apply_pair_pattern(
    first: _Item,
    second: _Item,
    pattern: tuple[object, ...],
) -> tuple[object, ...]:
    score, angle, offset_x, offset_y, combined_offsets, inside_hole = pattern
    rotated_members, rotated_bounds = _rotated_item(second, float(angle))
    target_x = first.bounds[0] + float(offset_x)
    target_y = first.bounds[1] + float(offset_y)
    delta_x = target_x - rotated_bounds[0]
    delta_y = target_y - rotated_bounds[1]
    moved_second = [
        transform_entity(entity, delta_x=delta_x, delta_y=delta_y)
        for entity in rotated_members
    ]
    combined = (
        first.bounds[0] + combined_offsets[0],
        first.bounds[1] + combined_offsets[1],
        first.bounds[0] + combined_offsets[2],
        first.bounds[1] + combined_offsets[3],
    )
    return score, moved_second, combined, bool(inside_hole), float(angle)


def _signature_hole_fit(container: dict[str, object], insert: dict[str, object], spacing: float) -> bool:
    width = float(insert["width"])
    height = float(insert["height"])
    for left, bottom, right, top in container["void_bounds"]:
        hole_width = right - left - spacing * 2
        hole_height = top - bottom - spacing * 2
        if (width <= hole_width + _TOLERANCE and height <= hole_height + _TOLERANCE) or (
            height <= hole_width + _TOLERANCE and width <= hole_height + _TOLERANCE
        ):
            return True
    return False


def _best_local_pair(
    first: _Item,
    second: _Item,
    *,
    spacing: float,
    angles: tuple[float, ...],
    use_holes: bool,
    resolution: float,
    max_positions: int = 256,
):
    best = None
    local_resolution = max(
        resolution,
        max(first.width, first.height, second.width, second.height) / 48.0,
    )
    base = _shape_footprint(
        first.members, use_holes, local_resolution, raster_point_limit=96,
    )
    validation_tolerance = min(
        local_resolution / 4.0,
        spacing / 8.0 if spacing > _TOLERANCE else local_resolution / 16.0,
    )
    validation_spacing = spacing + validation_tolerance * 2.0
    validation_base_template = _simplified_clearance_footprint(
        base, validation_tolerance,
    )
    for angle_index, angle in enumerate(angles):
        rotated_members, _rotated_bounds = _rotated_item(second, angle)
        moving = _shape_footprint(
            rotated_members, use_holes, local_resolution, raster_point_limit=96,
        )
        validation_moving = _simplified_clearance_footprint(moving, validation_tolerance)
        moving_width = moving.bounds[2] - moving.bounds[0]
        moving_height = moving.bounds[3] - moving.bounds[1]
        # Work in a temporary positive-quadrant frame. Real drawings may sit
        # at any coordinates, while Plate deliberately rejects negative
        # origins. The accepted mate is translated back into the first item's
        # original frame before it becomes a rigid pair.
        base_shift_x = moving_width - base.bounds[0]
        base_shift_y = moving_height - base.bounds[1]
        local_base = _translated_footprint(base, base_shift_x, base_shift_y)
        validation_base = _translated_footprint(
            validation_base_template, base_shift_x, base_shift_y,
        )
        origin_x = local_resolution
        origin_y = local_resolution
        max_grid_x = max(0, int(math.floor((local_base.bounds[2] - local_resolution - origin_x) / local_resolution)))
        max_grid_y = max(0, int(math.floor((local_base.bounds[3] - local_resolution - origin_y) / local_resolution)))
        local_state = _ShapePlateState(placed=[local_base], used_bounds=local_base.bounds)
        local_plate = Plate(
            1,
            0.0,
            0.0,
            (max_grid_x + 2) * local_resolution + moving_width,
            (max_grid_y + 2) * local_resolution + moving_height,
        )
        _add_absolute_mask(local_state, local_base, local_plate, local_resolution)
        _dilate_occupied_mask(
            local_state,
            int(math.ceil(spacing / local_resolution - _TOLERANCE)),
        )

        baseline_horizontal = (first.width + spacing + moving_width) * max(first.height, moving_height)
        baseline_vertical = max(first.width, moving_width) * (first.height + spacing + moving_height)
        baseline_area = min(baseline_horizontal, baseline_vertical)
        if baseline_area <= _TOLERANCE:
            continue

        # Compact-envelope positions are the useful interlock candidates, so
        # test those before walking the rest of the local grid. This makes an
        # obvious repeated-letter mate finish after a handful of checks.
        grid_positions = []
        for grid_y in range(max_grid_y + 1):
            target_y = origin_y + grid_y * local_resolution
            for grid_x in range(max_grid_x + 1):
                target_x = origin_x + grid_x * local_resolution
                candidate_bounds = (
                    target_x,
                    target_y,
                    target_x + moving_width,
                    target_y + moving_height,
                )
                envelope = _union_bounds(local_base.bounds, candidate_bounds)
                assert envelope is not None
                envelope_area = (envelope[2] - envelope[0]) * (envelope[3] - envelope[1])
                grid_positions.append((envelope_area, grid_y, grid_x, target_x, target_y))
        grid_positions.sort(key=lambda value: (value[0], value[1], value[2]))

        if use_holes and local_base.void_contours:
            hole_positions = []
            hole_grids = set()
            for void in local_base.void_contours:
                left, bottom, right, top = _path_bounds(void)
                targets = (
                    ((left + right - moving_width) / 2, (bottom + top - moving_height) / 2),
                    (left + spacing, bottom + spacing),
                )
                for target_x, target_y in targets:
                    center_x = int(round((target_x - origin_x) / local_resolution))
                    center_y = int(round((target_y - origin_y) / local_resolution))
                    for offset_y in (-1, 0, 1):
                        for offset_x in (-1, 0, 1):
                            grid_x = center_x + offset_x
                            grid_y = center_y + offset_y
                            if not (0 <= grid_x <= max_grid_x and 0 <= grid_y <= max_grid_y):
                                continue
                            key = (grid_x, grid_y)
                            if key in hole_grids:
                                continue
                            hole_grids.add(key)
                            x = origin_x + grid_x * local_resolution
                            y = origin_y + grid_y * local_resolution
                            hole_positions.append((-1.0, grid_y, grid_x, x, y))
            if hole_positions:
                grid_positions = hole_positions + [
                    position for position in grid_positions
                    if (position[2], position[1]) not in hole_grids
                ]

        exact_checks = 0
        for _envelope_area, grid_y, grid_x, target_x, target_y in grid_positions[:max_positions]:
                # grid_x/grid_y are relative to the search origin, while the
                # occupied mask is indexed from the local plate origin.  Use
                # the candidate's actual local coordinates so raster
                # rejection and exact contour validation evaluate the same
                # placement.
                mask_grid_x = int(round(target_x / local_resolution))
                mask_grid_y = int(round(target_y / local_resolution))
                if _mask_collides(local_state, moving.mask_rows, mask_grid_x, mask_grid_y):
                    continue
                exact_checks += 1
                if exact_checks > 12:
                    break
                candidate = _translated_footprint(
                    moving,
                    target_x - moving.bounds[0],
                    target_y - moving.bounds[1],
                )
                overlap = _bounds_overlap_area(local_base.bounds, candidate.bounds)
                if overlap <= _TOLERANCE:
                    continue
                validation_candidate = _translated_footprint(
                    validation_moving,
                    target_x - moving.bounds[0],
                    target_y - moving.bounds[1],
                )
                if not _footprints_clear(validation_base, validation_candidate, validation_spacing):
                    continue
                local_combined = _union_bounds(local_base.bounds, candidate.bounds)
                assert local_combined is not None
                combined_area = (local_combined[2] - local_combined[0]) * (local_combined[3] - local_combined[1])
                inside_hole = use_holes and _footprint_inside_void(candidate, local_base)
                # A pair must buy at least 2% over primitive side-by-side
                # packing unless it occupies a genuine internal hole.
                if not inside_hole and combined_area >= baseline_area * 0.98:
                    continue
                score = (
                    0 if inside_hole else 1,
                    combined_area / baseline_area,
                    -overlap,
                    angle_index,
                    grid_y,
                    grid_x,
                )
                if best is None or score < best[0]:
                    target_x_original = target_x - base_shift_x
                    target_y_original = target_y - base_shift_y
                    delta_x = target_x_original - moving.bounds[0]
                    delta_y = target_y_original - moving.bounds[1]
                    moved_members = [
                        transform_entity(entity, delta_x=delta_x, delta_y=delta_y)
                        for entity in moving.members
                    ]
                    combined = (
                        local_combined[0] - base_shift_x,
                        local_combined[1] - base_shift_y,
                        local_combined[2] - base_shift_x,
                        local_combined[3] - base_shift_y,
                    )
                    best = (score, moved_members, combined, bool(inside_hole), angle)
                    if inside_hole or score[1] <= 0.75:
                        return best
    return best


def _polyline_is_concave(points: list[Point]) -> bool:
    if len(points) < 4:
        return False
    sign = 0
    for index, current in enumerate(points):
        previous = points[index - 1]
        following = points[(index + 1) % len(points)]
        cross = (
            (current.x - previous.x) * (following.y - current.y)
            - (current.y - previous.y) * (following.x - current.x)
        )
        if abs(cross) <= _TOLERANCE:
            continue
        current_sign = 1 if cross > 0 else -1
        if sign and current_sign != sign:
            return True
        sign = current_sign
    return False


def _external_void_features(mask_rows: list[int], columns: int) -> tuple[float, float]:
    rows = len(mask_rows)
    if rows == 0 or columns <= 0:
        return 0.0, 0.0
    total_cells = rows * columns
    empty = {
        (column, row)
        for row, bits in enumerate(mask_rows)
        for column in range(columns)
        if not bits & (1 << column)
    }
    boundary = [
        cell for cell in empty
        if cell[0] in {0, columns - 1} or cell[1] in {0, rows - 1}
    ]
    visited = set()
    external_count = 0
    largest_component = 0
    for start in boundary:
        if start in visited:
            continue
        stack = [start]
        visited.add(start)
        component_size = 0
        while stack:
            column, row = stack.pop()
            component_size += 1
            for neighbor in (
                (column - 1, row), (column + 1, row),
                (column, row - 1), (column, row + 1),
            ):
                if neighbor in empty and neighbor not in visited:
                    visited.add(neighbor)
                    stack.append(neighbor)
        external_count += component_size
        largest_component = max(largest_component, component_size)
    return external_count / total_cells, largest_component / total_cells


def _dilate_occupied_mask(state: _ShapePlateState, radius: int) -> None:
    if radius <= 0 or not state.occupied_rows:
        return
    source = dict(state.occupied_rows)
    expanded: dict[int, int] = {}
    for row, bits in source.items():
        wide_bits = bits
        for amount in range(1, radius + 1):
            wide_bits |= bits << amount
            wide_bits |= bits >> amount
        for row_offset in range(-radius, radius + 1):
            target_row = row + row_offset
            if target_row >= 0:
                expanded[target_row] = expanded.get(target_row, 0) | wide_bits
    state.occupied_rows = expanded


def _nest_shape_aware(
    entities: list[Entity],
    output_plates: list[Plate],
    eligible_plates: list[Plate],
    items: list[_Item],
    nested_item_count: int,
    *,
    spacing: float,
    allow_rotation: bool,
    use_holes: bool,
    rotation_step_degrees: float | None,
    maximum_rotation_degrees: float,
    search_resolution: float,
    requested_ids: set[str] | None,
    allow_new_plates: bool,
    automatic_compounds: bool,
    progress_callback: Callable[[str, float], None] | None,
) -> NestResult:
    """Raster-assisted, contour-verified bottom-left nesting.

    The raster is only a fast rejection filter. Accepted placements are
    checked against the actual contour segments, so the search can use letter
    concavities and true internal holes without allowing overlapping parts.
    This is deliberately deterministic: the same geometry and controls always
    produce the same plate layout.
    """
    angles = _rotation_angles(
        allow_rotation,
        rotation_step_degrees,
        maximum_rotation_degrees,
    )
    states = {plate.number: _ShapePlateState() for plate in eligible_plates}
    nested_ids = {member.id for item in items for member in item.members}

    # Selected-only nesting preserves untouched geometry as real contour
    # obstacles. It does not reduce those obstacles to bounding rectangles.
    if requested_ids is not None:
        fixed_entities = [entity for entity in entities if entity.id not in nested_ids]
        for fixed_item in _compound_items(fixed_entities, automatic_compounds):
            footprint = _shape_footprint(fixed_item.members, use_holes, search_resolution)
            for plate in eligible_plates:
                if _bounds_inside_plate(footprint.bounds, plate):
                    state = states[plate.number]
                    state.placed.append(footprint)
                    state.used_bounds = _union_bounds(state.used_bounds, footprint.bounds)
                    _add_absolute_mask(state, footprint, plate, search_resolution)
                    break

    # Area first keeps large letters from being stranded after small drill
    # features. The contour fill area breaks equal-envelope ties in favor of
    # genuinely substantial shapes.
    _report_progress(progress_callback, "Analyzing solid contours and usable holes…", 0.02)
    items.sort(
        key=lambda item: (
            -_item_material_area(item, use_holes, search_resolution),
            -max(item.width, item.height),
            item.key,
        )
    )
    transformed: dict[str, Entity] = {}
    assignments: dict[str, int] = {}
    rotations: dict[str, float] = {}
    interlocked_count = 0
    hole_nested_count = 0

    for item_index, item in enumerate(items, start=1):
        _report_progress(
            progress_callback,
            f"Shape-aware nesting: searching part {item_index} of {len(items)}…",
            (item_index - 1) / max(1, len(items)),
        )
        placement = _best_shape_placement(
            item,
            eligible_plates,
            states,
            spacing=spacing,
            angles=angles,
            use_holes=use_holes,
            resolution=search_resolution,
            progress_callback=progress_callback,
            item_index=item_index,
            item_count=len(items),
        )
        if placement is None and allow_new_plates:
            template = eligible_plates[-1]
            next_x = max(plate.x + plate.width for plate in output_plates)
            new_plate = Plate(
                max(plate.number for plate in output_plates) + 1,
                next_x,
                0.0,
                template.width,
                template.height,
            )
            output_plates.append(new_plate)
            eligible_plates.append(new_plate)
            states[new_plate.number] = _ShapePlateState()
            placement = _best_shape_placement(
                item,
                [new_plate],
                states,
                spacing=spacing,
                angles=angles,
                use_holes=use_holes,
                resolution=search_resolution,
                # The selected-plate search has already consumed this item's
                # progress span; avoid making the UI bar jump backward while
                # the new empty overflow plate is tried.
                progress_callback=None,
                item_index=item_index,
                item_count=len(items),
            )
        if placement is None:
            raise ValueError(
                f"Shape-aware nesting could not fit item {item.width:g} × {item.height:g} "
                "on the selected plate scope. Reduce spacing/rotation increment, "
                "increase the rotation range, or permit overflow plates."
            )

        plate, angle, oriented, target_x, target_y, grid_x, grid_y = placement
        state = states[plate.number]
        delta_x = target_x - oriented.bounds[0]
        delta_y = target_y - oriented.bounds[1]
        moved_members = [
            transform_entity(entity, delta_x=delta_x, delta_y=delta_y)
            for entity in oriented.members
        ]
        moved = _shape_footprint(moved_members, use_holes, search_resolution)
        if any(_bounds_overlap_area(moved.bounds, other.bounds) > _TOLERANCE for other in state.placed):
            interlocked_count += 1
        if use_holes and any(_footprint_inside_void(moved, other) for other in state.placed):
            hole_nested_count += 1

        for entity in moved_members:
            transformed[entity.id] = entity
            assignments[entity.id] = plate.number
            rotations[entity.id] = angle
        state.placed.append(moved)
        state.used_bounds = _union_bounds(state.used_bounds, moved.bounds)
        _add_shifted_mask(state, oriented.mask_rows, grid_x, grid_y)

        _report_progress(
            progress_callback,
            f"Placed part {item_index} of {len(items)} on Plate {plate.number}.",
            item_index / max(1, len(items)),
        )

    _report_progress(progress_callback, "Finalizing shape-aware nest…", 1.0)
    return NestResult(
        [transformed.get(entity.id, entity) for entity in entities],
        sorted(output_plates, key=lambda plate: plate.number),
        assignments,
        rotations,
        nested_item_count,
        (),
        interlocked_count,
        hole_nested_count,
    )


def _rotation_angles(
    allow_rotation: bool,
    increment: float | None,
    maximum: float,
) -> tuple[float, ...]:
    if not allow_rotation or maximum <= _TOLERANCE:
        return (0.0,)
    if increment is None:
        return (0.0, 90.0) if maximum >= 90.0 else (0.0,)
    values = [0.0]
    count = int(math.floor(maximum / increment + _TOLERANCE))
    for index in range(1, count + 1):
        angle = min(maximum, index * increment)
        values.extend((angle, -angle))
    if not math.isclose(count * increment, maximum, abs_tol=_TOLERANCE):
        values.extend((maximum, -maximum))
    unique: list[float] = []
    seen: set[float] = set()
    # Useful lettering orientations are tried early even when the operator
    # chooses a fine increment. This quickly discovers E/E, A/A and T/T pairs.
    preferred = [0.0, 180.0, 90.0, -90.0, *values]
    for angle in preferred:
        if abs(angle) > maximum + _TOLERANCE:
            continue
        normalized = round(angle % 360.0, 9)
        if normalized not in seen:
            seen.add(normalized)
            unique.append(angle)
    return tuple(unique)


def _normalized_rotation(angle: float) -> float:
    normalized = angle % 360.0
    if normalized > 180.0:
        normalized -= 360.0
    if math.isclose(normalized, 0.0, abs_tol=_TOLERANCE):
        return 0.0
    return normalized


def _best_shape_placement(
    item: _Item,
    plates: list[Plate],
    states: dict[int, _ShapePlateState],
    *,
    spacing: float,
    angles: tuple[float, ...],
    use_holes: bool,
    resolution: float,
    progress_callback: Callable[[str, float], None] | None,
    item_index: int,
    item_count: int,
):
    candidates = []
    search_steps = max(1, len(plates) * len(angles))
    search_step = 0
    for plate in plates:
        state = states[plate.number]
        for angle_index, angle in enumerate(angles):
            search_step += 1
            item_fraction = (search_step - 1) / search_steps
            _report_progress(
                progress_callback,
                f"Part {item_index}/{item_count}: Plate {plate.number}, angle {angle:g}°…",
                ((item_index - 1) + item_fraction * 0.9) / max(1, item_count),
            )
            rotated_members, rotated_bounds = _rotated_item(item, angle)
            oriented = _shape_footprint(rotated_members, use_holes, resolution)
            width = rotated_bounds[2] - rotated_bounds[0]
            height = rotated_bounds[3] - rotated_bounds[1]
            available_width = plate.width - spacing * 2 - width
            available_height = plate.height - spacing * 2 - height
            if available_width < -_TOLERANCE or available_height < -_TOLERANCE:
                continue
            max_grid_x = max(0, int(math.floor(max(0.0, available_width) / resolution + _TOLERANCE)))
            max_grid_y = max(0, int(math.floor(max(0.0, available_height) / resolution + _TOLERANCE)))

            special = _hole_anchor_grid_positions(
                oriented, state, plate, spacing, resolution, max_grid_x, max_grid_y,
            ) if use_holes else []
            seen: set[tuple[int, int]] = set()
            valid_for_orientation = 0
            examined = 0
            positions = [*special, None]
            for position in positions:
                iterator = (
                    (position,)
                    if position is not None
                    else ((grid_x, grid_y) for grid_y in range(max_grid_y + 1) for grid_x in range(max_grid_x + 1))
                )
                for grid_x, grid_y in iterator:
                    if (grid_x, grid_y) in seen:
                        continue
                    seen.add((grid_x, grid_y))
                    examined += 1
                    if examined % 10_000 == 0:
                        grid_fraction = min(0.99, examined / max(1, (max_grid_x + 1) * (max_grid_y + 1)))
                        _report_progress(
                            progress_callback,
                            f"Part {item_index}/{item_count}: testing angle {angle:g}° ({examined:,} positions)…",
                            (
                                (item_index - 1)
                                + ((search_step - 1 + grid_fraction) / search_steps) * 0.9
                            ) / max(1, item_count),
                        )
                    if examined > 150_000:
                        break
                    if _mask_collides(state, oriented.mask_rows, grid_x, grid_y):
                        continue
                    target_x = plate.x + spacing + grid_x * resolution
                    target_y = plate.y + spacing + grid_y * resolution
                    delta_x = target_x - oriented.bounds[0]
                    delta_y = target_y - oriented.bounds[1]
                    candidate = _translated_footprint(oriented, delta_x, delta_y)
                    if not all(_footprints_clear(candidate, other, spacing) for other in state.placed):
                        continue
                    used = _union_bounds(state.used_bounds, candidate.bounds)
                    assert used is not None
                    envelope_width = used[2] - plate.x
                    envelope_height = used[3] - plate.y
                    overlap = sum(_bounds_overlap_area(candidate.bounds, other.bounds) for other in state.placed)
                    inside_void = any(_footprint_inside_void(candidate, other) for other in state.placed)
                    score = (
                        plate.number,
                        envelope_width * envelope_height,
                        envelope_height,
                        envelope_width,
                        0 if inside_void else 1,
                        -overlap,
                        angle_index,
                        grid_y,
                        grid_x,
                    )
                    candidates.append((score, plate, angle, oriented, target_x, target_y, grid_x, grid_y))
                    valid_for_orientation += 1
                    # Several valid positions let compact/hole scoring beat the
                    # first row-major location without turning this into an
                    # unbounded exhaustive solver.
                    if valid_for_orientation >= 8:
                        break
                if examined > 150_000 or valid_for_orientation >= 8:
                    break
    if not candidates:
        return None
    _score, plate, angle, oriented, target_x, target_y, grid_x, grid_y = min(candidates, key=lambda value: value[0])
    return plate, angle, oriented, target_x, target_y, grid_x, grid_y


def _shape_footprint(
    members: list[Entity],
    use_holes: bool,
    resolution: float,
    *,
    raster_point_limit: int | None = None,
) -> _ShapeFootprint:
    bounds = drawing_bounds(members)
    if bounds is None:
        raise ValueError("A nested group has no measurable geometry.")
    paths = [_entity_contour(entity, resolution) for entity in members if _is_closed(entity)]
    paths = [path for path in paths if len(path) >= 3]
    if not paths:
        min_x, min_y, max_x, max_y = bounds
        paths = [[Point(min_x, min_y), Point(max_x, min_y), Point(max_x, max_y), Point(min_x, max_y)]]
    depths = _contour_depths(paths)
    voids = [path for path, depth in zip(paths, depths) if depth % 2 == 1] if use_holes else []
    contours = paths if use_holes else [path for path, depth in zip(paths, depths) if depth == 0]
    footprint = _ShapeFootprint(list(members), bounds, contours, voids)
    raster_contours = (
        [_decimate_closed_path(path, raster_point_limit) for path in contours]
        if raster_point_limit is not None else contours
    )
    raster_footprint = _ShapeFootprint(list(members), bounds, raster_contours, [])
    footprint.mask_rows = _raster_mask(raster_footprint, resolution)
    return footprint


def _decimate_closed_path(path: list[Point], limit: int) -> list[Point]:
    if limit < 3 or len(path) <= limit:
        return path
    return [path[int(index * len(path) / limit)] for index in range(limit)]


def _simplified_clearance_footprint(
    footprint: _ShapeFootprint,
    tolerance: float,
) -> _ShapeFootprint:
    return _ShapeFootprint(
        footprint.members,
        footprint.bounds,
        [_simplify_closed_path(path, tolerance) for path in footprint.contours],
        [_simplify_closed_path(path, tolerance) for path in footprint.void_contours],
        footprint.mask_rows,
    )


def _simplify_closed_path(path: list[Point], tolerance: float) -> list[Point]:
    if tolerance <= _TOLERANCE or len(path) <= 4:
        return path
    result = list(path)
    changed = True
    while changed and len(result) > 4:
        changed = False
        kept = []
        count = len(result)
        for index, point in enumerate(result):
            previous = result[index - 1]
            following = result[(index + 1) % count]
            if _point_segment_distance(point, previous, following) <= tolerance:
                changed = True
                continue
            kept.append(point)
        if len(kept) < 3:
            break
        result = kept
    return result


def _entity_contour(entity: Entity, resolution: float) -> list[Point]:
    if isinstance(entity, Circle):
        segments = max(24, min(144, int(math.ceil(2 * math.pi * entity.radius / max(resolution / 2, 1e-4)))))
        return [
            Point(
                entity.center.x + entity.radius * math.cos(2 * math.pi * index / segments),
                entity.center.y + entity.radius * math.sin(2 * math.pi * index / segments),
            )
            for index in range(segments)
        ]
    if isinstance(entity, Rectangle):
        min_x, max_x = sorted((entity.corner_a.x, entity.corner_b.x))
        min_y, max_y = sorted((entity.corner_a.y, entity.corner_b.y))
        return [Point(min_x, min_y), Point(max_x, min_y), Point(max_x, max_y), Point(min_x, max_y)]
    if isinstance(entity, Polyline) and entity.closed:
        return list(entity.points)
    return []


def _contour_depths(paths: list[list[Point]]) -> list[int]:
    depths = []
    bounds = [_path_bounds(path) for path in paths]
    for index, path in enumerate(paths):
        sample = path[0]
        depth = 0
        for other_index, other in enumerate(paths):
            if index == other_index or not _bounds_contain(bounds[other_index], bounds[index]):
                continue
            if _point_in_polygon(sample, other):
                depth += 1
        depths.append(depth)
    return depths


def _raster_mask(footprint: _ShapeFootprint, resolution: float) -> list[int]:
    min_x, min_y, max_x, max_y = footprint.bounds
    columns = max(1, int(math.ceil((max_x - min_x) / resolution)))
    rows = max(1, int(math.ceil((max_y - min_y) / resolution)))
    result: list[int] = []
    for row in range(rows):
        y = min_y + (row + 0.5) * resolution
        bits = 0
        for column in range(columns):
            x = min_x + (column + 0.5) * resolution
            if _point_in_material(Point(x, y), footprint.contours):
                bits |= 1 << column
        result.append(bits)
    if not any(result):
        return [(1 << columns) - 1 for _ in range(rows)]
    return result


def _point_in_material(point: Point, contours: list[list[Point]]) -> bool:
    return sum(1 for path in contours if _point_in_polygon(point, path)) % 2 == 1


def _point_in_polygon(point: Point, path: list[Point]) -> bool:
    inside = False
    previous = path[-1]
    for current in path:
        if _point_on_segment(point, previous, current):
            return True
        if (current.y > point.y) != (previous.y > point.y):
            crossing_x = (previous.x - current.x) * (point.y - current.y) / (previous.y - current.y) + current.x
            if point.x < crossing_x:
                inside = not inside
        previous = current
    return inside


def _translated_footprint(footprint: _ShapeFootprint, delta_x: float, delta_y: float) -> _ShapeFootprint:
    def moved_path(path):
        return [Point(point.x + delta_x, point.y + delta_y) for point in path]

    bounds = (
        footprint.bounds[0] + delta_x,
        footprint.bounds[1] + delta_y,
        footprint.bounds[2] + delta_x,
        footprint.bounds[3] + delta_y,
    )
    return _ShapeFootprint(
        footprint.members,
        bounds,
        [moved_path(path) for path in footprint.contours],
        [moved_path(path) for path in footprint.void_contours],
        footprint.mask_rows,
    )


def _footprints_clear(first: _ShapeFootprint, second: _ShapeFootprint, spacing: float) -> bool:
    expanded_first = (
        first.bounds[0] - spacing, first.bounds[1] - spacing,
        first.bounds[2] + spacing, first.bounds[3] + spacing,
    )
    if not _bounds_intersect_inclusive(expanded_first, second.bounds):
        return True
    first_segments = _contour_segments(first.contours)
    second_segments = _contour_segments(second.contours)
    for start_a, end_a in first_segments:
        segment_bounds_a = _segment_bounds(start_a, end_a, spacing)
        for start_b, end_b in second_segments:
            if not _bounds_intersect_inclusive(segment_bounds_a, _segment_bounds(start_b, end_b, 0.0)):
                continue
            if _segments_intersect(start_a, end_a, start_b, end_b):
                return False
            if spacing > _TOLERANCE and _segment_distance(start_a, end_a, start_b, end_b) < spacing - _TOLERANCE:
                return False
    if any(_point_in_material(point, second.contours) for path in first.contours for point in path):
        return False
    if any(_point_in_material(point, first.contours) for path in second.contours for point in path):
        return False
    return True


def _contour_segments(contours: list[list[Point]]):
    return [
        (path[index - 1], point)
        for path in contours
        for index, point in enumerate(path)
    ]


def _segments_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    def orientation(first, second, third):
        return (second.x - first.x) * (third.y - first.y) - (second.y - first.y) * (third.x - first.x)

    ab_c = orientation(a, b, c)
    ab_d = orientation(a, b, d)
    cd_a = orientation(c, d, a)
    cd_b = orientation(c, d, b)
    if ((ab_c > _TOLERANCE and ab_d < -_TOLERANCE) or (ab_c < -_TOLERANCE and ab_d > _TOLERANCE)) and (
        (cd_a > _TOLERANCE and cd_b < -_TOLERANCE) or (cd_a < -_TOLERANCE and cd_b > _TOLERANCE)
    ):
        return True
    return (
        abs(ab_c) <= _TOLERANCE and _point_on_segment(c, a, b)
        or abs(ab_d) <= _TOLERANCE and _point_on_segment(d, a, b)
        or abs(cd_a) <= _TOLERANCE and _point_on_segment(a, c, d)
        or abs(cd_b) <= _TOLERANCE and _point_on_segment(b, c, d)
    )


def _segment_distance(a: Point, b: Point, c: Point, d: Point) -> float:
    return min(
        _point_segment_distance(a, c, d),
        _point_segment_distance(b, c, d),
        _point_segment_distance(c, a, b),
        _point_segment_distance(d, a, b),
    )


def _point_segment_distance(point: Point, start: Point, end: Point) -> float:
    length_squared = (end.x - start.x) ** 2 + (end.y - start.y) ** 2
    if length_squared <= _TOLERANCE:
        return point.distance_to(start)
    amount = (
        (point.x - start.x) * (end.x - start.x)
        + (point.y - start.y) * (end.y - start.y)
    ) / length_squared
    amount = max(0.0, min(1.0, amount))
    projected = Point(start.x + amount * (end.x - start.x), start.y + amount * (end.y - start.y))
    return point.distance_to(projected)


def _mask_collides(state: _ShapePlateState, rows: list[int], grid_x: int, grid_y: int) -> bool:
    return any(
        bits and state.occupied_rows.get(grid_y + row, 0) & (bits << grid_x)
        for row, bits in enumerate(rows)
    )


def _add_shifted_mask(state: _ShapePlateState, rows: list[int], grid_x: int, grid_y: int) -> None:
    for row, bits in enumerate(rows):
        if bits:
            target_row = grid_y + row
            state.occupied_rows[target_row] = state.occupied_rows.get(target_row, 0) | (bits << grid_x)


def _add_absolute_mask(state: _ShapePlateState, footprint: _ShapeFootprint, plate: Plate, resolution: float) -> None:
    min_x, min_y, max_x, max_y = footprint.bounds
    start_x = max(0, int(math.floor((min_x - plate.x) / resolution)))
    end_x = min(int(math.ceil(plate.width / resolution)), int(math.ceil((max_x - plate.x) / resolution)))
    start_y = max(0, int(math.floor((min_y - plate.y) / resolution)))
    end_y = min(int(math.ceil(plate.height / resolution)), int(math.ceil((max_y - plate.y) / resolution)))
    for row in range(start_y, end_y):
        y = plate.y + (row + 0.5) * resolution
        bits = state.occupied_rows.get(row, 0)
        for column in range(start_x, end_x):
            x = plate.x + (column + 0.5) * resolution
            if _point_in_material(Point(x, y), footprint.contours):
                bits |= 1 << column
        state.occupied_rows[row] = bits


def _hole_anchor_grid_positions(
    oriented: _ShapeFootprint,
    state: _ShapePlateState,
    plate: Plate,
    spacing: float,
    resolution: float,
    max_grid_x: int,
    max_grid_y: int,
) -> list[tuple[int, int]]:
    width = oriented.bounds[2] - oriented.bounds[0]
    height = oriented.bounds[3] - oriented.bounds[1]
    positions: list[tuple[int, int]] = []
    for placed in state.placed:
        for void in placed.void_contours:
            left, bottom, right, top = _path_bounds(void)
            targets = (
                ((left + right - width) / 2, (bottom + top - height) / 2),
                (left + spacing, bottom + spacing),
            )
            for target_x, target_y in targets:
                center_x = int(round((target_x - plate.x - spacing) / resolution))
                center_y = int(round((target_y - plate.y - spacing) / resolution))
                for offset_y in (-1, 0, 1):
                    for offset_x in (-1, 0, 1):
                        grid_x = center_x + offset_x
                        grid_y = center_y + offset_y
                        if 0 <= grid_x <= max_grid_x and 0 <= grid_y <= max_grid_y:
                            positions.append((grid_x, grid_y))
    return positions


def _footprint_inside_void(candidate: _ShapeFootprint, placed: _ShapeFootprint) -> bool:
    root_points = [point for path in candidate.contours for point in path]
    return any(root_points and all(_point_in_polygon(point, void) for point in root_points) for void in placed.void_contours)


def _item_material_area(item: _Item, use_holes: bool, resolution: float) -> float:
    footprint = _shape_footprint(item.members, use_holes, resolution)
    return sum(bits.bit_count() for bits in footprint.mask_rows) * resolution * resolution


def _path_bounds(path: list[Point]) -> tuple[float, float, float, float]:
    xs = [point.x for point in path]
    ys = [point.y for point in path]
    return min(xs), min(ys), max(xs), max(ys)


def _segment_bounds(start: Point, end: Point, padding: float) -> tuple[float, float, float, float]:
    return (
        min(start.x, end.x) - padding,
        min(start.y, end.y) - padding,
        max(start.x, end.x) + padding,
        max(start.y, end.y) + padding,
    )


def _bounds_overlap_area(first, second) -> float:
    width = min(first[2], second[2]) - max(first[0], second[0])
    height = min(first[3], second[3]) - max(first[1], second[1])
    return max(0.0, width) * max(0.0, height)


def _bounds_intersect_inclusive(first, second) -> bool:
    return not (
        first[2] < second[0] - _TOLERANCE
        or second[2] < first[0] - _TOLERANCE
        or first[3] < second[1] - _TOLERANCE
        or second[3] < first[1] - _TOLERANCE
    )


def _union_bounds(first, second):
    if first is None:
        return second
    if second is None:
        return first
    return (
        min(first[0], second[0]), min(first[1], second[1]),
        max(first[2], second[2]), max(first[3], second[3]),
    )


def _pair_common_line_items(items: list[_Item], tool_diameter: float) -> list[_Item]:
    """Make conservative rectangular pairs before ordinary best-fit nesting.

    Pairing the items, rather than merely reducing global spacing, guarantees
    that the matching edges actually land one cutter diameter apart.  Only
    singleton axis-aligned rectangles participate in this first qualified
    implementation; compound parts and arbitrary contours retain ordinary
    clearance nesting.
    """
    remaining = list(items)
    result: list[_Item] = []
    tolerance = max(_TOLERANCE, tool_diameter * 1e-6)
    while remaining:
        first = remaining.pop(0)
        first_entity = first.members[0] if len(first.members) == 1 else None
        first_bounds = _rectangular_bounds(first_entity) if first_entity is not None else None
        if first_bounds is None:
            result.append(first)
            continue

        paired_index = None
        vertical = False
        for index, second in enumerate(remaining):
            second_entity = second.members[0] if len(second.members) == 1 else None
            second_bounds = _rectangular_bounds(second_entity) if second_entity is not None else None
            if second_bounds is None:
                continue
            same_height = math.isclose(first.height, second.height, abs_tol=tolerance)
            same_width = math.isclose(first.width, second.width, abs_tol=tolerance)
            if same_height or same_width:
                paired_index = index
                vertical = not same_height and same_width
                break
        if paired_index is None:
            result.append(first)
            continue

        second = remaining.pop(paired_index)
        if vertical:
            delta_x = first.bounds[0] - second.bounds[0]
            delta_y = first.bounds[3] + tool_diameter - second.bounds[1]
        else:
            delta_x = first.bounds[2] + tool_diameter - second.bounds[0]
            delta_y = first.bounds[1] - second.bounds[1]
        moved_second = [
            transform_entity(entity, delta_x=delta_x, delta_y=delta_y)
            for entity in second.members
        ]
        members = [*first.members, *moved_second]
        bounds = drawing_bounds(members)
        if bounds is None:
            result.extend((first, second))
            continue
        result.append(_Item(f"common-line:{first.key}:{second.key}", members, bounds))
    return result


def _common_line_candidate_pairs(
    entities: list[Entity],
    assignments: dict[str, int],
    tool_diameter: float,
) -> tuple[tuple[str, str], ...]:
    """Report top-level rectangular pairs whose outside paths can coincide."""
    rectangular = [entity for entity in entities if _rectangular_bounds(entity) is not None]
    top_level = []
    for entity in rectangular:
        if any(
            other.id != entity.id
            and assignments.get(other.id) == assignments.get(entity.id)
            and _entity_contains_entity(other, entity)
            for other in entities
            if _is_closed(other)
        ):
            continue
        top_level.append(entity)

    tolerance = max(1e-7, tool_diameter * 1e-5)
    result: list[tuple[str, str]] = []
    for index, first in enumerate(top_level):
        first_bounds = _rectangular_bounds(first)
        assert first_bounds is not None
        for second in top_level[index + 1:]:
            if assignments.get(first.id) != assignments.get(second.id):
                continue
            second_bounds = _rectangular_bounds(second)
            assert second_bounds is not None
            horizontal = (
                abs(abs(second_bounds[0] - first_bounds[2]) - tool_diameter) <= tolerance
                or abs(abs(first_bounds[0] - second_bounds[2]) - tool_diameter) <= tolerance
            ) and (
                abs(first_bounds[1] - second_bounds[1]) <= tolerance
                and abs(first_bounds[3] - second_bounds[3]) <= tolerance
            )
            vertical = (
                abs(abs(second_bounds[1] - first_bounds[3]) - tool_diameter) <= tolerance
                or abs(abs(first_bounds[1] - second_bounds[3]) - tool_diameter) <= tolerance
            ) and (
                abs(first_bounds[0] - second_bounds[0]) <= tolerance
                and abs(first_bounds[2] - second_bounds[2]) <= tolerance
            )
            if horizontal or vertical:
                result.append((first.id, second.id))
    return tuple(result)


def _rectangular_bounds(entity: Entity) -> tuple[float, float, float, float] | None:
    if isinstance(entity, Rectangle):
        return entity_bounds(entity)
    if not isinstance(entity, Polyline) or not entity.closed or len(entity.points) != 4:
        return None
    points = entity.points
    for start, end in zip(points, points[1:] + points[:1]):
        if not (math.isclose(start.x, end.x, abs_tol=_TOLERANCE) or math.isclose(start.y, end.y, abs_tol=_TOLERANCE)):
            return None
    bounds = entity_bounds(entity)
    if bounds is None:
        return None
    corners = {
        (round(bounds[0], 9), round(bounds[1], 9)),
        (round(bounds[2], 9), round(bounds[1], 9)),
        (round(bounds[2], 9), round(bounds[3], 9)),
        (round(bounds[0], 9), round(bounds[3], 9)),
    }
    return bounds if {(round(point.x, 9), round(point.y, 9)) for point in points} == corners else None


def _compound_items(entities: list[Entity], automatic_compounds: bool) -> list[_Item]:
    explicit: dict[str, list[Entity]] = {}
    ungrouped: list[Entity] = []
    order: list[str] = []
    for entity in entities:
        # Mounting features and their source outlines form an associative part
        # even when the operator has not made (or has separately made) a normal
        # drafting group.  Treat that registration identity as the stronger
        # rigid-item key so nesting cannot strand stud/keyhole geometry.
        mounting_part_id = str(entity.metadata.get("mounting_part_id", "")).strip()
        compound_id = str(entity.metadata.get("compound_id", "")).strip()
        group_id = mounting_part_id or compound_id or str(entity.metadata.get("group_id", "")).strip()
        if group_id:
            if group_id not in explicit:
                explicit[group_id] = []
                order.append(group_id)
            explicit[group_id].append(entity)
        else:
            ungrouped.append(entity)

    groups: list[tuple[str, list[Entity]]] = [(f"group:{key}", explicit[key]) for key in order]
    if not automatic_compounds:
        groups.extend((f"entity:{entity.id}", [entity]) for entity in ungrouped)
        return _items_with_bounds(groups)

    closed = [entity for entity in ungrouped if _is_closed(entity)]
    open_entities = [entity for entity in ungrouped if not _is_closed(entity)]
    roots: dict[str, list[Entity]] = {entity.id: [entity] for entity in closed}
    parent: dict[str, str] = {}
    for inner in closed:
        candidates = [outer for outer in closed if outer.id != inner.id and _entity_contains_entity(outer, inner)]
        if candidates:
            candidates.sort(key=lambda entity: _bounds_area(entity_bounds(entity)))
            parent[inner.id] = candidates[0].id

    def root_id(entity_id: str) -> str:
        seen: set[str] = set()
        while entity_id in parent and entity_id not in seen:
            seen.add(entity_id)
            entity_id = parent[entity_id]
        return entity_id

    roots = {}
    for entity in closed:
        roots.setdefault(root_id(entity.id), []).append(entity)
    groups.extend((f"compound:{root}", members) for root, members in roots.items())
    groups.extend((f"entity:{entity.id}", [entity]) for entity in open_entities)
    return _items_with_bounds(groups)


def _items_with_bounds(groups: list[tuple[str, list[Entity]]]) -> list[_Item]:
    items: list[_Item] = []
    for key, members in groups:
        bounds = drawing_bounds(members)
        if bounds is not None:
            items.append(_Item(key, members, bounds))
    return items


def _best_placement(item, plates, free_by_plate, spacing, allow_rotation, *, angles=None):
    candidates = []
    candidate_angles = angles if angles is not None else ((0.0, 90.0) if allow_rotation else (0.0,))
    for plate in plates:
        free_rectangles = free_by_plate.get(plate.number, [])
        for angle in candidate_angles:
            rotated, bounds = _rotated_item(item, angle)
            width = bounds[2] - bounds[0]
            height = bounds[3] - bounds[1]
            footprint_width = width + spacing
            footprint_height = height + spacing
            for index, free in enumerate(free_rectangles):
                if footprint_width <= free.width + _TOLERANCE and footprint_height <= free.height + _TOLERANCE:
                    short_side = min(free.width - footprint_width, free.height - footprint_height)
                    area_waste = free.width * free.height - footprint_width * footprint_height
                    score = (short_side, area_waste, plate.number, free.y, free.x, angle)
                    candidates.append((score, plate, index, angle, rotated, bounds))
    if not candidates:
        return None
    _score, plate, index, angle, rotated, bounds = min(candidates, key=lambda candidate: candidate[0])
    return plate, index, angle, rotated, bounds


def _rotated_item(item: _Item, angle: float):
    if math.isclose(angle, 0.0):
        return item.members, item.bounds
    center = Point((item.bounds[0] + item.bounds[2]) / 2, (item.bounds[1] + item.bounds[3]) / 2)
    members = [transform_entity(entity, angle_degrees=angle, pivot=center) for entity in item.members]
    bounds = drawing_bounds(members)
    if bounds is None:
        raise ValueError("A nested group has no measurable geometry.")
    return members, bounds


def _split_free_rectangle(free: _FreeRectangle, occupied: _FreeRectangle) -> list[_FreeRectangle]:
    used_width = min(free.width, max(0.0, occupied.width))
    used_height = min(free.height, max(0.0, occupied.height))
    result = []
    right_width = free.width - used_width
    top_height = free.height - used_height
    if right_width > _TOLERANCE:
        result.append(_FreeRectangle(free.x + used_width, free.y, right_width, free.height))
    if top_height > _TOLERANCE and used_width > _TOLERANCE:
        result.append(_FreeRectangle(free.x, free.y + used_height, used_width, top_height))
    return result


def _subtract_from_free_rectangles(free_rectangles, occupied):
    result = []
    for free in free_rectangles:
        if not _rectangles_intersect(free, occupied):
            result.append(free)
            continue
        free_right = free.x + free.width
        free_top = free.y + free.height
        occupied_right = occupied.x + occupied.width
        occupied_top = occupied.y + occupied.height
        if occupied.x > free.x + _TOLERANCE:
            result.append(_FreeRectangle(free.x, free.y, occupied.x - free.x, free.height))
        if occupied_right < free_right - _TOLERANCE:
            result.append(_FreeRectangle(occupied_right, free.y, free_right - occupied_right, free.height))
        overlap_left = max(free.x, occupied.x)
        overlap_right = min(free_right, occupied_right)
        if overlap_right > overlap_left + _TOLERANCE:
            if occupied.y > free.y + _TOLERANCE:
                result.append(_FreeRectangle(overlap_left, free.y, overlap_right - overlap_left, occupied.y - free.y))
            if occupied_top < free_top - _TOLERANCE:
                result.append(_FreeRectangle(overlap_left, occupied_top, overlap_right - overlap_left, free_top - occupied_top))
    return _prune_free_rectangles(result)


def _prune_free_rectangles(rectangles):
    valid = [rectangle for rectangle in rectangles if rectangle.width > _TOLERANCE and rectangle.height > _TOLERANCE]
    result = []
    for index, rectangle in enumerate(valid):
        if any(index != other_index and _rectangle_contains(other, rectangle) for other_index, other in enumerate(valid)):
            continue
        result.append(rectangle)
    return result


def _rectangle_contains(outer, inner):
    return (
        outer.x <= inner.x + _TOLERANCE and outer.y <= inner.y + _TOLERANCE
        and outer.x + outer.width >= inner.x + inner.width - _TOLERANCE
        and outer.y + outer.height >= inner.y + inner.height - _TOLERANCE
    )


def _rectangles_intersect(first, second):
    return not (
        first.x + first.width <= second.x + _TOLERANCE
        or second.x + second.width <= first.x + _TOLERANCE
        or first.y + first.height <= second.y + _TOLERANCE
        or second.y + second.height <= first.y + _TOLERANCE
    )


def _is_closed(entity: Entity) -> bool:
    return isinstance(entity, (Circle, Rectangle)) or isinstance(entity, Polyline) and entity.closed and len(entity.points) >= 3


def _entity_contains_entity(outer: Entity, inner: Entity) -> bool:
    outer_bounds = entity_bounds(outer)
    inner_bounds = entity_bounds(inner)
    if (
        outer_bounds is None or inner_bounds is None
        or _bounds_area(outer_bounds) <= _bounds_area(inner_bounds) + _TOLERANCE
        or not _bounds_contain(outer_bounds, inner_bounds)
    ):
        return False
    samples = _outline_samples(inner)
    return bool(samples) and all(_contains_point(outer, point) for point in samples)


def _outline_samples(entity: Entity) -> list[Point]:
    if isinstance(entity, Circle):
        radius = entity.radius
        return [
            Point(entity.center.x + radius * math.cos(index * math.pi / 4), entity.center.y + radius * math.sin(index * math.pi / 4))
            for index in range(8)
        ]
    if isinstance(entity, Rectangle):
        min_x, max_x = sorted((entity.corner_a.x, entity.corner_b.x))
        min_y, max_y = sorted((entity.corner_a.y, entity.corner_b.y))
        return [Point(min_x, min_y), Point(max_x, min_y), Point(max_x, max_y), Point(min_x, max_y)]
    if isinstance(entity, Polyline):
        return list(entity.points)
    return []


def _contains_point(entity: Entity, point: Point) -> bool:
    if isinstance(entity, Circle):
        return entity.center.distance_to(point) <= entity.radius + _TOLERANCE
    if isinstance(entity, Rectangle):
        min_x, max_x = sorted((entity.corner_a.x, entity.corner_b.x))
        min_y, max_y = sorted((entity.corner_a.y, entity.corner_b.y))
        return min_x - _TOLERANCE <= point.x <= max_x + _TOLERANCE and min_y - _TOLERANCE <= point.y <= max_y + _TOLERANCE
    if isinstance(entity, Polyline):
        inside = False
        previous = entity.points[-1]
        for current in entity.points:
            if _point_on_segment(point, previous, current):
                return True
            if (current.y > point.y) != (previous.y > point.y):
                crossing_x = (previous.x - current.x) * (point.y - current.y) / (previous.y - current.y) + current.x
                if point.x < crossing_x:
                    inside = not inside
            previous = current
        return inside
    return False


def _point_on_segment(point, first, second):
    cross = (point.x - first.x) * (second.y - first.y) - (point.y - first.y) * (second.x - first.x)
    if abs(cross) > _TOLERANCE:
        return False
    dot = (point.x - first.x) * (second.x - first.x) + (point.y - first.y) * (second.y - first.y)
    length_squared = (second.x - first.x) ** 2 + (second.y - first.y) ** 2
    return -_TOLERANCE <= dot <= length_squared + _TOLERANCE


def _bounds_contain(outer, inner):
    return outer[0] <= inner[0] + _TOLERANCE and outer[1] <= inner[1] + _TOLERANCE and outer[2] >= inner[2] - _TOLERANCE and outer[3] >= inner[3] - _TOLERANCE


def _bounds_inside_plate(bounds, plate):
    return _bounds_contain((plate.x, plate.y, plate.x + plate.width, plate.y + plate.height), bounds)


def _bounds_area(bounds):
    return math.inf if bounds is None else max(0.0, bounds[2] - bounds[0]) * max(0.0, bounds[3] - bounds[1])
