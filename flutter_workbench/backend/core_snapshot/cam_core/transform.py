from __future__ import annotations

import copy
import math

from .model import Circle, Entity, Line, Point, Polyline, Rectangle, TextEntity, new_id


DEFAULT_JOIN_TOLERANCE = 0.001
ROTATION_FROM_HOME_KEY = "rotation_from_home_degrees"
BOUNDING_BOX_ANCHOR_KEYS = (
    "top_left", "top_center", "top_right",
    "middle_left", "center", "middle_right",
    "bottom_left", "bottom_center", "bottom_right",
)
REGISTERED_POINT_METADATA_KEYS = (
    "registered_center",
    "bottom_edge_pose_origin",
    "bottom_edge_pose_x",
    "bottom_edge_pose_y",
    "puzzle_pose_origin",
    "puzzle_pose_x",
    "puzzle_pose_y",
)


def bounding_box_anchor_points(
    bounds: tuple[float, float, float, float],
) -> dict[str, Point]:
    """Return the nine stable placement references on an axis-aligned box."""
    minimum_x, minimum_y, maximum_x, maximum_y = bounds
    center_x = (minimum_x + maximum_x) / 2.0
    center_y = (minimum_y + maximum_y) / 2.0
    return {
        "top_left": Point(minimum_x, maximum_y),
        "top_center": Point(center_x, maximum_y),
        "top_right": Point(maximum_x, maximum_y),
        "middle_left": Point(minimum_x, center_y),
        "center": Point(center_x, center_y),
        "middle_right": Point(maximum_x, center_y),
        "bottom_left": Point(minimum_x, minimum_y),
        "bottom_center": Point(center_x, minimum_y),
        "bottom_right": Point(maximum_x, minimum_y),
    }


def precise_move_delta(
    bounds: tuple[float, float, float, float],
    anchor: str,
    target_x: float,
    target_y: float,
) -> Point:
    """Return the translation that places one bounding-box anchor at X/Y."""
    anchors = bounding_box_anchor_points(bounds)
    if anchor not in anchors:
        raise ValueError(f"Unknown bounding-box anchor: {anchor}")
    current = anchors[anchor]
    return Point(float(target_x) - current.x, float(target_y) - current.y)


def normalized_rotation_degrees(angle: float) -> float:
    """Normalize an angle to -180..180 while keeping 180 positive."""
    normalized = float(angle) % 360.0
    if normalized > 180.0:
        normalized -= 360.0
    return 0.0 if math.isclose(normalized, 0.0, abs_tol=1e-9) else normalized


def entity_rotation_from_home(entity: Entity) -> float:
    """Return an entity's accumulated rotation from its original orientation."""
    try:
        angle = float(entity.metadata.get(ROTATION_FROM_HOME_KEY, 0.0))
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(angle):
        return 0.0
    return normalized_rotation_degrees(angle)


def common_rotation_from_home(
    entities: list[Entity],
    tolerance: float = 1e-6,
) -> float | None:
    """Return a shared home-relative angle, or None for a mixed selection."""
    if not entities:
        return None
    angles = [entity_rotation_from_home(entity) for entity in entities]
    reference = angles[0]
    if any(
        abs(normalized_rotation_degrees(angle - reference)) > tolerance
        for angle in angles[1:]
    ):
        return None
    return reference


