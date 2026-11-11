from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import copy
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Iterable
from uuid import uuid4

from .print_job import PrintJobData, validate_print_job
from .project_storage import read_project_payload, write_project_payload
from .appearance import ObjectAppearance

if TYPE_CHECKING:
    from .dimensions import DimensionAnnotation


SCHEMA_VERSION = 1
SUPPORTED_UNITS = {"in", "mm", "cm", "m", "ft", "yd"}


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


@dataclass(frozen=True)
class Point:
    x: float
    y: float

    def distance_to(self, other: "Point") -> float:
        return math.hypot(other.x - self.x, other.y - self.y)


@dataclass
class Entity:
    id: str = field(default_factory=lambda: new_id("entity"))
    layer: str = "0"
    metadata: dict[str, Any] = field(default_factory=dict)
    # Explicit artwork appearance. None means inherit the layer/display ink;
    # this never controls layer membership or machining intent.
    appearance: ObjectAppearance | None = None
    kind: ClassVar[str] = "entity"

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["kind"] = self.kind
        return result


@dataclass
class Line(Entity):
    start: Point = field(default_factory=lambda: Point(0.0, 0.0))
    end: Point = field(default_factory=lambda: Point(1.0, 0.0))
    kind: ClassVar[str] = "line"


@dataclass
class Circle(Entity):
    center: Point = field(default_factory=lambda: Point(0.0, 0.0))
    radius: float = 0.5
    kind: ClassVar[str] = "circle"


@dataclass
class Rectangle(Entity):
    corner_a: Point = field(default_factory=lambda: Point(0.0, 0.0))
    corner_b: Point = field(default_factory=lambda: Point(1.0, 1.0))
    kind: ClassVar[str] = "rectangle"


@dataclass
class Polyline(Entity):
    points: list[Point] = field(default_factory=list)
    closed: bool = False
    kind: ClassVar[str] = "polyline"


@dataclass
class TextEntity(Entity):
    """Editable drafting text that is never machinable until converted.

    ``insertion`` is the alignment point on the text baseline.  Height and
    coordinates use project units; rotation is counter-clockwise degrees.
    Keeping text in the domain model (instead of canvas-only labels) lets DXF
    notes survive save/open/export without ever becoming an accidental cut.
    """

    insertion: Point = field(default_factory=lambda: Point(0.0, 0.0))
    text: str = "Text"
    height: float = 0.25
    rotation: float = 0.0
    alignment: str = "left"
    style_name: str = "STANDARD"
    font_family: str = "Arial"
    width_factor: float = 1.0
    multiline: bool = False
    kind: ClassVar[str] = "text"


ENTITY_TYPES: dict[str, type[Entity]] = {
    Line.kind: Line,
    Circle.kind: Circle,
    Rectangle.kind: Rectangle,
    Polyline.kind: Polyline,
    TextEntity.kind: TextEntity,
}


class ToolKind(str, Enum):
    END_MILL = "end_mill"
    BALL_NOSE = "ball_nose"
    BULL_NOSE = "bull_nose"
    DRILL = "drill"
    V_BIT = "v_bit"
    PLANING = "planing"
    ENGRAVER = "engraver"
    DRAG_KNIFE = "drag_knife"
    BRUSH = "brush"
    MARKER = "marker"
    THREAD_MILL = "thread_mill"
    OTHER = "other"


@dataclass
class Tool:
    station: int
    name: str
    diameter: float
    kind: ToolKind = ToolKind.END_MILL
    spindle_rpm: int | None = None
    feed_rate: float | None = None
    plunge_rate: float | None = None
    max_stepdown: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    cutting_edge_length: float | None = None
    spindle_mode: str = "required"
    min_rpm: int | None = None
    max_rpm: int | None = None
    shank_diameter: float | None = None
    overall_length: float | None = None
    holder_diameter: float | None = None
    stickout: float | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["kind"] = self.kind.value
        return result


@dataclass
class ToolLibrary:
    tools: list[Tool] = field(default_factory=list)
    capacity: int = 12

    def by_station(self, station: int) -> Tool | None:
        return next((tool for tool in self.tools if tool.station == station), None)

    def validate(self) -> list["ValidationIssue"]:
        issues: list[ValidationIssue] = []
        stations = [tool.station for tool in self.tools]
        if len(self.tools) > self.capacity:
            issues.append(ValidationIssue("error", "tool.capacity", "Tool library exceeds 12 stations."))
        if len(set(stations)) != len(stations):
            issues.append(ValidationIssue("error", "tool.duplicate_station", "Tool station numbers must be unique."))
        for tool in self.tools:
            if not 1 <= tool.station <= self.capacity:
                issues.append(ValidationIssue("error", "tool.station", f"Tool station {tool.station} is outside 1-{self.capacity}."))
            if tool.diameter <= 0:
                issues.append(ValidationIssue("error", "tool.diameter", f"Tool {tool.station} must have a positive diameter."))
            if tool.cutting_edge_length is not None and tool.cutting_edge_length <= 0:
                issues.append(ValidationIssue("error", "tool.cutting_edge_length", f"Tool {tool.station} cutting-edge length must be positive."))
            for field_name, value in (("shank diameter", tool.shank_diameter), ("overall length", tool.overall_length), ("holder diameter", tool.holder_diameter), ("stickout", tool.stickout)):
                if value is not None and value <= 0:
                    issues.append(ValidationIssue("error", "tool.geometry", f"Tool {tool.station} {field_name} must be positive."))
            if tool.spindle_mode not in {"required", "optional", "disabled"}:
                issues.append(ValidationIssue("error", "tool.spindle_mode", f"Tool {tool.station} has an invalid spindle mode."))
            if tool.spindle_mode == "disabled" and tool.spindle_rpm is not None:
                issues.append(ValidationIssue("error", "tool.spindle_disabled", f"Tool {tool.station} must not have an RPM when its spindle is disabled."))
            if tool.min_rpm is not None and tool.max_rpm is not None and tool.min_rpm > tool.max_rpm:
                issues.append(ValidationIssue("error", "tool.rpm_range", f"Tool {tool.station} minimum RPM exceeds maximum RPM."))
        return issues


