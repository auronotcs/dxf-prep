from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, MutableMapping
import math

from .layout import entity_bounds
from .model import (
    CamProject,
    Circle,
    DrillCirclesOperation,
    EngraveOperation,
    DragKnifeOperation,
    IslandFillOperation,
    Line,
    OffsetSide,
    Point,
    Polyline,
    Rectangle,
    RouterOffsetOperation,
    inferred_sequence_role,
    ThreadMillOperation,
    RestMachiningOperation,
    ProfileRestMachiningOperation,
    ValidationIssue,
    effective_tool,
)
from .plates import PatchworkArea, Plate, validate_patchwork_areas
from .toolpath import (
    GeneratedToolpath,
    generate_closed_router_offset,
    generate_drill_circles,
    generate_drag_engraving,
    generate_open_router_offset,
    generate_rectangular_island_fill,
    generate_thread_mill,
    generate_rest_machining,
    generate_profile_rest_machining,
    router_offset_uses_open_geometry,
)
from .thread_specs import thread_standard
from .units import unit_scale
from .workflow import normalize_material, small_part_risks
from .workflow_features import (
    analyze_toolpath_completeness,
    entry_interference_violations,
    keepout_violations,
)
from .operation_links import profile_rest_dependency_status
from .optimization import (
    cycle_optimization_mode,
    cycle_optimization_settings,
    validate_cycle_optimization_settings,
)
from .topology import profile_containers


TOLERANCE = 1e-7
THICKNESS_ALLOWANCE_IN = 0.080