def transform_entity(
    entity: Entity,
    delta_x: float = 0.0,
    delta_y: float = 0.0,
    angle_degrees: float = 0.0,
    pivot: Point = Point(0.0, 0.0),
    preserve_id: bool = True,
) -> Entity:
    """Return an entity translated and rotated counter-clockwise around pivot."""
    angle = math.radians(angle_degrees)
    cosine = math.cos(angle)
    sine = math.sin(angle)

    def move(point: Point) -> Point:
        relative_x = point.x - pivot.x
        relative_y = point.y - pivot.y
        return Point(
            pivot.x + relative_x * cosine - relative_y * sine + delta_x,
            pivot.y + relative_x * sine + relative_y * cosine + delta_y,
        )

    identity = entity.id if preserve_id else new_id("entity")
    metadata = dict(entity.metadata)
    for key in REGISTERED_POINT_METADATA_KEYS:
        registered_point = metadata.get(key)
        if (
            isinstance(registered_point, (list, tuple)) and len(registered_point) == 2
            and all(isinstance(value, (int, float)) for value in registered_point)
        ):
            moved_point = move(Point(float(registered_point[0]), float(registered_point[1])))
            metadata[key] = [moved_point.x, moved_point.y]
    if not math.isclose(angle_degrees, 0.0, abs_tol=1e-12):
        metadata[ROTATION_FROM_HOME_KEY] = normalized_rotation_degrees(
            entity_rotation_from_home(entity) + angle_degrees,
        )
    common = {"id": identity, "layer": entity.layer, "metadata": metadata, "appearance": copy.deepcopy(entity.appearance)}
    if isinstance(entity, Line):
        return Line(**common, start=move(entity.start), end=move(entity.end))
    if isinstance(entity, Circle):
        return Circle(**common, center=move(entity.center), radius=entity.radius)
    if isinstance(entity, Polyline):
        return Polyline(**common, points=[move(point) for point in entity.points], closed=entity.closed)
    if isinstance(entity, Rectangle):
        min_x = min(entity.corner_a.x, entity.corner_b.x)
        min_y = min(entity.corner_a.y, entity.corner_b.y)
        max_x = max(entity.corner_a.x, entity.corner_b.x)
        max_y = max(entity.corner_a.y, entity.corner_b.y)
        corners = [
            Point(min_x, min_y),
            Point(max_x, min_y),
            Point(max_x, max_y),
            Point(min_x, max_y),
        ]
        moved = [move(point) for point in corners]
        quarter_turn = round(angle_degrees / 90.0)
        if math.isclose(angle_degrees, quarter_turn * 90.0, abs_tol=1e-9):
            return Rectangle(**common, corner_a=moved[0], corner_b=moved[2])
        return Polyline(**common, points=moved, closed=True)
    if isinstance(entity, TextEntity):
        return TextEntity(
            **common,
            insertion=move(entity.insertion),
            text=entity.text,
            height=entity.height,
            rotation=(entity.rotation + angle_degrees) % 360.0,
            alignment=entity.alignment,
            style_name=entity.style_name,
            font_family=entity.font_family,
            width_factor=entity.width_factor,
            multiline=entity.multiline,
        )
    raise TypeError(f"Unsupported entity type: {type(entity).__name__}")


def scale_entity(
    entity: Entity,
    scale_x: float,
    scale_y: float,
    pivot: Point = Point(0.0, 0.0),
    preserve_id: bool = True,
) -> Entity:
    """Scale one entity about an explicit stationary pivot.

    Circle geometry is kept analytic, so a non-proportional request is
    rejected instead of silently replacing it with a distorted approximation.
    Editable text retains its height and width-factor semantics.
    """
    scale_x = float(scale_x)
    scale_y = float(scale_y)
    if not math.isfinite(scale_x) or not math.isfinite(scale_y):
        raise ValueError("Scale factors must be finite numbers.")
    if scale_x <= 0 or scale_y <= 0:
        raise ValueError("Scale factors must be greater than zero.")
    proportional = math.isclose(scale_x, scale_y, rel_tol=1e-9, abs_tol=1e-12)
    if isinstance(entity, Circle) and not proportional:
        raise ValueError(
            "Non-proportional scaling of circles is not supported. "
            "Enable proportional scaling or convert the circle to curves first."
        )

    def resize(point: Point) -> Point:
        return Point(
            pivot.x + (point.x - pivot.x) * scale_x,
            pivot.y + (point.y - pivot.y) * scale_y,
        )

    identity = entity.id if preserve_id else new_id("entity")
    metadata = dict(entity.metadata)
    for key in REGISTERED_POINT_METADATA_KEYS:
        registered_point = metadata.get(key)
        if (
            isinstance(registered_point, (list, tuple)) and len(registered_point) == 2
            and all(isinstance(value, (int, float)) for value in registered_point)
        ):
            point = resize(Point(float(registered_point[0]), float(registered_point[1])))
            metadata[key] = [point.x, point.y]
    common = {"id": identity, "layer": entity.layer, "metadata": metadata, "appearance": copy.deepcopy(entity.appearance)}
    if isinstance(entity, Line):
        return Line(**common, start=resize(entity.start), end=resize(entity.end))
    if isinstance(entity, Circle):
        return Circle(**common, center=resize(entity.center), radius=entity.radius * scale_x)
    if isinstance(entity, Polyline):
        return Polyline(**common, points=[resize(point) for point in entity.points], closed=entity.closed)
    if isinstance(entity, Rectangle):
        first = resize(entity.corner_a)
        second = resize(entity.corner_b)
        return Rectangle(**common, corner_a=first, corner_b=second)
    if isinstance(entity, TextEntity):
        return TextEntity(
            **common,
            insertion=resize(entity.insertion),
            text=entity.text,
            height=entity.height * scale_y,
            rotation=entity.rotation,
            alignment=entity.alignment,
            style_name=entity.style_name,
            font_family=entity.font_family,
            width_factor=entity.width_factor * scale_x / scale_y,
            multiline=entity.multiline,
        )
    raise TypeError(f"Unsupported entity type: {type(entity).__name__}")


