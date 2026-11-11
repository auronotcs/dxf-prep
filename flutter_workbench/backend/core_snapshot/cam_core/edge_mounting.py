from __future__ import annotations

"""Bottom-edge drilling records and honest 2D DXF construction handoff.

The letter hole is perpendicular to the artwork face, so it is not represented
as a face-plane circle.  Durable assembly records drive both a non-machining
axis preview on the letter and the matching rail-pattern circle.  Jig DXFs
contain separate face-plane and orthogonal cross-section construction layers;
Fusion still owns the 3D extrusion and bore operations.
"""

from dataclasses import dataclass, field
import hashlib
import json
import math
from typing import Iterable
from uuid import uuid4

from .model import CamProject, Circle, Entity, Line, Point, Polyline, Rectangle, TextEntity
from .mounting import MountingPart, discover_mounting_parts
from .plates import Plate
from .units import unit_scale


LAYER_BOTTOM_EDGE_AXES = "BOTTOM_EDGE_DRILL_AXES"
LAYER_JIG_LETTER_REFERENCE = "JIG_LETTER_REFERENCE"
LAYER_JIG_FACE_PROFILE = "JIG_FACE_LOCATOR_PROFILE"
LAYER_JIG_CROSS_SECTION = "JIG_GUIDE_CROSS_SECTION"
LAYER_JIG_BORE_ENVELOPE = "JIG_GUIDE_BORE_ENVELOPE"
LAYER_JIG_BORE_PROFILE = "JIG_GUIDE_BORE_PROFILE"
LAYER_JIG_HOLE_AXIS = "JIG_HOLE_AXIS_REFERENCE"
LAYER_JIG_LABELS = "JIG_CONSTRUCTION_LABELS"
LAYER_RAIL_HOLES = "RAIL_CLEARANCE_HOLES"
LAYER_RAIL_COUNTERSINK_PATHS = "RAIL_COUNTERSINK_PATHS"
LAYER_RAIL_GUIDES = "RAIL_ASSEMBLY_GUIDES"
LAYER_RAIL_PROFILE = "RAIL_CUT_PROFILE"

POSE_ORIGIN_KEY = "bottom_edge_pose_origin"
POSE_X_KEY = "bottom_edge_pose_x"
POSE_Y_KEY = "bottom_edge_pose_y"
POSE_PROJECT_UNITS_KEY = "bottom_edge_pose_project_units"
ORIENTATION_KEY = "bottom_edge_orientation_key"

EDGE_MOUNTING_LAYER_COLORS = {
    LAYER_BOTTOM_EDGE_AXES: "#e8590c",
    LAYER_JIG_LETTER_REFERENCE: "#868e96",
    LAYER_JIG_FACE_PROFILE: "#1971c2",
    LAYER_JIG_CROSS_SECTION: "#2f9e44",
    LAYER_JIG_BORE_ENVELOPE: "#9c36b5",
    LAYER_JIG_BORE_PROFILE: "#9c36b5",
    LAYER_JIG_HOLE_AXIS: "#e8590c",
    LAYER_JIG_LABELS: "#495057",
    LAYER_RAIL_HOLES: "#1971c2",
    LAYER_RAIL_COUNTERSINK_PATHS: "#9c36b5",
    LAYER_RAIL_GUIDES: "#868e96",
    LAYER_RAIL_PROFILE: "#e03131",
}


@dataclass(frozen=True)
class RailCountersinkPlan:
    """Cutter-center geometry for one circular-interpolated rail countersink."""

    clearance_diameter: float
    target_major_diameter: float
    included_angle_degrees: float
    cutter_tip_diameter: float
    cutter_max_diameter: float
    target_depth: float
    orbit_radius: float
    required_working_diameter: float


def calculate_rail_countersink(
    clearance_diameter: float,
    target_major_diameter: float,
    included_angle_degrees: float,
    cutter_tip_diameter: float,
    cutter_max_diameter: float,
    rail_thickness: float,
) -> RailCountersinkPlan:
    """Validate and derive a circular-interpolation countersink path.

    The cutter's flat tip remains tangent to the existing clearance bore at
    full depth.  Orbiting that tip produces the requested major diameter while
    retaining the original through-hole as the inner edge of the countersink.
    All linear values use the caller's project units.
    """
    values = (
        clearance_diameter,
        target_major_diameter,
        included_angle_degrees,
        cutter_tip_diameter,
        cutter_max_diameter,
        rail_thickness,
    )
    if any(not math.isfinite(float(value)) or float(value) <= 0 for value in values):
        raise ValueError("Countersink dimensions, angle, and rail thickness must be finite and positive.")
    if not 0 < included_angle_degrees < 180:
        raise ValueError("Countersink included angle must be between 0 and 180 degrees.")
    if target_major_diameter <= clearance_diameter:
        raise ValueError("Countersink major diameter must be larger than the rail clearance hole.")
    if cutter_tip_diameter >= clearance_diameter:
        raise ValueError("Cutter tip diameter must be smaller than the rail clearance hole.")

    tangent = math.tan(math.radians(included_angle_degrees / 2.0))
    target_depth = (target_major_diameter - clearance_diameter) / (2.0 * tangent)
    orbit_radius = (clearance_diameter - cutter_tip_diameter) / 2.0
    required_working_diameter = target_major_diameter - clearance_diameter + cutter_tip_diameter
    if cutter_max_diameter + 1e-9 < required_working_diameter:
        raise ValueError(
            "Cutter maximum diameter is too small for this major diameter, clearance hole, and tip diameter."
        )
    if target_depth >= rail_thickness:
        raise ValueError("Countersink depth reaches or exceeds the rail thickness.")
    return RailCountersinkPlan(
        clearance_diameter=float(clearance_diameter),
        target_major_diameter=float(target_major_diameter),
        included_angle_degrees=float(included_angle_degrees),
        cutter_tip_diameter=float(cutter_tip_diameter),
        cutter_max_diameter=float(cutter_max_diameter),
        target_depth=target_depth,
        orbit_radius=orbit_radius,
        required_working_diameter=required_working_diameter,
    )


def _point_payload(point: Point) -> list[float]:
    return [point.x, point.y]


def _point_from(value) -> Point:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("Expected a two-coordinate point.")
    return Point(float(value[0]), float(value[1]))


def _loops_orientation_key(loops: tuple[tuple[Point, ...], ...], bounds: tuple[float, float, float, float]) -> str:
    min_x, min_y, _max_x, _max_y = bounds
    payload = [
        [[round(point.x - min_x, 7), round(point.y - min_y, 7)] for point in loop]
        for loop in loops
    ]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode("utf-8")).hexdigest()[:20]