def run_preflight(
    project: CamProject,
    plates: Iterable[Plate] = (),
    *,
    material: str = "",
    material_thickness: float | None = None,
    small_part_max_area: float | None = None,
    small_part_max_span: float | None = None,
    machine_min_rpm: int | None = None,
    machine_max_rpm: int | None = None,
    station_max_rpm: Mapping[int, int] | None = None,
    spindle_disabled_stations: Iterable[int] = (),
    generated_paths_out: MutableMapping[str, GeneratedToolpath] | None = None,
    progress_callback: Callable[[str], None] | None = None,
    settings_only: bool = False,
    issue_callback: Callable[[list[ValidationIssue]], None] | None = None,
) -> list[ValidationIssue]:
    """Return deterministic machining-readiness issues without mutating the job."""
    def report(stage):
        if progress_callback is not None:
            progress_callback(stage)

    report("Checking job, stock, and operation settings")
    from .depth_intent import resolved_fill_project
    try:
        project = resolved_fill_project(project)
    except (ValueError, TypeError) as exc:
        return [ValidationIssue("error", "fill.depth_intent", str(exc))]
    issues = list(project.validate())
    from .profile_second_tool import retention_order_issues
    issues.extend(retention_order_issues(project))
    station_limits = dict(station_max_rpm or {})
    disabled_stations = set(spindle_disabled_stations)
    entities = {entity.id: entity for entity in project.entities}
    plate_list = list(plates)
    generated_paths: dict[str, object] = {}
    patchwork_by_plate: dict[int, list[PatchworkArea]] = {}
    patchwork_payload = project.metadata.get("patchwork_areas", [])
    if plate_list and isinstance(patchwork_payload, list) and patchwork_payload:
        try:
            patchwork_areas = [
                PatchworkArea.from_dict(payload)
                for payload in patchwork_payload
                if isinstance(payload, dict)
            ]
            if len(patchwork_areas) != len(patchwork_payload):
                raise ValueError("Every saved Patchwork region must be a complete object record.")
            validate_patchwork_areas(patchwork_areas, plate_list)
        except (KeyError, TypeError, ValueError) as exc:
            issues.append(ValidationIssue(
                "error", "patchwork.invalid",
                f"Patchwork material regions are invalid: {exc}",
            ))
        else:
            for area in patchwork_areas:
                patchwork_by_plate.setdefault(area.plate_number, []).append(area)
    manufacturing_entity_ids = {
        entity_id
        for operation in project.operations
        if operation.enabled
        for entity_id in (
            *operation.entity_ids,
            *getattr(operation, "island_entity_ids", ()),
        )
    }
    ignored_cut_line_entity_ids: set[str] = set()
    simplified_cut_line_entity_ids: set[str] = set()
    for operation in project.operations:
        if (
            not operation.enabled
            or not isinstance(operation, RouterOffsetOperation)
            or not router_offset_uses_open_geometry(project, operation)
        ):
            continue
        for entity_id in operation.entity_ids:
            entity = entities.get(entity_id)
            if isinstance(entity, Line):
                if entity.start.distance_to(entity.end) <= TOLERANCE:
                    ignored_cut_line_entity_ids.add(entity_id)
                continue
            if not isinstance(entity, Polyline) or entity.closed:
                continue
            cleaned: list[Point] = []
            removed = False
            for point in entity.points:
                if cleaned and cleaned[-1].distance_to(point) <= TOLERANCE:
                    removed = True
                    continue
                cleaned.append(point)
            if len(cleaned) < 2:
                ignored_cut_line_entity_ids.add(entity_id)
            elif removed:
                simplified_cut_line_entity_ids.add(entity_id)

    if not project.operations:
        issues.append(ValidationIssue("warning", "operation.none", "The job has no machining operations."))

    cycle_settings = cycle_optimization_settings(project)
    try:
        validate_cycle_optimization_settings(cycle_settings)
    except ValueError as exc:
        issues.append(ValidationIssue("error", "cycle.settings", f"Cycle-time optimizer settings are invalid: {exc}"))
    if cycle_settings.enabled:
        enabled = []
        if cycle_settings.depth_linking:
            enabled.append("depth linking")
        if cycle_settings.minimum_retract:
            enabled.append("low retracts")
        if cycle_settings.profile_ordering or cycle_settings.drill_ordering or cycle_settings.engraving_ordering:
            enabled.append("travel ordering")
        if cycle_settings.motion_compaction:
            enabled.append("motion compaction")
        if cycle_optimization_mode(project) != "production":
            issues.append(ValidationIssue(
                "warning", "cycle.optimizer_enabled",
                "Custom cycle-time optimization is enabled"
                + (f" ({', '.join(enabled)})" if enabled else "")
                + "; compare the generated plan and qualify changed motion on scrap before production.",
            ))
        if cycle_settings.arc_fitting:
            issues.append(ValidationIssue(
                "warning", "cycle.arc_fitting",
                f"Arc fitting may replace short line segments with G02/G03 within {cycle_settings.arc_fitting_tolerance:g} {project.units}; verify controller motion and dimensional tolerance.",
            ))
        if cycle_settings.minimum_retract and not any(zone.enabled for zone in project.keepouts):
            issues.append(ValidationIssue(
                "warning", "cycle.keepouts_missing",
                "Low retracts are enabled but this job has no clamp/keepout zones; define every fixture above the material before relying on stay-low travel.",
            ))

    for entity in project.entities:
        # Geometry cleanup outside enabled operations belongs in Inspect, not
        # Output Problems. Leftover drafting marks cannot enter controller
        # output and must not block an otherwise valid recut.
        if entity.id not in manufacturing_entity_ids:
            continue
        if entity.id in ignored_cut_line_entity_ids:
            bounds = entity_bounds(entity)
            point = (
                Point((bounds[0] + bounds[2]) / 2.0, (bounds[1] + bounds[3]) / 2.0)
                if bounds is not None else None
            )
            issues.append(ValidationIssue(
                "warning", "cut_line.degenerate_ignored",
                f"Cut Line ignores zero-length source geometry {entity.id}; it will not enter controller output. "
                "Delete or repair the remnant when convenient.",
                (entity.id,), point,
            ))
            continue
        if isinstance(entity, Line) and entity.start.distance_to(entity.end) <= TOLERANCE:
            issues.append(ValidationIssue("error", "geometry.zero_length", f"Line {entity.id} has zero length."))
        elif isinstance(entity, Circle) and entity.radius <= TOLERANCE:
            issues.append(ValidationIssue("error", "geometry.zero_radius", f"Circle {entity.id} has zero radius."))
        elif isinstance(entity, Polyline):
            if len(entity.points) < (3 if entity.closed else 2):
                issues.append(ValidationIssue("error", "geometry.too_few_points", f"Polyline {entity.id} has too few points."))
            if entity.id in simplified_cut_line_entity_ids:
                bounds = entity_bounds(entity)
                point = (
                    Point((bounds[0] + bounds[2]) / 2.0, (bounds[1] + bounds[3]) / 2.0)
                    if bounds is not None else None
                )
                issues.append(ValidationIssue(
                    "warning", "cut_line.zero_segment_removed",
                    f"Cut Line removes repeated adjacent points from source geometry {entity.id} before output. "
                    "Review or repair the source contour when convenient.",
                    (entity.id,), point,
                ))
            elif any(a.distance_to(b) <= TOLERANCE for a, b in zip(entity.points, entity.points[1:])):
                issues.append(ValidationIssue("error", "geometry.zero_segment", f"Polyline {entity.id} contains a zero-length segment."))
            if entity.closed and _self_intersects(entity.points):
                issues.append(ValidationIssue("error", "geometry.self_intersection", f"Closed contour {entity.id} self-intersects."))

    if plate_list:
        for entity in project.entities:
            # Plate containment is an output-safety rule, not a drawing-cleanup
            # rule. Unused artwork may remain elsewhere in a multi-plate file;
            # only geometry feeding an enabled operation can enter CNC output.
            if entity.id not in manufacturing_entity_ids:
                continue
            bounds = entity_bounds(entity)
            if bounds is None:
                continue
            containing_plate = next(
                (plate for plate in plate_list if _bounds_inside(bounds, plate.bounds)),
                None,
            )
            if containing_plate is None:
                severity = "error" if any(_bounds_intersect(bounds, plate.bounds) for plate in plate_list) else "warning"
                code = "plate.crossing" if severity == "error" else "plate.outside"
                message = (
                    f"Geometry {entity.id} crosses a plate boundary."
                    if severity == "error"
                    else f"Geometry {entity.id} is outside every plate."
                )
                min_x, min_y, max_x, max_y = bounds
                issues.append(ValidationIssue(
                    severity,
                    code,
                    message,
                    entity_ids=(entity.id,),
                    point=Point((min_x + max_x) / 2, (min_y + max_y) / 2),
                ))
                continue
            physical_areas = patchwork_by_plate.get(containing_plate.number, [])
            if physical_areas:
                world_bounds = [
                    (
                        containing_plate.x + area.x,
                        containing_plate.y + area.y,
                        containing_plate.x + area.x + area.width,
                        containing_plate.y + area.y + area.height,
                    )
                    for area in physical_areas
                ]
                if not any(_bounds_inside(bounds, area_bounds) for area_bounds in world_bounds):
                    crosses_material_edge = any(
                        _bounds_intersect(bounds, area_bounds) for area_bounds in world_bounds
                    )
                    min_x, min_y, max_x, max_y = bounds
                    issues.append(ValidationIssue(
                        "error",
                        "patchwork.crossing" if crosses_material_edge else "patchwork.gap",
                        (
                            f"Geometry {entity.id} crosses a Patchwork material edge on Plate {containing_plate.number}."
                            if crosses_material_edge
                            else f"Geometry {entity.id} is in a Patchwork gap with no physical material on Plate {containing_plate.number}."
                        ),
                        entity_ids=(entity.id,),
                        point=Point((min_x + max_x) / 2, (min_y + max_y) / 2),
                    ))

    for operation in project.operations:
        if not operation.enabled:
            continue
        tool = effective_tool(project, operation)
        if tool is None:
            continue
        if operation.tool_station in disabled_stations and not isinstance(operation, (EngraveOperation, DragKnifeOperation)):
            issues.append(ValidationIssue("error", "station.spindle_disabled", f"{operation.name} uses spindle-disabled T{operation.tool_station}."))
        if tool.spindle_mode == "disabled" and isinstance(operation, (RouterOffsetOperation, IslandFillOperation, DrillCirclesOperation, ThreadMillOperation, RestMachiningOperation, ProfileRestMachiningOperation)):
            issues.append(ValidationIssue("error", "tool.spindle_disabled", f"{operation.name} uses spindle-disabled tool T{tool.station}."))
        if tool.spindle_mode == "required" and tool.spindle_rpm is None:
            issues.append(ValidationIssue("error", "tool.rpm_missing", f"{operation.name} requires an RPM for T{tool.station}."))
        if tool.spindle_rpm is not None:
            if machine_min_rpm is not None and tool.spindle_rpm < machine_min_rpm:
                issues.append(ValidationIssue("error", "machine.rpm_low", f"T{tool.station} RPM is below the machine minimum."))
            maximums = [value for value in (machine_max_rpm, station_limits.get(tool.station), tool.max_rpm) if value is not None]
            if maximums and tool.spindle_rpm > min(maximums):
                issues.append(ValidationIssue("error", "machine.rpm_high", f"T{tool.station} RPM exceeds a machine, station, or tool maximum."))
        if tool.feed_rate is None or tool.feed_rate <= 0:
            issues.append(ValidationIssue("error", "tool.feed_missing", f"{operation.name} requires a positive cutting feed for T{tool.station}."))
        if tool.plunge_rate is None or tool.plunge_rate <= 0:
            issues.append(ValidationIssue("error", "tool.plunge_missing", f"{operation.name} requires a positive plunge feed for T{tool.station}."))
        if tool.cutting_edge_length is not None and operation.target_depth > tool.cutting_edge_length + TOLERANCE:
            issues.append(ValidationIssue("error", "tool.cel_exceeded", f"{operation.name} depth exceeds T{tool.station} cutting-edge length."))
        operation_stepdown = getattr(operation, "stepdown", None)
        if operation_stepdown is not None and tool.max_stepdown is not None and operation_stepdown > tool.max_stepdown + TOLERANCE:
            issues.append(ValidationIssue(
                "warning", "tool.stepdown_exceeded",
                f"{operation.name} stepdown exceeds the T{tool.station} loaded recipe recommendation; verify cutter engagement, part size, and hold-down.",
            ))
        compatible = {normalize_material(str(value)) for value in tool.metadata.get("compatible_materials", [])}
        requested_material = normalize_material(material)
        if material and compatible and "anything" not in compatible and requested_material not in compatible:
            issues.append(ValidationIssue("warning", "tool.material", f"T{tool.station} is not marked compatible with {material}."))
        recipe = operation.recipe_metadata or tool.metadata.get("loaded_recipe", {})
        if isinstance(recipe, dict) and recipe:
            qualification = str(recipe.get("qualification", "prototype"))
            if qualification in {"archive_observed", "prototype"}:
                issues.append(ValidationIssue("warning", "recipe.unqualified", f"{operation.name} uses a {qualification.replace('_', ' ')} recipe rather than a shop-tested or production-approved recipe."))
            elif qualification == "retired":
                issues.append(ValidationIssue("error", "recipe.retired", f"{operation.name} uses a retired recipe."))
            roles = recipe.get("process_roles", ["any"])
            if isinstance(roles, str):
                roles = [item.strip() for item in roles.split(",") if item.strip()]
            actual_role = inferred_sequence_role(operation)
            if roles and "any" not in roles and actual_role not in roles:
                issues.append(ValidationIssue("warning", "recipe.process_role", f"{operation.name} is {actual_role.replace('_', ' ')}, but its loaded recipe is intended for {', '.join(roles)}."))
            if material_thickness is not None:
                minimum, maximum = recipe.get("thickness_min"), recipe.get("thickness_max")
                thickness_allowance = THICKNESS_ALLOWANCE_IN * unit_scale("in", project.units)
                below_range = minimum is not None and material_thickness < float(minimum) - thickness_allowance - TOLERANCE
                above_range = maximum is not None and material_thickness > float(maximum) + thickness_allowance + TOLERANCE
                if below_range or above_range:
                    issues.append(ValidationIssue(
                        "warning", "recipe.thickness",
                        f"{operation.name} material thickness is more than {thickness_allowance:g} {project.units} outside its loaded recipe range.",
                    ))
        for entity_id in operation.entity_ids:
            entity = entities.get(entity_id)
            if isinstance(operation, (DrillCirclesOperation, ThreadMillOperation)) and entity is not None and not isinstance(entity, Circle):
                issues.append(ValidationIssue("error", "drill.non_circle", f"{operation.name} references non-circle geometry {entity_id}."))
        if isinstance(operation, IslandFillOperation):
            outer = [entities.get(entity_id) for entity_id in operation.entity_ids]
            islands = [entities.get(entity_id) for entity_id in operation.island_entity_ids]
            is_closed = lambda entity: isinstance(entity, (Circle, Rectangle)) or isinstance(entity, Polyline) and entity.closed
            if not outer or any(not is_closed(entity) for entity in outer):
                issues.append(ValidationIssue(
                    "error", "fill.outer",
                    f"{operation.name} requires one or more closed outer circles, rectangles, or polylines.",
                ))
            if any(not is_closed(entity) for entity in islands):
                issues.append(ValidationIssue("error", "fill.islands", f"{operation.name} islands must be closed circles, rectangles, or polylines."))
            if operation.cut_direction not in {"climb", "conventional", "bidirectional"}:
                issues.append(ValidationIssue("error", "fill.direction", f"{operation.name} has an invalid cleanout direction."))
            if operation.entry_mode not in {"plunge", "ramp", "helical"}:
                issues.append(ValidationIssue("error", "fill.entry", f"{operation.name} has an invalid entry mode."))
            if operation.helix_fallback not in {"ramp", "plunge", "error"}:
                issues.append(ValidationIssue("error", "fill.helix_fallback", f"{operation.name} has an invalid helical-entry fallback."))
            if operation.entry_mode == "helical" and (operation.helix_diameter <= 0 or operation.helix_pitch <= 0):
                issues.append(ValidationIssue("error", "fill.helix", f"{operation.name} helical entry requires a positive diameter and pitch per revolution."))
            if operation.helix_feed_override is not None and operation.helix_feed_override <= 0:
                issues.append(ValidationIssue("error", "fill.helix_feed", f"{operation.name} helical feed must be positive."))
            if operation.helix_min_diameter < 0 or operation.helix_min_diameter > operation.helix_diameter + TOLERANCE:
                issues.append(ValidationIssue("error", "fill.helix_minimum", f"{operation.name} minimum helix diameter must be nonnegative and no larger than its preferred diameter."))
            if not 0 <= operation.helix_ramp_angle < 90 or operation.helix_clearance < 0 or operation.helix_finish_revolutions < 0:
                issues.append(ValidationIssue("error", "fill.helix_limits", f"{operation.name} has invalid helix angle, wall clearance, or finish-revolution controls."))
            if operation.keep_tool_down and operation.stay_down_max_distance <= 0:
                issues.append(ValidationIssue("error", "fill.stay_down_distance", f"{operation.name} Keep Tool Down requires a positive link-distance limit."))
            if operation.keep_tool_down and (operation.stay_down_max_engagement <= 0 or operation.stay_down_max_engagement > tool.diameter + TOLERANCE):
                issues.append(ValidationIssue("error", "fill.stay_down_engagement", f"{operation.name} Keep Tool Down engagement must be positive and no larger than T{tool.station}'s cutter diameter."))
            if not 0 < operation.stay_down_feed_percent <= 100:
                issues.append(ValidationIssue("error", "fill.stay_down_feed", f"{operation.name} Keep Tool Down feed must be above 0% and no more than 100%."))
            allowed_entries = recipe.get("entry_strategies", []) if isinstance(recipe, dict) else []
            if allowed_entries and operation.entry_mode not in allowed_entries:
                issues.append(ValidationIssue("warning", "recipe.entry", f"{operation.name} uses {operation.entry_mode}, which is not enabled by its loaded recipe."))
        if isinstance(operation, DrillCirclesOperation):
            if operation.peck_mode not in {"none", "chip_break", "full_clear"}:
                issues.append(ValidationIssue("error", "drill.peck_mode", f"{operation.name} has an invalid peck mode."))
            if operation.retract_height <= 0 or operation.reentry_clearance < 0:
                issues.append(ValidationIssue("error", "drill.retract", f"{operation.name} retract height must be positive and re-entry clearance cannot be negative."))
            if operation.dwell_seconds or operation.top_dwell_seconds:
                issues.append(ValidationIssue(
                    "error", "drill.dwell_unqualified",
                    f"{operation.name} requests dwell, but the verified EnRoute controller dialect has no qualified dwell command; use zero dwell for output.",
                ))
        if isinstance(operation, DragKnifeOperation):
            from .drag_knife import validate_knife_setup
            try:
                validate_knife_setup(project, operation)
            except ValueError as exc:
                issues.append(ValidationIssue('error','knife.setup',str(exc),tuple(operation.entity_ids)))
            if not operation.setup_verified:
                issues.append(ValidationIssue('error','knife.review','Review the knife alignment leads, holder clearance and spindle-disabled mounting before output.',tuple(operation.entity_ids)))
            if operation.tool_station != 8:
                issues.append(ValidationIssue('error','knife.station','The current EnRoute post qualifies drag tools only on spindle-disabled T8.'))
            issues.append(ValidationIssue('warning','knife.prototype','Prototype drag-knife path: verify waste-side alignment leads, blade calibration, clamp clearance and hold-down on a test coupon.',tuple(operation.entity_ids)))
        if isinstance(operation, EngraveOperation):
            if tool.spindle_mode != "disabled" or tool.spindle_rpm is not None:
                issues.append(ValidationIssue("error", "engrave.spindle", f"{operation.name} requires a spindle-disabled tool with no RPM."))
            if tool.kind.value not in {"engraver", "other"}:
                issues.append(ValidationIssue("error", "engrave.tool_kind", f"{operation.name} requires an engraver tool in T{tool.station}."))
            if operation.approach_height <= 0 or operation.safe_z <= operation.approach_height:
                issues.append(ValidationIssue("error", "engrave.approach", f"{operation.name} safe Z must be above its positive approach height."))
            travel = tool.metadata.get("spring_travel")
            if travel is not None and operation.target_depth > float(travel) + TOLERANCE:
                issues.append(ValidationIssue("error", "engrave.travel", f"{operation.name} compression exceeds T{tool.station} spring travel."))
            if operation.require_stay_on_material and plate_list:
                # Loaded drag tips need clearance from real stock drop-offs,
                # not a milling envelope around nearby engraving outlines.
                margin = 0.25 * unit_scale('in', project.units)
                stock_regions = []
                for plate in plate_list:
                    areas = patchwork_by_plate.get(plate.number, [])
                    stock_regions.extend(
                        [(plate.x+a.x, plate.y+a.y, plate.x+a.x+a.width, plate.y+a.y+a.height) for a in areas]
                        if areas else [plate.bounds])
                for entity_id in operation.entity_ids:
                    entity = entities.get(entity_id)
                    bounds = entity_bounds(entity) if entity is not None else None
                    if bounds is None:
                        continue
                    containing = [region for region in stock_regions if _bounds_inside(bounds, region)]
                    point = Point((bounds[0]+bounds[2])/2, (bounds[1]+bounds[3])/2)
                    if not containing:
                        issues.append(ValidationIssue("error", "engrave.off_material",
                            f"{operation.name} contour {entity_id} is not fully on stock; the loaded drag tip must stay at least {margin:g} {project.units} inside the plate/material edge.",
                            (entity_id,), point))
                    elif not any(_bounds_inside(bounds, (r[0]+margin, r[1]+margin, r[2]-margin, r[3]-margin)) for r in containing):
                        clearance = max(min(bounds[0]-r[0], bounds[1]-r[1], r[2]-bounds[2], r[3]-bounds[3]) for r in containing)
                        issues.append(ValidationIssue("error", "engrave.edge_clearance",
                            f"{operation.name} contour {entity_id} comes within {max(0,clearance):.4g} {project.units} of a plate/material edge. Keep the engraving tip at least {margin:g} {project.units} inside the edge to avoid a drop-off.",
                            (entity_id,), point))
        if isinstance(operation, RouterOffsetOperation):
            if operation.tab_mode == "aligned":
                from .aligned_tabs import prepare_aligned_tabs
                try:
                    prepare_aligned_tabs(operation, [entities.get(value) for value in operation.entity_ids])
                except (ValueError, TypeError, OverflowError) as exc:
                    issues.append(ValidationIssue("error", "router.aligned_tabs", f"{operation.name}: {exc}", tuple(operation.entity_ids)))
            if operation.contour_order not in {"inside_first", "part_by_part", "source_order"}:
                issues.append(ValidationIssue("error", "router.contour_order", f"{operation.name} has an invalid grouped contour order."))
            elif operation.contour_order == "source_order" and len(set(operation.entity_sides.values())) > 1:
                issues.append(ValidationIssue(
                    "warning", "router.source_contour_order",
                    f"{operation.name} follows literal DXF / selection order for mixed male and female contours; an outside profile may release before its internal features.",
                ))
            manual_order = operation.manual_contour_order
            if manual_order:
                profile_entities = [entities[value] for value in operation.entity_ids if value in entities]
                profile_ids = {entity.id for entity in profile_entities}
                if (
                    not isinstance(manual_order, list)
                    or len(manual_order) != len(profile_ids)
                    or len(set(manual_order)) != len(manual_order)
                    or set(manual_order) != profile_ids
                ):
                    issues.append(ValidationIssue(
                        "warning", "router.manual_contour_order_stale",
                        f"{operation.name}'s saved manual contour order no longer matches its geometry; CAM will use safe nested order instead.",
                        tuple(operation.entity_ids),
                    ))
                else:
                    positions = {entity_id: index for index, entity_id in enumerate(manual_order)}
                    violations = [
                        (entity_id, container_id)
                        for entity_id, container_ids in profile_containers(profile_entities).items()
                        for container_id in container_ids
                        if positions[container_id] < positions[entity_id]
                    ]
                    if violations:
                        affected = tuple(dict.fromkeys(value for pair in violations for value in pair))
                        issues.append(ValidationIssue(
                            "warning", "router.manual_contour_order_unsafe",
                            f"{operation.name}'s manual cut order schedules {len(violations)} containing contour(s) before nested geometry. Review Inspect > Cut Order before output.",
                            affected,
                        ))
            if operation.cut_intent not in {"custom", "through", "partial"}:
                issues.append(ValidationIssue("error", "router.cut_intent", f"{operation.name} has an invalid cut intent."))
            if operation.breakthrough < 0:
                issues.append(ValidationIssue("error", "router.breakthrough", f"{operation.name} breakthrough cannot be negative."))
            if material_thickness is not None and inferred_sequence_role(operation) in {"inside_profile", "outside_profile", "finish_cutoff"}:
                thickness_allowance = THICKNESS_ALLOWANCE_IN * unit_scale("in", project.units)
                if abs(operation.target_depth - material_thickness) > thickness_allowance + TOLERANCE:
                    issues.append(ValidationIssue(
                        "warning", "router.depth_vs_stock",
                        f"{operation.name} depth differs from stock thickness by more than {thickness_allowance:g} {project.units}; verify the intended through-cut or partial-depth operation.",
                    ))
            if operation.cut_direction not in {"climb", "conventional"}:
                issues.append(ValidationIssue("error", "router.direction", f"{operation.name} has an invalid cut direction."))
            if operation.entry_mode not in {"plunge", "ramp", "helical"}:
                issues.append(ValidationIssue("error", "router.entry", f"{operation.name} has an invalid entry mode."))
            if operation.curve_feed_mode not in {"off", "wall_compensation"}:
                issues.append(ValidationIssue("error", "router.curve_feed_mode", f"{operation.name} has an invalid curve-feed mode."))
            if not 0 < operation.curve_feed_min_percent <= 100 or operation.curve_feed_transition_distance <= 0:
                issues.append(ValidationIssue("error", "router.curve_feed_limits", f"{operation.name} curve feed requires a 0-100% minimum and positive transition distance."))
            if operation.common_line_mode not in {"off", "paired_rectangles"} or operation.common_line_tolerance <= 0:
                issues.append(ValidationIssue("error", "router.common_line_mode", f"{operation.name} has invalid common-line controls."))
            if operation.entry_side not in {"automatic_waste", "inside", "outside"}:
                issues.append(ValidationIssue("error", "router.entry_side", f"{operation.name} has an invalid helical entry side."))
            if operation.helix_fallback not in {"waste_plunge", "strict_error"}:
                issues.append(ValidationIssue("error", "router.helix_fallback", f"{operation.name} has an invalid helical-entry fallback."))
            if operation.entry_mode == "helical" and (operation.helix_diameter <= 0 or operation.helix_pitch <= 0):
                issues.append(ValidationIssue("error", "router.helix", f"{operation.name} helical entry requires a positive diameter and pitch per revolution."))
            if operation.helix_feed_override is not None and operation.helix_feed_override <= 0:
                issues.append(ValidationIssue("error", "router.helix_feed", f"{operation.name} helical feed must be positive."))
            if operation.helix_min_diameter < 0 or operation.helix_min_diameter > operation.helix_diameter + TOLERANCE:
                issues.append(ValidationIssue("error", "router.helix_minimum", f"{operation.name} minimum helix diameter must be nonnegative and no larger than its preferred diameter."))
            if not 0 <= operation.helix_ramp_angle < 90 or operation.helix_clearance < 0 or operation.helix_finish_revolutions < 0:
                issues.append(ValidationIssue("error", "router.helix_limits", f"{operation.name} has invalid helix angle, wall clearance, or finish-revolution controls."))
            if operation.entry_mode == "helical" and operation.entry_side != "automatic_waste":
                issues.append(ValidationIssue(
                    "warning", "router.helix_side_override",
                    f"{operation.name} manually overrides the helical entry side; verify that every entry is in disposable stock, especially on mixed male/female cut objects.",
                ))
                resolved_sides = {
                    operation.entity_sides.get(entity_id, operation.side.value)
                    for entity_id in operation.entity_ids
                }
                conflicting_side = (
                    operation.entry_side == "inside" and OffsetSide.OUTSIDE.value in resolved_sides
                    or operation.entry_side == "outside" and OffsetSide.INSIDE.value in resolved_sides
                )
                if conflicting_side:
                    issues.append(ValidationIssue(
                        "error", "router.helix_side_conflict",
                        f"{operation.name} forces its helix toward retained material for at least one resolved contour side. "
                        "Use Automatic waste or separate the conflicting contours into another operation.",
                        tuple(operation.entity_ids),
                    ))
            if operation.entry_mode == "helical" and tool is not None and operation.helix_diameter > tool.diameter + TOLERANCE:
                issues.append(ValidationIssue(
                    "warning", "router.helix_large_diameter",
                    f"{operation.name} helix diameter exceeds the T{tool.station} cutter diameter and may leave an uncut center boss; qualify the entry on scrap.",
                ))
            allowed_entries = recipe.get("entry_strategies", []) if isinstance(recipe, dict) else []
            if allowed_entries and operation.entry_mode not in allowed_entries:
                issues.append(ValidationIssue("warning", "recipe.entry", f"{operation.name} uses {operation.entry_mode}, which is not enabled by its loaded recipe."))
            if min(operation.lead_in, operation.lead_out, operation.onion_skin, operation.finish_axial_stock, operation.tab_width, operation.tab_height, operation.female_tab_min_perimeter, operation.tab_cleanup_min_perimeter, operation.helix_diameter, operation.helix_min_diameter, operation.helix_pitch, operation.helix_ramp_angle, operation.helix_clearance, operation.helix_finish_revolutions) < 0:
                issues.append(ValidationIssue("error", "router.negative_control", f"{operation.name} has a negative lead, onion-skin, or tab value."))
            if operation.onion_skin >= operation.target_depth and operation.onion_skin > 0:
                issues.append(ValidationIssue("error", "router.onion_skin", f"{operation.name} onion skin must be smaller than final depth."))
            if operation.finishing_pass and operation.finish_axial_stock >= operation.target_depth:
                issues.append(ValidationIssue("error", "router.finish_axial_stock", f"{operation.name} axial stock to leave must be smaller than final depth."))
            if operation.finish_feed_override is not None and operation.finish_feed_override <= 0:
                issues.append(ValidationIssue("error", "router.finish_feed", f"{operation.name} finishing feed must be positive."))
            finish_rpm = operation.finish_spindle_rpm_override
            if finish_rpm is not None:
                if finish_rpm <= 0:
                    issues.append(ValidationIssue("error", "router.finish_rpm", f"{operation.name} finishing RPM must be positive."))
                elif not operation.finishing_pass:
                    issues.append(ValidationIssue("warning", "router.finish_rpm_unused", f"{operation.name} has a finishing RPM but no finishing pass; the alternate RPM will not be used."))
                else:
                    if machine_min_rpm is not None and finish_rpm < machine_min_rpm:
                        issues.append(ValidationIssue("error", "machine.finish_rpm_low", f"{operation.name} finishing RPM is below the machine minimum."))
                    finish_maximums = [value for value in (machine_max_rpm, station_limits.get(tool.station), tool.max_rpm) if value is not None]
                    if finish_maximums and finish_rpm > min(finish_maximums):
                        issues.append(ValidationIssue("error", "machine.finish_rpm_high", f"{operation.name} finishing RPM exceeds a machine, station, or tool maximum."))
            finish_reserve = operation.finish_axial_stock if operation.finishing_pass else 0.0
            rough_depth = operation.target_depth - max(operation.onion_skin, finish_reserve)
            if any(value <= 0 for value in operation.pass_depths) or sum(operation.pass_depths) > rough_depth + 1e-9:
                issues.append(ValidationIssue("error", "router.pass_schedule", f"{operation.name} explicit pass increments must be positive and cannot exceed the depth above the onion skin."))
            has_tabs = operation.tab_count > 0 or operation.tab_mode in {"minimum_and_spacing", "minimum_avoid_corners", "support_aware", "corners_and_spacing", "manual", "aligned"}
            if operation.tab_mode not in {"even", "minimum_and_spacing", "minimum_avoid_corners", "support_aware", "corners_and_spacing", "manual", "aligned"} or operation.tab_count < 0 or has_tabs and (operation.tab_width <= 0 or operation.tab_height <= 0 or operation.tab_height >= operation.tab_reference_depth):
                issues.append(ValidationIssue("error", "router.tabs", f"{operation.name} tabs require a positive count/width and a height below final depth."))
            if operation.tab_mode in {"minimum_and_spacing", "minimum_avoid_corners", "support_aware", "corners_and_spacing", "aligned"} and operation.tab_spacing <= 0:
                issues.append(ValidationIssue("error", "router.tab_spacing", f"{operation.name} maximum tab spacing must be positive."))
            if operation.tab_mode == "corners_and_spacing" and operation.corner_tab_offset <= 0:
                issues.append(ValidationIssue("error", "router.corner_tabs", f"{operation.name} corner-tab distance must be positive."))
            if operation.remove_tabs_after and not has_tabs:
                issues.append(ValidationIssue("error", "router.tab_cleanup", f"{operation.name} tab cleanup requires enabled tabs."))
            if operation.common_line_mode != "off" and (
                operation.side != OffsetSide.OUTSIDE
                or abs(operation.allowance) > TOLERANCE
                or operation.entry_mode != "plunge"
                or operation.lead_in > TOLERANCE
                or operation.lead_out > TOLERANCE
                or has_tabs
            ):
                issues.append(ValidationIssue(
                    "error", "router.common_line_incompatible",
                    f"{operation.name} common-line pairing requires an outside plunge cut with zero allowance/leads and no tabs.",
                ))
            if operation.small_part_strategy == "tabs" and not has_tabs:
                issues.append(ValidationIssue("warning", "hold_down.tabs_missing", f"{operation.name} is marked for tab hold-down but has no tabs configured."))
            if operation.small_part_strategy == "onion_skin" and operation.onion_skin <= 0:
                issues.append(ValidationIssue("warning", "hold_down.onion_missing", f"{operation.name} is marked for onion-skin hold-down but has no onion skin configured."))
        if isinstance(operation, ThreadMillOperation):
            try:
                dimensions = thread_standard(operation.thread_designation).in_units(project.units)
            except ValueError as exc:
                issues.append(ValidationIssue("error", "thread.standard", f"{operation.name}: {exc}"))
                dimensions = None
            if tool.kind.value != "thread_mill":
                issues.append(ValidationIssue("error", "thread.tool_kind", f"{operation.name} requires a thread_mill tool in T{tool.station}."))
            qualified_tpi = {
                int(value.strip()) for value in str(tool.metadata.get("thread_tpi", "")).split(",")
                if value.strip().isdigit()
            }
            if dimensions is not None and qualified_tpi and dimensions.standard.tpi not in qualified_tpi:
                issues.append(ValidationIssue("error", "thread.tool_tpi", f"T{tool.station} is not cataloged for {dimensions.standard.tpi} TPI."))
            if not qualified_tpi:
                issues.append(ValidationIssue("warning", "thread.tool_tpi_unlisted", f"T{tool.station} has no cataloged TPI list; rely on the explicit cutter verification."))
            thread_form = str(tool.metadata.get("thread_form", "")).casefold()
            if thread_form and ("60" not in thread_form or "un" not in thread_form):
                issues.append(ValidationIssue("error", "thread.tool_form", f"T{tool.station} is not cataloged for a UN 60 degree thread form."))
            if not operation.cutter_verified:
                issues.append(ValidationIssue("error", "thread.cutter_unverified", f"{operation.name} cutter pitch/profile compatibility has not been verified."))
            if operation.radial_passes < 1 or operation.spring_passes < 0:
                issues.append(ValidationIssue("error", "thread.passes", f"{operation.name} has invalid radial or spring passes."))
            if operation.bottom_clearance < 0:
                issues.append(ValidationIssue("error", "thread.clearance", f"{operation.name} bottom clearance cannot be negative."))
            if operation.pilot_hole_depth is None:
                issues.append(ValidationIssue("error", "thread.pilot_depth", f"{operation.name} requires a verified pilot-hole depth."))
            elif operation.pilot_hole_depth + TOLERANCE < operation.target_depth + operation.bottom_clearance:
                issues.append(ValidationIssue("error", "thread.pilot_depth", f"{operation.name} pilot hole is not deep enough for thread depth plus bottom clearance."))
            if dimensions is not None and operation.target_depth + TOLERANCE < dimensions.pitch:
                issues.append(ValidationIssue("error", "thread.depth", f"{operation.name} depth must contain at least one full thread pitch."))
        if isinstance(operation, RestMachiningOperation):
            source = next((item for item in project.operations if item.id == operation.source_operation_id), None)
            if not isinstance(source, IslandFillOperation):
                issues.append(ValidationIssue("error", "rest.source", f"{operation.name} requires an existing island-fill source operation."))
            else:
                source_index = project.operations.index(source)
                rest_index = project.operations.index(operation)
                if not source.enabled:
                    issues.append(ValidationIssue("error", "rest.source_disabled", f"{operation.name} source operation is disabled."))
                if source_index >= rest_index:
                    issues.append(ValidationIssue("error", "rest.order", f"{operation.name} must run after its source operation."))
                source_tool = project.tool_library.by_station(source.tool_station)
                if source_tool is not None and tool.diameter >= source_tool.diameter - TOLERANCE:
                    issues.append(ValidationIssue("error", "rest.tool_size", f"{operation.name} tool must be smaller than source T{source.tool_station}."))
                if operation.target_depth > source.target_depth + TOLERANCE:
                    issues.append(ValidationIssue("error", "rest.depth", f"{operation.name} cannot cut deeper than its source operation."))
            if operation.stock_to_leave < 0 or operation.ignore_stock_less_than < 0:
                issues.append(ValidationIssue("error", "rest.threshold", f"{operation.name} stock thresholds cannot be negative."))
        if isinstance(operation, ProfileRestMachiningOperation):
            source = next((item for item in project.operations if item.id == operation.source_operation_id), None)
            if not isinstance(source, RouterOffsetOperation) or router_offset_uses_open_geometry(project, source):
                issues.append(ValidationIssue("error", "profile_rest.source", f"{operation.name} requires an existing closed router-offset source operation."))
            else:
                source_index = project.operations.index(source)
                rest_index = project.operations.index(operation)
                if not source.enabled:
                    issues.append(ValidationIssue("error", "profile_rest.source_disabled", f"{operation.name} source operation is disabled."))
                if source_index >= rest_index:
                    issues.append(ValidationIssue("error", "profile_rest.order", f"{operation.name} must run after its source profile."))
                source_tool = project.tool_library.by_station(source.tool_station)
                if source_tool is not None and tool.diameter >= source_tool.diameter - TOLERANCE:
                    issues.append(ValidationIssue("error", "profile_rest.tool_size", f"{operation.name} tool must be smaller than source T{source.tool_station}."))
                if operation.target_depth > source.target_depth + TOLERANCE:
                    issues.append(ValidationIssue("error", "profile_rest.depth", f"{operation.name} cannot cut deeper than its source profile."))
                if source.remove_tabs_after:
                    issues.append(ValidationIssue("error", "profile_rest.release_order", f"{operation.name} source removes tabs before corner rest; move tab cleanup to the profile-rest operation."))
                dependency = profile_rest_dependency_status(project, operation)
                if not dependency.current:
                    issues.append(ValidationIssue(
                        "error", f"profile_rest.dependency_{dependency.state}", dependency.message,
                        tuple(operation.entity_ids),
                    ))
                    # Preserve the specific, actionable dependency error rather
                    # than repeating it below as a generic generation failure.
                    continue
            if operation.stock_to_leave < 0 or operation.ignore_stock_less_than < 0:
                issues.append(ValidationIssue("error", "profile_rest.threshold", f"{operation.name} stock thresholds cannot be negative."))
        if settings_only:
            continue
        generated_path = None
        report(f"Generating operation {len(generated_paths) + 1}: {operation.name}")
        try:
            if isinstance(operation, RouterOffsetOperation):
                if router_offset_uses_open_geometry(project, operation):
                    generated_path = generate_open_router_offset(project, operation)
                else:
                    generated_path = generate_closed_router_offset(project, operation)
            elif isinstance(operation, IslandFillOperation):
                generated_path = generate_rectangular_island_fill(project, operation)
            elif isinstance(operation, DrillCirclesOperation):
                generated_path = generate_drill_circles(project, operation)
            elif isinstance(operation, DragKnifeOperation):
                from .drag_knife import generate_drag_knife
                generated_path = generate_drag_knife(project, operation)
            elif isinstance(operation, EngraveOperation):
                generated_path = generate_drag_engraving(project, operation)
            elif isinstance(operation, ThreadMillOperation):
                generated_path = generate_thread_mill(project, operation)
            elif isinstance(operation, RestMachiningOperation):
                generated_path = generate_rest_machining(project, operation)
            elif isinstance(operation, ProfileRestMachiningOperation):
                generated_path = generate_profile_rest_machining(project, operation)
        except ValueError as exc:
            issues.append(ValidationIssue("error", "toolpath.generate", f"{operation.name}: {exc}"))
        if generated_path is not None:
            generated_paths[operation.id] = generated_path
            reported_adjustments = set()
            reported_backtracks = set()
            for block in generated_path.blocks:
                cleaned_id = str(block.metadata.get("entity_id", ""))
                cleaned_count = block.metadata.get("micro_backtracks_removed", 0)
                if cleaned_count and cleaned_id not in reported_backtracks:
                    reported_backtracks.add(cleaned_id)
                    issues.append(ValidationIssue(
                        "warning", "geometry.micro_backtrack",
                        f"{operation.name}: {cleaned_count} microscopic backtrack(s) were removed "
                        "from the working contour before cutter compensation. Source geometry is unchanged; "
                        "use Transform > Join / Repair Contours to preview and save the repair.",
                        (cleaned_id,),
                    ))
                actual_mode = str(block.metadata.get("entry_mode_actual", ""))
                if block.metadata.get("entry_shelf_boundary_unresolved"):
                    issues.append(ValidationIssue(
                        "warning", "toolpath.shelf_entry",
                        f"{operation.name}: no checked waste-side relocation fit for this shelf entry. Its shallow helix still crosses the related through-cut boundary; inspect the shelf finish.",
                        (cleaned_id,),
                    ))
                fallback = actual_mode in {
                    "waste_plunge_fallback", "plunge_on_contour_fallback", "contour_ramp_fallback",
                }
                if not block.metadata.get("entry_adjusted") and not fallback:
                    continue
                entity_id = str(block.metadata.get("entity_id", ""))
                path_index = int(block.metadata.get("path_index", 0))
                kind = str(block.metadata.get("entry_adjustment_kind", ""))
                value = block.metadata.get("entry_adjustment_distance", block.metadata.get("entry_adjustment_vertices"))
                key = (entity_id, path_index, kind, value, actual_mode)
                if key in reported_adjustments:
                    continue
                reported_adjustments.add(key)
                if actual_mode == "waste_plunge_fallback":
                    message = (
                        f"{operation.name} could not fit its requested helix at or above the configured minimum diameter, "
                        "so it used a waste-side plunge and short link to the contour."
                    )
                elif actual_mode == "plunge_on_contour_fallback":
                    message = (
                        f"{operation.name} could not place its requested helix safely, "
                        "so it used a direct plunge on the programmed profile without XY entry travel."
                    )
                elif actual_mode == "contour_ramp_fallback":
                    message = (
                        f"{operation.name} could not place its requested helix with cutter-width clearance, "
                        f"so it used a {float(value):g} {project.units} on-contour ramp and recut that span at full depth."
                    )
                elif kind == "shelf_waste_shift":
                    message = f"{operation.name} moved a shelf helix {float(value):g} {project.units} into waste beyond the paired through-cut boundary, with a checked connection to the shelf."
                elif kind == "contour_distance":
                    movement = f"{float(value):g} {project.units} farther along the contour"
                    message = f"{operation.name} automatically moved one entry {movement} to maintain cutter-width clearance."
                elif kind == "direct_plunge":
                    movement = "a direct plunge on the programmed profile"
                    message = (
                        f"{operation.name} could not place its requested helix with cutter-width clearance, "
                        f"so it used {movement} without adding any XY entry travel."
                    )
                else:
                    movement = f"{int(value)} vertex position(s) farther along the contour"
                    message = f"{operation.name} automatically moved one entry {movement} to maintain cutter-width clearance."
                point_value = block.metadata.get(
                    "entry_original_interference_point",
                    block.metadata.get("entry_plunge_location"),
                )
                point = (
                    Point(float(point_value[0]), float(point_value[1]))
                    if isinstance(point_value, (list, tuple)) and len(point_value) == 2
                    else None
                )
                threatened_id = str(block.metadata.get("entry_original_interference_entity_id", ""))
                issue_ids = tuple(value for value in (entity_id, threatened_id) if value)
                issues.append(ValidationIssue(
                    "warning",
                    "toolpath.entry_relocated" if block.metadata.get("entry_adjusted") else "toolpath.entry_fallback",
                    message,
                    issue_ids,
                    point,
                ))

    if small_part_max_area is not None and small_part_max_span is not None:
        for risk in small_part_risks(project, maximum_area=small_part_max_area, maximum_span=small_part_max_span):
            operation = next((item for item in project.operations if item.id == risk.operation_id), None)
            if operation is None or operation.small_part_strategy != "automatic":
                continue
            issues.append(ValidationIssue("warning", "hold_down.small_part", f"{operation.name} includes a small outside profile ({risk.area:g} square units, {risk.minimum_span:g} minimum span). {risk.recommendation}"))

    if settings_only:
        return _deduplicate(issues)
    if issue_callback is not None:
        issue_callback(_deduplicate(issues))
    report("Checking fixture keepouts")
    issues.extend(
        ValidationIssue(item.severity, item.code, item.message, item.entity_ids, item.point)
        for item in keepout_violations(project, generated_paths=generated_paths)
    )
    report("Checking entry clearance against neighboring geometry")
    issues.extend(
        ValidationIssue(item.severity, item.code, item.message, item.entity_ids, item.point)
        for item in entry_interference_violations(project, generated_paths=generated_paths)
    )

    report("Checking full cut clearance against neighboring contours")
    from .neighbor_clearance import neighboring_cut_violations
    from .optimization import optimize_generated_toolpath
    clearance_paths = generated_paths
    if cycle_settings.enabled:
        try:
            clearance_paths = {
                op.id: optimize_generated_toolpath(project, op, generated_paths[op.id])
                for op in project.operations if op.id in generated_paths
            }
        except ValueError as exc:
            issues.append(ValidationIssue("error", "cycle.clearance", f"Cannot verify optimized cut clearance: {exc}"))
    issues.extend(neighboring_cut_violations(project, clearance_paths))
    if issue_callback is not None:
        issue_callback(_deduplicate(issues))
    # Show inexpensive entry/neighbor failures before the costly independent
    # sweep audit. Nothing is blessed until this audit also finishes.
    report("Verifying cutter coverage, depths, and Keep Tool Down links")
    completeness = analyze_toolpath_completeness(project, generated_paths=generated_paths)
    issues.extend(
        ValidationIssue(item.severity, item.code, item.message, item.entity_ids, item.point)
        for item in completeness.issues
    )
    result = _deduplicate(issues)
    report("Preflight complete")
    if generated_paths_out is not None:
        generated_paths_out.clear()
        generated_paths_out.update(generated_paths)
    return result


