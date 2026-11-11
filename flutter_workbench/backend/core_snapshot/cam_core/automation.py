"""Deterministic command layer shared by the GUI and headless automation.

Maintenance rule: a new user-visible model mutation should either register a
command here or be explicitly documented as view-only in AUTOMATION_COMMANDS.md.
The ``automation.commands`` command is the live, machine-readable reference.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import copy
from pathlib import Path
from typing import Any, Callable

from .dxf import dxf_output_filename, export_dxf, import_dxf, import_dxfs_as_plates
from .postprocess import cnc_output_filename, post_enroute_cnc
from .feature_recognition import circles_by_diameter, recognize_circular_polylines
from .selection import SELECTION_ACTIONS, combine_selection, filter_entities
from .geometry_diagnostics import GeometryDiagnosticOptions, diagnose_geometry
from .guides import GUIDE_ORIENTATIONS, Guide
from .appearance import ObjectAppearance
from .layout import drawing_bounds
from .nesting import nest_entities_to_plates
from .optimization import (
    cycle_optimization_settings, default_cycle_optimization_settings,
    production_cycle_optimization_settings,
    validate_cycle_optimization_settings,
)
from .stock import compare_project_cycle_time
from .model import (
    CamProject, Circle, DrillCirclesOperation, EngraveOperation, FillStrategy, IslandFillOperation,
    Line, OffsetSide, Point, Polyline, ProfileRestMachiningOperation, Rectangle, RouterOffsetOperation,
    TextEntity, ThreadMillOperation,
    OPERATION_SEQUENCE_ROLES, SUPPORTED_UNITS, Tool, ToolKind, KeepoutZone, new_id,
)
from .text_geometry import resolve_font, text_to_outline_polylines, text_to_single_line_geometry
from .plates import (
    Plate, append_plate_definition, install_automatic_cutoff_line, make_automatic_cutoff_line, make_plate_project, replace_plate_definition,
    save_plate_project, validate_plate_definitions,
)
from .preflight import run_preflight
from .thread_specs import thread_standard, thread_standards_for_pilot_diameters
from .transform import mirror_entity, transform_entity
from .shape_construction import apply_cleave_result, cleave_shapes
from .units import unit_scale
from .workflow import ordered_operations, output_verification_rows, small_part_risks, tool_consolidation_suggestions
from .workflow_features import (
    ToolpathTemplate, analyze_toolpath_completeness, create_toolpath_template,
    instantiate_toolpath_template, keepout_violations, reconcile_dxf_revision,
    refresh_associative_operations, selector_from_entities,
)


class AutomationCommandError(ValueError):
    """Expected command or parameter failure suitable for a JSON response."""

    def __init__(self, message: str, code: str = "command.invalid") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CommandEffect:
    data: Any = None
    changed: bool = False
    reset_history: bool = False
    record_history: bool = True


@dataclass(frozen=True)
class CommandSpec:
    name: str
    description: str
    parameters: dict[str, str]
    handler: Callable[["AutomationSession", dict[str, Any]], CommandEffect]

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": dict(self.parameters),
        }


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, CommandSpec] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, str] | None = None,
    ) -> Callable[[Callable[["AutomationSession", dict[str, Any]], CommandEffect]], Callable]:
        def decorator(handler: Callable[["AutomationSession", dict[str, Any]], CommandEffect]) -> Callable:
            if name in self._commands:
                raise RuntimeError(f"Duplicate automation command: {name}")
            self._commands[name] = CommandSpec(name, description, parameters or {}, handler)
            return handler

        return decorator

    def get(self, name: str) -> CommandSpec:
        try:
            return self._commands[name]
        except KeyError as exc:
            raise AutomationCommandError(f"Unknown automation command: {name}", "command.unknown") from exc

    def descriptions(self) -> list[dict[str, Any]]:
        return [self._commands[name].describe() for name in sorted(self._commands)]


REGISTRY = CommandRegistry()


@dataclass
class AutomationSession:
    """Stateful automation workspace with independent undo/redo snapshots."""

    project: CamProject = field(default_factory=lambda: CamProject("Untitled"))
    project_path: Path | None = None
    selected_ids: set[str] = field(default_factory=set)
    _history: list[dict[str, Any]] = field(default_factory=list, init=False, repr=False)
    _history_index: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        self.project_path = Path(self.project_path) if self.project_path is not None else None
        self.selected_ids.intersection_update(entity.id for entity in self.project.entities)
        self._reset_history()

    def _reset_history(self) -> None:
        self._history = [copy.deepcopy(self.project.to_dict())]
        self._history_index = 0

    def _record_history(self) -> None:
        self._history = self._history[: self._history_index + 1]
        self._history.append(copy.deepcopy(self.project.to_dict()))
        self._history_index = len(self._history) - 1

    def execute(self, command: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute one command and always return a JSON-serializable result."""
        request = parameters or {}
        if not isinstance(request, dict):
            return _error_result(command, "command.parameters", "Command parameters must be a JSON object.")
        original_project = copy.deepcopy(self.project.to_dict())
        original_path = self.project_path
        original_selection = set(self.selected_ids)
        try:
            spec = REGISTRY.get(command)
            effect = spec.handler(self, request)
            if effect.reset_history:
                self._reset_history()
            elif effect.changed and effect.record_history:
                self._record_history()
            return {
                "ok": True,
                "command": command,
                "changed": effect.changed,
                "data": effect.data,
            }
        except AutomationCommandError as exc:
            self.project = CamProject.from_dict(original_project)
            self.project_path = original_path
            self.selected_ids = original_selection
            return _error_result(command, exc.code, str(exc))
        except (KeyError, TypeError, ValueError, OSError) as exc:
            self.project = CamProject.from_dict(original_project)
            self.project_path = original_path
            self.selected_ids = original_selection
            return _error_result(command, "command.failed", str(exc))

    @property
    def can_undo(self) -> bool:
        return self._history_index > 0

    @property
    def can_redo(self) -> bool:
        return self._history_index < len(self._history) - 1


def _error_result(command: str, code: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "command": command,
        "changed": False,
        "error": {"code": code, "message": message},
    }


def _number(params: dict[str, Any], name: str, *, default: float | None = None) -> float:
    value = params.get(name, default)
    if value is None:
        raise AutomationCommandError(f"Missing required parameter: {name}", "parameter.missing")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise AutomationCommandError(f"Parameter {name!r} must be numeric.", "parameter.type") from exc


def _text(params: dict[str, Any], name: str, *, default: str | None = None) -> str:
    value = params.get(name, default)
    if value is None:
        raise AutomationCommandError(f"Missing required parameter: {name}", "parameter.missing")
    if not isinstance(value, str) or not value.strip():
        raise AutomationCommandError(f"Parameter {name!r} must be non-empty text.", "parameter.type")
    return value.strip()


def _requested_ids(session: AutomationSession, params: dict[str, Any]) -> set[str]:
    values = params.get("entity_ids")
    if values is None:
        values = sorted(session.selected_ids)
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise AutomationCommandError("entity_ids must be an array of entity ID strings.", "parameter.type")
    requested = set(values)
    known = {entity.id for entity in session.project.entities}
    missing = sorted(requested - known)
    if missing:
        raise AutomationCommandError(f"Unknown entity IDs: {', '.join(missing)}", "entity.missing")
    return requested


def _entity_summary(entity: object) -> dict[str, Any]:
    payload = entity.to_dict()
    return {"id": payload["id"], "kind": payload["kind"], "layer": payload.get("layer", "0")}


def _project_plates(project: CamProject) -> list[Plate]:
    manual = project.metadata.get("plates")
    if isinstance(manual, list) and manual:
        return sorted((Plate.from_dict(item) for item in manual), key=lambda plate: plate.number)
    layout = project.metadata.get("sheet_layout", {})
    if not isinstance(layout, dict) or not layout:
        return []
    width, height = float(layout["width"]), float(layout["height"])
    columns, rows = int(layout.get("columns", 1)), int(layout.get("rows", 1))
    origin_x, origin_y = float(layout.get("origin_x", 0)), float(layout.get("origin_y", 0))
    return [
        Plate(row * columns + column + 1, origin_x + column * width, origin_y + row * height, width, height)
        for row in range(rows) for column in range(columns)
    ]


@REGISTRY.register("automation.commands", "List every registered command and its parameter contract.")
def _commands(_session: AutomationSession, _params: dict[str, Any]) -> CommandEffect:
    return CommandEffect({"commands": REGISTRY.descriptions()})