@dataclass(frozen=True)
class BottomEdgePartRecord:
    owner_entity_id: str
    part_id: str
    member_ids: tuple[str, ...]
    label: str
    bounds: tuple[float, float, float, float]
    assembly_loops: tuple[tuple[Point, ...], ...]
    orientation_key: str

    @classmethod
    def from_part(cls, part: MountingPart) -> "BottomEdgePartRecord":
        return cls(
            owner_entity_id=part.owner_entity_id,
            part_id=part.part_id,
            member_ids=tuple(part.member_ids),
            label=part.label,
            bounds=tuple(part.bounds),
            assembly_loops=tuple(tuple(point for point in loop) for loop in part.loops),
            orientation_key=_loops_orientation_key(part.loops, part.bounds),
        )

    def to_dict(self) -> dict:
        return {
            "owner_entity_id": self.owner_entity_id,
            "part_id": self.part_id,
            "member_ids": list(self.member_ids),
            "label": self.label,
            "bounds": list(self.bounds),
            "assembly_loops": [[_point_payload(point) for point in loop] for loop in self.assembly_loops],
            "orientation_key": self.orientation_key,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "BottomEdgePartRecord":
        bounds = tuple(float(value) for value in payload["bounds"])
        loops = tuple(tuple(_point_from(point) for point in loop) for loop in payload["assembly_loops"])
        if len(bounds) != 4 or not loops:
            raise ValueError("Bottom-edge part record is missing its authoritative outline.")
        return cls(
            owner_entity_id=str(payload["owner_entity_id"]),
            part_id=str(payload["part_id"]),
            member_ids=tuple(str(value) for value in payload.get("member_ids", ())),
            label=str(payload.get("label", "Part")),
            bounds=bounds,
            assembly_loops=loops,
            orientation_key=str(payload.get("orientation_key") or _loops_orientation_key(loops, bounds)),
        )


@dataclass(frozen=True)
class BottomEdgeHoleRecord:
    hole_id: str
    owner_entity_id: str
    owner_part_id: str
    part_label: str
    along_from_left: float
    assembly_center: Point
    drill_axis: Point
    stock_thickness: float
    thread_designation: str
    rail_group: str

    def to_dict(self) -> dict:
        return {
            "hole_id": self.hole_id,
            "owner_entity_id": self.owner_entity_id,
            "owner_part_id": self.owner_part_id,
            "part_label": self.part_label,
            "along_from_left": self.along_from_left,
            "assembly_center": _point_payload(self.assembly_center),
            "drill_axis": _point_payload(self.drill_axis),
            "stock_thickness": self.stock_thickness,
            "thread_designation": self.thread_designation,
            "rail_group": self.rail_group,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "BottomEdgeHoleRecord":
        return cls(
            hole_id=str(payload["hole_id"]),
            owner_entity_id=str(payload["owner_entity_id"]),
            owner_part_id=str(payload["owner_part_id"]),
            part_label=str(payload.get("part_label", "Part")),
            along_from_left=float(payload["along_from_left"]),
            assembly_center=_point_from(payload["assembly_center"]),
            drill_axis=_point_from(payload.get("drill_axis", (0.0, 1.0))),
            stock_thickness=float(payload["stock_thickness"]),
            thread_designation=str(payload.get("thread_designation", "")),
            rail_group=str(payload.get("rail_group", "Rail 1")),
        )


@dataclass
class BottomEdgeMountingPlan:
    plan_id: str
    scope_key: str
    units: str
    parts: tuple[BottomEdgePartRecord, ...]
    holes: list[BottomEdgeHoleRecord] = field(default_factory=list)
    stock_thickness: float = 0.5
    thread_designation: str = "1/4-20"
    rail_group: str = "Rail 1"
    settings: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "schema": "cam-workbench-bottom-edge-mounting-v1",
            "plan_id": self.plan_id,
            "scope_key": self.scope_key,
            "units": self.units,
            "parts": [part.to_dict() for part in self.parts],
            "holes": [hole.to_dict() for hole in self.holes],
            "stock_thickness": self.stock_thickness,
            "thread_designation": self.thread_designation,
            "rail_group": self.rail_group,
            "settings": dict(self.settings),
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "BottomEdgeMountingPlan":
        if payload.get("schema") != "cam-workbench-bottom-edge-mounting-v1":
            raise ValueError("Unsupported bottom-edge mounting-plan schema.")
        return cls(
            plan_id=str(payload["plan_id"]),
            scope_key=str(payload["scope_key"]),
            units=str(payload.get("units", "in")),
            parts=tuple(BottomEdgePartRecord.from_dict(value) for value in payload.get("parts", ())),
            holes=[BottomEdgeHoleRecord.from_dict(value) for value in payload.get("holes", ())],
            stock_thickness=float(payload["stock_thickness"]),
            thread_designation=str(payload.get("thread_designation", "")),
            rail_group=str(payload.get("rail_group", "Rail 1")),
            settings=dict(payload.get("settings", {})),
        )


@dataclass(frozen=True)
class BottomEdgeJigSettings:
    tap_drill_diameter: float | None = None
    printed_guide_fit_allowance: float | None = None
    rail_clearance_diameter: float | None = None
    rail_thickness: float | None = None
    rail_length: float | None = None
    rail_width: float | None = None
    rail_origin_x: float | None = None
    rail_hole_row_offset: float = 0.0
    rail_routing_max_section_length: float | None = None
    rail_routing_split_clearance: float = 0.0
    drill_depth: float | None = None
    jig_style: str = ""
    cradle_clearance: float | None = None
    jig_wall_thickness: float | None = None
    jig_engagement_depth: float | None = None
    jig_span_along_edge: float | None = None

    def to_dict(self) -> dict:
        return {
            "tap_drill_diameter": self.tap_drill_diameter,
            "printed_guide_fit_allowance": self.printed_guide_fit_allowance,
            "rail_clearance_diameter": self.rail_clearance_diameter,
            "rail_thickness": self.rail_thickness,
            "rail_length": self.rail_length,
            "rail_width": self.rail_width,
            "rail_origin_x": self.rail_origin_x,
            "rail_hole_row_offset": self.rail_hole_row_offset,
            "rail_routing_max_section_length": self.rail_routing_max_section_length,
            "rail_routing_split_clearance": self.rail_routing_split_clearance,
            "drill_depth": self.drill_depth,
            "jig_style": self.jig_style,
            "cradle_clearance": self.cradle_clearance,
            "jig_wall_thickness": self.jig_wall_thickness,
            "jig_engagement_depth": self.jig_engagement_depth,
            "jig_span_along_edge": self.jig_span_along_edge,
        }

    @classmethod
    def from_mapping(cls, payload: dict) -> "BottomEdgeJigSettings":
        def optional_number(key: str) -> float | None:
            value = payload.get(key)
            if value in (None, ""):
                return None
            return float(value)

        return cls(
            tap_drill_diameter=optional_number("tap_drill_diameter"),
            printed_guide_fit_allowance=optional_number("printed_guide_fit_allowance"),
            rail_clearance_diameter=optional_number("rail_clearance_diameter"),
            rail_thickness=optional_number("rail_thickness"),
            rail_length=optional_number("rail_length"),
            rail_width=optional_number("rail_width"),
            rail_origin_x=optional_number("rail_origin_x"),
            rail_hole_row_offset=float(payload.get("rail_hole_row_offset", 0.0) or 0.0),
            rail_routing_max_section_length=optional_number("rail_routing_max_section_length"),
            rail_routing_split_clearance=float(payload.get("rail_routing_split_clearance", 0.0) or 0.0),
            drill_depth=optional_number("drill_depth"),
            jig_style=str(payload.get("jig_style", "")),
            cradle_clearance=optional_number("cradle_clearance"),
            jig_wall_thickness=optional_number("jig_wall_thickness"),
            jig_engagement_depth=optional_number("jig_engagement_depth"),
            jig_span_along_edge=optional_number("jig_span_along_edge"),
        )


def create_bottom_edge_mounting_plan(
    entities: Iterable[Entity],
    selected_ids: set[str],
    *,
    units: str,
    stock_thickness: float,
    thread_designation: str = "1/4-20",
    rail_group: str = "Rail 1",
) -> BottomEdgeMountingPlan:
    if not math.isfinite(stock_thickness) or stock_thickness <= 0:
        raise ValueError("Letter stock thickness must be a positive finite value.")
    thread = str(thread_designation).strip()
    if not thread:
        raise ValueError("Enter the confirmed screw thread designation.")
    parts = tuple(BottomEdgePartRecord.from_part(part) for part in discover_mounting_parts(entities, selected_ids))
    scope_key = "bottom_edge_rail:" + ",".join(sorted(part.part_id for part in parts))
    return BottomEdgeMountingPlan(
        plan_id=f"bottom_edge_{uuid4().hex}", scope_key=scope_key, units=units,
        parts=parts, stock_thickness=stock_thickness,
        thread_designation=thread, rail_group=str(rail_group).strip() or "Rail 1",
    )


def _point_in_loop(point: Point, loop: tuple[Point, ...]) -> bool:
    inside = False
    previous = loop[-1]
    for current in loop:
        if (current.y > point.y) != (previous.y > point.y):
            crossing_x = (
                (previous.x - current.x) * (point.y - current.y)
                / (previous.y - current.y) + current.x
            )
            if point.x < crossing_x:
                inside = not inside
        previous = current
    return inside


def _inside_material(point: Point, loops: tuple[tuple[Point, ...], ...]) -> bool:
    return sum(_point_in_loop(point, loop) for loop in loops) % 2 == 1


def _bottom_edge_y(part: BottomEdgePartRecord, x: float) -> float:
    min_x, min_y, max_x, max_y = part.bounds
    width = max_x - min_x
    if x <= min_x + 1e-9 or x >= max_x - 1e-9:
        raise ValueError(f"{part.label}: hole position must be inside the letter's left and right bounds.")
    intersections: list[float] = []
    for loop in part.assembly_loops:
        for first, second in zip(loop, loop[1:] + loop[:1]):
            dx = second.x - first.x
            if abs(dx) <= 1e-12:
                continue
            fraction = (x - first.x) / dx
            if -1e-10 <= fraction <= 1.0 + 1e-10:
                intersections.append(first.y + (second.y - first.y) * fraction)
    epsilon = max(1e-7, math.hypot(width, max_y - min_y) * 1e-7)
    for y in sorted(set(round(value, 10) for value in intersections)):
        if _inside_material(Point(x, y + epsilon), part.assembly_loops):
            if y > min_y + max(epsilon * 10.0, width * 1e-5):
                raise ValueError(
                    f"{part.label}: X{x - min_x:g} reaches an elevated/internal edge, not the rail-supported bottom edge. "
                    "Choose a position on a bottom foot or stroke."
                )
            return y
    raise ValueError(f"{part.label}: no bottom-edge material exists at X{x - min_x:g} from the left bound.")


def create_bottom_edge_hole(
    plan: BottomEdgeMountingPlan,
    owner_part_id: str,
    along_from_left: float,
    *,
    hole_id: str | None = None,
    rail_group: str | None = None,
) -> BottomEdgeHoleRecord:
    part = next((value for value in plan.parts if value.part_id == owner_part_id), None)
    if part is None:
        raise ValueError("Choose a letter from the current bottom-edge mounting plan.")
    if not math.isfinite(along_from_left):
        raise ValueError("Along-edge position must be finite.")
    x = part.bounds[0] + along_from_left
    y = _bottom_edge_y(part, x)
    return BottomEdgeHoleRecord(
        hole_id=hole_id or f"edge_hole_{uuid4().hex}",
        owner_entity_id=part.owner_entity_id,
        owner_part_id=part.part_id,
        part_label=part.label,
        along_from_left=along_from_left,
        assembly_center=Point(x, y),
        drill_axis=Point(0.0, 1.0),
        stock_thickness=plan.stock_thickness,
        thread_designation=plan.thread_designation,
        rail_group=str(rail_group if rail_group is not None else plan.rail_group).strip() or "Rail 1",
    )


def bottom_edge_jig_missing_inputs(settings: BottomEdgeJigSettings) -> tuple[str, ...]:
    """Return values that still need shop verification.

    This is an honesty/status list, not an export gate.  A construction DXF is
    useful before guide fit and rail-clearance dimensions are qualified; those
    values remain visibly marked as unverified in its metadata and manifest.
    """
    missing = []
    numeric = (
        ("Letter tap-drill diameter", settings.tap_drill_diameter, False),
        ("Printed-guide diametral allowance", settings.printed_guide_fit_allowance, True),
        ("Rail screw-clearance diameter", settings.rail_clearance_diameter, False),
        ("Rail thickness", settings.rail_thickness, False),
        ("Rail length", settings.rail_length, False),
        ("Rail face width", settings.rail_width, False),
        ("Cradle fit clearance", settings.cradle_clearance, True),
        ("Printed jig wall thickness", settings.jig_wall_thickness, False),
        ("Jig engagement depth", settings.jig_engagement_depth, False),
        ("Jig span along letter edge", settings.jig_span_along_edge, False),
    )
    for label, value, allow_zero in numeric:
        if value is None or not math.isfinite(value) or value < 0 or (not allow_zero and value <= 0):
            missing.append(label)
    if settings.jig_style != "u_channel_saddle":
        missing.append("Jig cradle style")
    return tuple(missing)


def _require_jig_export_ready(plan: BottomEdgeMountingPlan, settings: BottomEdgeJigSettings) -> None:
    if not plan.holes:
        raise ValueError("Add and review at least one bottom-edge hole before export.")
    required = (
        ("Letter tap-drill diameter", settings.tap_drill_diameter, False),
        ("Cradle fit clearance", settings.cradle_clearance, True),
        ("Printed jig wall thickness", settings.jig_wall_thickness, False),
        ("Jig engagement depth", settings.jig_engagement_depth, False),
        ("Jig span along letter edge", settings.jig_span_along_edge, False),
    )
    missing = [
        label for label, value, allow_zero in required
        if value is None or not math.isfinite(value) or value < 0 or (not allow_zero and value <= 0)
    ]
    if settings.jig_style != "u_channel_saddle":
        missing.append("Jig cradle style")
    if missing:
        raise ValueError("Jig construction DXF needs: " + "; ".join(missing) + ".")
    assert settings.tap_drill_diameter is not None
    if settings.tap_drill_diameter >= plan.stock_thickness:
        raise ValueError("Letter tap-drill diameter must be smaller than the letter stock thickness.")
    guide_allowance = settings.printed_guide_fit_allowance or 0.0
    if settings.tap_drill_diameter + guide_allowance >= plan.stock_thickness:
        raise ValueError("Printed guide hole diameter must be smaller than the letter stock thickness.")


def _require_rail_export_ready(plan: BottomEdgeMountingPlan, settings: BottomEdgeJigSettings) -> None:
    if not plan.holes:
        raise ValueError("Add and review at least one bottom-edge hole before export.")
    missing = []
    for label, value in (
        ("Rail screw-clearance diameter", settings.rail_clearance_diameter),
        ("Rail thickness", settings.rail_thickness),
        ("Rail length", settings.rail_length),
        ("Rail face width", settings.rail_width),
    ):
        if value is None or not math.isfinite(value) or value <= 0:
            missing.append(label)
    if missing:
        raise ValueError("Rail-pattern DXF needs: " + "; ".join(missing) + ".")
    assert settings.rail_length is not None
    assert settings.rail_width is not None
    if settings.rail_origin_x is not None and not math.isfinite(settings.rail_origin_x):
        raise ValueError("Rail X origin must be finite or left blank for automatic centering.")
    if not math.isfinite(settings.rail_hole_row_offset):
        raise ValueError("Rail hole-row offset must be finite.")


def _reference_metadata(plan: BottomEdgeMountingPlan, hole: BottomEdgeHoleRecord, plane: str) -> dict:
    return {
        "bottom_edge_mounting_export": True,
        "bottom_edge_plan_id": plan.plan_id,
        "bottom_edge_hole_id": hole.hole_id,
        "bottom_edge_part_id": hole.owner_part_id,
        "projection_plane": plane,
        "reference_only": True,
        "machinable": False,
    }


def build_bottom_edge_jig_projects(
    plan: BottomEdgeMountingPlan,
    settings: BottomEdgeJigSettings,
) -> list[tuple[BottomEdgePartRecord, CamProject]]:
    """Return one exact-unit 2D Fusion construction DXF project per letter."""
    _require_jig_export_ready(plan, settings)
    assert settings.tap_drill_diameter is not None
    assert settings.cradle_clearance is not None
    assert settings.jig_wall_thickness is not None
    assert settings.jig_engagement_depth is not None
    assert settings.jig_span_along_edge is not None
    guide_fit_unverified = settings.printed_guide_fit_allowance is None
    guide_diameter = settings.tap_drill_diameter + (settings.printed_guide_fit_allowance or 0.0)
    result = []
    for part in plan.parts:
        holes = [hole for hole in plan.holes if hole.owner_part_id == part.part_id]
        if not holes:
            continue
        entities: list[Entity] = []
        min_x, min_y, max_x, max_y = part.bounds
        for loop_index, loop in enumerate(part.assembly_loops):
            entities.append(Polyline(
                layer=LAYER_JIG_LETTER_REFERENCE,
                points=[Point(point.x - min_x, point.y - min_y) for point in loop], closed=True,
                metadata={
                    "bottom_edge_mounting_export": True,
                    "bottom_edge_part_id": part.part_id,
                    "authoritative_artwork_loop": loop_index,
                    "reference_only": True,
                    "machinable": False,
                },
            ))
        part_width = max_x - min_x
        cross_origin_x = part_width + max(settings.jig_span_along_edge, plan.stock_thickness) * 1.5
        inner_width = plan.stock_thickness + 2.0 * settings.cradle_clearance
        outer_width = inner_width + 2.0 * settings.jig_wall_thickness
        outer_height = settings.jig_engagement_depth + settings.jig_wall_thickness
        for index, hole in enumerate(holes):
            metadata = _reference_metadata(plan, hole, "artwork_face_xy")
            half_span = settings.jig_span_along_edge / 2.0
            local_center = Point(hole.assembly_center.x - min_x, hole.assembly_center.y - min_y)
            entities.append(Rectangle(
                layer=LAYER_JIG_FACE_PROFILE,
                corner_a=Point(local_center.x - half_span, local_center.y),
                corner_b=Point(local_center.x + half_span, local_center.y + settings.jig_engagement_depth),
                metadata={
                    **metadata, "closed_extrudable_profile": True,
                    "local_center": _point_payload(local_center),
                    "assembly_center": _point_payload(hole.assembly_center),
                },
            ))
            face_axis_start = Point(local_center.x, local_center.y - settings.jig_wall_thickness)
            face_axis_end = Point(local_center.x, local_center.y + settings.jig_engagement_depth)
            entities.append(Line(
                layer=LAYER_JIG_HOLE_AXIS, start=face_axis_start, end=face_axis_end,
                metadata={**metadata, "drill_axis": [0.0, 1.0]},
            ))

            origin_x = cross_origin_x + index * (outer_width + settings.jig_wall_thickness * 3.0)
            origin_y = 0.0
            inner_left = origin_x + settings.jig_wall_thickness
            inner_right = inner_left + inner_width
            inner_bottom = origin_y + settings.jig_wall_thickness
            cross_section = (
                Point(origin_x, origin_y), Point(origin_x + outer_width, origin_y),
                Point(origin_x + outer_width, origin_y + outer_height),
                Point(inner_right, origin_y + outer_height), Point(inner_right, inner_bottom),
                Point(inner_left, inner_bottom), Point(inner_left, origin_y + outer_height),
                Point(origin_x, origin_y + outer_height),
            )
            cross_metadata = _reference_metadata(plan, hole, "orthogonal_thickness_drill_axis")
            entities.append(Polyline(
                layer=LAYER_JIG_CROSS_SECTION, points=list(cross_section), closed=True,
                metadata={**cross_metadata, "closed_extrudable_profile": True, "jig_style": "u_channel_saddle"},
            ))
            axis_x = origin_x + outer_width / 2.0
            entities.append(Rectangle(
                layer=LAYER_JIG_BORE_ENVELOPE,
                corner_a=Point(axis_x - guide_diameter / 2.0, origin_y - settings.jig_wall_thickness * 0.25),
                corner_b=Point(axis_x + guide_diameter / 2.0, inner_bottom + settings.jig_wall_thickness * 0.25),
                metadata={**cross_metadata, "closed_bore_envelope": True, "guide_hole_diameter": guide_diameter},
            ))
            entities.append(Line(
                layer=LAYER_JIG_HOLE_AXIS,
                start=Point(axis_x, origin_y - settings.jig_wall_thickness * 0.5),
                end=Point(axis_x, inner_bottom + settings.jig_engagement_depth),
                metadata={**cross_metadata, "drill_axis": [0.0, 1.0], "stock_midplane": plan.stock_thickness / 2.0},
            ))
            # A third, drill-normal X/Z view carries the actual circular bore
            # profile.  The adjacent orthogonal section shows how its axis
            # passes through the U-channel saddle; neither view pretends the
            # hole belongs on the letter's artwork face.
            bore_view_center = Point(axis_x, origin_y - outer_width * 1.5)
            entities.append(Circle(
                layer=LAYER_JIG_BORE_PROFILE,
                center=bore_view_center,
                radius=guide_diameter / 2.0,
                metadata={
                    **_reference_metadata(plan, hole, "drill_normal_xz"),
                    "closed_bore_profile": True,
                    "guide_hole_diameter": guide_diameter,
                    "guide_fit_unverified": guide_fit_unverified,
                    "stock_midplane": plan.stock_thickness / 2.0,
                },
            ))
            entities.append(TextEntity(
                layer=LAYER_JIG_LABELS,
                insertion=Point(origin_x, origin_y - settings.jig_wall_thickness),
                text=(
                    f"{part.label} [{part.part_id[-8:]}] H{index + 1} | {plan.thread_designation} | "
                    f"local edge X {hole.along_from_left:g} | assembly {hole.assembly_center.x:g},{hole.assembly_center.y:g}"
                ),
                height=max(plan.stock_thickness * 0.25, settings.jig_wall_thickness * 0.5),
                metadata=cross_metadata,
            ))
        project = CamProject(
            f"{part.label} bottom-edge drilling jig", units=plan.units, entities=entities,
            metadata={
                "bottom_edge_plan_id": plan.plan_id,
                "bottom_edge_part_id": part.part_id,
                "fusion_construction_only": True,
                "letter_stock_thickness": plan.stock_thickness,
                "thread_designation": plan.thread_designation,
                "guide_hole_diameter": guide_diameter,
                "guide_fit_unverified": guide_fit_unverified,
                "tap_drill_status": "tentative",
                "construction_handoff_only": True,
            },
        )
        result.append((part, project))
    return result


def build_bottom_edge_rail_pattern_project(
    plan: BottomEdgeMountingPlan,
    settings: BottomEdgeJigSettings,
) -> CamProject:
    """Build editable local rail blanks linked to assembly-space letter anchors."""
    _require_rail_export_ready(plan, settings)
    assert settings.rail_clearance_diameter is not None
    assert settings.rail_length is not None
    assert settings.rail_width is not None
    entities: list[Entity] = []
    grouped: dict[str, list[BottomEdgeHoleRecord]] = {}
    for hole in plan.holes:
        grouped.setdefault(hole.rail_group, []).append(hole)
    label_height = max(plan.stock_thickness * 0.5, settings.rail_clearance_diameter)
    row_origin_y = 0.0
    rail_records = []
    radius = settings.rail_clearance_diameter / 2.0
    local_hole_y = settings.rail_width / 2.0 + settings.rail_hole_row_offset
    if local_hole_y - radius < -1e-9 or local_hole_y + radius > settings.rail_width + 1e-9:
        raise ValueError(
            "The rail hole row and clearance diameter do not fit inside the rail face width. "
            "Use centered holes, reduce the offset/diameter, or increase rail width."
        )
    for group_index, (group, holes) in enumerate(sorted(grouped.items(), key=lambda item: item[0].casefold()), start=1):
        ordered = sorted(holes, key=lambda value: value.assembly_center.x)
        minimum_anchor_x = ordered[0].assembly_center.x
        maximum_anchor_x = ordered[-1].assembly_center.x
        anchor_span = maximum_anchor_x - minimum_anchor_x
        if anchor_span + settings.rail_clearance_diameter > settings.rail_length + 1e-9:
            raise ValueError(
                f"{group}: its {anchor_span:g} {plan.units} hole span plus clearance diameter does not fit "
                f"the {settings.rail_length:g} {plan.units} rail length."
            )
        assembly_origin_x = (
            settings.rail_origin_x
            if settings.rail_origin_x is not None
            else minimum_anchor_x - (settings.rail_length - anchor_span) / 2.0
        )
        profile = Rectangle(
            layer=LAYER_RAIL_PROFILE,
            corner_a=Point(0.0, row_origin_y),
            corner_b=Point(settings.rail_length, row_origin_y + settings.rail_width),
            metadata={
                "bottom_edge_mounting_export": True,
                "bottom_edge_plan_id": plan.plan_id,
                "rail_group": group,
                "rail_length": settings.rail_length,
                "rail_width": settings.rail_width,
                "rail_thickness": settings.rail_thickness,
                "assembly_origin_x": assembly_origin_x,
                "closed_cut_profile": True,
            },
        )
        entities.append(profile)
        local_centers = []
        for hole in ordered:
            local_x = hole.assembly_center.x - assembly_origin_x
            if local_x - radius < -1e-9 or local_x + radius > settings.rail_length + 1e-9:
                raise ValueError(
                    f"{group}: {hole.part_label}'s hole falls outside the rail after applying the entered X origin."
                )
            local_center = Point(local_x, row_origin_y + local_hole_y)
            local_centers.append(local_center)
            entities.append(Circle(
                layer=LAYER_RAIL_HOLES, center=local_center,
                radius=radius,
                metadata={
                    "bottom_edge_mounting_export": True,
                    "bottom_edge_plan_id": plan.plan_id,
                    "bottom_edge_hole_id": hole.hole_id,
                    "bottom_edge_part_id": hole.owner_part_id,
                    "rail_group": group,
                    "assembly_coordinate": _point_payload(hole.assembly_center),
                    "rail_local_coordinate": _point_payload(Point(local_x, local_hole_y)),
                    "rail_clearance_diameter": settings.rail_clearance_diameter,
                },
            ))
        entities.append(Line(
            layer=LAYER_RAIL_GUIDES,
            start=Point(0.0, row_origin_y + settings.rail_width / 2.0),
            end=Point(settings.rail_length, row_origin_y + settings.rail_width / 2.0),
            metadata={
                "rail_group": group, "reference_only": True, "machinable": False,
                "description": "rail width centerline",
            },
        ))
        entities.append(TextEntity(
            layer=LAYER_JIG_LABELS,
            insertion=Point(0.0, row_origin_y - label_height * 1.5),
            text=(
                f"{group} | rail {settings.rail_length:g} x {settings.rail_width:g} x {settings.rail_thickness:g} {plan.units} | "
                f"{len(holes)} holes | row {'centered' if abs(settings.rail_hole_row_offset) <= 1e-12 else f'offset {settings.rail_hole_row_offset:g}'}"
            ),
            height=label_height,
            metadata={"rail_group": group, "reference_only": True, "machinable": False},
        ))
        rail_records.append({
            "rail_group": group,
            "rail_number": group_index,
            "assembly_origin_x": assembly_origin_x,
            "hole_ids": [hole.hole_id for hole in ordered],
            "local_centers": [_point_payload(Point(center.x, center.y - row_origin_y)) for center in local_centers],
        })
        row_origin_y += settings.rail_width + max(settings.rail_width * 0.75, label_height * 3.0)
    return CamProject(
        "Bottom-edge editable rail blanks", units=plan.units, entities=entities,
        metadata={
            "bottom_edge_plan_id": plan.plan_id,
            "assembly_coordinates_authoritative": True,
            "rail_clearance_diameter": settings.rail_clearance_diameter,
            "rail_thickness": settings.rail_thickness,
            "rail_length": settings.rail_length,
            "rail_width": settings.rail_width,
            "rail_origin_x": settings.rail_origin_x,
            "rail_hole_row_offset": settings.rail_hole_row_offset,
            "rail_records": rail_records,
        },
    )


def _rail_routing_section_ranges(
    rail_length: float,
    maximum_section_length: float | None,
    hole_centers: Iterable[float],
    hole_radius: float,
    split_clearance: float,
) -> tuple[tuple[float, float], ...]:
    """Divide a finished rail into router-sized sections without bisecting holes."""
    if maximum_section_length is None or rail_length <= maximum_section_length + 1e-9:
        return ((0.0, rail_length),)
    if not math.isfinite(maximum_section_length) or maximum_section_length <= 0:
        raise ValueError("Maximum routed rail-section length must be positive or left blank.")
    if not math.isfinite(split_clearance) or split_clearance < 0:
        raise ValueError("Rail split-to-hole clearance must be zero or positive.")

    count = int(math.ceil(rail_length / maximum_section_length))
    protected = max(0.0, hole_radius) + split_clearance
    centers = tuple(sorted(float(value) for value in hole_centers))
    boundaries = [0.0]
    epsilon = max(1e-9, rail_length * 1e-12)

    def is_safe(value: float) -> bool:
        return all(abs(value - center) + epsilon >= protected for center in centers)

    for section_index in range(1, count):
        start = boundaries[-1]
        remaining_sections = count - section_index
        lower = max(start + epsilon, rail_length - remaining_sections * maximum_section_length)
        upper = min(start + maximum_section_length, rail_length - epsilon)
        nominal = start + (rail_length - start) / (remaining_sections + 1)
        nominal = min(max(nominal, lower), upper)
        candidates = [nominal, lower, upper]
        for center in centers:
            candidates.extend((center - protected, center + protected))
        choices = [
            value for value in candidates
            if lower - epsilon <= value <= upper + epsilon and is_safe(value)
        ]
        if not choices:
            raise ValueError(
                "No safe rail split fits between the registered holes. Increase the maximum routed section length, "
                "reduce split-to-hole clearance, or move the nearby letter hole."
            )
        boundary = min(choices, key=lambda value: (abs(value - nominal), -value))
        if boundary - start > maximum_section_length + epsilon:
            raise ValueError("A routed rail section would exceed the entered maximum length.")
        boundaries.append(boundary)
    boundaries.append(rail_length)
    return tuple((boundaries[index], boundaries[index + 1]) for index in range(len(boundaries) - 1))


def build_bottom_edge_rail_routing_project(
    plan: BottomEdgeMountingPlan,
    settings: BottomEdgeJigSettings,
    *,
    source_project_name: str = "",
) -> CamProject:
    """Build a separate, route-ready rail document from the registered plan.

    The rectangles and clearance circles are machinable. Centerlines and
    labels remain reference-only. Each rail group is its own individual plate
    so output can be limited to one physical rail without disturbing the
    assembly drawing or the per-letter jig handoff.
    """
    project = build_bottom_edge_rail_pattern_project(plan, settings)
    assert settings.rail_thickness is not None
    assert settings.rail_clearance_diameter is not None
    assert settings.rail_width is not None
    scale = unit_scale("in", plan.units)
    source_profiles = sorted(
        (
            entity for entity in project.entities
            if isinstance(entity, Rectangle) and entity.layer == LAYER_RAIL_PROFILE
        ),
        key=lambda entity: (entity.corner_a.y, entity.corner_a.x),
    )
    source_holes = [
        entity for entity in project.entities
        if isinstance(entity, Circle) and entity.layer == LAYER_RAIL_HOLES
    ]
    routing_entities: list[Entity] = []
    plates: list[Plate] = []
    profile_ids: list[str] = []
    hole_ids: list[str] = []
    routing_sections = []
    row_origin_y = 0.0
    label_height = max(plan.stock_thickness * 0.5, settings.rail_clearance_diameter)
    radius = settings.rail_clearance_diameter / 2.0

    for group_index, source_profile in enumerate(source_profiles, start=1):
        group = str(source_profile.metadata.get("rail_group", f"Rail {group_index}"))
        full_origin_x = float(source_profile.metadata.get("assembly_origin_x", 0.0))
        group_holes = sorted(
            (entity for entity in source_holes if str(entity.metadata.get("rail_group", "")) == group),
            key=lambda entity: entity.center.x,
        )
        ranges = _rail_routing_section_ranges(
            float(settings.rail_length), settings.rail_routing_max_section_length,
            (entity.center.x for entity in group_holes), radius,
            settings.rail_routing_split_clearance,
        )
        section_count = len(ranges)
        assigned_holes: set[str] = set()
        for section_index, (section_start, section_end) in enumerate(ranges, start=1):
            section_length = section_end - section_start
            section_name = group if section_count == 1 else f"{group} — Section {section_index}/{section_count}"
            profile = Rectangle(
                layer=LAYER_RAIL_PROFILE,
                corner_a=Point(0.0, row_origin_y),
                corner_b=Point(section_length, row_origin_y + settings.rail_width),
                metadata={
                    **source_profile.metadata,
                    "assembly_origin_x": full_origin_x + section_start,
                    "parent_rail_assembly_origin_x": full_origin_x,
                    "parent_rail_length": settings.rail_length,
                    "rail_section_number": section_index,
                    "rail_section_count": section_count,
                    "rail_section_start": section_start,
                    "rail_section_end": section_end,
                    "rail_section_length": section_length,
                    "rail_routing_role": "outside_profile",
                    "recommended_sequence_role": "outside_profile",
                    "machinable": True,
                },
            )
            routing_entities.append(profile)
            profile_ids.append(profile.id)
            plate_number = len(plates) + 1
            plates.append(Plate(plate_number, 0.0, row_origin_y, section_length, settings.rail_width, section_name))

            section_hole_ids = []
            for source_hole in group_holes:
                if source_hole.id in assigned_holes:
                    continue
                local_x = source_hole.center.x
                if local_x + radius < section_start - 1e-9 or local_x - radius > section_end + 1e-9:
                    continue
                if local_x - radius < section_start - 1e-9 or local_x + radius > section_end + 1e-9:
                    raise ValueError(
                        f"{group}: a registered hole would cross the split beside section {section_index}. "
                        "Increase the maximum routed section length or move that hole."
                    )
                hole = Circle(
                    layer=LAYER_RAIL_HOLES,
                    center=Point(local_x - section_start, row_origin_y + settings.rail_width / 2.0 + settings.rail_hole_row_offset),
                    radius=source_hole.radius,
                    metadata={
                        **source_hole.metadata,
                        "rail_section_number": section_index,
                        "rail_section_count": section_count,
                        "rail_section_start": section_start,
                        "rail_section_local_coordinate": _point_payload(Point(local_x - section_start, settings.rail_width / 2.0 + settings.rail_hole_row_offset)),
                        "rail_routing_role": "drill_then_countersink",
                        "recommended_sequence_role": "drill_thread",
                        "machinable": True,
                    },
                )
                routing_entities.append(hole)
                hole_ids.append(hole.id)
                section_hole_ids.append(str(hole.metadata.get("bottom_edge_hole_id", hole.id)))
                assigned_holes.add(source_hole.id)

            routing_entities.append(Line(
                layer=LAYER_RAIL_GUIDES,
                start=Point(0.0, row_origin_y + settings.rail_width / 2.0),
                end=Point(section_length, row_origin_y + settings.rail_width / 2.0),
                metadata={
                    "rail_group": group, "rail_section_number": section_index,
                    "reference_only": True, "machinable": False,
                    "description": "rail width centerline",
                },
            ))
            routing_entities.append(TextEntity(
                layer=LAYER_JIG_LABELS,
                insertion=Point(0.0, row_origin_y - label_height * 1.5),
                text=(
                    f"{section_name} | cut {section_length:g} {plan.units} | assembly X "
                    f"{full_origin_x + section_start:g} to {full_origin_x + section_end:g} | {len(section_hole_ids)} holes"
                ),
                height=label_height,
                metadata={
                    "rail_group": group, "rail_section_number": section_index,
                    "reference_only": True, "machinable": False,
                },
            ))
            routing_sections.append({
                "rail_group": group,
                "section_number": section_index,
                "section_count": section_count,
                "section_name": section_name,
                "section_start": section_start,
                "section_end": section_end,
                "section_length": section_length,
                "assembly_origin_x": full_origin_x + section_start,
                "hole_ids": section_hole_ids,
                "plate_number": plate_number,
            })
            row_origin_y += settings.rail_width + max(settings.rail_width * 0.75, label_height * 3.0)
        if len(assigned_holes) != len(group_holes):
            raise ValueError(f"{group}: one or more registered holes could not be assigned to a routed rail section.")
    project.entities = routing_entities
    project.name = (
        f"{source_project_name.strip()} — rail routing" if source_project_name.strip()
        else "Bottom-edge rail routing"
    )
    project.metadata.update({
        "plates": [plate.to_dict() for plate in plates],
        "active_plate_number": plates[0].number if plates else 1,
        "bottom_edge_rail_routing_job": True,
        "source_project_name": source_project_name,
        "rail_profile_entity_ids": profile_ids,
        "rail_hole_entity_ids": hole_ids,
        "rail_routing_max_section_length": settings.rail_routing_max_section_length,
        "rail_routing_split_clearance": settings.rail_routing_split_clearance,
        "rail_routing_sections": routing_sections,
        "rail_routing_sequence": (
            "Drill linked clearance holes; countersink with a separately verified tool/angle; "
            "cut each section's outside rail profile last."
        ),
        "job_setup": {
            "material": "Aluminum",
            "material_detail": "Bottom-edge mounting rail",
            "material_thickness": settings.rail_thickness,
            "through_cut_breakthrough": 0.015 * scale,
            "automatic_safe_z": True,
            "safe_z": max(settings.rail_thickness * 1.5, 0.125 * scale),
            "xy_origin": "Plate lower-left",
            "z_zero": "Material top",
            "output_scope": "All plates",
            "small_part_checks": True,
        },
    })
    return project


def apply_bottom_edge_mounting_plan(project: CamProject, plan: BottomEdgeMountingPlan) -> tuple[str, ...]:
    """Persist assembly records and non-machining edge-axis previews idempotently."""
    by_id = {entity.id: entity for entity in project.entities}
    pose_scale = unit_scale(plan.units, project.units)
    for part in plan.parts:
        owner = by_id.get(part.owner_entity_id)
        if owner is None:
            raise ValueError(f"{part.label}: source letter is no longer present.")
        for member_id in part.member_ids:
            member = by_id.get(member_id)
            if member is None:
                continue
            member.metadata["mounting_part_id"] = part.part_id
            member.metadata["mounting_owner_entity_id"] = part.owner_entity_id
            member.metadata[ORIENTATION_KEY] = part.orientation_key
            member.metadata.setdefault(POSE_ORIGIN_KEY, [0.0, 0.0])
            member.metadata.setdefault(POSE_X_KEY, [pose_scale, 0.0])
            member.metadata.setdefault(POSE_Y_KEY, [0.0, pose_scale])
            member.metadata.setdefault(POSE_PROJECT_UNITS_KEY, project.units)

    # Reject stale pre-contract registrations instead of generating plausible
    # but physically mis-scaled axes. New plans and projects converted by
    # convert_project_units carry an explicit project-unit pose marker.
    validate_bottom_edge_registration(project, plan)

    prior_plan = next(
        (saved for saved in load_bottom_edge_mounting_plans(project) if saved.plan_id == plan.plan_id),
        None,
    )
    affected_part_ids = {part.part_id for part in plan.parts}
    if prior_plan is not None:
        # A review may explicitly replace/remove letters while retaining the
        # plan identity. Remove those letters' old generated axes on Apply so
        # narrowing the working set cannot leave stale shop marks behind.
        affected_part_ids.update(part.part_id for part in prior_plan.parts)
    old = {
        str(entity.metadata.get("bottom_edge_hole_id")): entity
        for entity in project.entities
        if entity.metadata.get("bottom_edge_mounting_generated")
        and entity.metadata.get("mounting_part_id") in affected_part_ids
    }
    project.entities = [
        entity for entity in project.entities
        if not (
            entity.metadata.get("bottom_edge_mounting_generated")
            and entity.metadata.get("mounting_part_id") in affected_part_ids
        )
    ]
    additions = []
    for hole in plan.holes:
        center = current_bottom_edge_hole_center(project, plan, hole)
        length = max(
            plan.stock_thickness * pose_scale,
            0.5 * unit_scale("in", project.units),
        )
        prior = old.get(hole.hole_id)
        additions.append(Line(
            id=prior.id if prior is not None else f"{hole.hole_id}:axis",
            layer=LAYER_BOTTOM_EDGE_AXES,
            start=Point(center.x, center.y - length * 0.35),
            end=Point(center.x, center.y + length),
            metadata={
                "bottom_edge_mounting_generated": True,
                "bottom_edge_plan_id": plan.plan_id,
                "bottom_edge_hole_id": hole.hole_id,
                "mounting_generated": True,
                "mounting_workflow": "bottom_edge_rail",
                "mounting_owner_entity_id": hole.owner_entity_id,
                "mounting_part_id": hole.owner_part_id,
                "group_id": hole.owner_part_id,
                "registered_center": _point_payload(center),
                "assembly_center": _point_payload(hole.assembly_center),
                "assembly_units": plan.units,
                "drill_axis": _point_payload(hole.drill_axis),
                "reference_only": True,
                "machinable": False,
            },
        ))
    project.entities.extend(additions)
    plans = [
        value for value in project.metadata.get("bottom_edge_mounting_plans", [])
        if isinstance(value, dict)
        and value.get("plan_id") != plan.plan_id
        and value.get("scope_key") != plan.scope_key
    ]
    plans.append(plan.to_dict())
    project.metadata["bottom_edge_mounting_plans"] = plans
    layer_settings = project.metadata.setdefault("layer_settings", {})
    values = layer_settings.setdefault(LAYER_BOTTOM_EDGE_AXES, {})
    # Keep the reference axes transformable with their registered letter.
    # reference_only/machinable=False, rather than a layer lock, is what keeps
    # them out of router operations.
    values.update(visible=True, locked=False, intentional=True, color=EDGE_MOUNTING_LAYER_COLORS[LAYER_BOTTOM_EDGE_AXES])
    project.metadata["last_mounting_workflow"] = "bottom_edge_rail"
    project.metadata["last_mounting_scope_key"] = plan.scope_key
    return tuple(entity.id for entity in additions)


def load_bottom_edge_mounting_plans(project: CamProject) -> list[BottomEdgeMountingPlan]:
    result = []
    for payload in project.metadata.get("bottom_edge_mounting_plans", []):
        if not isinstance(payload, dict):
            continue
        try:
            result.append(BottomEdgeMountingPlan.from_dict(payload))
        except (KeyError, TypeError, ValueError):
            continue
    return result


def validate_bottom_edge_registration(project: CamProject, plan: BottomEdgeMountingPlan) -> None:
    try:
        expected_scale = unit_scale(plan.units, project.units)
    except ValueError as exc:
        raise ValueError(
            f"Bottom-edge plan units {plan.units!r} are incompatible with project units {project.units!r}."
        ) from exc
    scale_tolerance = max(1e-7, expected_scale * 1e-7)
    determinant_tolerance = max(1e-7, expected_scale * expected_scale * 1e-7)
    by_id = {entity.id: entity for entity in project.entities}
    for part in plan.parts:
        owner = by_id.get(part.owner_entity_id)
        if owner is None:
            raise ValueError(f"{part.label}: registered letter is missing.")
        if owner.metadata.get(ORIENTATION_KEY) != part.orientation_key:
            raise ValueError(f"{part.label}: letter orientation/identity no longer matches this jig plan.")
        pose_units = str(owner.metadata.get(POSE_PROJECT_UNITS_KEY, "")).strip()
        if pose_units and pose_units != project.units:
            raise ValueError(
                f"{part.label}: pose registration is stored in {pose_units}, not the current "
                f"{project.units}; reconvert or regenerate the mounting plan before output."
            )
        try:
            origin = _point_from(owner.metadata[POSE_ORIGIN_KEY])
            axis_x = _point_from(owner.metadata[POSE_X_KEY])
            axis_y = _point_from(owner.metadata[POSE_Y_KEY])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{part.label}: letter pose registration is incomplete.") from exc
        determinant = (
            (axis_x.x - origin.x) * (axis_y.y - origin.y)
            - (axis_x.y - origin.y) * (axis_y.x - origin.x)
        )
        if determinant <= 0:
            raise ValueError(f"{part.label}: mirrored orientation does not match the reviewed drilling jig.")
        vector_x = Point(axis_x.x - origin.x, axis_x.y - origin.y)
        vector_y = Point(axis_y.x - origin.x, axis_y.y - origin.y)
        if not (
            math.isclose(math.hypot(vector_x.x, vector_x.y), expected_scale, abs_tol=scale_tolerance)
            and math.isclose(math.hypot(vector_y.x, vector_y.y), expected_scale, abs_tol=scale_tolerance)
            and math.isclose(
                vector_x.x * vector_y.x + vector_x.y * vector_y.y,
                0.0, abs_tol=determinant_tolerance,
            )
            and math.isclose(
                determinant, expected_scale * expected_scale,
                abs_tol=determinant_tolerance,
            )
        ):
            raise ValueError(
                f"{part.label}: the letter pose has the wrong unit scale, or it was scaled or distorted after review; "
                "reconvert or regenerate its physical-fit jig."
            )


def current_bottom_edge_hole_center(project: CamProject, plan: BottomEdgeMountingPlan, hole: BottomEdgeHoleRecord) -> Point:
    validate_bottom_edge_registration(project, plan)
    part = next(value for value in plan.parts if value.part_id == hole.owner_part_id)
    owner = next((entity for entity in project.entities if entity.id == part.owner_entity_id), None)
    if owner is None:
        raise ValueError(f"{part.label}: registered letter is missing.")
    origin = _point_from(owner.metadata.get(POSE_ORIGIN_KEY, (0.0, 0.0)))
    axis_x = _point_from(owner.metadata.get(POSE_X_KEY, (1.0, 0.0)))
    axis_y = _point_from(owner.metadata.get(POSE_Y_KEY, (0.0, 1.0)))
    return Point(
        origin.x + hole.assembly_center.x * (axis_x.x - origin.x) + hole.assembly_center.y * (axis_y.x - origin.x),
        origin.y + hole.assembly_center.x * (axis_x.y - origin.y) + hole.assembly_center.y * (axis_y.y - origin.y),
    )


def bottom_edge_fusion_instructions(plan: BottomEdgeMountingPlan, settings: BottomEdgeJigSettings) -> str:
    missing = bottom_edge_jig_missing_inputs(settings)
    readiness = "All listed values verified" if not missing else "VERIFY BEFORE PRINT/USE: " + "; ".join(missing)
    guide_diameter = (
        settings.tap_drill_diameter + (settings.printed_guide_fit_allowance or 0.0)
        if settings.tap_drill_diameter is not None
        else None
    )
    return "\n".join((
        "CAM Workbench bottom-edge drilling jig / rail handoff",
        readiness,
        f"Units: {plan.units} (import DXFs 1:1; do not scale letter outlines)",
        f"Letter stock thickness: {plan.stock_thickness:g} {plan.units}; midplane: {plan.stock_thickness / 2.0:g} {plan.units}",
        f"Confirmed thread: {plan.thread_designation}",
        f"Letter tap-drill diameter: {settings.tap_drill_diameter if settings.tap_drill_diameter is not None else 'UNVERIFIED'} (shop starting value; #7 remains tentative)",
        f"Printed guide hole diameter: {guide_diameter if guide_diameter is not None else 'UNVERIFIED'} (tap drill + diametral allowance; no bushing; zero allowance is visibly unverified)",
        f"Rail clearance diameter: {settings.rail_clearance_diameter if settings.rail_clearance_diameter is not None else 'UNVERIFIED'}",
        f"Rail thickness: {settings.rail_thickness if settings.rail_thickness is not None else 'UNVERIFIED'}",
        f"Rail blank length x face width: {settings.rail_length if settings.rail_length is not None else 'UNVERIFIED'} x {settings.rail_width if settings.rail_width is not None else 'UNVERIFIED'}",
        f"Rail X registration: {'auto-center the assembly hole span' if settings.rail_origin_x is None else settings.rail_origin_x}",
        f"Rail hole row: {'centered across face width' if abs(settings.rail_hole_row_offset) <= 1e-12 else f'offset {settings.rail_hole_row_offset:g} from center'}",
        "",
        "Fusion workflow:",
        "1. Import each letter jig DXF at the stated units. JIG_LETTER_REFERENCE is the authoritative, unscaled artwork snapshot.",
        "2. JIG_FACE_LOCATOR_PROFILE contains closed face-plane guide footprints. Extrude only after confirming the intended guide-body direction.",
        "3. JIG_GUIDE_CROSS_SECTION is an orthogonal thickness/drill-axis construction, drawn beside the face view. Recreate it on a perpendicular Fusion sketch plane; a flat DXF cannot assign that plane for you.",
        "4. JIG_GUIDE_BORE_ENVELOPE shows the bore in the Y/Z section. JIG_GUIDE_BORE_PROFILE is the circular bore on a drill-normal X/Z sketch; center it on the stated 0.5 in stock midplane and create the cylindrical Fusion cut along JIG_HOLE_AXIS_REFERENCE.",
        "5. The optional rail DXF contains closed, local-origin RAIL_CUT_PROFILE blanks and RAIL_CLEARANCE_HOLES from the same durable anchor IDs. Assembly X is mapped through the entered rail origin, or the complete hole span is centered by default. It is not derived from nested positions.",
        "6. Print a test jig and verify cradle fit, guide-hole fit, drilling depth stop, screw engagement, and rail registration before production. Drill depth and screw engagement are hand-process decisions and do not block this construction export.",
    )) + "\n"
