from __future__ import annotations

from .model import CamProject, Circle, Line, Point, Polyline, Rectangle, TextEntity
from .dimensions import scale_dimension
from .appearance import scaled_appearance


UNIT_TO_INCHES = {
    "in": 1.0,
    "mm": 1.0 / 25.4,
    "cm": 1.0 / 2.54,
    "m": 100.0 / 2.54,
    "ft": 12.0,
    "yd": 36.0,
}

# Dimensional ownership contracts. Values in these fields are expressed in the
# containing project's units. Catalog values whose names explicitly end in
# ``_in`` and LED-layout records whose schema declares ``units: in`` remain
# intentionally inch-based and are not included here.
TOOL_PROJECT_DIMENSION_FIELDS = (
    "diameter", "feed_rate", "plunge_rate", "max_stepdown",
    "cutting_edge_length", "shank_diameter", "overall_length",
    "holder_diameter", "stickout",
)
TOOL_METADATA_PROJECT_DIMENSION_FIELDS = ("spring_travel",)
OPERATION_PROJECT_FEED_FIELDS = (
    "feed_rate_override", "plunge_rate_override", "surface_feed_override",
    "retract_feed_override", "bottom_finish_feed_override",
    "finish_feed_override", "helix_feed_override",
)
OPERATION_PROJECT_LENGTH_FIELDS = (
    "target_depth", "safe_z", "allowance", "stepdown", "stepover",
    "peck_depth", "retract_height", "reentry_clearance",
    "pitch_diameter_adjustment", "pilot_hole_depth", "bottom_clearance",
    "stock_to_leave", "ignore_stock_less_than", "breakthrough",
    "bottom_finish_depth", "ramp_length", "helix_diameter",
    "helix_min_diameter", "helix_pitch", "helix_clearance",
    "stay_down_max_distance", "stay_down_max_engagement",
    "sliver_max_path_length", "sliver_link_distance",
    "speed_max_full_depth_engagement", "speed_link_distance",
    "curve_feed_transition_distance", "common_line_tolerance",
    "finish_axial_stock", "approach_height",
    "blade_offset", "swivel_depth", "alignment_length", "curve_tolerance",
)
OPERATION_PROJECT_LENGTH_SEQUENCE_FIELDS = ("pass_depths",)
OPERATION_PROJECT_REQUIRED_LENGTH_FIELDS = (
    "corner_tab_offset", "tab_spacing", "female_tab_min_perimeter",
    "tab_cleanup_min_perimeter", "tab_width", "tab_height", "onion_skin",
    "lead_in", "lead_out",
)
ENTITY_METADATA_PROJECT_POINT_FIELDS = (
    "registered_center",
    # Bottom-edge mounting pose values are points in the containing project's
    # coordinate system. The saved mounting plan itself retains its explicit
    # plan.units and is deliberately not rewritten during project conversion.
    "bottom_edge_pose_origin", "bottom_edge_pose_x", "bottom_edge_pose_y",
    # Puzzle assembly records likewise retain explicit assembly units; entity
    # pose bases live in the containing project's coordinate system.
    "puzzle_pose_origin", "puzzle_pose_x", "puzzle_pose_y",
)
ENTITY_METADATA_PROJECT_UNIT_FIELDS = (
    "bottom_edge_pose_project_units", "puzzle_pose_project_units",
)
ENTITY_METADATA_PROJECT_SCALE_FIELDS = ("keyhole_units_per_inch",)


def unit_scale(from_units: str, to_units: str) -> float:
    try:
        return UNIT_TO_INCHES[from_units] / UNIT_TO_INCHES[to_units]
    except KeyError as exc:
        raise ValueError(f"Unsupported units: {exc.args[0]}") from exc