def has_blocking_errors(issues: Iterable[ValidationIssue]) -> bool:
    return any(issue.severity == "error" for issue in issues)


def _deduplicate(issues: Iterable[ValidationIssue]) -> list[ValidationIssue]:
    result: list[ValidationIssue] = []
    seen: set[tuple] = set()
    for issue in issues:
        key = (issue.severity, issue.code, issue.message)
        if issue.code in {"toolpath.neighbor_cut", "toolpath.shelf_entry"}:
            key += (issue.entity_ids,)
        if key not in seen:
            seen.add(key)
            result.append(issue)
    return result


def _bounds_inside(inner: tuple[float, float, float, float], outer: tuple[float, float, float, float]) -> bool:
    return inner[0] >= outer[0] - TOLERANCE and inner[1] >= outer[1] - TOLERANCE and inner[2] <= outer[2] + TOLERANCE and inner[3] <= outer[3] + TOLERANCE


def _bounds_intersect(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return not (a[2] < b[0] - TOLERANCE or a[0] > b[2] + TOLERANCE or a[3] < b[1] - TOLERANCE or a[1] > b[3] + TOLERANCE)


def _self_intersects(points: list[Point]) -> bool:
    if len(points) < 4:
        return False

    def orientation(a: Point, b: Point, c: Point) -> float:
        return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)

    count = len(points)
    for first in range(count):
        a, b = points[first], points[(first + 1) % count]
        for second in range(first + 1, count):
            if second in {first, (first + 1) % count} or (second + 1) % count == first:
                continue
            c, d = points[second], points[(second + 1) % count]
            if orientation(a, b, c) * orientation(a, b, d) < -TOLERANCE and orientation(c, d, a) * orientation(c, d, b) < -TOLERANCE:
                return True
    return False