class OffsetSide(str, Enum):
    INSIDE = "inside"
    OUTSIDE = "outside"
    LEFT = "left"
    RIGHT = "right"
    ON = "on"


class FillStrategy(str, Enum):
    CONCENTRIC = "concentric"
    SPEED_CLEANOUT = "speed_cleanout"
    RASTER = "raster"


OPERATION_SEQUENCE_ROLES = (
    "automatic", "mark_engrave", "drill_thread", "shelf_recess",
    "pocket_fill", "inside_profile", "outside_profile", "finish_cutoff",
)


@dataclass
class Operation:
    name: str
    tool_station: int
    entity_ids: list[str]
    target_depth: float
    safe_z: float
    enabled: bool = True
    sequence_role: str = "automatic"
    sequence_priority: int = 0
    sequence_locked: bool = False
    recipe_name: str = ""
    recipe_metadata: dict[str, Any] = field(default_factory=dict)
    small_part_strategy: str = "automatic"
    feed_rate_override: float | None = None
    plunge_rate_override: float | None = None
    spindle_rpm_override: int | None = None
    # Optional associative query used to re-resolve geometry after a revised
    # DXF is imported. Empty means the operation remains explicitly bound to
    # its stored entity IDs.
    geometry_selector: dict[str, Any] = field(default_factory=dict)
    # Hard ordering constraints. These are independent of process-role sorting
    # and are honored by GUI, simulation, preflight, and postprocessing.
    depends_on_ids: list[str] = field(default_factory=list)
    # Explicit retained-bottom-skin contract for an integrated two-tool profile.
    # Not an onion-skin removal pass; release is a separate operator action.
    profile_retention: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("operation"))
    kind: ClassVar[str] = "operation"

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["kind"] = self.kind
        for key, value in list(result.items()):
            if isinstance(value, Enum):
                result[key] = value.value
        return result


@dataclass
class RouterOffsetOperation(Operation):
    side: OffsetSide = OffsetSide.OUTSIDE
    entity_sides: dict[str, str] = field(default_factory=dict)
    # Ordering within a grouped closed-profile operation.  The compatibility
    # key ``inside_first`` means deepest nested geometry outward; compensation
    # side alone is not a safe release order for three-level nesting.
    contour_order: str = "inside_first"
    # Optional job-specific override authored by Inspect > Cut Order. Recipes
    # deliberately do not save these geometry IDs.
    manual_contour_order: list[str] = field(default_factory=list)
    allowance: float = 0.0
    stepdown: float | None = None
    pass_depths: list[float] = field(default_factory=list)
    cut_direction: str = "climb"
    entry_mode: str = "plunge"
    entry_side: str = "automatic_waste"
    helix_diameter: float = 0.0
    helix_min_diameter: float = 0.0
    helix_pitch: float = 0.0
    helix_ramp_angle: float = 0.0
    helix_clearance: float = 0.0
    helix_finish_revolutions: int = 0
    # Tangential feed used while descending around the helix. ``None`` keeps
    # legacy jobs at their plunge feed; new recipes may qualify a faster
    # shallow-ramp feed independently from the straight plunge-to-surface.
    helix_feed_override: float | None = None
    helix_fallback: str = "waste_plunge"
    start_index: int = 0
    lead_in: float = 0.0
    lead_out: float = 0.0
    finishing_pass: bool = False
    # Finishing stock is expressed in the conventional CAM directions:
    # allowance is radial (wall) stock and finish_axial_stock is axial
    # (bottom) stock.  A finishing pass with both values at zero is a true
    # spring pass over the programmed wall at final depth.
    finish_axial_stock: float = 0.0
    finish_feed_override: float | None = None
    # Optional spindle setpoint used only by the final finishing / spring
    # pass.  None preserves the roughing RPM for legacy jobs and recipes.
    finish_spindle_rpm_override: int | None = None
    onion_skin: float = 0.0
    tab_count: int = 0
    tab_width: float = 0.0
    tab_height: float = 0.0
    tab_mode: str = "even"
    corner_tab_offset: float = 1.0
    tab_spacing: float = 8.0
    female_tab_min_perimeter: float = 4.0
    tab_cleanup_min_perimeter: float = 0.0
    remove_tabs_after: bool = False
    tab_cleanup_order: str = "after_each_part"
    # Job-specific reviewed staged cleanup; never a recipe or tab-placement rule.
    tab_release_plan: dict = field(default_factory=dict)
    cut_intent: str = "custom"
    breakthrough: float = 0.0
    # Normalized contour positions (0 <= value < 1). When tab_mode is manual,
    # these positions are the authoritative tab centers.
    manual_tab_positions: list[float] = field(default_factory=list)
    # Curve-aware feed scheduling keeps the cutter-center feed at or below the
    # recipe ceiling while reducing tight inside/concave moves so the cutting
    # edge sees a more consistent wall speed.  It is opt-in until a material
    # recipe has been qualified on the machine.
    curve_feed_mode: str = "off"
    curve_feed_min_percent: float = 25.0
    curve_feed_transition_distance: float = 0.5
    # Common-line pairing is deliberately narrow: exact, reversed straight
    # cutter-center edges are shared once between two compatible outside
    # profiles.  The nesting option places source rectangles one cutter
    # diameter apart so those centerlines coincide.
    common_line_mode: str = "off"
    common_line_tolerance: float = 0.001
    # Legacy jobs retain source directions. New Cut Line operations explicitly
    # assemble mixed-direction connected chains before compensation.
    open_chain_mode: str = "source"
    kind: ClassVar[str] = "router_offset"

    @property
    def tab_reference_depth(self) -> float:
        """Through-cut stock bottom; custom/partial cuts keep their cut-floor datum."""
        return self.target_depth - (self.breakthrough if self.cut_intent == "through" else 0.0)

    @property
    def tab_floor_depth(self) -> float:
        """Depth to the top of the remaining bridge, excluding breakthrough."""
        return self.tab_reference_depth - self.tab_height