def mirror_entity(
    entity: Entity,
    axis_start: Point,
    axis_end: Point,
    preserve_id: bool = True,
) -> Entity:
    """Reflect an entity across the infinite line through two axis points."""
    axis_x = axis_end.x - axis_start.x
    axis_y = axis_end.y - axis_start.y
    axis_length_squared = axis_x * axis_x + axis_y * axis_y
    if axis_length_squared <= 1e-18:
        raise ValueError("Mirror axis requires two distinct points.")

    def reflect(point: Point) -> Point:
        relative_x = point.x - axis_start.x
        relative_y = point.y - axis_start.y
        scale = (relative_x * axis_x + relative_y * axis_y) / axis_length_squared
        projection_x = axis_start.x + scale * axis_x
        projection_y = axis_start.y + scale * axis_y
        return Point(2.0 * projection_x - point.x, 2.0 * projection_y - point.y)

    identity = entity.id if preserve_id else new_id("entity")
    metadata = dict(entity.metadata)
    for key in REGISTERED_POINT_METADATA_KEYS:
        registered_point = metadata.get(key)
        if (
            isinstance(registered_point, (list, tuple)) and len(registered_point) == 2
            and all(isinstance(value, (int, float)) for value in registered_point)
        ):
            mirrored_point = reflect(Point(float(registered_point[0]), float(registered_point[1])))
            metadata[key] = [mirrored_point.x, mirrored_point.y]
    # A reflection changes handedness and cannot be undone by rotation alone.
    # Treat the reflected pose as the new, unsurprising home orientation.
    metadata.pop(ROTATION_FROM_HOME_KEY, None)
    common = {"id": identity, "layer": entity.layer, "metadata": metadata, "appearance": copy.deepcopy(entity.appearance)}
    if isinstance(entity, Line):
        return Line(**common, start=reflect(entity.start), end=reflect(entity.end))
    if isinstance(entity, Circle):
        return Circle(**common, center=reflect(entity.center), radius=entity.radius)
    if isinstance(entity, Polyline):
        return Polyline(**common, points=[reflect(point) for point in entity.points], closed=entity.closed)
    if isinstance(entity, Rectangle):
        min_x = min(entity.corner_a.x, entity.corner_b.x)
        min_y = min(entity.corner_a.y, entity.corner_b.y)
        max_x = max(entity.corner_a.x, entity.corner_b.x)
        max_y = max(entity.corner_a.y, entity.corner_b.y)
        mirrored = [
            reflect(Point(min_x, min_y)),
            reflect(Point(max_x, min_y)),
            reflect(Point(max_x, max_y)),
            reflect(Point(min_x, max_y)),
        ]
        mirrored_min_x = min(point.x for point in mirrored)
        mirrored_max_x = max(point.x for point in mirrored)
        mirrored_min_y = min(point.y for point in mirrored)
        mirrored_max_y = max(point.y for point in mirrored)
        tolerance = 1e-9
        remains_axis_aligned = all(
            (math.isclose(point.x, mirrored_min_x, abs_tol=tolerance)
             or math.isclose(point.x, mirrored_max_x, abs_tol=tolerance))
            and (math.isclose(point.y, mirrored_min_y, abs_tol=tolerance)
                 or math.isclose(point.y, mirrored_max_y, abs_tol=tolerance))
            for point in mirrored
        )
        if remains_axis_aligned:
            return Rectangle(
                **common,
                corner_a=Point(mirrored_min_x, mirrored_min_y),
                corner_b=Point(mirrored_max_x, mirrored_max_y),
            )
        return Polyline(**common, points=mirrored, closed=True)
    if isinstance(entity, TextEntity):
        insertion = reflect(entity.insertion)
        direction = Point(
            entity.insertion.x + math.cos(math.radians(entity.rotation)),
            entity.insertion.y + math.sin(math.radians(entity.rotation)),
        )
        reflected_direction = reflect(direction)
        rotation = math.degrees(math.atan2(
            reflected_direction.y - insertion.y,
            reflected_direction.x - insertion.x,
        )) % 360.0
        metadata = dict(common["metadata"])
        metadata["mirrored_text"] = True
        return TextEntity(
            id=identity, layer=entity.layer, metadata=metadata, appearance=copy.deepcopy(entity.appearance),
            insertion=insertion, text=entity.text, height=entity.height,
            rotation=rotation, alignment=entity.alignment,
            style_name=entity.style_name, font_family=entity.font_family,
            width_factor=entity.width_factor, multiline=entity.multiline,
        )
    raise TypeError(f"Unsupported entity type: {type(entity).__name__}")