@REGISTRY.register(
    "validation.compare_controller_files",
    "Semantically compare an EnRoute reference CNC file with a CAM Workbench candidate without writing machine data.",
    {"reference_path": "reference .cnc path", "candidate_path": "candidate .cnc path", "xy_tolerance": "default 0.002 in", "z_tolerance": "default 0.001 in", "feed_tolerance": "default 0.02 in/s"},
)
def _validation_compare_files(_session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    from .validation import compare_controller_programs, parse_controller_file
    reference = parse_controller_file(_text(params, "reference_path"))
    candidate = parse_controller_file(_text(params, "candidate_path"))
    report = compare_controller_programs(
        reference, candidate,
        xy_tolerance=_number(params, "xy_tolerance", default=0.002),
        z_tolerance=_number(params, "z_tolerance", default=0.001),
        feed_tolerance=_number(params, "feed_tolerance", default=0.02),
    )
    return CommandEffect({
        "passed": report.passed,
        "matched_segments": report.matched_segments,
        "reference_segments": report.reference_segments,
        "candidate_segments": report.candidate_segments,
        "issues": [issue.__dict__ for issue in report.issues],
    })


@REGISTRY.register("validation.pass_plan", "Return every generated block in process order for inspection; no machine file is written.")
def _validation_pass_plan(session: AutomationSession, _params: dict[str, Any]) -> CommandEffect:
    from .stock import generate_operation_toolpath
    rows = []
    order = 0
    for operation in ordered_operations([item for item in session.project.operations if item.enabled]):
        path = generate_operation_toolpath(session.project, operation)
        for block in path.blocks:
            order += 1
            feed = next((motion.feed for motion in block.motions if motion.feed is not None and motion.kind.value != "rapid"), None)
            rows.append({
                "order": order, "operation_id": operation.id, "operation": operation.name,
                "tool_station": block.tool_station, "depth": block.metadata.get("depth", operation.target_depth),
                "feed": feed, "purpose": block.metadata.get("role", block.label or operation.name),
                "motion_count": len(block.motions),
            })
    return CommandEffect({"passes": rows})


@REGISTRY.register("project.state", "Return the current project, selection, bounds, and history state.")
def _project_state(session: AutomationSession, _params: dict[str, Any]) -> CommandEffect:
    bounds = drawing_bounds(session.project.entities)
    visual_bounds = drawing_bounds(session.project.entities, include_annotations=True)
    return CommandEffect({
        "name": session.project.name,
        "path": str(session.project_path) if session.project_path else None,
        "units": session.project.units,
        "bounds": list(bounds) if bounds is not None else None,
        "visual_bounds": list(visual_bounds) if visual_bounds is not None else None,
        "editable_text_count": sum(isinstance(entity, TextEntity) for entity in session.project.entities),
        "entities": [_entity_summary(entity) for entity in session.project.entities],
        "operations": [operation.to_dict() for operation in session.project.operations],
        "tools": [tool.to_dict() for tool in session.project.tool_library.tools],
        "selected_entity_ids": sorted(session.selected_ids),
        "can_undo": session.can_undo,
        "can_redo": session.can_redo,
    })


@REGISTRY.register(
    "project.new", "Replace the workspace with a new project.",
    {"name": "optional project name", "units": "in, mm, cm, m, ft, or yd"},
)
def _project_new(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    units = str(params.get("units", "in"))
    if units not in SUPPORTED_UNITS:
        raise AutomationCommandError(f"Unsupported units: {units}", "project.units")
    session.project = CamProject(str(params.get("name", "Untitled")), units=units)
    session.project_path = None
    session.selected_ids.clear()
    return CommandEffect({"name": session.project.name, "units": units}, changed=True, reset_history=True)


@REGISTRY.register("project.open", "Open a CAM JSON project or ASCII DXF.", {"path": "input file path"})
def _project_open(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    path = Path(_text(params, "path")).expanduser().resolve()
    if not path.exists():
        raise AutomationCommandError(f"Project file does not exist: {path}", "file.missing")
    if path.suffix.casefold() == ".dxf":
        imported = import_dxf(path)
        session.project = imported.project
        warnings = imported.warnings
    else:
        session.project = CamProject.load(path)
        warnings = []
    session.project_path = path
    session.selected_ids.clear()
    return CommandEffect({"path": str(path), "warnings": warnings}, changed=True, reset_history=True)


@REGISTRY.register(
    "project.import_dxfs_as_plates",
    "Import multiple ASCII DXFs into one project, assigning one positive-X plate to each file.",
    {
        "paths": "two or more DXF input paths",
        "units": "optional output units; defaults to the first DXF",
        "additional_stock_sizes": "optional mapping of stock label to [width, height] in inches",
        "replace_current": "required true when replacing a non-empty automation session",
    },
)
def _project_import_dxfs_as_plates(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    if (session.project.entities or session.project.operations or session.project_path is not None) and params.get("replace_current") is not True:
        raise AutomationCommandError(
            "Batch DXF import would replace the current automation project. Save it first, then explicitly pass replace_current=true.",
            "project.replace_confirmation_required",
        )
    raw_paths = params.get("paths")
    if not isinstance(raw_paths, list) or not raw_paths or not all(isinstance(value, str) for value in raw_paths):
        raise AutomationCommandError("paths must be a non-empty string array.", "parameter.type")
    paths = [Path(value).expanduser().resolve() for value in raw_paths]
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        raise AutomationCommandError("DXF file(s) do not exist: " + ", ".join(missing), "file.missing")
    units = params.get("units")
    if units is not None and str(units) not in SUPPORTED_UNITS:
        raise AutomationCommandError(f"Unsupported units: {units}", "project.units")
    raw_sizes = params.get("additional_stock_sizes")
    additional_sizes = None
    if raw_sizes is not None:
        if not isinstance(raw_sizes, dict):
            raise AutomationCommandError(
                "additional_stock_sizes must map labels to [width, height] in inches.",
                "parameter.type",
            )
        additional_sizes = {}
        try:
            for label, size in raw_sizes.items():
                if not isinstance(label, str) or not isinstance(size, (list, tuple)) or len(size) != 2:
                    raise ValueError
                width, height = float(size[0]), float(size[1])
                if width <= 0 or height <= 0:
                    raise ValueError
                additional_sizes[label] = (width, height)
        except (TypeError, ValueError) as exc:
            raise AutomationCommandError(
                "Each additional stock size must be a positive [width, height] pair.",
                "parameter.value",
            ) from exc
    try:
        imported = import_dxfs_as_plates(
            paths,
            target_units=str(units) if units is not None else None,
            additional_sizes=additional_sizes,
        )
    except (OSError, ValueError, KeyError) as exc:
        raise AutomationCommandError(str(exc), "dxf.import") from exc
    session.project = imported.project
    session.project_path = None
    session.selected_ids.clear()
    return CommandEffect(
        {
            "paths": [str(path) for path in paths],
            "warnings": imported.warnings,
            "entity_counts": dict(imported.entity_counts),
            "plates": [plate.to_dict() for plate in imported.plates],
            "plate_sources": list(imported.plate_sources),
        },
        changed=True,
        reset_history=True,
    )


@REGISTRY.register("project.save", "Save the current project as versioned CAM JSON.", {"path": "optional output path"})
def _project_save(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    raw_path = params.get("path")
    path = Path(raw_path).expanduser().resolve() if raw_path else session.project_path
    if path is None:
        raise AutomationCommandError("A path is required the first time a project is saved.", "file.path_required")
    if path.suffix.casefold() == ".dxf":
        raise AutomationCommandError("CAM projects cannot overwrite their DXF source; choose a JSON path.", "file.extension")
    session.project.save(path)
    session.project_path = path
    return CommandEffect({"path": str(path)})


@REGISTRY.register(
    "project.export_dxf", "Export editable source geometry to an ASCII DXF with declared units and layers.",
    {"path": "DXF output path", "entity_ids": "optional geometry IDs; omit for the entire drawing"},
)
def _project_export_dxf(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    path = Path(_text(params, "path")).expanduser().resolve()
    requested = params.get("entity_ids")
    entities = None
    if requested is not None:
        if not isinstance(requested, list) or not requested or not all(isinstance(value, str) for value in requested):
            raise AutomationCommandError("entity_ids must be a non-empty string array.", "parameter.type")
        known = {entity.id: entity for entity in session.project.entities}
        missing = sorted(set(requested) - set(known))
        if missing:
            raise AutomationCommandError(f"Unknown entity IDs: {', '.join(missing)}", "geometry.missing")
        entities = [known[entity_id] for entity_id in requested]
    try:
        result = export_dxf(session.project, path, entities)
    except (OSError, ValueError) as exc:
        raise AutomationCommandError(str(exc), "dxf.export") from exc
    return CommandEffect({"path": str(result.path), "entity_counts": dict(result.entity_counts), "layers": list(result.layer_names)})


@REGISTRY.register("project.validate", "Run deterministic CAM preflight validation.")
def _project_validate(session: AutomationSession, _params: dict[str, Any]) -> CommandEffect:
    issues = run_preflight(session.project)
    return CommandEffect({
        "issues": [issue.__dict__ for issue in issues],
        "blocking": any(issue.severity == "error" for issue in issues),
    })


@REGISTRY.register(
    "geometry.diagnose",
    "Find suspicious drawing geometry without modifying it.",
    {
        "gap_min": "default 0.0005, in project units",
        "gap_max": "default 0.03, in project units",
        "short_segment": "default 0.005, in project units",
        "spike_angle_degrees": "default 12",
    },
)
def _geometry_diagnose(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    options = GeometryDiagnosticOptions(
        gap_min=_number(params, "gap_min", default=0.0005),
        gap_max=_number(params, "gap_max", default=0.03),
        short_segment=_number(params, "short_segment", default=0.005),
        spike_angle_degrees=_number(params, "spike_angle_degrees", default=12.0),
    )
    issues = diagnose_geometry(session.project.entities, options)
    return CommandEffect({
        "units": session.project.units,
        "issues": [
            {
                "severity": issue.severity,
                "code": issue.code,
                "message": issue.message,
                "point": {"x": issue.point.x, "y": issue.point.y},
                "entity_ids": list(issue.entity_ids),
                "segment_indices": list(issue.segment_indices),
            }
            for issue in issues
        ],
        "blocking": any(issue.severity == "error" for issue in issues),
    })


@REGISTRY.register("selection.all", "Select every entity in the project.")
def _selection_all(session: AutomationSession, _params: dict[str, Any]) -> CommandEffect:
    session.selected_ids = {entity.id for entity in session.project.entities}
    return CommandEffect({"entity_ids": sorted(session.selected_ids)})


@REGISTRY.register("selection.clear", "Clear the current entity selection.")
def _selection_clear(session: AutomationSession, _params: dict[str, Any]) -> CommandEffect:
    session.selected_ids.clear()
    return CommandEffect({"entity_ids": []})


@REGISTRY.register("selection.set", "Select entities by stable ID.", {"entity_ids": "array of entity IDs"})
def _selection_set(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    session.selected_ids = _requested_ids(session, params)
    return CommandEffect({"entity_ids": sorted(session.selected_ids)})


@REGISTRY.register(
    "selection.circles", "Select circles by tunable diameter and optional layer.",
    {"diameter": "target diameter", "tolerance": "diameter tolerance, default 0", "layer": "optional exact layer", "action": "replace, add, remove, or intersect"},
)
def _selection_circles(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    diameter = _number(params, "diameter")
    tolerance = _number(params, "tolerance", default=0.0)
    layer = params.get("layer")
    layers = None if layer in (None, "") else [str(layer)]
    matches = circles_by_diameter(session.project.entities, diameter, tolerance, layers=layers)
    action = str(params.get("action", "replace"))
    if action not in SELECTION_ACTIONS:
        raise AutomationCommandError("action must be replace, add, remove, or intersect.", "selection.action")
    session.selected_ids = combine_selection(session.selected_ids, (circle.id for circle in matches), action)
    return CommandEffect({"entity_ids": sorted(session.selected_ids), "count": len(matches)})


@REGISTRY.register("selection.invert", "Invert selection across all project geometry.")
def _selection_invert(session: AutomationSession, _params: dict[str, Any]) -> CommandEffect:
    all_ids = {entity.id for entity in session.project.entities}
    session.selected_ids = all_ids - session.selected_ids
    return CommandEffect({"entity_ids": sorted(session.selected_ids), "count": len(session.selected_ids)})


@REGISTRY.register(
    "selection.filter", "Modify selection using geometry type, layer, contour state, nesting, perimeter, and bounding size.",
    {
        "action": "replace, add, remove, or intersect", "kinds": "optional entity-kind array",
        "layers": "optional layer-name array", "closed": "optional boolean", "holes_only": "optional boolean",
        "perimeter_min/perimeter_max": "optional project units", "width_min/width_max": "optional project units",
        "height_min/height_max": "optional project units",
    },
)
def _selection_filter(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    action = str(params.get("action", "replace"))
    if action not in SELECTION_ACTIONS:
        raise AutomationCommandError("action must be replace, add, remove, or intersect.", "selection.action")
    kinds, layers = params.get("kinds"), params.get("layers")
    if kinds is not None and not isinstance(kinds, list) or layers is not None and not isinstance(layers, list):
        raise AutomationCommandError("kinds and layers must be arrays when supplied.", "parameter.type")
    closed = params.get("closed")
    if closed is not None and not isinstance(closed, bool):
        raise AutomationCommandError("closed must be true or false.", "parameter.type")
    optional_values = {}
    try:
        for key in ("perimeter_min", "perimeter_max", "width_min", "width_max", "height_min", "height_max"):
            optional_values[key] = None if params.get(key) in (None, "") else float(params[key])
            if optional_values[key] is not None and optional_values[key] < 0:
                raise ValueError
    except (TypeError, ValueError) as exc:
        raise AutomationCommandError("Selection size limits must be nonnegative numbers.", "parameter.range") from exc
    for low_key, high_key in (("perimeter_min", "perimeter_max"), ("width_min", "width_max"), ("height_min", "height_max")):
        low, high = optional_values[low_key], optional_values[high_key]
        if low is not None and high is not None and low > high:
            raise AutomationCommandError(f"{low_key} cannot exceed {high_key}.", "parameter.range")
    matches = filter_entities(
        session.project.entities, kinds=kinds, layers=layers, closed=closed,
        holes_only=bool(params.get("holes_only", False)), **optional_values,
    )
    session.selected_ids = combine_selection(session.selected_ids, (entity.id for entity in matches), action)
    return CommandEffect({"entity_ids": sorted(session.selected_ids), "match_count": len(matches), "action": action})


@REGISTRY.register(
    "geometry.recognize_circles",
    "Convert qualifying closed polylines into true circles while preserving IDs and layers.",
    {
        "entity_ids": "optional ID array; omit for the entire drawing",
        "layer": "optional exact layer",
        "absolute_tolerance": "maximum radial error in project units; default 0.001",
        "relative_tolerance": "maximum radial error as a radius fraction; default 0.005",
        "minimum_vertices": "default 8",
        "minimum_diameter": "optional diameter lower bound",
        "maximum_diameter": "optional diameter upper bound",
    },
)
def _geometry_recognize_circles(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    requested = params.get("entity_ids")
    entity_ids = _requested_ids(session, params) if requested is not None else None
    layer = params.get("layer")
    layers = None if layer in (None, "") else {str(layer)}
    minimum_diameter = _number(params, "minimum_diameter") if "minimum_diameter" in params else None
    maximum_diameter = _number(params, "maximum_diameter") if "maximum_diameter" in params else None
    try:
        updated, converted = recognize_circular_polylines(
            session.project.entities,
            entity_ids=entity_ids,
            layers=layers,
            absolute_tolerance=_number(params, "absolute_tolerance", default=0.001),
            relative_tolerance=_number(params, "relative_tolerance", default=0.005),
            minimum_vertices=int(_number(params, "minimum_vertices", default=8)),
            minimum_diameter=minimum_diameter,
            maximum_diameter=maximum_diameter,
        )
    except ValueError as exc:
        raise AutomationCommandError(str(exc), "geometry.circle_recognition") from exc
    session.project.entities = updated
    session.selected_ids = {circle.id for circle in converted}
    return CommandEffect({
        "count": len(converted),
        "entity_ids": sorted(session.selected_ids),
        "circles": [
            {
                "id": circle.id,
                "layer": circle.layer,
                "center": {"x": circle.center.x, "y": circle.center.y},
                "diameter": circle.radius * 2.0,
                "maximum_error": circle.metadata.get("circle_fit_max_error"),
            }
            for circle in converted
        ],
    }, changed=bool(converted))


def _automation_layer_settings(project: CamProject) -> dict[str, dict]:
    settings = project.metadata.setdefault("layer_settings", {})
    if not isinstance(settings, dict):
        settings = {}
        project.metadata["layer_settings"] = settings
    known = {entity.layer or "0" for entity in project.entities}
    for name in known:
        value = settings.setdefault(name, {})
        value.setdefault("visible", True)
        value.setdefault("locked", False)
        value.setdefault("color", "#263238")
        value.setdefault("order", len(settings) - 1)
    return settings


@REGISTRY.register("layer.list", "List drawing layers, display state, order, and object counts.")
def _layer_list(session: AutomationSession, _params: dict[str, Any]) -> CommandEffect:
    settings = _automation_layer_settings(session.project)
    counts: dict[str, int] = {}
    for entity in session.project.entities:
        counts[entity.layer or "0"] = counts.get(entity.layer or "0", 0) + 1
    names = sorted(settings, key=lambda name: (int(settings[name].get("order", 999999)), name.casefold()))
    return CommandEffect({"layers": [
        {"name": name, "objects": counts.get(name, 0), **settings[name]} for name in names
    ]})


@REGISTRY.register(
    "layer.set",
    "Create or update a drawing layer without changing geometry.",
    {"name": "layer name", "visible": "optional boolean", "locked": "optional boolean", "color": "optional #RRGGBB", "order": "optional integer"},
)
def _layer_set(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    name = _text(params, "name").strip()
    if not name:
        raise AutomationCommandError("Layer name cannot be empty.", "layer.name")
    settings = _automation_layer_settings(session.project)
    value = settings.setdefault(name, {"visible": True, "locked": False, "color": "#263238", "order": len(settings)})
    value["intentional"] = True
    if "visible" in params:
        value["visible"] = bool(params["visible"])
    if "locked" in params:
        value["locked"] = bool(params["locked"])
    if "color" in params:
        color = str(params["color"])
        if len(color) != 7 or not color.startswith("#") or any(character not in "0123456789abcdefABCDEF" for character in color[1:]):
            raise AutomationCommandError("Layer color must use #RRGGBB format.", "layer.color")
        value["color"] = color
    if "order" in params:
        value["order"] = int(_number(params, "order"))
    return CommandEffect({"name": name, **value}, changed=True)


@REGISTRY.register(
    "layer.move_selection",
    "Move selected or named geometry to an unlocked layer, creating it if needed.",
    {"target": "destination layer", "entity_ids": "optional IDs; defaults to selection"},
)
def _layer_move_selection(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    target = _text(params, "target").strip()
    if not target:
        raise AutomationCommandError("Destination layer cannot be empty.", "layer.name")
    entity_ids = _requested_ids(session, params)
    if not entity_ids:
        raise AutomationCommandError("Select one or more objects to move.", "selection.empty")
    settings = _automation_layer_settings(session.project)
    target_settings = settings.setdefault(target, {"visible": True, "locked": False, "color": "#263238", "order": len(settings)})
    target_settings["intentional"] = True
    if target_settings.get("locked", False):
        raise AutomationCommandError(f"Layer {target} is locked.", "layer.locked")
    entities = {entity.id: entity for entity in session.project.entities}
    locked_sources = sorted({entities[entity_id].layer for entity_id in entity_ids if settings.get(entities[entity_id].layer, {}).get("locked", False)})
    if locked_sources:
        raise AutomationCommandError(f"Selection includes locked layer(s): {', '.join(locked_sources)}.", "layer.locked")
    for entity_id in entity_ids:
        entities[entity_id].layer = target
    target_settings["visible"] = True
    return CommandEffect({"target": target, "entity_ids": sorted(entity_ids), "count": len(entity_ids)}, changed=True)


@REGISTRY.register(
    "geometry.set_appearance",
    "Apply durable fill/stroke appearance to selected or named geometry without changing its layer or CAM role.",
    {
        "entity_ids": "optional IDs; defaults to selection",
        "appearance": "ObjectAppearance object; omit when inherit_layer is true",
        "inherit_layer": "optional boolean; clear explicit object appearance",
    },
)
def _geometry_set_appearance(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    entity_ids = _requested_ids(session, params)
    if not entity_ids:
        raise AutomationCommandError("Select one or more objects to style.", "selection.empty")
    settings = _automation_layer_settings(session.project)
    entities = {entity.id: entity for entity in session.project.entities}
    locked = sorted({
        entities[entity_id].layer for entity_id in entity_ids
        if settings.get(entities[entity_id].layer or "0", {}).get("locked", False)
    })
    if locked:
        raise AutomationCommandError(f"Selection includes locked layer(s): {', '.join(locked)}.", "layer.locked")
    if bool(params.get("inherit_layer", False)):
        appearance = None
    else:
        payload = params.get("appearance")
        if not isinstance(payload, dict):
            raise AutomationCommandError("appearance must be an ObjectAppearance object.", "appearance.type")
        try:
            appearance = ObjectAppearance.from_dict(payload)
        except ValueError as exc:
            raise AutomationCommandError(str(exc), "appearance.invalid") from exc
    for entity_id in entity_ids:
        entities[entity_id].appearance = copy.deepcopy(appearance)
    return CommandEffect({
        "entity_ids": sorted(entity_ids),
        "appearance": None if appearance is None else appearance.to_dict(),
    }, changed=True)


@REGISTRY.register("guide.list", "List persistent non-machining ruler guides.")
def _guide_list(session: AutomationSession, _params: dict[str, Any]) -> CommandEffect:
    return CommandEffect({"guides": [guide.to_dict() for guide in session.project.guides]})


@REGISTRY.register(
    "guide.add", "Add a persistent non-machining horizontal or vertical ruler guide.",
    {
        "orientation": "horizontal or vertical", "position": "Y for horizontal; X for vertical",
        "visible": "optional boolean", "locked": "optional boolean", "name": "optional label",
    },
)
def _guide_add(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    orientation = _text(params, "orientation").casefold()
    if orientation not in GUIDE_ORIENTATIONS:
        raise AutomationCommandError("Guide orientation must be horizontal or vertical.", "guide.orientation")
    guide = Guide(
        orientation=orientation,
        position=_number(params, "position"),
        visible=bool(params.get("visible", True)),
        locked=bool(params.get("locked", False)),
        name=str(params.get("name", "")).strip(),
    )
    try:
        guide.validate()
    except ValueError as exc:
        raise AutomationCommandError(str(exc), "guide.invalid") from exc
    session.project.guides.append(guide)
    return CommandEffect(guide.to_dict(), changed=True)


@REGISTRY.register(
    "guide.update", "Edit the position or display/lock state of a ruler guide.",
    {
        "guide_id": "guide ID", "position": "optional project coordinate",
        "visible": "optional boolean", "locked": "optional boolean", "name": "optional label",
    },
)
def _guide_update(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    guide_id = _text(params, "guide_id")
    guide = next((item for item in session.project.guides if item.id == guide_id), None)
    if guide is None:
        raise AutomationCommandError("Guide was not found.", "guide.missing")
    if "position" in params:
        if guide.locked:
            raise AutomationCommandError("Unlock the guide before moving it.", "guide.locked")
        guide.position = _number(params, "position")
    if "visible" in params:
        guide.visible = bool(params["visible"])
    if "locked" in params:
        guide.locked = bool(params["locked"])
    if "name" in params:
        guide.name = str(params["name"]).strip()
    try:
        guide.validate()
    except ValueError as exc:
        raise AutomationCommandError(str(exc), "guide.invalid") from exc
    return CommandEffect(guide.to_dict(), changed=True)


@REGISTRY.register("guide.delete", "Delete an unlocked ruler guide.", {"guide_id": "guide ID"})
def _guide_delete(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    guide_id = _text(params, "guide_id")
    guide = next((item for item in session.project.guides if item.id == guide_id), None)
    if guide is None:
        raise AutomationCommandError("Guide was not found.", "guide.missing")
    if guide.locked:
        raise AutomationCommandError("Unlock the guide before deleting it.", "guide.locked")
    session.project.guides = [item for item in session.project.guides if item.id != guide_id]
    return CommandEffect({"guide_id": guide_id}, changed=True)


@REGISTRY.register(
    "geometry.add_line", "Add an editable line and select it.",
    {"x1": "start X", "y1": "start Y", "x2": "end X", "y2": "end Y", "layer": "optional layer"},
)
def _add_line(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    entity = Line(
        layer=str(params.get("layer", "0")),
        start=Point(_number(params, "x1"), _number(params, "y1")),
        end=Point(_number(params, "x2"), _number(params, "y2")),
    )
    session.project.entities.append(entity)
    session.selected_ids = {entity.id}
    return CommandEffect(entity.to_dict(), changed=True)


@REGISTRY.register(
    "geometry.add_cutoff_line",
    "Add a full-height active-plate cutoff line to the right of selected or all geometry.",
    {
        "plate_number": "optional plate number; defaults to active plate",
        "entity_ids": "optional geometry IDs; defaults to selection, then all geometry",
        "clearance_inches": "physical right-side clearance; default 0.5 in",
    },
)
def _add_cutoff_line(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    plates = _project_plates(session.project)
    number = int(_number(
        params,
        "plate_number",
        default=float(session.project.metadata.get("active_plate_number", 1)),
    ))
    plate = next((candidate for candidate in plates if candidate.number == number), None)
    if plate is None:
        raise AutomationCommandError(f"Plate {number} does not exist.", "plate.missing")
    if "entity_ids" in params:
        requested = _requested_ids(session, params)
    elif session.selected_ids:
        requested = set(session.selected_ids)
    else:
        requested = {entity.id for entity in session.project.entities}
    entities = [entity for entity in session.project.entities if entity.id in requested]
    clearance_inches = _number(params, "clearance_inches", default=0.5)
    if clearance_inches < 0:
        raise AutomationCommandError("Cutoff clearance cannot be negative.", "geometry.clearance")
    try:
        entity = make_automatic_cutoff_line(
            entities,
            plate,
            clearance_inches * unit_scale("in", session.project.units),
        )
    except ValueError as exc:
        raise AutomationCommandError(str(exc), "geometry.cutoff") from exc
    entity.metadata["clearance_in"] = clearance_inches
    entity, replaced_count = install_automatic_cutoff_line(session.project, plate, entity)
    session.selected_ids = {entity.id}
    return CommandEffect({
        "entity": entity.to_dict(), "plate_number": number,
        "replaced_cutoff_count": replaced_count,
    }, changed=True)


@REGISTRY.register(
    "geometry.add_circle", "Add an editable circle and select it.",
    {"x": "center X", "y": "center Y", "diameter": "circle diameter", "layer": "optional layer"},
)
def _add_circle(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    diameter = _number(params, "diameter")
    if diameter <= 0:
        raise AutomationCommandError("Circle diameter must be positive.", "geometry.diameter")
    entity = Circle(
        layer=str(params.get("layer", "0")), center=Point(_number(params, "x"), _number(params, "y")), radius=diameter / 2.0,
    )
    session.project.entities.append(entity)
    session.selected_ids = {entity.id}
    return CommandEffect(entity.to_dict(), changed=True)


@REGISTRY.register(
    "geometry.add_rectangle", "Add an axis-aligned rectangle and select it.",
    {"x": "lower-left X", "y": "lower-left Y", "width": "positive width", "height": "positive height", "layer": "optional layer"},
)
def _add_rectangle(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    x, y = _number(params, "x"), _number(params, "y")
    width, height = _number(params, "width"), _number(params, "height")
    if width <= 0 or height <= 0:
        raise AutomationCommandError("Rectangle width and height must be positive.", "geometry.size")
    entity = Rectangle(
        layer=str(params.get("layer", "0")), corner_a=Point(x, y), corner_b=Point(x + width, y + height),
    )
    session.project.entities.append(entity)
    session.selected_ids = {entity.id}
    return CommandEffect(entity.to_dict(), changed=True)


@REGISTRY.register(
    "geometry.add_polyline", "Add an editable open or closed polyline and select it.",
    {"points": "array of {x,y} objects", "closed": "boolean, default false", "layer": "optional layer"},
)
def _add_polyline(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    raw_points = params.get("points")
    if not isinstance(raw_points, list) or len(raw_points) < 2:
        raise AutomationCommandError("points must contain at least two {x,y} objects.", "geometry.points")
    try:
        points = [Point(float(point["x"]), float(point["y"])) for point in raw_points]
    except (KeyError, TypeError, ValueError) as exc:
        raise AutomationCommandError("Every point must contain numeric x and y values.", "geometry.points") from exc
    closed = bool(params.get("closed", False))
    if closed and len(points) < 3:
        raise AutomationCommandError("A closed polyline requires at least three points.", "geometry.points")
    entity = Polyline(layer=str(params.get("layer", "0")), points=points, closed=closed)
    session.project.entities.append(entity)
    session.selected_ids = {entity.id}
    return CommandEffect(entity.to_dict(), changed=True)


@REGISTRY.register(
    "geometry.cleave",
    "Zero-width split selected or named closed shapes with a finite straight line or a ruler guide; keep both sides.",
    {
        "entity_ids": "optional target IDs; defaults to selection",
        "start": "{x,y} for a finite drawn splitter", "end": "{x,y} for a finite drawn splitter",
        "guide_id": "optional ruler guide ID; uses its infinite line instead of start/end",
        "infinite_line": "optional boolean for a coordinate-defined infinite line",
    },
)
def _geometry_cleave(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    requested = _requested_ids(session, params)
    if not requested:
        raise AutomationCommandError("Select at least one closed shape to cleave.", "selection.empty")
    compound_ids = {
        str(entity.metadata.get("compound_id", ""))
        for entity in session.project.entities
        if entity.id in requested and entity.metadata.get("compound_id")
    }
    if compound_ids:
        requested.update(
            entity.id for entity in session.project.entities
            if str(entity.metadata.get("compound_id", "")) in compound_ids
        )
    selected = [entity for entity in session.project.entities if entity.id in requested]
    settings = _automation_layer_settings(session.project)
    locked = sorted({
        entity.layer for entity in selected
        if settings.get(entity.layer or "0", {}).get("locked", False)
    })
    if locked:
        raise AutomationCommandError(f"Selection includes locked layer(s): {', '.join(locked)}.", "layer.locked")
    if any(entity.metadata.get("reference_only") or entity.metadata.get("mounting_generated") for entity in selected):
        raise AutomationCommandError("Reference-only or generated mounting geometry cannot be cleaved.", "geometry.reference")

    guide_id = str(params.get("guide_id", "")).strip()
    infinite_line = bool(params.get("infinite_line", False))
    if guide_id:
        guide = next((item for item in session.project.guides if item.id == guide_id), None)
        if guide is None:
            raise AutomationCommandError("Guide was not found.", "guide.missing")
        if guide.orientation == "horizontal":
            start, end = Point(0, guide.position), Point(1, guide.position)
        else:
            start, end = Point(guide.position, 0), Point(guide.position, 1)
        infinite_line = True
    else:
        try:
            raw_start, raw_end = params["start"], params["end"]
            start = Point(float(raw_start["x"]), float(raw_start["y"]))
            end = Point(float(raw_end["x"]), float(raw_end["y"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise AutomationCommandError(
                "Cleave requires start/end {x,y} coordinates or a guide_id.", "geometry.cleave_line",
            ) from exc
    try:
        result = cleave_shapes(selected, start, end, infinite_line=infinite_line)
        applied = apply_cleave_result(session.project, result)
    except (ValueError, RuntimeError) as exc:
        raise AutomationCommandError(str(exc), "geometry.cleave") from exc
    session.selected_ids = {entity.id for entity in result.entities} | set(result.unaffected_entity_ids)
    return CommandEffect({
        "source_entity_ids": list(result.source_entity_ids),
        "unaffected_entity_ids": list(result.unaffected_entity_ids),
        "result_entity_ids": [entity.id for entity in result.entities],
        "area_before": result.area_before,
        "area_after": result.area_after,
        "line": {
            "start": {"x": result.line_start.x, "y": result.line_start.y},
            "end": {"x": result.line_end.x, "y": result.line_end.y},
            "infinite": result.infinite_line,
        },
        "removed_dependent_ids": list(applied.removed_dependent_ids),
        "disabled_operation_ids": list(applied.disabled_operation_ids),
        "reviewed_led_plan_ids": list(applied.reviewed_led_plan_ids),
    }, changed=True)


@REGISTRY.register(
    "geometry.add_text",
    "Add editable, non-machining text. Explicit conversion is required before CAM.",
    {
        "text": "text content, including optional newlines", "x": "baseline insertion X",
        "y": "baseline insertion Y", "height": "positive text height",
        "font_family": "requested font family or font path; default Arial",
        "width_factor": "positive horizontal scale; default 1", "rotation": "CCW degrees",
        "alignment": "left, center, or right", "layer": "optional layer",
    },
)
def _add_text_entity(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    content = params.get("text")
    if not isinstance(content, str) or not content.strip():
        raise AutomationCommandError("text must contain at least one visible character.", "parameter.type")
    height = _number(params, "height")
    width_factor = _number(params, "width_factor", default=1.0)
    if height <= 0 or width_factor <= 0:
        raise AutomationCommandError("Text height and width factor must be positive.", "text.dimensions")
    alignment = str(params.get("alignment", "left")).strip().casefold()
    if alignment not in {"left", "center", "right"}:
        raise AutomationCommandError("alignment must be left, center, or right.", "text.alignment")
    resolution = resolve_font(str(params.get("font_family", "Arial")))
    entity = TextEntity(
        layer=str(params.get("layer", "0")),
        insertion=Point(_number(params, "x"), _number(params, "y")),
        text=content, height=height, rotation=_number(params, "rotation", default=0.0),
        alignment=alignment, font_family=resolution.resolved_family,
        width_factor=width_factor, multiline="\n" in content,
        metadata={
            "annotation": True, "requested_font": resolution.requested_family,
            "resolved_font": resolution.resolved_family,
            "font_substituted": resolution.substituted,
        },
    )
    session.project.entities.append(entity)
    session.selected_ids = {entity.id}
    return CommandEffect({
        "entity": entity.to_dict(),
        "font_warning": (
            f"Font {resolution.requested_family!r} was substituted with {resolution.resolved_family!r}."
            if resolution.substituted else None
        ),
    }, changed=True)


@REGISTRY.register(
    "geometry.update_text",
    "Edit an existing text annotation while preserving its stable ID.",
    {
        "entity_id": "one editable text ID", "text": "optional replacement content",
        "x": "optional insertion X", "y": "optional insertion Y", "height": "optional positive height",
        "font_family": "optional font family", "width_factor": "optional positive horizontal scale",
        "rotation": "optional CCW degrees", "alignment": "optional left, center, or right",
        "layer": "optional destination layer", "hidden": "optional annotation visibility",
    },
)
def _update_text_entity(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    entity_id = _text(params, "entity_id")
    entity = next((item for item in session.project.entities if item.id == entity_id), None)
    if not isinstance(entity, TextEntity):
        raise AutomationCommandError(f"{entity_id!r} is not editable text.", "text.entity")
    if "text" in params:
        content = params["text"]
        if not isinstance(content, str) or not content.strip():
            raise AutomationCommandError("text must contain at least one visible character.", "parameter.type")
        entity.text = content
        entity.multiline = "\n" in content
    if "x" in params or "y" in params:
        entity.insertion = Point(
            _number(params, "x", default=entity.insertion.x),
            _number(params, "y", default=entity.insertion.y),
        )
    if "height" in params:
        entity.height = _number(params, "height")
    if "width_factor" in params:
        entity.width_factor = _number(params, "width_factor")
    if entity.height <= 0 or entity.width_factor <= 0:
        raise AutomationCommandError("Text height and width factor must be positive.", "text.dimensions")
    if "rotation" in params:
        entity.rotation = _number(params, "rotation")
    if "alignment" in params:
        alignment = str(params["alignment"]).strip().casefold()
        if alignment not in {"left", "center", "right"}:
            raise AutomationCommandError("alignment must be left, center, or right.", "text.alignment")
        entity.alignment = alignment
    if "layer" in params:
        entity.layer = _text(params, "layer")
    if "font_family" in params:
        resolution = resolve_font(str(params["font_family"]))
        entity.font_family = resolution.resolved_family
        entity.metadata.update({
            "requested_font": resolution.requested_family,
            "resolved_font": resolution.resolved_family,
            "font_substituted": resolution.substituted,
        })
    if "hidden" in params:
        entity.metadata["annotation_hidden"] = bool(params["hidden"])
    session.selected_ids = {entity.id}
    return CommandEffect(entity.to_dict(), changed=True)


def _convert_text_geometry(
    session: AutomationSession,
    entity_ids: set[str],
    *,
    conversion: str,
    target_layer: str | None,
    keep_source: bool,
    hide_source: bool,
) -> list[Line | Polyline]:
    sources = [entity for entity in session.project.entities if entity.id in entity_ids]
    if not sources or any(not isinstance(entity, TextEntity) for entity in sources):
        raise AutomationCommandError("Every selected entity must be editable text.", "text.entity")
    if conversion not in {"outline", "single_line"}:
        raise AutomationCommandError("conversion must be outline or single_line.", "text.conversion")
    if not keep_source:
        referenced = {
            entity_id for operation in session.project.operations
            for entity_id in (*operation.entity_ids, *getattr(operation, "island_entity_ids", ()))
        }
        if entity_ids.intersection(referenced):
            raise AutomationCommandError("Referenced source text cannot be removed from the project.", "text.referenced")
    created: list[Line | Polyline] = []
    replacements: dict[str, TextEntity] = {}
    for source in sources:
        try:
            geometry = (
                text_to_outline_polylines(source)
                if conversion == "outline" else text_to_single_line_geometry(source)
            )
        except ValueError as exc:
            raise AutomationCommandError(str(exc), "text.conversion") from exc
        group_id = new_id("group")
        for item in geometry:
            item.layer = target_layer or source.layer
            item.metadata["group_id"] = group_id
        created.extend(geometry)
        if keep_source:
            source_copy = copy.deepcopy(source)
            source_copy.metadata["annotation_hidden"] = bool(hide_source)
            source_copy.metadata["converted_geometry_ids"] = [item.id for item in geometry]
            replacements[source.id] = source_copy
    session.project.entities = [
        replacements.get(entity.id, entity)
        for entity in session.project.entities
        if keep_source or entity.id not in entity_ids
    ]
    session.project.entities.extend(created)
    session.selected_ids = {entity.id for entity in created}
    return created


@REGISTRY.register(
    "geometry.convert_text",
    "Explicitly convert editable text to machinable outline contours or deterministic single-line geometry.",
    {
        "entity_ids": "optional editable text IDs; defaults to selection",
        "conversion": "outline or single_line", "target_layer": "optional geometry layer",
        "keep_source": "preserve editable source; default true",
        "hide_source": "hide preserved source after conversion; default true",
    },
)
def _convert_text_command(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    entity_ids = _requested_ids(session, params)
    created = _convert_text_geometry(
        session, entity_ids,
        conversion=str(params.get("conversion", "outline")).strip().casefold(),
        target_layer=str(params["target_layer"]) if params.get("target_layer") else None,
        keep_source=bool(params.get("keep_source", True)),
        hide_source=bool(params.get("hide_source", True)),
    )
    return CommandEffect({
        "source_entity_ids": sorted(entity_ids),
        "created_entity_ids": [entity.id for entity in created],
        "created_count": len(created),
    }, changed=True)


@REGISTRY.register(
    "geometry.transform", "Move and/or rotate selected or named geometry while preserving IDs.",
    {"entity_ids": "optional ID array; defaults to selection", "dx": "translation X", "dy": "translation Y", "angle": "CCW degrees", "pivot": "optional {x,y}"},
)
def _transform_geometry(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    entity_ids = _requested_ids(session, params)
    if not entity_ids:
        raise AutomationCommandError("No geometry is selected for transformation.", "selection.empty")
    dx, dy = _number(params, "dx", default=0.0), _number(params, "dy", default=0.0)
    angle = _number(params, "angle", default=0.0)
    pivot_value = params.get("pivot")
    if pivot_value is None:
        selected = [entity for entity in session.project.entities if entity.id in entity_ids]
        bounds = drawing_bounds(selected, include_annotations=True)
        assert bounds is not None
        pivot = Point((bounds[0] + bounds[2]) / 2.0, (bounds[1] + bounds[3]) / 2.0)
    else:
        try:
            pivot = Point(float(pivot_value["x"]), float(pivot_value["y"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise AutomationCommandError("pivot must contain numeric x and y values.", "parameter.type") from exc
    session.project.entities = [
        transform_entity(entity, dx, dy, angle, pivot) if entity.id in entity_ids else entity
        for entity in session.project.entities
    ]
    session.selected_ids = entity_ids
    return CommandEffect({"entity_ids": sorted(entity_ids), "pivot": {"x": pivot.x, "y": pivot.y}}, changed=True)


@REGISTRY.register(
    "geometry.mirror", "Mirror selected or named geometry across a horizontal, vertical, or existing line axis.",
    {
        "entity_ids": "optional ID array; defaults to selection",
        "axis": "vertical, horizontal, or line",
        "axis_coordinate": "optional X for vertical or Y for horizontal; defaults to selection center",
        "axis_entity_id": "selected Line entity used when axis=line; the axis line itself is not mirrored",
        "copy": "create mirrored copies instead of replacing source geometry; default false",
    },
)
def _mirror_geometry(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    entity_ids = _requested_ids(session, params)
    if not entity_ids:
        raise AutomationCommandError("No geometry is selected for mirroring.", "selection.empty")
    axis = str(params.get("axis", "vertical")).strip().casefold()
    copy_geometry = params.get("copy", False)
    if not isinstance(copy_geometry, bool):
        raise AutomationCommandError("copy must be true or false.", "parameter.type")

    selected = [entity for entity in session.project.entities if entity.id in entity_ids]
    bounds = drawing_bounds(selected, include_annotations=True)
    if bounds is None:
        raise AutomationCommandError("Selected geometry has no usable bounds.", "geometry.bounds")
    targets = set(entity_ids)
    if axis == "vertical":
        coordinate = _number(params, "axis_coordinate", default=(bounds[0] + bounds[2]) / 2.0)
        axis_start, axis_end = Point(coordinate, 0.0), Point(coordinate, 1.0)
    elif axis == "horizontal":
        coordinate = _number(params, "axis_coordinate", default=(bounds[1] + bounds[3]) / 2.0)
        axis_start, axis_end = Point(0.0, coordinate), Point(1.0, coordinate)
    elif axis == "line":
        axis_entity_id = str(params.get("axis_entity_id", "")).strip()
        axis_entity = next(
            (entity for entity in selected if entity.id == axis_entity_id and isinstance(entity, Line)),
            None,
        )
        if axis_entity is None:
            raise AutomationCommandError(
                "axis_entity_id must identify a selected Line entity.", "geometry.mirror_axis"
            )
        axis_start, axis_end = axis_entity.start, axis_entity.end
        targets.discard(axis_entity.id)
    else:
        raise AutomationCommandError("axis must be vertical, horizontal, or line.", "parameter.value")
    if not targets:
        raise AutomationCommandError("The mirror selection contains only the axis line.", "selection.empty")

    if copy_geometry:
        copies = [
            mirror_entity(entity, axis_start, axis_end, preserve_id=False)
            for entity in session.project.entities if entity.id in targets
        ]
        group_map: dict[str, str] = {}
        for source_id, entity in zip(
            [entity.id for entity in session.project.entities if entity.id in targets], copies
        ):
            source_group = entity.metadata.get("group_id")
            if source_group:
                entity.metadata["group_id"] = group_map.setdefault(source_group, new_id("group"))
            entity.metadata["mirrored_from"] = source_id
        session.project.entities.extend(copies)
        session.selected_ids = {entity.id for entity in copies}
    else:
        session.project.entities = [
            mirror_entity(entity, axis_start, axis_end) if entity.id in targets else entity
            for entity in session.project.entities
        ]
        session.selected_ids = targets
    return CommandEffect({
        "entity_ids": sorted(session.selected_ids),
        "axis": axis,
        "axis_start": {"x": axis_start.x, "y": axis_start.y},
        "axis_end": {"x": axis_end.x, "y": axis_end.y},
        "copied": copy_geometry,
    }, changed=True)


@REGISTRY.register(
    "geometry.auto_nest",
    "Nest all or named geometry onto defined plates while protecting compound outer/hole parts.",
    {
        "spacing": "minimum spacing in project units; default 0.25",
        "entity_ids": "optional ID array; omit for all geometry",
        "plate_numbers": "optional plate-number array; omit for all plates",
        "allow_rotation": "allow 90-degree rotation; default true",
        "rotation_step_degrees": "optional positive angle increment for shape-aware nesting",
        "maximum_rotation_degrees": "maximum clockwise/counter-clockwise rotation, 0-180; default 90",
        "placement_strategy": "rectangular (default), smart_pairs (recommended interactive behavior), or shape_aware (deep/slow)",
        "use_holes": "allow complete rigid items inside usable internal holes; smart_pairs or shape_aware",
        "search_resolution": "positive contour-pair/deep-search placement grid size in project units",
        "automatic_compounds": "keep nested contours together; default true",
        "allow_new_plates": "create positive-X overflow plates; default true",
        "common_line_tool_station": "optional T1-T12 cutter; source bounds use one cutter diameter spacing and report exact rectangular pairs",
    },
)
def _auto_nest_geometry(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    plates = _project_plates(session.project)
    if not plates:
        raise AutomationCommandError("Define at least one plate before nesting.", "plate.none")
    spacing = _number(params, "spacing", default=0.25)
    if spacing < 0:
        raise AutomationCommandError("Nest spacing cannot be negative.", "geometry.nest.spacing")

    requested = params.get("entity_ids")
    entity_ids = None
    if requested is not None:
        if not isinstance(requested, list) or not requested or not all(isinstance(value, str) for value in requested):
            raise AutomationCommandError("entity_ids must be a non-empty string array.", "parameter.type")
        entity_ids = set(requested)
        known = {entity.id for entity in session.project.entities}
        missing = sorted(entity_ids - known)
        if missing:
            raise AutomationCommandError(f"Unknown entity IDs: {', '.join(missing)}", "geometry.missing")

    requested_plates = params.get("plate_numbers")
    plate_numbers = None
    if requested_plates is not None:
        if not isinstance(requested_plates, list) or not requested_plates:
            raise AutomationCommandError("plate_numbers must be a non-empty array.", "parameter.type")
        try:
            plate_numbers = {int(value) for value in requested_plates}
        except (TypeError, ValueError) as exc:
            raise AutomationCommandError("plate_numbers must contain integers.", "parameter.type") from exc
        missing = sorted(plate_numbers - {plate.number for plate in plates})
        if missing:
            raise AutomationCommandError(f"Unknown plate numbers: {', '.join(map(str, missing))}", "plate.missing")

    try:
        common_line_station = params.get("common_line_tool_station")
        common_line_tool = None
        if common_line_station is not None:
            common_line_station = int(common_line_station)
            common_line_tool = session.project.tool_library.by_station(common_line_station)
            if common_line_tool is None or common_line_tool.spindle_mode == "disabled":
                raise AutomationCommandError(
                    "common_line_tool_station must name a loaded rotary cutter.",
                    "geometry.nest.common_line_tool",
                )
        result = nest_entities_to_plates(
            session.project.entities,
            plates,
            spacing,
            allow_rotation=bool(params.get("allow_rotation", True)),
            automatic_compounds=bool(params.get("automatic_compounds", True)),
            entity_ids=entity_ids,
            plate_numbers=plate_numbers,
            allow_new_plates=bool(params.get("allow_new_plates", True)),
            common_line_tool_diameter=(common_line_tool.diameter if common_line_tool is not None else None),
            placement_strategy=str(params.get("placement_strategy", "rectangular")),
            use_holes=bool(params.get("use_holes", False)),
            rotation_step_degrees=(
                _number(params, "rotation_step_degrees")
                if params.get("rotation_step_degrees") is not None else None
            ),
            maximum_rotation_degrees=_number(params, "maximum_rotation_degrees", default=90.0),
            search_resolution=(
                _number(params, "search_resolution")
                if params.get("search_resolution") is not None else None
            ),
        )
    except AutomationCommandError:
        raise
    except ValueError as exc:
        raise AutomationCommandError(str(exc), "geometry.nest.failed") from exc
    session.project.entities = result.entities
    session.project.metadata["plates"] = [plate.to_dict() for plate in result.plates]
    session.project.metadata.pop("sheet_layout", None)
    session.selected_ids = set(result.assignments)
    return CommandEffect({
        "entity_ids": sorted(result.assignments),
        "assignments": dict(result.assignments),
        "rotations": dict(result.rotations),
        "group_count": result.nested_group_count,
        "common_line_pairs": [list(pair) for pair in result.common_line_pairs],
        "interlocked_group_count": result.interlocked_group_count,
        "hole_nested_group_count": result.hole_nested_group_count,
        "plates": [plate.to_dict() for plate in result.plates],
    }, changed=True)


@REGISTRY.register("geometry.delete", "Delete selected or named geometry and dependent operations.", {"entity_ids": "optional ID array"})
def _delete_geometry(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    entity_ids = _requested_ids(session, params)
    if not entity_ids:
        raise AutomationCommandError("No geometry is selected for deletion.", "selection.empty")
    session.project.entities = [entity for entity in session.project.entities if entity.id not in entity_ids]
    session.project.operations = [
        operation for operation in session.project.operations
        if not entity_ids.intersection(set(operation.entity_ids) | set(getattr(operation, "island_entity_ids", [])))
    ]
    session.selected_ids.clear()
    return CommandEffect({"deleted_entity_ids": sorted(entity_ids)}, changed=True)


@REGISTRY.register("operation.list", "List machining operations with stable IDs.")
def _operation_list(session: AutomationSession, _params: dict[str, Any]) -> CommandEffect:
    return CommandEffect({"operations": [operation.to_dict() for operation in session.project.operations]})


@REGISTRY.register(
    "operation.add_router_offset", "Create a closed profile or a Cut Line on center, left, or right of connected open geometry.",
    {"entity_ids": "optional IDs; defaults to selection", "name": "optional name", "tool_station": "T1-T12", "target_depth": "positive depth", "safe_z": "positive clearance", "side": "inside/outside for closed profiles; left/right/on for open Cut Line", "contour_order": "inside_first, part_by_part, or source_order", "cut_intent": "custom, through, or partial", "breakthrough": "nonnegative depth below verified stock for through cuts", "stepdown": "optional positive stepdown", "pass_depths": "optional positive depth increments", "allowance": "optional radial/wall stock to leave", "finishing_pass": "add one exact-wall, final-depth finishing or spring pass", "finish_axial_stock": "optional bottom stock reserved for the finishing pass", "finish_feed": "optional finishing feed in project units per second", "finish_spindle_rpm": "optional finishing-pass RPM; roughing uses spindle_rpm", "entry_mode": "plunge or ramp for Cut Line; plunge, ramp, or helical for closed profiles", "lead_in": "optional straight approach distance", "lead_out": "optional straight exit distance", "entry_side": "automatic_waste, inside, or outside", "helix_feed": "optional tangential helix feed in project units per second", "helix_diameter": "positive cutter-center helix diameter", "helix_pitch": "positive maximum depth per revolution", "helix_fallback": "waste_plunge or strict_error", "onion_skin": "optional bottom skin", "tab_mode": "even, minimum_and_spacing, minimum_avoid_corners, support_aware, or corners_and_spacing", "tab_count": "minimum requested count", "tab_width": "finished retained-material length; CAM adds one cutter diameter to the raised centerline span", "tab_height": "remaining thickness", "corner_tab_offset": "distance either side of each corner", "tab_spacing": "maximum center spacing", "female_tab_min_perimeter": "female contours below this perimeter receive no tabs", "tab_cleanup_min_perimeter": "profiles below this perimeter retain their tabs", "remove_tabs_after": "cut only eligible tab intervals after profiling", "tab_cleanup_order": "after_each_part or end_of_operation", "curve_feed_mode": "off or wall_compensation", "curve_feed_min_percent": "minimum scheduled feed percentage", "curve_feed_transition_distance": "distance for smooth feed transitions", "common_line_mode": "off or paired_rectangles", "common_line_tolerance": "positive centerline-match tolerance"},
)
def _operation_add_router(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    entity_ids = sorted(_requested_ids(session, params))
    if not entity_ids:
        raise AutomationCommandError("Select geometry before creating a router offset.", "selection.empty")
    pass_depths = params.get("pass_depths", [])
    if not isinstance(pass_depths, list):
        raise AutomationCommandError("pass_depths must be an array of positive depth increments.", "parameter.type")
    try:
        pass_depths = [float(value) for value in pass_depths]
    except (TypeError, ValueError) as exc:
        raise AutomationCommandError("pass_depths must contain only numbers.", "parameter.type") from exc
    side = OffsetSide(str(params.get("side", "outside")))
    operation = RouterOffsetOperation(
        name=str(params.get("name", f"{side.value.title()} cut")),
        tool_station=int(_number(params, "tool_station")), entity_ids=entity_ids,
        target_depth=_number(params, "target_depth"), safe_z=_number(params, "safe_z", default=0.2),
        side=side, allowance=_number(params, "allowance", default=0.0),
        contour_order=str(params.get("contour_order", "inside_first")),
        finishing_pass=bool(params.get("finishing_pass", False)),
        finish_axial_stock=_number(params, "finish_axial_stock", default=0.0),
        finish_feed_override=(
            _number(params, "finish_feed") if params.get("finish_feed") is not None else None
        ),
        finish_spindle_rpm_override=(
            int(_number(params, "finish_spindle_rpm")) if params.get("finish_spindle_rpm") is not None else None
        ),
        stepdown=_number(params, "stepdown", default=_number(params, "target_depth")),
        cut_direction=str(params.get("cut_direction", "climb")), entry_mode=str(params.get("entry_mode", "plunge")),
        entry_side=str(params.get("entry_side", "automatic_waste")),
        helix_feed_override=None if params.get("helix_feed") is None else _number(params, "helix_feed"),
        helix_diameter=_number(params, "helix_diameter", default=0.0),
        helix_pitch=_number(params, "helix_pitch", default=0.0),
        helix_fallback=str(params.get("helix_fallback", "waste_plunge")),
        pass_depths=pass_depths,
        lead_in=_number(params, "lead_in", default=0.0),
        lead_out=_number(params, "lead_out", default=0.0),
        onion_skin=_number(params, "onion_skin", default=0.0),
        tab_mode=str(params.get("tab_mode", "even")), tab_count=int(params.get("tab_count", 0)),
        tab_width=_number(params, "tab_width", default=0.0), tab_height=_number(params, "tab_height", default=0.0),
        corner_tab_offset=_number(params, "corner_tab_offset", default=1.0),
        tab_spacing=_number(params, "tab_spacing", default=8.0),
        female_tab_min_perimeter=_number(params, "female_tab_min_perimeter", default=4.0),
        tab_cleanup_min_perimeter=_number(params, "tab_cleanup_min_perimeter", default=0.0),
        remove_tabs_after=bool(params.get("remove_tabs_after", False)),
        tab_cleanup_order=str(params.get("tab_cleanup_order", "after_each_part")),
        manual_tab_positions=[float(value) for value in params.get("manual_tab_positions", [])],
        geometry_selector=copy.deepcopy(params.get("geometry_selector", {})),
        depends_on_ids=[str(value) for value in params.get("depends_on_ids", [])],
        cut_intent=str(params.get("cut_intent", "custom")),
        breakthrough=_number(params, "breakthrough", default=0.0),
        curve_feed_mode=str(params.get("curve_feed_mode", "off")),
        curve_feed_min_percent=_number(params, "curve_feed_min_percent", default=25.0),
        curve_feed_transition_distance=_number(params, "curve_feed_transition_distance", default=0.5),
        common_line_mode=str(params.get("common_line_mode", "off")),
        common_line_tolerance=_number(params, "common_line_tolerance", default=0.001),
    )
    if not 1 <= operation.tool_station <= 12 or operation.target_depth <= 0 or operation.safe_z <= 0 or operation.stepdown <= 0 or operation.female_tab_min_perimeter < 0 or operation.tab_cleanup_min_perimeter < 0:
        raise AutomationCommandError("Tool station must be T1-T12 and depth, safe Z, and stepdown must be positive.", "operation.parameters")
    if operation.finish_axial_stock < 0 or operation.finishing_pass and operation.finish_axial_stock >= operation.target_depth:
        raise AutomationCommandError("Axial stock to leave must be nonnegative and smaller than final depth.", "operation.parameters.finish_axial_stock")
    if operation.finish_feed_override is not None and operation.finish_feed_override <= 0:
        raise AutomationCommandError("Finishing feed must be positive.", "operation.parameters.finish_feed")
    if operation.finish_spindle_rpm_override is not None and operation.finish_spindle_rpm_override <= 0:
        raise AutomationCommandError("Finishing RPM must be positive.", "operation.parameters.finish_spindle_rpm")
    if operation.helix_feed_override is not None and operation.helix_feed_override <= 0:
        raise AutomationCommandError("Helical feed must be positive.", "operation.parameters.helix_feed")
    if operation.entry_mode not in {"plunge", "ramp", "helical"} or operation.entry_side not in {"automatic_waste", "inside", "outside"}:
        raise AutomationCommandError("Choose a valid entry mode and helical entry side.", "operation.parameters")
    if operation.contour_order not in {"inside_first", "part_by_part", "source_order"}:
        raise AutomationCommandError("Contour order must be inside_first, part_by_part, or source_order.", "operation.parameters.contour_order")
    entities = {entity.id: entity for entity in session.project.entities}
    selected = [entities.get(entity_id) for entity_id in entity_ids]
    open_sources = [
        entity for entity in selected
        if isinstance(entity, Line) or isinstance(entity, Polyline) and not entity.closed
    ]
    closed_sources = [entity for entity in selected if entity is not None and entity not in open_sources]
    if open_sources and closed_sources:
        raise AutomationCommandError("Do not mix open Cut Line geometry with closed profile geometry.", "operation.geometry")
    if open_sources and (operation.side not in {OffsetSide.LEFT, OffsetSide.RIGHT, OffsetSide.ON} or operation.entry_mode == "helical"):
        raise AutomationCommandError("Open Cut Line geometry uses side left/right/on and plunge or ramp entry.", "operation.parameters")
    if closed_sources and operation.side in {OffsetSide.LEFT, OffsetSide.RIGHT}:
        raise AutomationCommandError("Closed profiles use inside, outside, or on-contour side.", "operation.parameters")
    if operation.helix_fallback not in {"waste_plunge", "strict_error"}:
        raise AutomationCommandError("Helical fallback must be waste_plunge or strict_error.", "operation.parameters.helix_fallback")
    if operation.cut_intent not in {"custom", "through", "partial"} or operation.breakthrough < 0:
        raise AutomationCommandError("Cut intent must be custom, through, or partial and breakthrough cannot be negative.", "operation.parameters.cut_intent")
    if operation.curve_feed_mode not in {"off", "wall_compensation"} or not 0 < operation.curve_feed_min_percent <= 100 or operation.curve_feed_transition_distance <= 0:
        raise AutomationCommandError("Choose valid curve-feed mode, minimum percentage, and transition distance.", "operation.parameters.curve_feed")
    if operation.common_line_mode not in {"off", "paired_rectangles"} or operation.common_line_tolerance <= 0:
        raise AutomationCommandError("Choose a valid common-line mode and positive tolerance.", "operation.parameters.common_line")
    if operation.entry_mode == "helical" and (operation.helix_diameter <= 0 or operation.helix_pitch <= 0):
        raise AutomationCommandError("Helical entry requires positive helix diameter and pitch.", "operation.parameters")
    finish_reserve = operation.finish_axial_stock if operation.finishing_pass else 0.0
    rough_depth = operation.target_depth - max(operation.onion_skin, finish_reserve)
    if any(value <= 0 for value in operation.pass_depths) or sum(operation.pass_depths) > rough_depth + 1e-9:
        raise AutomationCommandError("Pass increments must be positive and cannot exceed the roughing depth above reserved bottom stock.", "operation.parameters")
    has_tabs = operation.tab_count > 0 or operation.tab_mode in {"minimum_and_spacing", "minimum_avoid_corners", "support_aware", "corners_and_spacing", "manual", "aligned"}
    if operation.common_line_mode != "off" and (
        operation.side != OffsetSide.OUTSIDE or abs(operation.allowance) > 1e-9
        or operation.entry_mode != "plunge" or operation.lead_in > 1e-9 or operation.lead_out > 1e-9
        or has_tabs
    ):
        raise AutomationCommandError(
            "Paired common-line cutting requires an outside plunge cut with zero allowance/leads and no tabs.",
            "operation.parameters.common_line",
        )
    if operation.tab_mode not in {"even", "minimum_and_spacing", "minimum_avoid_corners", "support_aware", "corners_and_spacing", "manual", "aligned"} or operation.remove_tabs_after and not has_tabs:
        raise AutomationCommandError("Choose valid tab placement; cleanup requires enabled tabs.", "operation.parameters")
    if operation.tab_cleanup_order not in {"after_each_part", "end_of_operation"}:
        raise AutomationCommandError("Tab cleanup order must be after_each_part or end_of_operation.", "operation.parameters")
    if has_tabs and (operation.tab_width <= 0 or operation.tab_height <= 0 or operation.tab_height >= operation.tab_reference_depth):
        raise AutomationCommandError("Tabs require positive width/height below final depth.", "operation.parameters")
    if operation.tab_mode in {"minimum_and_spacing", "minimum_avoid_corners", "support_aware", "corners_and_spacing", "aligned"} and operation.tab_spacing <= 0:
        raise AutomationCommandError("Maximum tab spacing must be positive for the selected strategy.", "operation.parameters")
    session.project.operations.append(operation)
    return CommandEffect(operation.to_dict(), changed=True)


@REGISTRY.register(
    "operation.add_drill_circles", "Create a drilling cycle from selected circles.",
    {"entity_ids": "optional circle IDs", "tool_station": "T1-T12", "target_depth": "positive depth", "safe_z": "positive clearance", "peck_definition": "max_depth or count", "peck_depth": "optional maximum peck", "peck_count": "optional equal peck count", "peck_mode": "none, chip_break, or full_clear", "retract_height": "above-surface height for full clear or lift amount for chip break", "reentry_clearance": "fed approach distance above the previous peck depth", "surface_feed": "optional surface approach feed", "retract_feed": "optional controlled retract feed", "controlled_retract": "use feed rather than rapid for peck retracts", "return_to_surface": "feed to material surface before safe rapid", "dwell_seconds": "optional bottom dwell", "top_dwell_seconds": "optional dwell after retract", "plunge_rate": "optional operation feed override", "spindle_rpm": "optional operation RPM override"},
)
def _operation_add_drill(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    entity_ids = sorted(_requested_ids(session, params))
    entities = {entity.id: entity for entity in session.project.entities}
    if not entity_ids or any(not isinstance(entities[entity_id], Circle) for entity_id in entity_ids):
        raise AutomationCommandError("Drill Circles requires one or more selected circles.", "operation.geometry")
    peck = params.get("peck_depth")
    operation = DrillCirclesOperation(
        name=str(params.get("name", "Drill circles")), tool_station=int(_number(params, "tool_station")),
        entity_ids=entity_ids, target_depth=_number(params, "target_depth"), safe_z=_number(params, "safe_z", default=0.2),
        peck_depth=None if peck in (None, 0, 0.0) else float(peck),
        peck_definition=str(params.get("peck_definition", "max_depth")),
        peck_count=None if params.get("peck_count") in (None, "") else int(_number(params, "peck_count")),
        peck_mode=str(params.get("peck_mode", "full_clear")),
        retract_height=_number(params, "retract_height", default=0.1),
        reentry_clearance=_number(params, "reentry_clearance", default=0.02),
        surface_feed_override=None if params.get("surface_feed") is None else _number(params, "surface_feed"),
        retract_feed_override=None if params.get("retract_feed") is None else _number(params, "retract_feed"),
        controlled_retract=bool(params.get("controlled_retract", False)),
        return_to_surface=bool(params.get("return_to_surface", False)),
        dwell_seconds=_number(params, "dwell_seconds", default=0.0),
        top_dwell_seconds=_number(params, "top_dwell_seconds", default=0.0),
        plunge_rate_override=None if params.get("plunge_rate") is None else _number(params, "plunge_rate"),
        spindle_rpm_override=None if params.get("spindle_rpm") is None else int(_number(params, "spindle_rpm")),
    )
    if (
        not 1 <= operation.tool_station <= 12 or operation.target_depth <= 0 or operation.safe_z <= 0
        or operation.peck_mode not in {"none", "chip_break", "full_clear"}
        or operation.peck_mode != "none" and operation.peck_definition not in {"max_depth", "count"}
        or operation.peck_mode != "none" and operation.peck_definition == "count" and (operation.peck_count is None or operation.peck_count < 1)
        or operation.retract_height <= 0 or operation.reentry_clearance < 0
        or operation.surface_feed_override is not None and operation.surface_feed_override <= 0
        or operation.retract_feed_override is not None and operation.retract_feed_override <= 0
        or operation.dwell_seconds < 0 or operation.top_dwell_seconds < 0
    ):
        raise AutomationCommandError("Drill station, depth, peck mode, retract, re-entry, and dwell parameters are invalid.", "operation.parameters")
    session.project.operations.append(operation)
    return CommandEffect(operation.to_dict(), changed=True)


@REGISTRY.register(
    "operation.add_thread_mill", "Create a qualified internal UN thread-milling cycle from pilot circles.",
    {
        "entity_ids": "optional pilot-circle IDs; defaults to selection",
        "tool_station": "T1-T12 thread-mill station",
        "thread_designation": "10-24 UNC-2B or 1/4-20 UNC-2B; inferred when omitted",
        "target_depth": "positive threaded depth",
        "pilot_hole_depth": "verified physical pilot depth",
        "bottom_clearance": "nonnegative clearance below thread",
        "safe_z": "positive clearance height",
        "pitch_diameter_adjustment": "signed adjustment within Class 2B limits",
        "radial_passes": "one or more radial cutting passes",
        "spring_passes": "zero or more repeated finish passes",
        "handedness": "right or left",
        "cut_direction": "climb or conventional",
        "cutter_verified": "explicit physical cutter/TPI/form attestation",
    },
)
def _operation_add_thread_mill(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    entity_ids = sorted(_requested_ids(session, params))
    entities = {entity.id: entity for entity in session.project.entities}
    if not entity_ids or any(not isinstance(entities[entity_id], Circle) for entity_id in entity_ids):
        raise AutomationCommandError("Thread Mill requires one or more pilot-hole circles.", "operation.geometry")
    diameters = [entities[entity_id].radius * 2.0 for entity_id in entity_ids]
    compatible = thread_standards_for_pilot_diameters(diameters, session.project.units)
    designation = str(params.get("thread_designation", "")).strip()
    if not designation:
        if len(compatible) != 1:
            raise AutomationCommandError(
                "Pilot diameters do not identify one supported thread standard; provide a supported designation or correct the pilot geometry.",
                "thread.pilot_range",
            )
        designation = compatible[0]
    thread_standard(designation)
    if designation not in compatible:
        raise AutomationCommandError(
            f"Selected pilot circles are outside the qualified {designation} diameter range.",
            "thread.pilot_range",
        )
    operation = ThreadMillOperation(
        name=str(params.get("name", f"{designation} Internal Thread")),
        tool_station=int(_number(params, "tool_station")), entity_ids=entity_ids,
        target_depth=_number(params, "target_depth"), safe_z=_number(params, "safe_z", default=0.5),
        thread_designation=designation,
        pitch_diameter_adjustment=_number(params, "pitch_diameter_adjustment", default=0.0),
        radial_passes=int(_number(params, "radial_passes", default=2)),
        spring_passes=int(_number(params, "spring_passes", default=1)),
        handedness=str(params.get("handedness", "right")),
        cut_direction=str(params.get("cut_direction", "climb")),
        cutter_verified=bool(params.get("cutter_verified", False)),
        pilot_hole_depth=_number(params, "pilot_hole_depth"),
        bottom_clearance=_number(params, "bottom_clearance", default=0.05),
        sequence_role="drill_thread",
    )
    from .toolpath import generate_thread_mill
    try:
        generate_thread_mill(session.project, operation)
    except ValueError as exc:
        raise AutomationCommandError(str(exc), "thread.parameters") from exc
    session.project.operations.append(operation)
    return CommandEffect(operation.to_dict(), changed=True)


@REGISTRY.register(
    "operation.add_engrave", "Create a spindle-off spring-loaded drag engraving operation.",
    {"entity_ids": "optional IDs; defaults to selection", "tool_station": "spindle-disabled station, default T8", "compression_depth": "spring compression", "approach_height": "positive height above surface", "safe_z": "clearance", "feed_rate": "engraving feed", "plunge_rate": "engagement feed", "tip_angle": "60, 90, or 120"},
)
def _operation_add_engrave(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    entity_ids = sorted(_requested_ids(session, params))
    if not entity_ids:
        raise AutomationCommandError("Select geometry before creating engraving.", "selection.empty")
    entity_by_id = {entity.id: entity for entity in session.project.entities}
    if any(isinstance(entity_by_id[entity_id], TextEntity) for entity_id in entity_ids):
        raise AutomationCommandError(
            "Editable text is an annotation. Use operation.add_engrave_text or geometry.convert_text first.",
            "operation.annotation_entity",
        )
    station = int(_number(params, "tool_station", default=8))
    tool = session.project.tool_library.by_station(station)
    if tool is None:
        tool = Tool(
            station, "RDZ 90° Diamond Drag Engraver", 0.25, kind=ToolKind.ENGRAVER,
            feed_rate=_number(params, "feed_rate", default=20),
            plunge_rate=_number(params, "plunge_rate", default=20), spindle_mode="disabled",
            metadata={"manufacturer": "RDZ", "tip_angle": int(params.get("tip_angle", 90)), "spring_travel": 0.375},
        )
        session.project.tool_library.tools.append(tool)
    if tool.spindle_mode != "disabled":
        raise AutomationCommandError(f"T{station} must be spindle-disabled for drag engraving.", "engrave.spindle")
    tool.kind = ToolKind.ENGRAVER
    tool.spindle_rpm = None
    tool.feed_rate = _number(params, "feed_rate", default=tool.feed_rate or 20)
    tool.plunge_rate = _number(params, "plunge_rate", default=tool.plunge_rate or 20)
    tool.metadata["tip_angle"] = int(params.get("tip_angle", tool.metadata.get("tip_angle", 90)))
    tool.metadata.setdefault("spring_travel", 0.375)
    operation = EngraveOperation(
        name=str(params.get("name", "RDZ Drag Engrave")), tool_station=station, entity_ids=entity_ids,
        target_depth=_number(params, "compression_depth", default=0.25), safe_z=_number(params, "safe_z", default=0.2),
        approach_height=_number(params, "approach_height", default=0.05), reverse_open_paths=bool(params.get("reverse_open_paths", False)),
    )
    from .toolpath import generate_drag_engraving
    generate_drag_engraving(session.project, operation)
    session.project.operations.append(operation)
    return CommandEffect(operation.to_dict(), changed=True)


@REGISTRY.register(
    "operation.add_engrave_text",
    "Convert editable text to deterministic single-line strokes and create a drag-engraving operation.",
    {
        "entity_ids": "optional editable text IDs; defaults to selection",
        "target_layer": "optional generated-stroke layer", "keep_source": "default true",
        "hide_source": "default true", "tool_station": "spindle-disabled station; default T8",
        "compression_depth": "spring compression", "approach_height": "height above surface",
        "safe_z": "clearance", "feed_rate": "engraving feed", "plunge_rate": "engagement feed",
        "tip_angle": "60, 90, or 120", "name": "optional operation name",
    },
)
def _operation_add_engrave_text(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    source_ids = _requested_ids(session, params)
    created = _convert_text_geometry(
        session, source_ids, conversion="single_line",
        target_layer=str(params["target_layer"]) if params.get("target_layer") else None,
        keep_source=bool(params.get("keep_source", True)),
        hide_source=bool(params.get("hide_source", True)),
    )
    engraving_params = dict(params)
    engraving_params.pop("entity_ids", None)
    operation_effect = _operation_add_engrave(session, engraving_params)
    return CommandEffect({
        "operation": operation_effect.data,
        "source_entity_ids": sorted(source_ids),
        "created_entity_ids": [entity.id for entity in created],
    }, changed=True)


@REGISTRY.register(
    "operation.add_island_fill", "Create a Concentric or safety-checked Speed Cleanout fill from classified boundaries and optional islands.",
    {
        "boundary_id": "one closed boundary entity ID (legacy single-region form)",
        "boundary_ids": "one or more independent/nested fill-boundary entity IDs",
        "island_ids": "optional protected island entity IDs",
        "tool_station": "T1-T12", "target_depth": "positive final depth", "safe_z": "positive clearance",
        "stepdown": "maximum roughing stepdown", "stepover": "positive centerline spacing",
        "pass_mode": "maximum_stepdown, equal_count, or custom_increments",
        "pass_count": "roughing count for equal_count", "pass_depths": "exact increments for custom_increments",
        "bottom_finish_pass": "reserve and clean the pocket floor", "bottom_finish_depth": "reserved axial depth",
        "bottom_finish_feed": "optional cleanup feed override", "corner_tags": "add corner-coverage motion",
        "cut_direction": "climb, conventional, or bidirectional (climb walls plus alternating verified interior links)", "entry_mode": "plunge, ramp, or helical",
        "ramp_length": "distance used to descend or for ramp fallback",
        "helix_feed": "optional tangential helix feed in project units per second",
        "helix_diameter": "preferred positive cutter-center orbit diameter",
        "helix_pitch": "positive maximum axial depth per revolution",
        "helix_min_diameter": "hard lower diameter for automatic orbit reduction",
        "helix_ramp_angle": "optional maximum descent angle in degrees; zero uses pitch only",
        "helix_clearance": "additional cutter-center clearance from walls and islands",
        "helix_finish_revolutions": "whole final-depth cleanup revolutions",
        "helix_fallback": "ramp, plunge, or error when a safe orbit will not fit",
        "dwell_seconds": "optional bottom entry pause",
        "sliver_cleanup": "defer short residual loops to one final-depth cleanup phase",
        "sliver_max_path_length": "maximum total loop length treated as a short cleanup path",
        "sliver_link_distance": "maximum same-region full-depth connector; zero always retracts",
        "keep_tool_down": "link adjacent fill loops only after a swept-area engagement proof",
        "stay_down_max_distance": "maximum same-depth connector length",
        "stay_down_max_engagement": "maximum newly engaged material width on the connector",
        "stay_down_feed_percent": "1-100 percent of cut feed used by verified connectors",
        "strategy": "concentric or speed_cleanout",
        "speed_max_full_depth_engagement": "maximum residual material width allowed for the full-depth center phase",
        "speed_center_feed_percent": "1-100 percent of cut feed for the full-depth center phase",
        "speed_link_distance": "maximum verified link from a depth-cleared wall track; no greater than cutter diameter",
        "speed_fallback": "depth_step or error when the safety proof fails",
        "feed_rate": "optional cut-feed override", "plunge_rate": "optional plunge override",
        "spindle_rpm": "optional spindle override", "recipe_name": "optional reusable recipe name",
    },
)
def _operation_add_fill(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    boundary_values = params.get("boundary_ids")
    if boundary_values is None:
        boundary_values = [_text(params, "boundary_id")]
    if not isinstance(boundary_values, list) or not boundary_values or any(not isinstance(value, str) or not value for value in boundary_values):
        raise AutomationCommandError("boundary_ids must be a non-empty JSON list of entity IDs.", "parameter.type")
    known = {entity.id for entity in session.project.entities}
    islands = params.get("island_ids", [])
    if any(value not in known for value in boundary_values) or not isinstance(islands, list) or any(value not in known for value in islands):
        raise AutomationCommandError("Boundary or island IDs are invalid.", "operation.geometry")
    pass_depths = params.get("pass_depths", [])
    if not isinstance(pass_depths, list):
        raise AutomationCommandError("pass_depths must be a JSON list of positive increments.", "parameter.type")
    operation = IslandFillOperation(
        name=str(params.get("name", "Island fill")), tool_station=int(_number(params, "tool_station")),
        entity_ids=list(boundary_values), island_entity_ids=list(islands), target_depth=_number(params, "target_depth"),
        safe_z=_number(params, "safe_z", default=0.2), stepdown=_number(params, "stepdown"), stepover=_number(params, "stepover"),
        pass_mode=str(params.get("pass_mode", "maximum_stepdown")),
        pass_count=None if params.get("pass_count") is None else int(_number(params, "pass_count")),
        pass_depths=[float(value) for value in pass_depths],
        bottom_finish_pass=bool(params.get("bottom_finish_pass", False)),
        bottom_finish_depth=_number(params, "bottom_finish_depth", default=0.0),
        bottom_finish_feed_override=None if params.get("bottom_finish_feed") is None else _number(params, "bottom_finish_feed"),
        corner_tags=bool(params.get("corner_tags", True)),
        cut_direction=str(params.get("cut_direction", "climb")),
        entry_mode=str(params.get("entry_mode", "plunge")),
        ramp_length=_number(params, "ramp_length", default=0.0),
        helix_feed_override=None if params.get("helix_feed") is None else _number(params, "helix_feed"),
        helix_diameter=_number(params, "helix_diameter", default=0.0),
        helix_min_diameter=_number(params, "helix_min_diameter", default=0.0),
        helix_pitch=_number(params, "helix_pitch", default=0.0),
        helix_ramp_angle=_number(params, "helix_ramp_angle", default=0.0),
        helix_clearance=_number(params, "helix_clearance", default=0.0),
        helix_finish_revolutions=int(_number(params, "helix_finish_revolutions", default=0)),
        helix_fallback=str(params.get("helix_fallback", "ramp")),
        dwell_seconds=_number(params, "dwell_seconds", default=0.0),
        sliver_cleanup=bool(params.get("sliver_cleanup", False)),
        sliver_max_path_length=_number(params, "sliver_max_path_length", default=0.0),
        sliver_link_distance=_number(params, "sliver_link_distance", default=0.0),
        keep_tool_down=bool(params.get("keep_tool_down", False)),
        stay_down_max_distance=_number(params, "stay_down_max_distance", default=0.0),
        stay_down_max_engagement=_number(params, "stay_down_max_engagement", default=0.0),
        stay_down_feed_percent=_number(params, "stay_down_feed_percent", default=100.0),
        speed_max_full_depth_engagement=_number(params, "speed_max_full_depth_engagement", default=0.0),
        speed_center_feed_percent=_number(params, "speed_center_feed_percent", default=60.0),
        speed_link_distance=_number(params, "speed_link_distance", default=0.0),
        speed_fallback=str(params.get("speed_fallback", "depth_step")),
        feed_rate_override=None if params.get("feed_rate") is None else _number(params, "feed_rate"),
        plunge_rate_override=None if params.get("plunge_rate") is None else _number(params, "plunge_rate"),
        spindle_rpm_override=None if params.get("spindle_rpm") is None else int(_number(params, "spindle_rpm")),
        recipe_name=str(params.get("recipe_name", "")),
        strategy=FillStrategy(str(params.get("strategy", "concentric"))),
    )
    if (
        not 1 <= operation.tool_station <= 12
        or min(operation.target_depth, operation.safe_z, operation.stepdown, operation.stepover) <= 0
        or min(operation.ramp_length, operation.helix_diameter, operation.helix_min_diameter, operation.helix_pitch, operation.helix_ramp_angle, operation.helix_clearance, operation.helix_finish_revolutions, operation.dwell_seconds) < 0
        or operation.helix_min_diameter > operation.helix_diameter + 1e-9
        or operation.helix_ramp_angle >= 90
        or operation.entry_mode not in {"plunge", "ramp", "helical"}
        or operation.cut_direction not in {"climb", "conventional", "bidirectional"}
        or operation.helix_fallback not in {"ramp", "plunge", "error"}
        or operation.entry_mode == "helical" and (operation.helix_diameter <= 0 or operation.helix_pitch <= 0)
        or operation.entry_mode == "helical" and operation.ramp_length <= 0
        or operation.helix_feed_override is not None and operation.helix_feed_override <= 0
        or min(operation.sliver_max_path_length, operation.sliver_link_distance) < 0
        or operation.sliver_cleanup and operation.sliver_max_path_length <= 0
        or operation.keep_tool_down and operation.stay_down_max_distance <= 0
        or operation.keep_tool_down and operation.stay_down_max_engagement <= 0
        or not 0 < operation.stay_down_feed_percent <= 100
        or operation.strategy is FillStrategy.SPEED_CLEANOUT and operation.speed_max_full_depth_engagement <= 0
        or operation.strategy is FillStrategy.SPEED_CLEANOUT and operation.speed_link_distance <= 0
        or not 0 < operation.speed_center_feed_percent <= 100
        or operation.speed_fallback not in {"depth_step", "error"}
    ):
        raise AutomationCommandError("Tool station, depth, Safe Z, passes, stepover, and entry values are invalid.", "operation.parameters")
    from .toolpath import generate_rectangular_island_fill
    try:
        generate_rectangular_island_fill(session.project, operation)
    except ValueError as exc:
        raise AutomationCommandError(str(exc), "fill.parameters") from exc
    session.project.operations.append(operation)
    return CommandEffect(operation.to_dict(), changed=True)


@REGISTRY.register(
    "operation.add_profile_rest",
    "Create source-linked profile/corner rest machining with a smaller cutter.",
    {
        "source_operation_id": "enabled closed inside/outside router-offset operation ID",
        "tool_station": "T1-T12 containing a cutter smaller than the source cutter",
        "safe_z": "optional positive clearance; defaults to source",
        "stepdown": "optional positive rest-tool stepdown",
        "stock_to_leave": "optional nonnegative radial allowance",
        "ignore_stock_less_than": "optional nonnegative residual threshold",
        "inherit_pass_schedule": "inherit source depth/onion-skin schedule (recommended)",
        "preserve_tabs": "preserve source tab intervals during rest cutting",
        "remove_tabs_after": "cut source tab intervals only after all rest regions",
    },
)
def _operation_add_profile_rest(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    source_id = _text(params, "source_operation_id")
    source = next((item for item in session.project.operations if item.id == source_id), None)
    if not isinstance(source, RouterOffsetOperation) or source.side not in {OffsetSide.INSIDE, OffsetSide.OUTSIDE}:
        raise AutomationCommandError("Profile rest requires a closed inside/outside router-offset source.", "operation.source")
    if not source.enabled:
        raise AutomationCommandError("The source router operation must be enabled.", "operation.source")
    station = int(_number(params, "tool_station"))
    source_tool = session.project.tool_library.by_station(source.tool_station)
    rest_tool = session.project.tool_library.by_station(station)
    if source_tool is None or rest_tool is None or rest_tool.diameter >= source_tool.diameter:
        raise AutomationCommandError("The rest station must contain a cutter smaller than the source cutter.", "operation.tool")
    if source.remove_tabs_after:
        raise AutomationCommandError("Disable source tab cleanup before adding dependent profile rest.", "operation.release_order")
    stepdown_value = params.get("stepdown")
    operation = ProfileRestMachiningOperation(
        name=str(params.get("name", f"Corner rest after {source.name}")),
        tool_station=station,
        entity_ids=list(source.entity_ids),
        target_depth=source.target_depth,
        safe_z=_number(params, "safe_z", default=source.safe_z),
        source_operation_id=source.id,
        stepdown=None if stepdown_value in (None, "") else float(stepdown_value),
        stock_to_leave=_number(params, "stock_to_leave", default=0.0),
        ignore_stock_less_than=_number(params, "ignore_stock_less_than", default=0.0),
        inherit_pass_schedule=bool(params.get("inherit_pass_schedule", True)),
        preserve_tabs=bool(params.get("preserve_tabs", True)),
        remove_tabs_after=bool(params.get("remove_tabs_after", False)),
        sequence_role=source.sequence_role,
        sequence_priority=source.sequence_priority + 1,
    )
    if operation.safe_z <= 0 or operation.stepdown is not None and operation.stepdown <= 0:
        raise AutomationCommandError("Safe Z and any explicit stepdown must be positive.", "operation.parameters")
    if min(operation.stock_to_leave, operation.ignore_stock_less_than) < 0:
        raise AutomationCommandError("Rest stock thresholds cannot be negative.", "operation.parameters")
    from .operation_links import capture_profile_rest_dependency
    from .toolpath import generate_profile_rest_machining
    capture_profile_rest_dependency(session.project, operation)
    generate_profile_rest_machining(session.project, operation)
    session.project.operations.append(operation)
    return CommandEffect(operation.to_dict(), changed=True)


@REGISTRY.register(
    "operation.refresh_profile_rest",
    "Recapture a profile-rest parent snapshot and regenerate its verified dependent motion.",
    {"operation_id": "profile/corner-rest operation ID"},
)
def _operation_refresh_profile_rest(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    operation_id = _text(params, "operation_id")
    operation = next((item for item in session.project.operations if item.id == operation_id), None)
    if not isinstance(operation, ProfileRestMachiningOperation):
        raise AutomationCommandError("Choose an existing profile/corner-rest operation.", "operation.missing")
    from .operation_links import capture_profile_rest_dependency, profile_rest_dependency_status
    from .toolpath import generate_profile_rest_machining
    capture_profile_rest_dependency(session.project, operation)
    try:
        path = generate_profile_rest_machining(session.project, operation)
    except ValueError as exc:
        raise AutomationCommandError(str(exc), "profile_rest.parameters") from exc
    status = profile_rest_dependency_status(session.project, operation)
    return CommandEffect({
        "operation_id": operation.id,
        "source_operation_id": operation.source_operation_id,
        "dependency_state": status.state,
        "generated_blocks": len(path.blocks),
        "source_snapshot": operation.source_snapshot,
    }, changed=True)


@REGISTRY.register("plate.list", "List defined or generated plates and the automation-active plate.")
def _plate_list(session: AutomationSession, _params: dict[str, Any]) -> CommandEffect:
    plates = _project_plates(session.project)
    return CommandEffect({"plates": [plate.to_dict() for plate in plates], "active_plate": int(session.project.metadata.get("active_plate_number", 1))})


@REGISTRY.register("plate.define", "Replace individual positive-quadrant plates.", {"plates": "array of {number,x,y,width,height,name?}"})
def _plate_define(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    values = params.get("plates")
    if not isinstance(values, list) or not values:
        raise AutomationCommandError("plates must be a non-empty array.", "plate.parameters")
    plates = validate_plate_definitions([Plate.from_dict(value) for value in values])
    session.project.metadata["plates"] = [plate.to_dict() for plate in plates]
    session.project.metadata.pop("sheet_layout", None)
    session.project.metadata["active_plate_number"] = plates[0].number
    return CommandEffect({"plates": session.project.metadata["plates"]}, changed=True)


@REGISTRY.register(
    "plate.add",
    "Append one independently sized positive-quadrant plate, defaulting to the right edge.",
    {"width": "X size", "height": "Y size", "x": "optional X origin", "y": "optional Y origin; default 0", "name": "optional plate name"},
)
def _plate_add(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    plates = _project_plates(session.project)
    x_value = params.get("x")
    x = None if x_value in (None, "") else _number(params, "x")
    name = params.get("name", "")
    if not isinstance(name, str):
        raise AutomationCommandError("Parameter 'name' must be text.", "parameter.type")
    updated = append_plate_definition(
        plates,
        _number(params, "width"),
        _number(params, "height"),
        name=name.strip(),
        x=x,
        y=_number(params, "y", default=0.0),
    )
    added = updated[-1]
    session.project.metadata["plates"] = [plate.to_dict() for plate in updated]
    session.project.metadata.pop("sheet_layout", None)
    session.project.metadata["active_plate_number"] = added.number
    return CommandEffect({"plate": added.to_dict(), "plates": session.project.metadata["plates"]}, changed=True)


@REGISTRY.register(
    "plate.update",
    "Edit one plate without replacing the other plate definitions.",
    {
        "number": "plate number", "width": "optional X size", "height": "optional Y size",
        "x": "optional X origin", "y": "optional Y origin", "name": "optional plate name",
        "reflow": "move a touching positive-X row when width changes; default true",
    },
)
def _plate_update(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    number = int(_number(params, "number"))
    plates = _project_plates(session.project)
    current = next((plate for plate in plates if plate.number == number), None)
    if current is None:
        raise AutomationCommandError(f"Plate {number} does not exist.", "plate.missing")
    reflow = params.get("reflow", True)
    if not isinstance(reflow, bool):
        raise AutomationCommandError("Parameter 'reflow' must be true or false.", "parameter.type")
    name = params.get("name", current.name)
    if not isinstance(name, str):
        raise AutomationCommandError("Parameter 'name' must be text.", "parameter.type")
    updated_plate = Plate(
        current.number,
        _number(params, "x", default=current.x),
        _number(params, "y", default=current.y),
        _number(params, "width", default=current.width),
        _number(params, "height", default=current.height),
        name.strip(),
    )
    updated = replace_plate_definition(plates, updated_plate, reflow_contiguous=reflow)
    session.project.metadata["plates"] = [plate.to_dict() for plate in updated]
    session.project.metadata.pop("sheet_layout", None)
    session.project.metadata["active_plate_number"] = number
    return CommandEffect({"plate": updated_plate.to_dict(), "plates": session.project.metadata["plates"]}, changed=True)


@REGISTRY.register("plate.set_active", "Set the plate targeted by alignment and active-only output.", {"number": "plate number"})
def _plate_set_active(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    number = int(_number(params, "number"))
    if number not in {plate.number for plate in _project_plates(session.project)}:
        raise AutomationCommandError(f"Plate {number} does not exist.", "plate.missing")
    session.project.metadata["active_plate_number"] = number
    return CommandEffect({"active_plate": number}, changed=True)


@REGISTRY.register(
    "plate.output", "Validate and write active or all plates as controller CNC files, with optional editable CAM projects.",
    {
        "directory": "output directory",
        "base_name": "file name prefix",
        "scope": "active or all",
        "package": "cnc_only (default) or cnc_and_project",
    },
)
def _plate_output(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    plates = _project_plates(session.project)
    if not plates:
        raise AutomationCommandError("Define or generate at least one plate before output.", "plate.none")
    scope = str(params.get("scope", "active"))
    if scope not in {"active", "all"}:
        raise AutomationCommandError("scope must be 'active' or 'all'.", "parameter.value")
    package = str(params.get("package", "cnc_only"))
    if package not in {"cnc_only", "cnc_and_project"}:
        raise AutomationCommandError("package must be 'cnc_only' or 'cnc_and_project'.", "parameter.value")
    active = int(session.project.metadata.get("active_plate_number", 1))
    targets = plates if scope == "all" else [plate for plate in plates if plate.number == active]
    if not targets:
        raise AutomationCommandError(f"Active plate {active} does not exist.", "plate.missing")
    issues = run_preflight(session.project, plates)
    errors = [issue for issue in issues if issue.severity == "error"]
    if errors:
        raise AutomationCommandError("Preflight blocks output: " + "; ".join(issue.message for issue in errors), "preflight.blocked")
    directory = Path(_text(params, "directory")).expanduser().resolve()
    base_name = _text(params, "base_name")
    outputs = []
    for plate in targets:
        plate_result = make_plate_project(session.project, plate)
        try:
            cnc = post_enroute_cnc(plate_result.project, directory / cnc_output_filename(base_name, plate.number))
        except (OSError, ValueError) as exc:
            raise AutomationCommandError(str(exc), "cnc.output") from exc
        output = {
            "plate": plate.number,
            "cnc_path": str(cnc.path),
            "cnc_blocks": cnc.block_count,
            "crossing_entity_ids": list(plate_result.crossing_entity_ids),
        }
        if package == "cnc_and_project":
            output["path"] = str(save_plate_project(plate_result, directory, base_name, plate.number))
        outputs.append(output)
    return CommandEffect({"outputs": outputs, "warnings": [issue.message for issue in issues if issue.severity != "error"]})


@REGISTRY.register(
    "plate.export_dxf", "Export active or all plates as numbered local-X0/Y0 ASCII DXFs.",
    {"directory": "output directory", "base_name": "file name prefix", "scope": "active or all"},
)
def _plate_export_dxf(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    plates = _project_plates(session.project)
    if not plates:
        raise AutomationCommandError("Define or generate at least one plate before DXF export.", "plate.none")
    scope = str(params.get("scope", "active"))
    if scope not in {"active", "all"}:
        raise AutomationCommandError("scope must be 'active' or 'all'.", "parameter.value")
    active = int(session.project.metadata.get("active_plate_number", 1))
    targets = plates if scope == "all" else [plate for plate in plates if plate.number == active]
    directory = Path(_text(params, "directory")).expanduser().resolve()
    base_name = _text(params, "base_name")
    outputs = []
    for plate in targets:
        result = make_plate_project(session.project, plate)
        if not result.project.entities:
            continue
        try:
            exported = export_dxf(result.project, directory / dxf_output_filename(base_name, plate.number))
        except (OSError, ValueError) as exc:
            raise AutomationCommandError(str(exc), "dxf.export") from exc
        outputs.append({"plate": plate.number, "path": str(exported.path), "crossing_entity_ids": list(result.crossing_entity_ids)})
    return CommandEffect({"outputs": outputs})


@REGISTRY.register("operation.delete", "Delete machining operations by stable ID.", {"operation_ids": "array of operation IDs"})
def _operation_delete(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    values = params.get("operation_ids")
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise AutomationCommandError("operation_ids must be an array of strings.", "parameter.type")
    requested = set(values)
    known = {operation.id for operation in session.project.operations}
    missing = sorted(requested - known)
    if missing:
        raise AutomationCommandError(f"Unknown operation IDs: {', '.join(missing)}", "operation.missing")
    dependent = {
        operation.id for operation in session.project.operations
        if isinstance(operation, ProfileRestMachiningOperation) and operation.source_operation_id in requested
    }
    requested.update(dependent)
    session.project.operations = [operation for operation in session.project.operations if operation.id not in requested]
    return CommandEffect({"deleted_operation_ids": sorted(requested)}, changed=bool(requested))


@REGISTRY.register("operation.set_enabled", "Enable or disable machining operations.", {"operation_ids": "array of operation IDs", "enabled": "boolean"})
def _operation_set_enabled(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    values = params.get("operation_ids")
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise AutomationCommandError("operation_ids must be an array of strings.", "parameter.type")
    enabled = params.get("enabled")
    if not isinstance(enabled, bool):
        raise AutomationCommandError("enabled must be true or false.", "parameter.type")
    requested = set(values)
    known = {operation.id for operation in session.project.operations}
    missing = sorted(requested - known)
    if missing:
        raise AutomationCommandError(f"Unknown operation IDs: {', '.join(missing)}", "operation.missing")
    for operation in session.project.operations:
        if operation.id in requested:
            operation.enabled = enabled
    return CommandEffect({"operation_ids": sorted(requested), "enabled": enabled}, changed=bool(requested))


@REGISTRY.register(
    "operation.set_sequence", "Assign process role/priority and reorder operations independently of tool station.",
    {"operation_ids": "array of operation IDs", "sequence_role": "automatic, shelf_recess, pocket_fill, etc.", "sequence_priority": "optional -999..999; lower runs earlier within role", "sequence_locked": "optional boolean; preserve current list position during auto order"},
)
def _operation_set_sequence(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    values = params.get("operation_ids")
    if not isinstance(values, list) or not values or not all(isinstance(value, str) for value in values):
        raise AutomationCommandError("operation_ids must be a non-empty array of strings.", "parameter.type")
    requested = set(values)
    known = {operation.id for operation in session.project.operations}
    missing = sorted(requested - known)
    if missing:
        raise AutomationCommandError(f"Unknown operation IDs: {', '.join(missing)}", "operation.missing")
    role = str(params.get("sequence_role", "automatic"))
    priority = int(params.get("sequence_priority", 0))
    locked = params.get("sequence_locked", False)
    if not isinstance(locked, bool):
        raise AutomationCommandError("sequence_locked must be true or false.", "parameter.type")
    if role not in OPERATION_SEQUENCE_ROLES or not -999 <= priority <= 999:
        raise AutomationCommandError("Invalid sequence role or priority outside -999..999.", "operation.sequence")
    for operation in session.project.operations:
        if operation.id in requested:
            operation.sequence_role = role
            operation.sequence_priority = priority
            operation.sequence_locked = locked
    session.project.operations = ordered_operations(session.project.operations)
    return CommandEffect({"operation_ids": sorted(requested), "sequence_role": role, "sequence_priority": priority, "sequence_locked": locked}, changed=True)


@REGISTRY.register("operation.auto_order", "Dependency-order operations while preserving locked list positions.")
def _operation_auto_order(session: AutomationSession, _params: dict[str, Any]) -> CommandEffect:
    before = [operation.id for operation in session.project.operations]
    session.project.operations = ordered_operations(session.project.operations)
    after = [operation.id for operation in session.project.operations]
    return CommandEffect({"operation_ids": after, "locked": [operation.id for operation in session.project.operations if operation.sequence_locked]}, changed=before != after)


@REGISTRY.register(
    "job.setup", "Update material-aware job setup and small-part warning thresholds.",
    {"material": "material family", "material_detail": "grade or construction", "material_batch": "optional lot", "material_thickness": "positive project units", "through_cut_breakthrough": "nonnegative project units", "automatic_safe_z": "suggest 1.5 times thickness for new UI through-cuts only; never overwrite recipe, manual, or existing operation clearance", "safe_z": "positive job starting default, not a forced operation override", "small_part_checks": "boolean", "small_part_max_area": "positive square project units", "small_part_max_span": "positive project units"},
)
def _job_setup(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    setup = dict(session.project.metadata.get("job_setup", {}))
    for key in ("material", "material_detail", "material_batch", "machine", "xy_origin", "z_zero", "output_scope", "output_directory"):
        if key in params:
            setup[key] = str(params[key]).strip()
    for key in ("material_thickness", "safe_z", "small_part_max_area", "small_part_max_span"):
        if key in params:
            value = float(params[key])
            if value <= 0:
                raise AutomationCommandError(f"{key} must be positive.", "parameter.range")
            setup[key] = value
    if "through_cut_breakthrough" in params:
        breakthrough = float(params["through_cut_breakthrough"])
        if breakthrough < 0:
            raise AutomationCommandError("through_cut_breakthrough cannot be negative.", "parameter.range")
        setup["through_cut_breakthrough"] = breakthrough
    if "small_part_checks" in params:
        if not isinstance(params["small_part_checks"], bool):
            raise AutomationCommandError("small_part_checks must be true or false.", "parameter.type")
        setup["small_part_checks"] = params["small_part_checks"]
    if "automatic_safe_z" in params:
        if not isinstance(params["automatic_safe_z"], bool):
            raise AutomationCommandError("automatic_safe_z must be true or false.", "parameter.type")
        setup["automatic_safe_z"] = params["automatic_safe_z"]
    elif "safe_z" in params:
        # An explicit clearance from an older automation is a manual policy.
        setup["automatic_safe_z"] = False
    setup.setdefault("safe_z", 0.5 * unit_scale("in", session.project.units))
    session.project.metadata["job_setup"] = setup
    return CommandEffect({"job_setup": setup}, changed=True)


@REGISTRY.register(
    "workflow.analyze", "Report consolidation, small-part, and output-verification information without changing the job.",
    {"small_part_max_area": "optional square project units", "small_part_max_span": "optional project units"},
)
def _workflow_analyze(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    setup = dict(session.project.metadata.get("job_setup", {}))
    area = float(params.get("small_part_max_area", setup.get("small_part_max_area", 4.0)))
    span = float(params.get("small_part_max_span", setup.get("small_part_max_span", 1.5)))
    consolidation = tool_consolidation_suggestions(session.project)
    risks = small_part_risks(session.project, maximum_area=area, maximum_span=span)
    plates = _project_plates(session.project)
    rows = output_verification_rows(session.project, material=str(setup.get("material", "")), thickness=float(setup.get("material_thickness", 0)), plate_count=len(plates), material_detail=str(setup.get("material_detail", "")), material_batch=str(setup.get("material_batch", "")))
    return CommandEffect({
        "consolidation": [suggestion.__dict__ for suggestion in consolidation],
        "small_parts": [risk.__dict__ for risk in risks],
        "output_verification": [{"check": key, "value": value} for key, value in rows],
    })


@REGISTRY.register(
    "tool.consolidate", "Move operations between equivalent catalog tools and release the redundant station while preserving operation cutting values.",
    {"keep_station": "station to retain", "release_station": "equivalent station to clear"},
)
def _tool_consolidate(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    keep, release = int(params.get("keep_station", 0)), int(params.get("release_station", 0))
    suggestion = next((item for item in tool_consolidation_suggestions(session.project) if item.keep_station == keep and item.release_station == release), None)
    if suggestion is None:
        raise AutomationCommandError("Stations are not a current equivalent-tool consolidation pair.", "tool.not_equivalent")
    release_tool = session.project.tool_library.by_station(release)
    if release_tool is None:
        raise AutomationCommandError(f"T{release} is empty.", "tool.missing")
    moved = []
    for operation in session.project.operations:
        if operation.tool_station != release:
            continue
        operation.feed_rate_override = operation.feed_rate_override if operation.feed_rate_override is not None else release_tool.feed_rate
        operation.plunge_rate_override = operation.plunge_rate_override if operation.plunge_rate_override is not None else release_tool.plunge_rate
        operation.spindle_rpm_override = operation.spindle_rpm_override if operation.spindle_rpm_override is not None else release_tool.spindle_rpm
        operation.recipe_name = operation.recipe_name or str(release_tool.metadata.get("material_recipe", ""))
        operation.recipe_metadata = operation.recipe_metadata or copy.deepcopy(release_tool.metadata.get("loaded_recipe", {}))
        operation.tool_station = keep
        moved.append(operation.id)
    session.project.tool_library.tools = [tool for tool in session.project.tool_library.tools if tool.station != release]
    return CommandEffect({"keep_station": keep, "released_station": release, "operation_ids": moved}, changed=True)


@REGISTRY.register(
    "operation.set_hold_down", "Set explicit small-part handling on router operations.",
    {"operation_ids": "router operation IDs", "strategy": "automatic, none, tabs, onion_skin, or order_early", "tab_mode": "even, minimum_and_spacing, minimum_avoid_corners, support_aware, or corners_and_spacing", "tab_count": "optional minimum", "tab_width": "optional", "tab_height": "optional", "corner_tab_offset": "optional", "tab_spacing": "optional", "female_tab_min_perimeter": "optional female-cleanout threshold", "tab_cleanup_min_perimeter": "optional minimum perimeter for automatic tab removal", "remove_tabs_after": "optional", "onion_skin": "optional"},
)
def _operation_set_hold_down(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    requested = params.get("operation_ids")
    if not isinstance(requested, list) or not requested or not all(isinstance(value, str) for value in requested):
        raise AutomationCommandError("operation_ids must be a non-empty string array.", "parameter.type")
    strategy = str(params.get("strategy", "automatic"))
    if strategy not in {"automatic", "none", "tabs", "onion_skin", "order_early"}:
        raise AutomationCommandError("Invalid small-part strategy.", "operation.hold_down")
    matches = [operation for operation in session.project.operations if operation.id in set(requested)]
    if len(matches) != len(set(requested)) or any(not isinstance(operation, RouterOffsetOperation) for operation in matches):
        raise AutomationCommandError("All IDs must reference router-offset operations.", "operation.type")
    for operation in matches:
        operation.small_part_strategy = strategy
        for key in ("tab_count", "tab_width", "tab_height", "onion_skin", "corner_tab_offset", "tab_spacing", "female_tab_min_perimeter", "tab_cleanup_min_perimeter"):
            if key in params:
                setattr(operation, key, int(params[key]) if key == "tab_count" else float(params[key]))
        if "tab_mode" in params:
            operation.tab_mode = str(params["tab_mode"])
        if "remove_tabs_after" in params:
            operation.remove_tabs_after = bool(params["remove_tabs_after"])
        if "tab_cleanup_order" in params:
            operation.tab_cleanup_order = str(params["tab_cleanup_order"])
        if "manual_tab_positions" in params:
            operation.manual_tab_positions = [float(value) for value in params["manual_tab_positions"]]
        has_tabs = operation.tab_count > 0 or operation.tab_mode in {"minimum_and_spacing", "minimum_avoid_corners", "support_aware", "corners_and_spacing", "manual", "aligned"}
        if operation.tab_mode not in {"even", "minimum_and_spacing", "minimum_avoid_corners", "support_aware", "corners_and_spacing", "manual", "aligned"} or operation.remove_tabs_after and not has_tabs:
            raise AutomationCommandError("Choose valid tab placement; cleanup requires enabled tabs.", "operation.parameters")
        if operation.tab_cleanup_order not in {"after_each_part", "end_of_operation"}:
            raise AutomationCommandError("Tab cleanup order must be after_each_part or end_of_operation.", "operation.parameters")
        if operation.tab_mode in {"minimum_and_spacing", "minimum_avoid_corners", "support_aware", "corners_and_spacing", "aligned"} and operation.tab_spacing <= 0:
            raise AutomationCommandError("Maximum tab spacing must be positive for the selected strategy.", "operation.parameters")
    return CommandEffect({"operation_ids": requested, "strategy": strategy}, changed=True)


@REGISTRY.register(
    "operation.completeness", "Reconcile selected source geometry with generated toolpath blocks and keepout zones.",
    {"operation_ids": "optional operation ID array"},
)
def _operation_completeness(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    requested = params.get("operation_ids")
    ids = None if requested is None else {str(value) for value in requested}
    report = analyze_toolpath_completeness(session.project, ids)
    issues = [
        {
            "severity": item.severity, "code": item.code, "message": item.message,
            "operation_id": item.operation_id, "entity_ids": list(item.entity_ids),
            "point": None if item.point is None else {"x": item.point.x, "y": item.point.y},
        }
        for item in (*report.issues, *keepout_violations(session.project, ids))
    ]
    return CommandEffect({
        "complete": report.complete and not any(item["severity"] == "error" for item in issues),
        "operation_count": report.operation_count, "expected_entity_count": report.expected_entity_count,
        "covered_entity_count": report.covered_entity_count, "generated_block_count": report.generated_block_count,
        "issues": issues,
    })


@REGISTRY.register(
    "operation.bind_selector", "Associate an operation with geometry properties so revised DXF geometry can be rebound.",
    {"operation_id": "operation ID", "selector": "optional selector object; omit to derive from current geometry"},
)
def _operation_bind_selector(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    operation_id = str(params.get("operation_id", ""))
    operation = next((item for item in session.project.operations if item.id == operation_id), None)
    if operation is None:
        raise AutomationCommandError("Operation was not found.", "operation.missing")
    selector = params.get("selector")
    if selector is None:
        by_id = {entity.id: entity for entity in session.project.entities}
        selected = [by_id[value] for value in operation.entity_ids if value in by_id]
        selector = selector_from_entities(selected)
    if not isinstance(selector, dict):
        raise AutomationCommandError("selector must be an object.", "parameter.type")
    operation.geometry_selector = copy.deepcopy(selector)
    changed = refresh_associative_operations(session.project)
    return CommandEffect({"operation_id": operation.id, "selector": operation.geometry_selector, "entity_ids": operation.entity_ids, "refreshed_operation_ids": list(changed)}, changed=True)


@REGISTRY.register("operation.refresh_selectors", "Re-evaluate every associative geometry selector against the current drawing.")
def _operation_refresh_selectors(session: AutomationSession, _params: dict[str, Any]) -> CommandEffect:
    changed = refresh_associative_operations(session.project)
    return CommandEffect({"operation_ids": list(changed)}, changed=bool(changed))


@REGISTRY.register(
    "operation.set_dependencies", "Set hard prerequisite operations used by ordering, simulation, and output.",
    {"operation_id": "operation ID", "depends_on_ids": "operation ID array"},
)
def _operation_set_dependencies(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    operation_id = str(params.get("operation_id", ""))
    operation = next((item for item in session.project.operations if item.id == operation_id), None)
    requested = params.get("depends_on_ids", [])
    if operation is None or not isinstance(requested, list):
        raise AutomationCommandError("Choose an operation and dependency ID array.", "operation.dependencies")
    valid = {item.id for item in session.project.operations}
    dependencies = [str(value) for value in requested]
    if operation_id in dependencies or any(value not in valid for value in dependencies):
        raise AutomationCommandError("Dependencies must reference other existing operations.", "operation.dependencies")
    operation.depends_on_ids = list(dict.fromkeys(dependencies))
    return CommandEffect(operation.to_dict(), changed=True)


@REGISTRY.register(
    "project.reimport_dxf", "Update source geometry from a revised DXF while retaining CAM operations and associative bindings.",
    {"path": "revised ASCII DXF path"},
)
def _project_reimport_dxf(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    path = Path(str(params.get("path", "")))
    if not path.is_file():
        raise AutomationCommandError("Revised DXF file was not found.", "file.missing")
    revised = import_dxf(path).project
    report = reconcile_dxf_revision(session.project, revised)
    session.project.source["revision_path"] = str(path)
    session.selected_ids.intersection_update(entity.id for entity in session.project.entities)
    return CommandEffect(report.__dict__, changed=True)


@REGISTRY.register("keepout.list", "List machine-space keepout zones.")
def _keepout_list(session: AutomationSession, _params: dict[str, Any]) -> CommandEffect:
    return CommandEffect({"keepouts": [zone.to_dict() for zone in session.project.keepouts]})


@REGISTRY.register(
    "keepout.add", "Create a polygonal keepout with a required clearance height.",
    {"name": "operator-facing name", "points": "array of {x,y}", "clearance_z": "required crossing height"},
)
def _keepout_add(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    raw = params.get("points")
    if not isinstance(raw, list) or len(raw) < 3:
        raise AutomationCommandError("Keepout points must contain at least three coordinates.", "keepout.geometry")
    try:
        points = [Point(float(value["x"]), float(value["y"])) for value in raw]
        clearance = float(params.get("clearance_z", 0.5))
    except (KeyError, TypeError, ValueError) as exc:
        raise AutomationCommandError("Keepout coordinates and clearance must be numeric.", "keepout.geometry") from exc
    if clearance <= 0:
        raise AutomationCommandError("Keepout clearance must be positive.", "keepout.clearance")
    zone = KeepoutZone(str(params.get("name", f"Keepout {len(session.project.keepouts) + 1}")), points, clearance)
    session.project.keepouts.append(zone)
    return CommandEffect(zone.to_dict(), changed=True)


@REGISTRY.register("keepout.delete", "Delete a keepout zone.", {"keepout_id": "keepout ID"})
def _keepout_delete(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    keepout_id = str(params.get("keepout_id", ""))
    before = len(session.project.keepouts)
    session.project.keepouts = [zone for zone in session.project.keepouts if zone.id != keepout_id]
    if len(session.project.keepouts) == before:
        raise AutomationCommandError("Keepout was not found.", "keepout.missing")
    return CommandEffect({"keepout_id": keepout_id}, changed=True)


@REGISTRY.register("template.list", "List multi-operation toolpath templates stored with the project.")
def _template_list(session: AutomationSession, _params: dict[str, Any]) -> CommandEffect:
    return CommandEffect({"templates": copy.deepcopy(session.project.metadata.get("toolpath_templates", []))})


@REGISTRY.register(
    "template.create", "Save a reusable template containing a complete ordered group of operations.",
    {"name": "template name", "operation_ids": "operation ID array", "description": "optional notes"},
)
def _template_create(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    ids = [str(value) for value in params.get("operation_ids", [])]
    operations = [item for item in session.project.operations if item.id in ids]
    if len(operations) != len(set(ids)):
        raise AutomationCommandError("Every template operation ID must exist.", "template.operations")
    template = create_toolpath_template(str(params.get("name", "")), operations, str(params.get("description", "")))
    session.project.metadata.setdefault("toolpath_templates", []).append(template.to_dict())
    return CommandEffect(template.to_dict(), changed=True)


@REGISTRY.register("template.apply", "Instantiate a stored multi-operation template and resolve its geometry selectors.", {"template_id": "template ID"})
def _template_apply(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    template_id = str(params.get("template_id", ""))
    payload = next((item for item in session.project.metadata.get("toolpath_templates", []) if str(item.get("id", "")) == template_id), None)
    if payload is None:
        raise AutomationCommandError("Toolpath template was not found.", "template.missing")
    created = instantiate_toolpath_template(session.project, ToolpathTemplate.from_dict(payload))
    return CommandEffect({"operation_ids": [item.id for item in created], "operations": [item.to_dict() for item in created]}, changed=True)


@REGISTRY.register(
    "cycle.settings", "Read or update the project-level conservative cycle-time optimizer.",
    {
        "preset": "optional 'production' preset ('balanced' remains a compatibility alias)",
        "enabled": "enable optimized preview, simulation, and output",
        "depth_linking": "stay down between eligible profile depth passes",
        "profile_ordering": "nearest profile order within safe process phases",
        "profile_travel_strategy": "generated, nearest_safe, or y_serpentine",
        "profile_start_optimization": "rotate eligible closed-profile start points",
        "drill_ordering": "optimize drilling order",
        "engraving_ordering": "optimize engraving order and open-path direction",
        "minimum_retract": "use low retracts between nearby blocks",
        "minimum_retract_height": "low travel height in project units",
        "minimum_retract_max_distance": "maximum low-link XY distance in project units",
        "motion_compaction": "merge redundant line motions",
        "line_merge_tolerance": "line-merge deviation in project units",
        "arc_fitting": "fit eligible linear runs to G02/G03",
        "arc_fitting_tolerance": "maximum arc-fit radial deviation in project units",
        "rapid_xy_ipm": "estimated machine XY rapid in inches/minute",
        "rapid_z_ipm": "estimated machine Z rapid in inches/minute",
        "tool_change_seconds": "estimated seconds per tool change",
        "spindle_start_seconds": "estimated seconds per station start",
    },
)
def _cycle_settings(session: AutomationSession, params: dict[str, Any]) -> CommandEffect:
    defaults = default_cycle_optimization_settings(session.project.units)
    existing = dict(session.project.metadata.get("cycle_optimization", {}))
    values = dict(
        production_cycle_optimization_settings(session.project.units)
        if params.get("preset") in {"balanced", "production"}
        else {**defaults, **existing}
    )
    boolean_fields = {
        "enabled", "depth_linking", "profile_ordering", "profile_start_optimization",
        "drill_ordering", "engraving_ordering", "minimum_retract",
        "motion_compaction", "arc_fitting",
    }
    choice_fields = {"profile_travel_strategy"}
    numeric_fields = set(defaults) - boolean_fields - choice_fields
    for key in boolean_fields:
        if key in params:
            if not isinstance(params[key], bool):
                raise AutomationCommandError(f"{key} must be true or false.", "parameter.type")
            values[key] = params[key]
    for key in numeric_fields:
        if key in params:
            try:
                values[key] = float(params[key])
            except (TypeError, ValueError) as exc:
                raise AutomationCommandError(f"{key} must be numeric.", "parameter.type") from exc
    if "profile_travel_strategy" in params:
        values["profile_travel_strategy"] = str(params["profile_travel_strategy"])
    clone = CamProject.from_dict(copy.deepcopy(session.project.to_dict()))
    clone.metadata["cycle_optimization"] = values
    try:
        validate_cycle_optimization_settings(cycle_optimization_settings(clone))
    except ValueError as exc:
        raise AutomationCommandError(str(exc), "cycle.settings") from exc
    changed = bool(params) and values != existing
    if changed:
        session.project.metadata["cycle_optimization"] = values
    return CommandEffect({"units": session.project.units, "settings": values}, changed=changed)


@REGISTRY.register("cycle.analyze", "Compare baseline and optimized generated motion for the current project.")
def _cycle_analyze(session: AutomationSession, _params: dict[str, Any]) -> CommandEffect:
    try:
        comparison = compare_project_cycle_time(session.project)
    except ValueError as exc:
        raise AutomationCommandError(str(exc), "cycle.analysis") from exc
    return CommandEffect(comparison.to_dict())


@REGISTRY.register("history.undo", "Undo the most recent automation mutation.")
def _history_undo(session: AutomationSession, _params: dict[str, Any]) -> CommandEffect:
    if not session.can_undo:
        raise AutomationCommandError("There is no automation action to undo.", "history.empty")
    session._history_index -= 1
    session.project = CamProject.from_dict(copy.deepcopy(session._history[session._history_index]))
    session.selected_ids.intersection_update(entity.id for entity in session.project.entities)
    return CommandEffect({"can_undo": session.can_undo, "can_redo": session.can_redo}, changed=True, record_history=False)


@REGISTRY.register("history.redo", "Redo the next automation mutation.")
def _history_redo(session: AutomationSession, _params: dict[str, Any]) -> CommandEffect:
    if not session.can_redo:
        raise AutomationCommandError("There is no automation action to redo.", "history.empty")
    session._history_index += 1
    session.project = CamProject.from_dict(copy.deepcopy(session._history[session._history_index]))
    session.selected_ids.intersection_update(entity.id for entity in session.project.entities)
    return CommandEffect({"can_undo": session.can_undo, "can_redo": session.can_redo}, changed=True, record_history=False)