@dataclass
class TabReleaseOperation(RouterOffsetOperation):
    """Job-specific final cleanup stage, never a second copy of the profiles."""
    source_operation_id: str = ""
    release_plan: dict = field(default_factory=dict)
    kind: ClassVar[str] = "tab_release"


@dataclass
class IslandFillOperation(Operation):
    island_entity_ids: list[str] = field(default_factory=list)
    stepover: float | None = None
    stepdown: float | None = None
    pass_mode: str = "maximum_stepdown"
    pass_count: int | None = None
    pass_depths: list[float] = field(default_factory=list)
    bottom_finish_pass: bool = False
    bottom_finish_depth: float = 0.0
    bottom_finish_feed_override: float | None = None
    corner_tags: bool = False
    cut_direction: str = "climb"
    entry_mode: str = "plunge"
    ramp_length: float = 0.0
    helix_diameter: float = 0.0
    helix_min_diameter: float = 0.0
    helix_pitch: float = 0.0
    helix_ramp_angle: float = 0.0
    helix_clearance: float = 0.0
    helix_finish_revolutions: int = 0
    helix_feed_override: float | None = None
    helix_fallback: str = "ramp"
    dwell_seconds: float = 0.0
    # Keep Tool Down links adjacent fill loops only after a same-region swept
    # engagement proof.  Disabled by default so older jobs retain their exact
    # retract behavior.
    keep_tool_down: bool = False
    stay_down_max_distance: float = 0.0
    stay_down_max_engagement: float = 0.0
    stay_down_feed_percent: float = 100.0
    # Very short residual offset loops can otherwise be repeated at every
    # roughing depth, each with its own entry/retract.  When enabled they are
    # deferred to one deliberately separate full-depth cleanup phase.
    sliver_cleanup: bool = False
    sliver_max_path_length: float = 0.0
    sliver_link_distance: float = 0.0
    # Speed Cleanout depth-steps the cutter-center paths bordering the fill
    # walls, then cuts the narrow material left between them once at final
    # depth.  The full-depth phase is only allowed after a swept-area proof;
    # otherwise it follows speed_fallback.
    speed_max_full_depth_engagement: float = 0.0
    speed_center_feed_percent: float = 60.0
    speed_link_distance: float = 0.0
    speed_fallback: str = "depth_step"
    strategy: FillStrategy = FillStrategy.CONCENTRIC
    # Missing intent in legacy jobs must retain the exact saved numeric depth.
    cut_intent: str = "custom"
    breakthrough: float = 0.0
    kind: ClassVar[str] = "island_fill"


@dataclass
class DrillCirclesOperation(Operation):
    peck_depth: float | None = None
    peck_definition: str = "max_depth"
    peck_count: int | None = None
    peck_mode: str = "full_clear"
    retract_height: float = 0.1
    reentry_clearance: float = 0.02
    surface_feed_override: float | None = None
    retract_feed_override: float | None = None
    controlled_retract: bool = False
    return_to_surface: bool = False
    dwell_seconds: float = 0.0
    top_dwell_seconds: float = 0.0
    kind: ClassVar[str] = "drill_circles"