def merge_lines(lines: list[Line], tolerance: float = DEFAULT_JOIN_TOLERANCE) -> Polyline:
    """Connect selected line entities into one ordered open or closed contour."""
    if len(lines) < 2:
        raise ValueError("Select at least two lines to merge.")
    if tolerance <= 0:
        raise ValueError("Merge tolerance must be positive.")

    chain = [lines[0].start, lines[0].end]
    remaining = list(lines[1:])
    while remaining:
        matched = False
        for index, line in enumerate(remaining):
            if chain[-1].distance_to(line.start) <= tolerance:
                chain.append(line.end)
            elif chain[-1].distance_to(line.end) <= tolerance:
                chain.append(line.start)
            elif chain[0].distance_to(line.end) <= tolerance:
                chain.insert(0, line.start)
            elif chain[0].distance_to(line.start) <= tolerance:
                chain.insert(0, line.end)
            else:
                continue
            remaining.pop(index)
            matched = True
            break
        if not matched:
            raise ValueError("The selected lines do not form one connected contour.")

    closed = chain[0].distance_to(chain[-1]) <= tolerance
    if closed:
        chain.pop()
    if len(chain) < 2:
        raise ValueError("Merged contour does not contain enough distinct points.")
    metadata = dict(lines[0].metadata)
    metadata["merged_from"] = [line.id for line in lines]
    return Polyline(
        id=new_id("entity"),
        layer=lines[0].layer,
        metadata=metadata,
        appearance=copy.deepcopy(lines[0].appearance),
        points=chain,
        closed=closed,
    )


def explode_contour(entity: Entity) -> list[Line]:
    """Break a rectangle or polyline into individually selectable line segments."""
    if isinstance(entity, Rectangle):
        min_x = min(entity.corner_a.x, entity.corner_b.x)
        min_y = min(entity.corner_a.y, entity.corner_b.y)
        max_x = max(entity.corner_a.x, entity.corner_b.x)
        max_y = max(entity.corner_a.y, entity.corner_b.y)
        points = [
            Point(min_x, min_y),
            Point(max_x, min_y),
            Point(max_x, max_y),
            Point(min_x, max_y),
        ]
        closed = True
    elif isinstance(entity, Polyline):
        points = list(entity.points)
        closed = entity.closed
    else:
        raise ValueError(f"{entity.kind.title()} cannot be exploded into line segments.")
    if len(points) < 2:
        raise ValueError("Contour does not contain enough points to explode.")

    pairs = list(zip(points, points[1:]))
    if closed:
        pairs.append((points[-1], points[0]))
    return [
        Line(
            id=new_id("entity"),
            layer=entity.layer,
            metadata={**entity.metadata, "exploded_from": entity.id},
            appearance=copy.deepcopy(entity.appearance),
            start=start,
            end=end,
        )
        for start, end in pairs
    ]