def convert_project_units(project: CamProject, to_units: str) -> CamProject:
    """Return a physically equivalent project represented in different units."""
    factor = unit_scale(project.units, to_units)
    output = CamProject.from_dict(project.to_dict())
    output.units = to_units
    if factor == 1:
        return output

    def point(value: Point) -> Point:
        return Point(value.x * factor, value.y * factor)

    def scale_optional_fields(owner, fields: tuple[str, ...]) -> None:
        for field in fields:
            value = getattr(owner, field, None)
            if value is not None:
                setattr(owner, field, value * factor)

    def scale_mapping_fields(mapping: dict, fields: tuple[str, ...]) -> None:
        for field in fields:
            value = mapping.get(field)
            if value is not None:
                mapping[field] = float(value) * factor

    def scale_entity_metadata(metadata: dict) -> dict:
        forming = metadata.get('bent_tab_forming')
        if isinstance(forming, dict):
            forming = dict(forming)
            metadata['bent_tab_forming'] = forming
            scale_mapping_fields(forming, ('thickness', 'inside_radius', 'bend_allowance',
                'finished_standoff', 'hole_diameter', 'hole_tip_offset', 'flat_length_adjustment'))
            scale_mapping_fields(metadata, ('flat_projection', 'tab_width', 'root_relief_radius',
                'tip_corner_radius', 'curve_tolerance'))
            if isinstance(metadata.get('tab_widths'), (list, tuple)):
                metadata['tab_widths'] = [float(value) * factor for value in metadata['tab_widths']]
        for field in ENTITY_METADATA_PROJECT_POINT_FIELDS:
            value = metadata.get(field)
            if (
                isinstance(value, (list, tuple)) and len(value) == 2
                and all(isinstance(component, (int, float)) for component in value)
            ):
                metadata[field] = [float(component) * factor for component in value]
        scale_mapping_fields(metadata, ENTITY_METADATA_PROJECT_SCALE_FIELDS)
        for field in ENTITY_METADATA_PROJECT_UNIT_FIELDS:
            if field in metadata:
                metadata[field] = to_units
        return metadata

    converted = []
    for entity in output.entities:
        common = {
            "id": entity.id, "layer": entity.layer,
            "metadata": scale_entity_metadata(dict(entity.metadata)),
            "appearance": scaled_appearance(entity.appearance, factor),
        }
        if isinstance(entity, Line):
            converted.append(Line(**common, start=point(entity.start), end=point(entity.end)))
        elif isinstance(entity, Circle):
            converted.append(Circle(**common, center=point(entity.center), radius=entity.radius * factor))
        elif isinstance(entity, Rectangle):
            converted.append(Rectangle(**common, corner_a=point(entity.corner_a), corner_b=point(entity.corner_b)))
        elif isinstance(entity, Polyline):
            converted.append(Polyline(**common, points=[point(value) for value in entity.points], closed=entity.closed))
        elif isinstance(entity, TextEntity):
            converted.append(TextEntity(
                **common, insertion=point(entity.insertion), text=entity.text,
                height=entity.height * factor, rotation=entity.rotation,
                alignment=entity.alignment, style_name=entity.style_name,
                font_family=entity.font_family, width_factor=entity.width_factor,
                multiline=entity.multiline,
            ))
        else:
            converted.append(entity)
    output.entities = converted
    output.dimensions = [scale_dimension(item, factor) for item in output.dimensions]
    output.guides = [item.scaled(factor) for item in output.guides]

    for tool in output.tool_library.tools:
        scale_optional_fields(tool, TOOL_PROJECT_DIMENSION_FIELDS)
        scale_mapping_fields(tool.metadata, TOOL_METADATA_PROJECT_DIMENSION_FIELDS)
        loaded_recipe = tool.metadata.get("loaded_recipe")
        if isinstance(loaded_recipe, dict):
            scale_mapping_fields(loaded_recipe, ("thickness_min", "thickness_max"))
    for operation in output.operations:
        scale_optional_fields(operation, OPERATION_PROJECT_FEED_FIELDS)
        if operation.recipe_metadata:
            scale_mapping_fields(operation.recipe_metadata, ("thickness_min", "thickness_max"))
        scale_optional_fields(operation, OPERATION_PROJECT_LENGTH_FIELDS)
        for field in OPERATION_PROJECT_LENGTH_SEQUENCE_FIELDS:
            if hasattr(operation, field):
                setattr(operation, field, [value * factor for value in getattr(operation, field)])
        for field in OPERATION_PROJECT_REQUIRED_LENGTH_FIELDS:
            if hasattr(operation, field):
                setattr(operation, field, getattr(operation, field) * factor)
        selector = operation.geometry_selector
        if selector:
            for field in ("diameter", "diameter_tolerance", "perimeter_min", "perimeter_max"):
                if selector.get(field) is not None:
                    selector[field] = float(selector[field]) * factor
            if selector.get("bounds"):
                selector["bounds"] = [float(value) * factor for value in selector["bounds"]]

    for zone in output.keepouts:
        zone.points = [point(value) for value in zone.points]
        zone.clearance_z *= factor

    for plate in output.metadata.get("plates", []):
        for field in ("x", "y", "width", "height"):
            if field in plate:
                plate[field] = float(plate[field]) * factor
    for area in output.metadata.get("patchwork_areas", []):
        if not isinstance(area, dict):
            continue
        for field in ("x", "y", "width", "height"):
            if field in area:
                area[field] = float(area[field]) * factor
    layout = output.metadata.get("sheet_layout")
    if isinstance(layout, dict):
        for field in ("width", "height", "origin_x", "origin_y"):
            if field in layout:
                layout[field] = float(layout[field]) * factor
    setup = output.metadata.get("job_setup")
    if isinstance(setup, dict):
        for field in ("material_thickness", "safe_z", "small_part_max_span"):
            if setup.get(field) is not None:
                setup[field] = float(setup[field]) * factor
        if setup.get("small_part_max_area") is not None:
            setup["small_part_max_area"] = float(setup["small_part_max_area"]) * factor * factor
    optimization = output.metadata.get("cycle_optimization")
    if isinstance(optimization, dict):
        for field in (
            "minimum_retract_height", "minimum_retract_max_distance",
            "line_merge_tolerance", "arc_fitting_tolerance",
        ):
            if optimization.get(field) is not None:
                optimization[field] = float(optimization[field]) * factor
    for source in output.metadata.get("multi_dxf_import", []):
        if not isinstance(source, dict):
            continue
        for field in ("source_bounds_in_project_units", "translation"):
            values = source.get(field)
            if isinstance(values, (list, tuple)):
                source[field] = [float(value) * factor for value in values]
    return output