@dataclass
class DragKnifeOperation(Operation):
    """Passive swivel knife; depth is blade penetration, not spring compression."""
    blade_offset: float = 0.0
    swivel_depth: float = 0.0
    swivel_angle: float = 20.0
    approach_height: float = 0.05
    alignment_length: float = 0.0
    curve_tolerance: float = 0.001
    passes: int = 1
    reverse_paths: bool = False
    setup_verified: bool = False
    kind: ClassVar[str] = "drag_knife"


@dataclass
class EngraveOperation(Operation):
    """Spring-loaded spindle-off drag engraving directly on source contours."""

    approach_height: float = 0.05
    reverse_open_paths: bool = False
    require_stay_on_material: bool = True
    kind: ClassVar[str] = "engrave"


@dataclass
class ThreadMillOperation(Operation):
    """Controller-neutral internal UN thread-milling operation."""

    thread_designation: str = "1/4-20 UNC-2B"
    pitch_diameter_adjustment: float = 0.0
    radial_passes: int = 1
    spring_passes: int = 0
    handedness: str = "right"
    cut_direction: str = "climb"
    cutter_verified: bool = False
    pilot_hole_depth: float | None = None
    bottom_clearance: float = 0.0
    kind: ClassVar[str] = "thread_mill"


@dataclass
class RestMachiningOperation(Operation):
    """Clear material inaccessible to a referenced earlier island-fill tool."""

    source_operation_id: str = ""
    island_entity_ids: list[str] = field(default_factory=list)
    stepover: float | None = None
    stepdown: float | None = None
    stock_to_leave: float = 0.0
    ignore_stock_less_than: float = 0.0
    kind: ClassVar[str] = "rest_machining"


@dataclass
class ProfileRestMachiningOperation(Operation):
    """Clean profile regions that a referenced larger router cutter could not reach."""

    source_operation_id: str = ""
    stepdown: float | None = None
    stock_to_leave: float = 0.0
    ignore_stock_less_than: float = 0.0
    inherit_pass_schedule: bool = True
    preserve_tabs: bool = True
    remove_tabs_after: bool = False
    # Versioned fingerprints of the parent geometry, cut strategy, source
    # cutter, and stock-model interpretation used to generate this child.
    source_snapshot: dict[str, Any] = field(default_factory=dict)
    kind: ClassVar[str] = "profile_rest_machining"


OPERATION_TYPES: dict[str, type[Operation]] = {
    DragKnifeOperation.kind: DragKnifeOperation,
    TabReleaseOperation.kind: TabReleaseOperation,
    RouterOffsetOperation.kind: RouterOffsetOperation,
    IslandFillOperation.kind: IslandFillOperation,
    DrillCirclesOperation.kind: DrillCirclesOperation,
    EngraveOperation.kind: EngraveOperation,
    ThreadMillOperation.kind: ThreadMillOperation,
    RestMachiningOperation.kind: RestMachiningOperation,
    ProfileRestMachiningOperation.kind: ProfileRestMachiningOperation,
}

SEQUENCE_ROLE_ORDER = {
    "mark_engrave": 10,
    "drill_thread": 20,
    "shelf_recess": 30,
    "pocket_fill": 40,
    "inside_profile": 50,
    "outside_profile": 60,
    "finish_cutoff": 70,
}


def inferred_sequence_role(operation: Operation) -> str:
    if operation.sequence_role != "automatic":
        return operation.sequence_role
    if isinstance(operation, EngraveOperation):
        return "mark_engrave"
    if isinstance(operation, (DrillCirclesOperation, ThreadMillOperation)):
        return "drill_thread"
    if isinstance(operation, (IslandFillOperation, RestMachiningOperation)):
        return "pocket_fill"
    if isinstance(operation, ProfileRestMachiningOperation):
        return operation.sequence_role if operation.sequence_role != "automatic" else "inside_profile"
    if isinstance(operation, RouterOffsetOperation):
        if operation.side is OffsetSide.INSIDE:
            return "inside_profile"
        if operation.side is OffsetSide.OUTSIDE:
            return "outside_profile"
    return "finish_cutoff"


def operation_sequence_key(operation: Operation) -> tuple[int, int, int]:
    """Stable ordering key independent of physical tool station."""
    role = inferred_sequence_role(operation)
    hold_down_priority = -1 if operation.small_part_strategy == "order_early" else 0
    return SEQUENCE_ROLE_ORDER.get(role, 80), hold_down_priority, operation.sequence_priority


def effective_tool(project: "CamProject", operation: Operation) -> Tool | None:
    """Return an operation-specific tool view without mutating the turret."""
    from dataclasses import replace
    tool = project.tool_library.by_station(operation.tool_station)
    if tool is None:
        return None
    return replace(
        tool,
        feed_rate=operation.feed_rate_override if operation.feed_rate_override is not None else tool.feed_rate,
        plunge_rate=operation.plunge_rate_override if operation.plunge_rate_override is not None else tool.plunge_rate,
        spindle_rpm=operation.spindle_rpm_override if operation.spindle_rpm_override is not None else tool.spindle_rpm,
    )


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    code: str
    message: str
    # Geometry-aware issues keep their targets as structured data.  The UI can
    # then use readable object names and navigate to the exact problem without
    # scraping an internal UUID out of the operator-facing message.
    entity_ids: tuple[str, ...] = ()
    point: Point | None = None


@dataclass
class CamProject:
    name: str
    units: str = "in"
    entities: list[Entity] = field(default_factory=list)
    tool_library: ToolLibrary = field(default_factory=ToolLibrary)
    operations: list[Operation] = field(default_factory=list)
    source: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    keepouts: list["KeepoutZone"] = field(default_factory=list)
    # Shop-drawing annotations are intentionally separate from entities so
    # they cannot enter selection, nesting, CAM operations, or controller
    # output merely because they are visible on the canvas.
    dimensions: list["DimensionAnnotation"] = field(default_factory=list)
    # Infinite drafting guides are document aids, not Entity instances.  This
    # separate collection is the hard boundary that keeps them out of CAM.
    guides: list["Guide"] = field(default_factory=list)
    print_job: PrintJobData | None = None
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> list[ValidationIssue]:
        issues = self.tool_library.validate()
        if self.units not in SUPPORTED_UNITS:
            issues.append(ValidationIssue("error", "project.units", f"Unsupported units: {self.units!r}."))

        entity_ids = [entity.id for entity in self.entities]
        entity_id_set = set(entity_ids)
        entity_by_id = {entity.id: entity for entity in self.entities}
        if len(entity_ids) != len(entity_id_set):
            issues.append(ValidationIssue("error", "entity.duplicate_id", "Entity IDs must be unique."))
        dimension_ids: set[str] = set()
        for dimension in self.dimensions:
            if dimension.id in dimension_ids:
                issues.append(ValidationIssue("error", "dimension.duplicate_id", f"Duplicate dimension ID: {dimension.id}."))
            dimension_ids.add(dimension.id)
            try:
                dimension.validate()
            except ValueError as exc:
                issues.append(ValidationIssue("error", "dimension.invalid", str(exc)))
        guide_ids: set[str] = set()
        for guide in self.guides:
            if guide.id in guide_ids:
                issues.append(ValidationIssue("error", "guide.duplicate_id", f"Duplicate guide ID: {guide.id}."))
            guide_ids.add(guide.id)
            try:
                guide.validate()
            except ValueError as exc:
                issues.append(ValidationIssue("error", "guide.invalid", str(exc)))
        for entity in self.entities:
            if entity.appearance is not None:
                try:
                    entity.appearance.validate()
                except ValueError as exc:
                    issues.append(ValidationIssue("error", "entity.appearance", str(exc), (entity.id,)))
            if not isinstance(entity, TextEntity):
                continue
            if not entity.text.strip():
                issues.append(ValidationIssue("warning", "text.empty", "An editable text annotation is empty.", (entity.id,)))
            if entity.height <= 0 or entity.width_factor <= 0:
                issues.append(ValidationIssue("error", "text.dimensions", "Editable text requires positive height and width factor.", (entity.id,)))
            if entity.alignment not in {"left", "center", "right"}:
                issues.append(ValidationIssue("error", "text.alignment", f"Editable text has unsupported alignment {entity.alignment!r}.", (entity.id,)))

        operation_ids: set[str] = set()
        for operation in self.operations:
            if operation.id in operation_ids:
                issues.append(ValidationIssue("error", "operation.duplicate_id", f"Duplicate operation ID: {operation.id}."))
            operation_ids.add(operation.id)
            if operation.sequence_role not in OPERATION_SEQUENCE_ROLES:
                issues.append(ValidationIssue("error", "operation.sequence_role", f"{operation.name} has an unsupported sequence role: {operation.sequence_role!r}."))
            if not -999 <= operation.sequence_priority <= 999:
                issues.append(ValidationIssue("error", "operation.sequence_priority", f"{operation.name} sequence priority must be between -999 and 999."))
            if operation.small_part_strategy not in {"automatic", "none", "tabs", "onion_skin", "order_early"}:
                issues.append(ValidationIssue("error", "operation.small_part_strategy", f"{operation.name} has an unsupported small-part strategy."))
            if isinstance(operation, RouterOffsetOperation) and operation.female_tab_min_perimeter < 0:
                issues.append(ValidationIssue("error", "operation.female_tab_min_perimeter", f"{operation.name} female tab threshold cannot be negative."))
            if isinstance(operation, RouterOffsetOperation) and operation.tab_cleanup_min_perimeter < 0:
                issues.append(ValidationIssue("error", "operation.tab_cleanup_min_perimeter", f"{operation.name} tab-cleanup perimeter threshold cannot be negative."))
            if isinstance(operation, RouterOffsetOperation) and operation.tab_cleanup_order not in {"after_each_part", "end_of_operation"}:
                issues.append(ValidationIssue("error", "operation.tab_cleanup_order", f"{operation.name} has an invalid tab-cleanup order."))
            if isinstance(operation, RouterOffsetOperation) and not isinstance(operation.tab_release_plan, dict):
                issues.append(ValidationIssue("error", "operation.tab_release_plan", f"{operation.name} has an invalid staged tab-cleanup plan; clear or re-plan it."))
            if isinstance(operation, RouterOffsetOperation) and operation.contour_order not in {"inside_first", "part_by_part", "source_order"}:
                issues.append(ValidationIssue("error", "operation.contour_order", f"{operation.name} has an invalid contour order."))
            missing_dependencies = sorted(set(operation.depends_on_ids) - {item.id for item in self.operations})
            if operation.id in operation.depends_on_ids:
                issues.append(ValidationIssue("error", "operation.self_dependency", f"{operation.name} cannot depend on itself."))
            if missing_dependencies:
                issues.append(ValidationIssue("error", "operation.missing_dependency", f"{operation.name} references missing prerequisite operations: {', '.join(missing_dependencies)}."))
            if isinstance(operation, RouterOffsetOperation):
                if operation.tab_mode == "manual" and not operation.manual_tab_positions:
                    issues.append(ValidationIssue("error", "operation.manual_tabs", f"{operation.name} manual tab placement requires at least one position."))
                if any(not 0 <= value < 1 for value in operation.manual_tab_positions):
                    issues.append(ValidationIssue("error", "operation.manual_tabs", f"{operation.name} manual tab positions must be normalized from 0 up to, but not including, 1."))
                if operation.finish_axial_stock < 0:
                    issues.append(ValidationIssue("error", "operation.finish_axial_stock", f"{operation.name} axial stock to leave cannot be negative."))
                if operation.finishing_pass and operation.finish_axial_stock >= operation.target_depth:
                    issues.append(ValidationIssue("error", "operation.finish_axial_stock", f"{operation.name} axial stock to leave must be smaller than final depth."))
                if operation.finish_feed_override is not None and operation.finish_feed_override <= 0:
                    issues.append(ValidationIssue("error", "operation.finish_feed", f"{operation.name} finishing feed must be positive."))
                if operation.finish_spindle_rpm_override is not None and operation.finish_spindle_rpm_override <= 0:
                    issues.append(ValidationIssue("error", "operation.finish_rpm", f"{operation.name} finishing RPM must be positive."))
                if operation.helix_feed_override is not None and operation.helix_feed_override <= 0:
                    issues.append(ValidationIssue("error", "operation.helix_feed", f"{operation.name} helical feed must be positive."))
            if isinstance(operation, IslandFillOperation):
                if operation.strategy not in {FillStrategy.CONCENTRIC, FillStrategy.SPEED_CLEANOUT}:
                    issues.append(ValidationIssue("error", "operation.fill_strategy", f"{operation.name} has an unsupported fill strategy."))
                if operation.pass_mode not in {"maximum_stepdown", "equal_count", "custom_increments"}:
                    issues.append(ValidationIssue("error", "operation.fill_pass_mode", f"{operation.name} has an unsupported fill pass mode."))
                if operation.cut_direction not in {"climb", "conventional", "bidirectional"}:
                    issues.append(ValidationIssue("error", "operation.fill_direction", f"{operation.name} has an unsupported fill direction."))
                if operation.entry_mode not in {"plunge", "ramp", "helical"}:
                    issues.append(ValidationIssue("error", "operation.fill_entry", f"{operation.name} has an unsupported fill entry mode."))
                if operation.helix_fallback not in {"ramp", "plunge", "error"}:
                    issues.append(ValidationIssue("error", "operation.fill_entry", f"{operation.name} has an unsupported helical-entry fallback."))
                if min(operation.ramp_length, operation.helix_diameter, operation.helix_pitch, operation.dwell_seconds) < 0:
                    issues.append(ValidationIssue("error", "operation.fill_entry", f"{operation.name} entry dimensions and dwell cannot be negative."))
                if operation.entry_mode == "helical" and (operation.helix_diameter <= 0 or operation.helix_pitch <= 0):
                    issues.append(ValidationIssue("error", "operation.fill_entry", f"{operation.name} helical entry requires a positive diameter and pitch per revolution."))
                if operation.entry_mode == "helical" and operation.ramp_length <= 0:
                    issues.append(ValidationIssue("error", "operation.fill_entry", f"{operation.name} helical fill requires a positive follow-on ramp length for its concentric coverage loops."))
                if operation.helix_feed_override is not None and operation.helix_feed_override <= 0:
                    issues.append(ValidationIssue("error", "operation.fill_helix_feed", f"{operation.name} helical feed must be positive."))
                if operation.bottom_finish_feed_override is not None and operation.bottom_finish_feed_override <= 0:
                    issues.append(ValidationIssue("error", "operation.fill_finish_feed", f"{operation.name} bottom-cleanup feed must be positive."))
                if operation.sliver_max_path_length < 0 or operation.sliver_link_distance < 0:
                    issues.append(ValidationIssue("error", "operation.fill_sliver_cleanup", f"{operation.name} short-path cleanup dimensions cannot be negative."))
                if operation.sliver_cleanup and operation.sliver_max_path_length <= 0:
                    issues.append(ValidationIssue("error", "operation.fill_sliver_cleanup", f"{operation.name} short-path cleanup requires a positive path-length threshold."))
                if min(operation.speed_max_full_depth_engagement, operation.speed_link_distance) < 0:
                    issues.append(ValidationIssue("error", "operation.speed_cleanout", f"{operation.name} Speed Cleanout dimensions cannot be negative."))
                if not 0 < operation.speed_center_feed_percent <= 100:
                    issues.append(ValidationIssue("error", "operation.speed_cleanout", f"{operation.name} Speed Cleanout feed must be greater than 0% and no more than 100%."))
                if operation.speed_fallback not in {"depth_step", "error"}:
                    issues.append(ValidationIssue("error", "operation.speed_cleanout", f"{operation.name} has an unsupported Speed Cleanout fallback."))
                if operation.strategy is FillStrategy.SPEED_CLEANOUT and operation.speed_max_full_depth_engagement <= 0:
                    issues.append(ValidationIssue("error", "operation.speed_cleanout", f"{operation.name} Speed Cleanout requires a positive full-depth engagement limit."))
                if operation.strategy is FillStrategy.SPEED_CLEANOUT and operation.speed_link_distance <= 0:
                    issues.append(ValidationIssue("error", "operation.speed_cleanout", f"{operation.name} Speed Cleanout requires a positive cleared-entry link distance."))
            if isinstance(operation, DrillCirclesOperation):
                if operation.peck_mode not in {"none", "chip_break", "full_clear"}:
                    issues.append(ValidationIssue("error", "operation.peck_mode", f"{operation.name} has an unsupported peck mode."))
                if operation.peck_mode != "none":
                    if operation.peck_definition not in {"max_depth", "count"}:
                        issues.append(ValidationIssue("error", "operation.peck_definition", f"{operation.name} has an unsupported peck definition."))
                    if operation.peck_definition == "count" and (operation.peck_count is None or operation.peck_count < 1):
                        issues.append(ValidationIssue("error", "operation.peck_count", f"{operation.name} peck count must be at least 1."))
                if operation.retract_height <= 0 or operation.reentry_clearance < 0:
                    issues.append(ValidationIssue("error", "operation.peck_retract", f"{operation.name} has invalid retract or re-entry clearance values."))
                if operation.surface_feed_override is not None and operation.surface_feed_override <= 0:
                    issues.append(ValidationIssue("error", "operation.surface_feed", f"{operation.name} surface approach feed must be positive."))
                if operation.retract_feed_override is not None and operation.retract_feed_override <= 0:
                    issues.append(ValidationIssue("error", "operation.retract_feed", f"{operation.name} retract feed must be positive."))
                if operation.dwell_seconds < 0 or operation.top_dwell_seconds < 0:
                    issues.append(ValidationIssue("error", "operation.drill_dwell", f"{operation.name} dwell times cannot be negative."))
            if not operation.enabled:
                continue
            if self.tool_library.by_station(operation.tool_station) is None:
                issues.append(ValidationIssue("error", "operation.missing_tool", f"{operation.name} references empty tool station {operation.tool_station}."))
            missing = sorted(set(operation.entity_ids) - entity_id_set)
            if hasattr(operation, "island_entity_ids"):
                missing.extend(sorted(set(operation.island_entity_ids) - entity_id_set))
            if missing:
                issues.append(ValidationIssue("error", "operation.missing_entity", f"{operation.name} references missing entities: {', '.join(missing)}."))
            annotation_ids = sorted(
                entity_id for entity_id in set(operation.entity_ids) | set(getattr(operation, "island_entity_ids", ()))
                if isinstance(entity_by_id.get(entity_id), TextEntity)
            )
            if annotation_ids:
                issues.append(ValidationIssue(
                    "error", "operation.annotation_entity",
                    f"{operation.name} references editable text. Convert text to curves or use Engrave Text before machining.",
                    tuple(annotation_ids),
                ))
            if operation.safe_z <= 0:
                issues.append(ValidationIssue("error", "operation.safe_z", f"{operation.name} must use a positive clearance magnitude."))
            if operation.target_depth <= 0:
                issues.append(ValidationIssue("error", "operation.depth", f"{operation.name} must use a positive cut-depth magnitude."))
        dependencies = {operation.id: set(operation.depends_on_ids) for operation in self.operations}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(operation_id: str) -> bool:
            if operation_id in visiting:
                return True
            if operation_id in visited:
                return False
            visiting.add(operation_id)
            cyclic = any(visit(value) for value in dependencies.get(operation_id, ()) if value in dependencies)
            visiting.remove(operation_id)
            visited.add(operation_id)
            return cyclic

        if any(visit(operation_id) for operation_id in dependencies):
            issues.append(ValidationIssue("error", "operation.dependency_cycle", "Operation prerequisites contain a cycle; remove at least one dependency before output."))
        if self.print_job is not None:
            issues.extend(
                ValidationIssue(issue.severity, issue.code, issue.message)
                for issue in validate_print_job(self.print_job, self)
            )
        return issues

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "name": self.name,
            "units": self.units,
            # A serialized project is also used as the clone boundary for
            # per-plate output.  These mappings must not remain shared with
            # the live project or localizing an output plate can silently
            # replace the source project's plate metadata.
            "source": copy.deepcopy(self.source),
            "metadata": copy.deepcopy(self.metadata),
            "keepouts": [zone.to_dict() for zone in self.keepouts],
            "entities": [entity.to_dict() for entity in self.entities],
            "tool_library": {
                "capacity": self.tool_library.capacity,
                "tools": [tool.to_dict() for tool in self.tool_library.tools],
            },
            "operations": [operation.to_dict() for operation in self.operations],
        }
        if self.dimensions:
            payload["dimensions"] = [dimension.to_dict() for dimension in self.dimensions]
        if self.guides:
            payload["guides"] = [guide.to_dict() for guide in self.guides]
        if self.print_job is not None:
            payload["print_job"] = self.print_job.to_dict()
        return payload

    def save(self, path: Path, *, cache_entries=None) -> None:
        payload = self.to_dict()
        if cache_entries is not None:
            from .saved_cleanout_cache import FIELD, export_cache
            saved = export_cache(self, cache_entries)
            if saved is not None:
                payload[FIELD] = saved
        write_project_payload(path, payload)

    @classmethod
    def load(cls, path: Path) -> "CamProject":
        payload = read_project_payload(path)
        project = cls.from_dict(payload)
        from .saved_cleanout_cache import FIELD
        if FIELD in payload:
            # Transient compressed data, excluded from history/fingerprints and
            # stripped before launching workers. Decode only through safe JSON.
            project._saved_cleanout_cache = payload[FIELD]
        return project

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CamProject":
        from .dimensions import DimensionAnnotation
        from .guides import Guide

        version = payload.get("schema_version")
        if version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported CAM project schema version: {version!r}")

        entities = [_entity_from_dict(item) for item in payload.get("entities", [])]
        tool_data = payload.get("tool_library", {})
        tools = [Tool(**{**item, "kind": ToolKind(item.get("kind", ToolKind.END_MILL.value))}) for item in tool_data.get("tools", [])]
        operations = [_operation_from_dict(item) for item in payload.get("operations", [])]
        keepouts = [KeepoutZone.from_dict(item) for item in payload.get("keepouts", [])]
        dimensions = [DimensionAnnotation.from_dict(item) for item in payload.get("dimensions", [])]
        guides = [Guide.from_dict(item) for item in payload.get("guides", [])]
        project = cls(
            name=payload["name"],
            units=payload.get("units", "in"),
            entities=entities,
            tool_library=ToolLibrary(tools=tools, capacity=tool_data.get("capacity", 12)),
            operations=operations,
            source=copy.deepcopy(payload.get("source", {})),
            metadata=copy.deepcopy(payload.get("metadata", {})),
            keepouts=keepouts,
            dimensions=dimensions,
            guides=guides,
            print_job=PrintJobData.from_dict(payload["print_job"]) if payload.get("print_job") is not None else None,
            schema_version=version,
        )
        # Jobs saved before inside-profile containment alternation stored every
        # selected contour as female/inside. Repair that unambiguous legacy
        # signature while all entities and operations are available together.
        from .topology import upgrade_legacy_inside_profile_sides
        upgrade_legacy_inside_profile_sides(project.entities, project.operations)
        return project


def _point(value: dict[str, Any]) -> Point:
    return Point(float(value["x"]), float(value["y"]))


def _entity_from_dict(data: dict[str, Any]) -> Entity:
    values = dict(data)
    kind = values.pop("kind")
    entity_type = ENTITY_TYPES.get(kind)
    if entity_type is None:
        raise ValueError(f"Unsupported entity kind: {kind!r}")
    for key in ("start", "end", "center", "corner_a", "corner_b", "insertion"):
        if key in values:
            values[key] = _point(values[key])
    if "points" in values:
        values["points"] = [_point(point) for point in values["points"]]
    if values.get("appearance") is not None:
        values["appearance"] = ObjectAppearance.from_dict(values["appearance"])
    return entity_type(**values)


def _operation_from_dict(data: dict[str, Any]) -> Operation:
    values = dict(data)
    kind = values.pop("kind")
    operation_type = OPERATION_TYPES.get(kind)
    if operation_type is None:
        raise ValueError(f"Unsupported operation kind: {kind!r}")
    if issubclass(operation_type, RouterOffsetOperation):
        values["side"] = OffsetSide(values.get("side", OffsetSide.OUTSIDE.value))
    if operation_type is IslandFillOperation:
        values["strategy"] = FillStrategy(values.get("strategy", FillStrategy.CONCENTRIC.value))
    return operation_type(**values)


def issues_with_severity(issues: Iterable[ValidationIssue], severity: str) -> list[ValidationIssue]:
    return [issue for issue in issues if issue.severity == severity]


@dataclass
class KeepoutZone:
    """A machine-space polygon that cutting/rapid motion must not enter."""

    name: str
    points: list[Point]
    clearance_z: float = 0.5
    enabled: bool = True
    id: str = field(default_factory=lambda: new_id("keepout"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "KeepoutZone":
        values = dict(payload)
        values["points"] = [_point(point) for point in values.get("points", [])]
        return cls(**values)
