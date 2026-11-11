from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import math
from pathlib import Path
import re
from typing import Iterable

from .model import CamProject, Circle, Entity, Line, Point, Polyline, Rectangle, TextEntity
from .feature_recognition import recognize_circular_polylines
from .layout import SHEET_SIZES, drawing_bounds
from .plates import Plate, validate_plate_definitions
from .transform import transform_entity
from .units import convert_project_units, unit_scale
from .text_geometry import resolve_font


JOIN_TOLERANCE = 0.002
ARC_CHORD_ERROR = 0.001


@dataclass
class DxfImportResult:
    project: CamProject
    warnings: list[str]
    entity_counts: Counter


@dataclass
class MultiDxfImportResult:
    project: CamProject
    warnings: list[str]
    entity_counts: Counter
    plates: tuple[Plate, ...]
    plate_sources: tuple[dict, ...]


@dataclass(frozen=True)
class DxfExportResult:
    path: Path
    entity_counts: Counter
    layer_names: tuple[str, ...]


DXF_UNIT_CODES = {"in": 1, "ft": 2, "mm": 4, "cm": 5, "m": 6, "yd": 10}

# Notes and drafting aids are intentionally not machining geometry. Silently
# converting them to cutter paths would be dangerous; retain their counts for
# an unobtrusive import summary instead of presenting them as failures.
IGNORED_ANNOTATION_TYPES = frozenset({
    "DIMENSION", "LEADER", "MLEADER",
    "ATTDEF", "ATTRIB", "TOLERANCE",
})


def dxf_output_filename(base_name: str, plate_number: int | None = None) -> str:
    clean = base_name.strip()
    if clean.casefold().endswith(".dxf"):
        clean = clean[:-4].rstrip()
    if not clean:
        raise ValueError("DXF file name cannot be empty.")
    if any(character in clean for character in '<>:\"/\\|?*') or clean.endswith((".", " ")):
        raise ValueError("DXF file name contains characters Windows cannot use.")
    suffix = "" if plate_number is None else f" {plate_number:02d}"
    stem = clean if not suffix or clean.endswith(suffix) else clean + suffix
    return f"{stem}.dxf"


def is_dxf_export_eligible(entity: Entity) -> bool:
    """Return whether an entity is intentional DXF drawing content.

    Bent-tab source references are private associative backups.  They remain
    in the CAM job so tabs can be regenerated, but exporting them would put a
    stale pre-tab/pre-transform outline into Flexi.  Other non-machining
    entities, including bend guides and drafting annotations, remain eligible
    because they are intentional drawing content.
    """
    metadata = entity.metadata
    if metadata.get("bent_tab_source_reference") or metadata.get("tab_slot_source_reference"):
        return False
    return not (
        entity.layer in {"_BENT_TAB_SOURCE", "TAB_SLOT_SOURCE_REFERENCE"}
        and metadata.get("reference_only")
        and (metadata.get("bent_tab_generated") or metadata.get("tab_slot_generated"))
    )


def dxf_export_entities(
    project: CamProject,
    entities: Iterable[Entity] | None = None,
) -> list[Entity]:
    """Resolve the public drawing entities for every DXF export scope."""
    candidates = project.entities if entities is None else entities
    return [entity for entity in candidates if is_dxf_export_eligible(entity)]


def export_dxf(project: CamProject, path: Path, entities: list[Entity] | None = None, *, dialect: str = "enroute_r12") -> DxfExportResult:
    """Write editable source geometry as deterministic ASCII DXF.

    Generated CAM previews are intentionally not part of ``CamProject.entities``
    and therefore cannot leak into this interchange file.
    """
    selected = dxf_export_entities(project, entities)
    unsupported = [entity for entity in selected if not isinstance(entity, (Line, Circle, Rectangle, Polyline, TextEntity))]
    if unsupported:
        raise ValueError(f"DXF export does not support: {', '.join(sorted({entity.kind for entity in unsupported}))}.")
    if not selected:
        raise ValueError("DXF export requires at least one geometry object.")
    if project.units not in DXF_UNIT_CODES:
        raise ValueError(f"DXF export does not support project units {project.units!r}.")
    if dialect not in {"enroute_r12", "acad2000"}:
        raise ValueError("DXF dialect must be 'enroute_r12' or 'acad2000'.")

    path = Path(path)
    if path.suffix.casefold() != ".dxf":
        path = path.with_suffix(".dxf")
    path.parent.mkdir(parents=True, exist_ok=True)
    layer_map = _export_layer_map(selected)
    entity_styles, text_styles = _export_text_styles(selected)
    if dialect == "enroute_r12":
        return _export_enroute_r12(project, path, selected, layer_map, entity_styles, text_styles)
    pairs: list[tuple[int, object]] = []

    def add(code: int, value: object) -> None:
        pairs.append((code, value))

    add(0, "SECTION"); add(2, "HEADER")
    add(9, "$ACADVER"); add(1, "AC1015")
    add(9, "$INSUNITS"); add(70, DXF_UNIT_CODES[project.units])
    add(0, "ENDSEC")
    add(0, "SECTION"); add(2, "TABLES")
    add(0, "TABLE"); add(2, "LAYER"); add(70, len(set(layer_map.values())))
    for layer in sorted(set(layer_map.values()), key=str.casefold):
        add(0, "LAYER"); add(5, _next_handle(pairs)); add(100, "AcDbSymbolTableRecord"); add(100, "AcDbLayerTableRecord")
        add(2, layer); add(70, 0); add(62, 7); add(6, "CONTINUOUS")
    add(0, "ENDTAB")
    add(0, "TABLE"); add(2, "STYLE"); add(70, len(text_styles))
    for style_name, font_file in text_styles:
        add(0, "STYLE"); add(5, _next_handle(pairs)); add(100, "AcDbSymbolTableRecord"); add(100, "AcDbTextStyleTableRecord")
        add(2, style_name); add(70, 0); add(40, 0.0); add(41, 1.0); add(50, 0.0); add(71, 0); add(42, 0.2)
        add(3, font_file); add(4, "")
    add(0, "ENDTAB"); add(0, "ENDSEC")
    add(0, "SECTION"); add(2, "ENTITIES")
    counts = Counter()
    for entity in selected:
        layer = layer_map[entity.layer or "0"]
        handle = _next_handle(pairs)
        if isinstance(entity, Line):
            counts["LINE"] += 1
            add(0, "LINE"); add(5, handle); add(100, "AcDbEntity"); add(8, layer); add(100, "AcDbLine")
            add(10, entity.start.x); add(20, entity.start.y); add(30, 0.0)
            add(11, entity.end.x); add(21, entity.end.y); add(31, 0.0)
        elif isinstance(entity, Circle):
            counts["CIRCLE"] += 1
            add(0, "CIRCLE"); add(5, handle); add(100, "AcDbEntity"); add(8, layer); add(100, "AcDbCircle")
            add(10, entity.center.x); add(20, entity.center.y); add(30, 0.0); add(40, entity.radius)
        elif isinstance(entity, TextEntity):
            if entity.multiline or "\n" in entity.text:
                counts["MTEXT"] += 1
                add(0, "MTEXT"); add(5, handle); add(100, "AcDbEntity"); add(8, layer); add(100, "AcDbMText")
                add(10, entity.insertion.x); add(20, entity.insertion.y); add(30, 0.0)
                add(40, entity.height); add(41, max(entity.height, len(entity.text) * entity.height * 0.62))
                add(71, {"left": 7, "center": 8, "right": 9}.get(entity.alignment, 7))
                add(1, entity.text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", r"\P"))
                add(7, entity_styles[entity.id]); add(50, entity.rotation)
            else:
                counts["TEXT"] += 1
                _append_text_pairs(
                    add, entity, layer, handle=handle, acad2000=True,
                    style_name=entity_styles[entity.id],
                )
        else:
            points = (
                [
                    Point(entity.corner_a.x, entity.corner_a.y), Point(entity.corner_b.x, entity.corner_a.y),
                    Point(entity.corner_b.x, entity.corner_b.y), Point(entity.corner_a.x, entity.corner_b.y),
                ]
                if isinstance(entity, Rectangle) else list(entity.points)
            )
            closed = isinstance(entity, Rectangle) or entity.closed
            if len(points) < (3 if closed else 2):
                raise ValueError(f"Geometry {entity.id} has too few points for DXF export.")
            counts["LWPOLYLINE"] += 1
            add(0, "LWPOLYLINE"); add(5, handle); add(100, "AcDbEntity"); add(8, layer); add(100, "AcDbPolyline")
            add(90, len(points)); add(70, 1 if closed else 0); add(43, 0.0)
            for point in points:
                add(10, point.x); add(20, point.y)
    add(0, "ENDSEC"); add(0, "EOF")
    text = "".join(f"{code}\n{_dxf_value(value)}\n" for code, value in pairs)
    path.write_text(text, encoding="ascii", newline="\r\n")
    return DxfExportResult(path, counts, tuple(sorted(set(layer_map.values()), key=str.casefold)))


def _export_enroute_r12(
    project: CamProject,
    path: Path,
    entities: list[Entity],
    layer_map: dict[str, str],
    entity_styles: dict[str, str],
    text_styles: tuple[tuple[str, str], ...],
) -> DxfExportResult:
    """Write the conservative entity dialect observed in local EnRoute files."""
    from .layout import drawing_bounds

    pairs: list[tuple[int, object]] = []
    add = lambda code, value: pairs.append((code, value))
    bounds = drawing_bounds(entities, include_annotations=True) or (0.0, 0.0, 0.0, 0.0)
    add(0, "SECTION"); add(2, "HEADER")
    add(9, "$EXTMIN"); add(10, bounds[0]); add(20, bounds[1])
    add(9, "$EXTMAX"); add(10, bounds[2]); add(20, bounds[3])
    add(9, "$INSUNITS"); add(70, DXF_UNIT_CODES[project.units])
    add(9, "$MEASUREMENT"); add(70, 1 if project.units in {"mm", "cm", "m"} else 0)
    add(0, "ENDSEC")
    add(0, "SECTION"); add(2, "TABLES")
    add(0, "TABLE"); add(2, "LAYER"); add(70, len(set(layer_map.values())))
    for layer in sorted(set(layer_map.values()), key=str.casefold):
        add(0, "LAYER"); add(2, layer); add(70, 0); add(62, 7); add(6, "CONTINUOUS")
    add(0, "ENDTAB")
    add(0, "TABLE"); add(2, "STYLE"); add(70, len(text_styles))
    for style_name, font_file in text_styles:
        add(0, "STYLE"); add(2, style_name); add(70, 0); add(40, 0.0); add(41, 1.0)
        add(50, 0.0); add(71, 0); add(42, 0.2); add(3, font_file); add(4, "")
    add(0, "ENDTAB"); add(0, "ENDSEC")
    add(0, "SECTION"); add(2, "BLOCKS"); add(0, "ENDSEC")
    add(0, "SECTION"); add(2, "ENTITIES")
    counts = Counter()
    for entity in entities:
        layer = layer_map[entity.layer or "0"]
        if isinstance(entity, Line):
            counts["LINE"] += 1
            add(0, "LINE"); add(8, layer); add(62, 7)
            add(10, entity.start.x); add(20, entity.start.y); add(11, entity.end.x); add(21, entity.end.y)
        elif isinstance(entity, Circle):
            counts["CIRCLE"] += 1
            add(0, "CIRCLE"); add(8, layer); add(62, 7)
            add(10, entity.center.x); add(20, entity.center.y); add(40, entity.radius)
        elif isinstance(entity, TextEntity):
            text_lines = entity.text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            for line_index, line in enumerate(text_lines):
                if not line:
                    continue
                counts["TEXT"] += 1
                radians = math.radians(entity.rotation)
                insertion = Point(
                    entity.insertion.x + math.sin(radians) * line_index * entity.height * 1.2,
                    entity.insertion.y - math.cos(radians) * line_index * entity.height * 1.2,
                )
                line_entity = TextEntity(
                    layer=entity.layer, insertion=insertion, text=line,
                    height=entity.height, rotation=entity.rotation,
                    alignment=entity.alignment, style_name=entity.style_name,
                    font_family=entity.font_family, width_factor=entity.width_factor,
                )
                _append_text_pairs(add, line_entity, layer, style_name=entity_styles[entity.id])
        else:
            points = (
                [
                    Point(entity.corner_a.x, entity.corner_a.y), Point(entity.corner_b.x, entity.corner_a.y),
                    Point(entity.corner_b.x, entity.corner_b.y), Point(entity.corner_a.x, entity.corner_b.y),
                ]
                if isinstance(entity, Rectangle) else list(entity.points)
            )
            closed = isinstance(entity, Rectangle) or entity.closed
            if len(points) < (3 if closed else 2):
                raise ValueError(f"Geometry {entity.id} has too few points for DXF export.")
            counts["POLYLINE"] += 1
            add(0, "POLYLINE"); add(8, layer); add(66, 1); add(10, 0.0); add(20, 0.0); add(30, 0.0); add(70, 1 if closed else 0)
            for point in points:
                add(0, "VERTEX"); add(8, layer); add(10, point.x); add(20, point.y); add(30, 0.0); add(70, 32)
            add(0, "SEQEND"); add(8, layer)
    add(0, "ENDSEC"); add(0, "EOF")
    text = "".join(f"{code:>3}\n{_dxf_value(value)}\n" for code, value in pairs)
    path.write_text(text, encoding="ascii", newline="\r\n")
    return DxfExportResult(path, counts, tuple(sorted(set(layer_map.values()), key=str.casefold)))


def _append_text_pairs(
    add,
    entity: TextEntity,
    layer: str,
    *,
    handle: str | None = None,
    acad2000: bool = False,
    style_name: str | None = None,
) -> None:
    """Append one portable TEXT entity in R12 or AutoCAD 2000 form."""
    add(0, "TEXT")
    if handle is not None:
        add(5, handle)
    if acad2000:
        add(100, "AcDbEntity")
    add(8, layer)
    if acad2000:
        add(100, "AcDbText")
    add(10, entity.insertion.x); add(20, entity.insertion.y); add(30, 0.0)
    add(40, entity.height); add(1, entity.text.replace("\n", " "))
    add(50, entity.rotation); add(41, entity.width_factor); add(7, style_name or entity.style_name or "STANDARD")
    horizontal = {"left": 0, "center": 1, "right": 2}.get(entity.alignment, 0)
    add(72, horizontal)
    if horizontal:
        add(11, entity.insertion.x); add(21, entity.insertion.y); add(31, 0.0)
    if acad2000:
        add(100, "AcDbText"); add(73, 0)


def _export_layer_map(entities: list[Entity]) -> dict[str, str]:
    output: dict[str, str] = {}
    used: set[str] = set()
    for original in dict.fromkeys(entity.layer or "0" for entity in entities):
        cleaned = "".join("_" if character in '<>/\\\":;?*|=' or ord(character) < 32 or ord(character) > 126 else character for character in original).strip() or "0"
        cleaned = cleaned[:31]
        candidate, suffix = cleaned, 2
        while candidate.casefold() in used:
            tail = f"_{suffix}"
            candidate = cleaned[:31 - len(tail)] + tail
            suffix += 1
        used.add(candidate.casefold())
        output[original] = candidate
    return output


def _export_text_styles(
    entities: list[Entity],
) -> tuple[dict[str, str], tuple[tuple[str, str], ...]]:
    """Build portable STYLE records while preserving each text font choice."""
    text_entities = [entity for entity in entities if isinstance(entity, TextEntity)]
    if not text_entities:
        return {}, ()
    entity_styles: dict[str, str] = {}
    styles: dict[str, str] = {}
    style_keys: dict[tuple[str, str], str] = {}
    used: set[str] = set()
    for entity in text_entities:
        resolution = resolve_font(entity.font_family)
        font_file = resolution.path.name if resolution.path is not None else "txt.shx"
        preferred = (entity.style_name or "STANDARD").strip() or "STANDARD"
        if preferred.casefold() == "standard" and resolution.resolved_family.casefold() not in {"arial", "tkdefaultfont"}:
            preferred = f"CW_{resolution.resolved_family}"
        key = (preferred.casefold(), font_file.casefold())
        style_name = style_keys.get(key)
        if style_name is None:
            cleaned = "".join(
                "_" if character in '<>/\\\":;?*|=,`' or ord(character) < 32 or ord(character) > 126 else character
                for character in preferred
            ).strip() or "STANDARD"
            cleaned = cleaned[:31]
            candidate, suffix = cleaned, 2
            while candidate.casefold() in used:
                tail = f"_{suffix}"
                candidate = cleaned[:31 - len(tail)] + tail
                suffix += 1
            style_name = candidate
            used.add(style_name.casefold())
            style_keys[key] = style_name
            styles[style_name] = font_file
        entity_styles[entity.id] = style_name
    return entity_styles, tuple(sorted(styles.items(), key=lambda item: item[0].casefold()))


def _next_handle(pairs: list[tuple[int, object]]) -> str:
    return f"{0x100 + sum(code == 5 for code, _value in pairs):X}"


def _dxf_value(value: object) -> str:
    if isinstance(value, float):
        if abs(value) < 5e-13:
            value = 0.0
        return f"{value:.12g}"
    return str(value).encode("ascii", errors="replace").decode("ascii")


@dataclass
class _Primitive:
    points: list[Point]
    layer: str
    source_type: str
    handle: str
    closed: bool = False


def import_dxf(path: Path) -> DxfImportResult:
    pairs = _read_pairs(path)
    units, unit_warning = _read_units(pairs)
    raw_entities = _parse_entities(pairs)
    warnings = [unit_warning] if unit_warning else []
    counts = Counter(entity["type"] for entity in raw_entities)
    primitives: list[_Primitive] = []
    circles: list[Circle] = []
    texts: list[TextEntity] = []
    unsupported = Counter()
    ignored_annotations = Counter()
    styles = _read_text_styles(pairs)
    substitutions: set[tuple[str, str]] = set()

    index = 0
    while index < len(raw_entities):
        entity = raw_entities[index]
        kind = entity["type"]
        if kind == "LINE":
            start = Point(_float(entity, 10), _float(entity, 20))
            end = Point(_float(entity, 11), _float(entity, 21))
            if start.distance_to(end) > 1e-12:
                primitives.append(_Primitive([start, end], _layer(entity), kind, _value(entity, 5)))
        elif kind == "ARC":
            primitives.append(
                _Primitive(
                    _arc_points(
                        Point(_float(entity, 10), _float(entity, 20)),
                        _float(entity, 40),
                        _float(entity, 50),
                        _float(entity, 51),
                    ),
                    _layer(entity),
                    kind,
                    _value(entity, 5),
                )
            )
        elif kind == "CIRCLE":
            radius = _float(entity, 40)
            if radius > 0:
                circles.append(
                    Circle(
                        layer=_layer(entity),
                        center=Point(_float(entity, 10), _float(entity, 20)),
                        radius=radius,
                        metadata={"dxf_type": kind, "dxf_handle": _value(entity, 5)},
                    )
                )
        elif kind in {"TEXT", "MTEXT"}:
            text_entity = _text_entity(entity, styles)
            if text_entity.text.strip() and text_entity.height > 0:
                texts.append(text_entity)
                if text_entity.metadata.get("font_substituted"):
                    substitutions.add((
                        str(text_entity.metadata.get("requested_font", text_entity.font_family)),
                        str(text_entity.metadata.get("resolved_font", "Arial")),
                    ))
        elif kind == "LWPOLYLINE":
            points, bulges = _lwpolyline_vertices(entity)
            closed = bool(_int(entity, 70) & 1)
            expanded = _expand_bulges(points, bulges, closed)
            if len(expanded) >= 2:
                primitives.append(_Primitive(expanded, _layer(entity), kind, _value(entity, 5), closed))
        elif kind == "POLYLINE":
            vertices = []
            polyline = entity
            index += 1
            while index < len(raw_entities) and raw_entities[index]["type"] == "VERTEX":
                vertex = raw_entities[index]
                vertices.append((Point(_float(vertex, 10), _float(vertex, 20)), _float(vertex, 42)))
                index += 1
            if index < len(raw_entities) and raw_entities[index]["type"] == "SEQEND":
                pass
            else:
                index -= 1
            closed = bool(_int(polyline, 70) & 1)
            if len(vertices) >= 2:
                points = [vertex[0] for vertex in vertices]
                bulges = [vertex[1] for vertex in vertices]
                primitives.append(
                    _Primitive(
                        _expand_bulges(points, bulges, closed),
                        _layer(polyline),
                        kind,
                        _value(polyline, 5),
                        closed,
                    )
                )
        elif kind in IGNORED_ANNOTATION_TYPES:
            ignored_annotations[kind] += 1
        elif kind not in {"VERTEX", "SEQEND"}:
            unsupported[kind] += 1
        index += 1

    geometry = _join_primitives(primitives, JOIN_TOLERANCE * unit_scale("in", units))
    geometry.extend(circles)
    geometry.extend(texts)
    # Many graphics/DXF programs export a circle as two ARC entities or as a
    # tessellated closed polyline. Recover only essentially exact fits here;
    # the editor offers a tunable repair command for less certain geometry.
    geometry, recognized_circles = recognize_circular_polylines(
        geometry,
        absolute_tolerance=1e-6,
        relative_tolerance=1e-5,
        minimum_vertices=8,
    )
    if recognized_circles:
        warnings.append(
            f"Recovered {len(recognized_circles)} true circle "
            f"entit{'y' if len(recognized_circles) == 1 else 'ies'} from closed DXF contours."
        )
        counts["RECOVERED_CIRCLE"] = len(recognized_circles)
    if unsupported:
        detail = ", ".join(f"{kind} ({count})" for kind, count in sorted(unsupported.items()))
        warnings.append(f"Skipped unsupported DXF entities: {detail}.")
    for requested, resolved in sorted(substitutions):
        warnings.append(f"DXF text font {requested!r} is unavailable; displaying it with {resolved!r}.")
    if not geometry:
        warnings.append("No supported 2D geometry was found in the DXF ENTITIES section.")
    for entity in geometry:
        # Provenance is deliberately entity-level: a CAM job may later contain
        # native drafting, mounting, bent-tab, and other generated features
        # alongside the imported source. Revision reconciliation must never
        # infer ownership merely from the fact that the project began as DXF.
        entity.metadata.setdefault("source_provenance", "dxf")
        if entity.layer.strip().upper() == 'BEND_GUIDES':
            # Public bend-reference layer survives DXF without native metadata.
            entity.metadata.update(reference_only=True, machinable=False, role='reference_only')

    project = CamProject(
        name=path.stem,
        units=units,
        entities=geometry,
        source={
            "application": "DXF import",
            "file": str(path),
            "format": "dxf",
        },
        metadata={
            "dxf_entity_counts": dict(counts),
            "dxf_ignored_annotations": dict(ignored_annotations),
            "dxf_text_annotations": len(texts),
        },
    )
    return DxfImportResult(project, warnings, counts)


def import_dxfs_as_plates(
    paths: Iterable[Path],
    *,
    target_units: str | None = None,
    additional_sizes: dict[str, tuple[float, float]] | None = None,
) -> MultiDxfImportResult:
    """Import multiple DXFs into one project with one positive-X plate per file.

    Each drawing is rebased to the lower-left of its own plate. Standard and
    configured custom stock sizes are interpreted in inches and the smallest
    single plate that contains the drawing is selected. Oversize drawings get
    an exact drawing-bounds plate so geometry is never silently split.
    """
    source_paths = [Path(path) for path in paths]
    if not source_paths:
        raise ValueError("Choose at least one DXF file.")
    non_dxf = [path.name for path in source_paths if path.suffix.casefold() != ".dxf"]
    if non_dxf:
        raise ValueError(
            "Multi-file plate import accepts DXF files only: " + ", ".join(non_dxf)
        )

    imported = [import_dxf(path) for path in source_paths]
    output_units = target_units or imported[0].project.units
    stock_sizes = {**SHEET_SIZES, **(additional_sizes or {})}
    converted_projects = [
        result.project
        if result.project.units == output_units
        else convert_project_units(result.project, output_units)
        for result in imported
    ]

    combined_entities: list[Entity] = []
    plates: list[Plate] = []
    plate_sources: list[dict] = []
    warnings: list[str] = []
    counts: Counter = Counter()
    ignored_annotations: Counter = Counter()
    text_annotations = 0
    next_x = 0.0
    scale = unit_scale("in", output_units)

    for plate_number, (path, result, project) in enumerate(
        zip(source_paths, imported, converted_projects), start=1,
    ):
        bounds = drawing_bounds(project.entities)
        if bounds is None:
            # A notes-only DXF still deserves a plate and must remain editable,
            # but annotations never enlarge stock when machining geometry exists.
            bounds = drawing_bounds(project.entities, include_annotations=True)
        if bounds is None:
            raise ValueError(f"{path.name} contains no supported 2D geometry.")
        min_x, min_y, max_x, max_y = bounds
        drawing_width = max(max_x - min_x, 0.001 * scale)
        drawing_height = max(max_y - min_y, 0.001 * scale)
        candidates = []
        for label, (width_in, height_in) in stock_sizes.items():
            width = float(width_in) * scale
            height = float(height_in) * scale
            if drawing_width <= width + 0.001 * scale and drawing_height <= height + 0.001 * scale:
                candidates.append((width * height, width - drawing_width, height - drawing_height, label, width, height))
        if candidates:
            _area, _x_waste, _y_waste, stock_label, plate_width, plate_height = min(candidates)
        else:
            stock_label = "Drawing bounds"
            plate_width, plate_height = drawing_width, drawing_height

        plate = Plate(plate_number, next_x, 0.0, plate_width, plate_height, path.stem)
        plates.append(plate)
        delta_x = plate.x - min_x
        delta_y = plate.y - min_y
        for entity in project.entities:
            moved = transform_entity(entity, delta_x=delta_x, delta_y=delta_y)
            moved.metadata.update({
                "source_dxf": str(path),
                "source_plate_number": plate_number,
            })
            combined_entities.append(moved)

        counts.update(result.entity_counts)
        ignored_annotations.update(result.project.metadata.get("dxf_ignored_annotations", {}))
        text_annotations += int(result.project.metadata.get("dxf_text_annotations", 0))
        warnings.extend(f"{path.name}: {warning}" for warning in result.warnings)
        if result.project.units != output_units:
            warnings.append(
                f"{path.name}: Converted drawing from {result.project.units} to {output_units} "
                "to match the combined project."
            )
        plate_sources.append({
            "plate_number": plate_number,
            "plate_name": path.stem,
            "file": str(path),
            "source_units": result.project.units,
            "stock_label": stock_label,
            "source_bounds_in_project_units": [min_x, min_y, max_x, max_y],
            "translation": [delta_x, delta_y],
        })
        next_x += plate_width

    validate_plate_definitions(plates)
    project_name = source_paths[0].stem if len(source_paths) == 1 else f"{source_paths[0].stem} + {len(source_paths) - 1} more"
    project = CamProject(
        name=project_name,
        units=output_units,
        entities=combined_entities,
        source={
            "application": "DXF multi-import",
            "files": [str(path) for path in source_paths],
            "format": "dxf",
        },
        metadata={
            "dxf_entity_counts": dict(counts),
            "dxf_ignored_annotations": dict(ignored_annotations),
            "dxf_text_annotations": text_annotations,
            "plates": [plate.to_dict() for plate in plates],
            "active_plate_number": 1,
            "multi_dxf_import": plate_sources,
        },
    )
    return MultiDxfImportResult(
        project=project,
        warnings=warnings,
        entity_counts=counts,
        plates=tuple(plates),
        plate_sources=tuple(plate_sources),
    )


def _read_pairs(path: Path) -> list[tuple[int, str]]:
    data = path.read_bytes()
    if data.startswith(b"AutoCAD Binary DXF"):
        raise ValueError("Binary DXF is not supported. Export an ASCII DXF from the cleaner.")
    lines = data.decode("utf-8", errors="ignore").splitlines()
    pairs = []
    for index in range(0, len(lines) - 1, 2):
        try:
            code = int(lines[index].strip())
        except ValueError:
            continue
        pairs.append((code, lines[index + 1].strip()))
    return pairs


def _read_units(pairs: list[tuple[int, str]]) -> tuple[str, str | None]:
    unit_codes = {1: "in", 4: "mm"}
    for index, (code, value) in enumerate(pairs):
        if code == 9 and value == "$INSUNITS":
            for following_code, following_value in pairs[index + 1 : index + 6]:
                if following_code in {70, 280}:
                    try:
                        unit_code = int(following_value)
                    except ValueError:
                        break
                    if unit_code in unit_codes:
                        return unit_codes[unit_code], None
                    if unit_code == 0:
                        return "in", "DXF units are unspecified; imported as inches."
                    return "in", f"DXF unit code {unit_code} is not supported; imported as inches."
    return "in", "DXF does not declare units; imported as inches."


def _parse_entities(pairs: list[tuple[int, str]]) -> list[dict]:
    entities = []
    current = None
    in_entities = False
    for code, value in pairs:
        if code == 2 and value == "ENTITIES":
            in_entities = True
            continue
        if not in_entities:
            continue
        if code == 0 and value == "ENDSEC":
            if current:
                entities.append(current)
            break
        if code == 0:
            if current:
                entities.append(current)
            current = {"type": value, "groups": []}
        elif current is not None:
            current["groups"].append((code, value))
    return entities


def _read_text_styles(pairs: list[tuple[int, str]]) -> dict[str, str]:
    """Return STYLE name to requested font family/file stem."""
    styles: dict[str, str] = {"STANDARD": "Arial"}
    index = 0
    while index < len(pairs):
        code, value = pairs[index]
        if code == 0 and value == "STYLE":
            groups = []
            index += 1
            while index < len(pairs) and pairs[index][0] != 0:
                groups.append(pairs[index])
                index += 1
            name = next((item for group, item in groups if group == 2), "STANDARD") or "STANDARD"
            font_file = next((item for group, item in groups if group == 3), "")
            if font_file:
                styles[name] = Path(font_file).stem
            elif name != "STANDARD":
                styles[name] = name
            continue
        index += 1
    return styles


def _text_entity(entity: dict, styles: dict[str, str]) -> TextEntity:
    kind = entity["type"]
    style_name = _value(entity, 7, "STANDARD") or "STANDARD"
    requested_font = styles.get(style_name, "Arial" if style_name == "STANDARD" else style_name)
    resolution = resolve_font(requested_font)
    horizontal = _int(entity, 72)
    vertical = _int(entity, 73)
    use_alignment_point = kind == "TEXT" and (horizontal or vertical) and _has_group(entity, 11)
    insertion = Point(
        _float(entity, 11) if use_alignment_point else _float(entity, 10),
        _float(entity, 21) if use_alignment_point else _float(entity, 20),
    )
    if kind == "MTEXT":
        raw_text = "".join(value for code, value in entity["groups"] if code in {1, 3})
        text = _plain_mtext(raw_text)
        attachment = _int(entity, 71, 7)
        alignment = {0: "left", 1: "center", 2: "right"}.get((max(1, attachment) - 1) % 3, "left")
    else:
        text = _value(entity, 1)
        alignment = {0: "left", 1: "center", 2: "right"}.get(horizontal, "left")
    rotation = _float(entity, 50)
    if not _has_group(entity, 50) and _has_group(entity, 11) and kind == "MTEXT":
        rotation = math.degrees(math.atan2(_float(entity, 21), _float(entity, 11)))
    return TextEntity(
        layer=_layer(entity), insertion=insertion, text=text,
        height=max(_float(entity, 40, 0.25), 1e-9), rotation=rotation,
        alignment=alignment, style_name=style_name,
        font_family=resolution.resolved_family,
        width_factor=max(_float(entity, 41, 1.0), 1e-9) if kind == "TEXT" else 1.0,
        multiline=kind == "MTEXT" or "\n" in text,
        metadata={
            "dxf_type": kind, "dxf_handle": _value(entity, 5),
            "requested_font": requested_font,
            "resolved_font": resolution.resolved_family,
            "font_substituted": resolution.substituted,
            "annotation": True,
        },
    )


def _plain_mtext(value: str) -> str:
    text = value.replace(r"\P", "\n").replace("~", " ")
    text = re.sub(r"\\[A-Za-z][^;]*;", "", text)
    text = text.replace("{", "").replace("}", "")
    return text.replace(r"\\", "\\")


def _value(entity: dict, code: int, default: str = "") -> str:
    return next((value for group_code, value in entity["groups"] if group_code == code), default)


def _has_group(entity: dict, code: int) -> bool:
    return any(group_code == code for group_code, _value in entity["groups"])


def _float(entity: dict, code: int, default: float = 0.0) -> float:
    value = _value(entity, code)
    try:
        return float(value) if value else default
    except ValueError:
        return default


def _int(entity: dict, code: int, default: int = 0) -> int:
    value = _value(entity, code)
    try:
        return int(value) if value else default
    except ValueError:
        return default


def _layer(entity: dict) -> str:
    return _value(entity, 8, "0") or "0"


def _arc_points(center: Point, radius: float, start_degrees: float, end_degrees: float) -> list[Point]:
    if radius <= 0:
        return []
    sweep = math.radians((end_degrees - start_degrees) % 360)
    if sweep <= 1e-12:
        sweep = 2 * math.pi
    ratio = min(1.0, ARC_CHORD_ERROR / radius)
    max_angle = 2 * math.acos(max(-1.0, 1.0 - ratio)) if ratio < 1 else math.pi / 2
    max_angle = min(max_angle, math.radians(10))
    count = max(2, math.ceil(sweep / max(max_angle, math.radians(0.25))))
    start = math.radians(start_degrees)
    return [
        Point(center.x + radius * math.cos(start + sweep * index / count), center.y + radius * math.sin(start + sweep * index / count))
        for index in range(count + 1)
    ]


def _lwpolyline_vertices(entity: dict) -> tuple[list[Point], list[float]]:
    vertices = []
    current = None
    for code, value in entity["groups"]:
        if code == 10:
            if current is not None:
                vertices.append(current)
            current = {"x": float(value), "y": 0.0, "bulge": 0.0}
        elif current is not None and code == 20:
            current["y"] = float(value)
        elif current is not None and code == 42:
            current["bulge"] = float(value)
    if current is not None:
        vertices.append(current)
    return [Point(vertex["x"], vertex["y"]) for vertex in vertices], [vertex["bulge"] for vertex in vertices]


def _expand_bulges(points: list[Point], bulges: list[float], closed: bool) -> list[Point]:
    if len(points) < 2:
        return points
    result = [points[0]]
    segment_count = len(points) if closed else len(points) - 1
    for index in range(segment_count):
        start = points[index]
        end = points[(index + 1) % len(points)]
        bulge = bulges[index] if index < len(bulges) else 0.0
        if abs(bulge) <= 1e-12:
            result.append(end)
            continue
        theta = 4 * math.atan(bulge)
        chord = start.distance_to(end)
        if chord <= 1e-12:
            continue
        midpoint = Point((start.x + end.x) / 2, (start.y + end.y) / 2)
        dx, dy = (end.x - start.x) / chord, (end.y - start.y) / chord
        center_distance = chord * (1 - bulge * bulge) / (4 * bulge)
        center = Point(midpoint.x - dy * center_distance, midpoint.y + dx * center_distance)
        radius = center.distance_to(start)
        start_angle = math.atan2(start.y - center.y, start.x - center.x)
        ratio = min(1.0, ARC_CHORD_ERROR / radius)
        max_angle = min(2 * math.acos(max(-1.0, 1.0 - ratio)), math.radians(10)) if ratio < 1 else math.pi / 2
        count = max(2, math.ceil(abs(theta) / max(max_angle, math.radians(0.25))))
        result.extend(
            Point(center.x + radius * math.cos(start_angle + theta * step / count), center.y + radius * math.sin(start_angle + theta * step / count))
            for step in range(1, count + 1)
        )
    if closed and result[-1].distance_to(result[0]) <= JOIN_TOLERANCE:
        result.pop()
    return result


def _join_primitives(primitives: list[_Primitive], tolerance=JOIN_TOLERANCE) -> list[Line | Polyline]:
    """Join mutually unambiguous endpoints, never an arbitrary grid neighbor.

    Build connectivity before consuming primitives so traversal order cannot
    turn an ambiguous junction into a seemingly unique connection. Exact
    continuations outrank all tolerant ones. Spatial cells are only a broad
    phase; distances and reciprocal choices decide every join.
    """
    output = []
    by_layer = defaultdict(list)
    for primitive in primitives:
        by_layer[primitive.layer].append(primitive)
    for layer, layer_primitives in by_layer.items():
        closed = [primitive for primitive in layer_primitives if primitive.closed]
        open_primitives = [primitive for primitive in layer_primitives if not primitive.closed]
        for primitive in closed:
            output.append(
                Polyline(
                    layer=layer,
                    points=primitive.points,
                    closed=True,
                    metadata={"dxf_type": primitive.source_type, "dxf_handle": primitive.handle},
                )
            )
        exact = tolerance * 1e-6
        unused = set(range(len(open_primitives)))
        endpoint_map = defaultdict(list)
        endpoints = {}
        for index, primitive in enumerate(open_primitives):
            for at_start, point in ((True, primitive.points[0]), (False, primitive.points[-1])):
                key = (index, at_start)
                endpoints[key] = point
                endpoint_map[_point_key(point, tolerance)].append(key)
        preferred, reviews = {}, {}
        for key, point in endpoints.items():
            cx, cy = _point_key(point, tolerance)
            candidates = []
            for x in range(cx-1, cx+2):
                for y in range(cy-1, cy+2):
                    for other in endpoint_map.get((x,y), ()):
                        if other[0] == key[0]:
                            continue
                        distance = point.distance_to(endpoints[other])
                        if distance <= tolerance:
                            candidates.append((distance, other))
            precise = [other for distance, other in candidates if distance <= exact]
            choices = precise or [other for _, other in candidates]
            if not precise and len(candidates) > 1:
                ordered = sorted(candidates)
                # Rounded ARC endpoints need not be bit-identical to their
                # adjoining LINE. Accept a uniquely dominant nearest match,
                # not two similarly plausible ends. Reciprocity is still
                # required below; this cannot steal an exact continuation.
                if ordered[1][0] > 4*ordered[0][0] + exact:
                    choices = [ordered[0][1]]
            if len(choices) == 1:
                preferred[key] = choices[0]
            elif choices:
                reviews[key] = "Ambiguous DXF endpoint: multiple plausible connections; not joined."
        links = {key: other for key,other in preferred.items() if preferred.get(other) == key}
        for key in preferred.keys() - links.keys():
            reviews[key] = "Non-reciprocal DXF endpoint match; not joined."
        while unused:
            first_index = min(unused)
            unused.remove(first_index)
            first = open_primitives[first_index]
            chain = list(first.points)
            consumed = {first_index}
            head, tail = (first_index, True), (first_index, False)
            source_types = [first.source_type]
            handles = [first.handle]
            while True:
                match = links.get(tail)
                if match is None or match[0] not in unused:
                    break
                index, at_start = match
                unused.remove(index)
                consumed.add(index)
                primitive = open_primitives[index]
                addition = primitive.points if at_start else list(reversed(primitive.points))
                # Preserve both ends of a real gap instead of dropping the
                # first segment's endpoint and silently changing its shape.
                chain.extend(addition[1:] if chain[-1].distance_to(addition[0]) <= exact else addition)
                tail = (index, not at_start)
                source_types.append(primitive.source_type)
                handles.append(primitive.handle)
            while True:
                match = links.get(head)
                if match is None or match[0] not in unused:
                    break
                index, at_start = match
                unused.remove(index)
                consumed.add(index)
                primitive = open_primitives[index]
                addition = list(reversed(primitive.points)) if at_start else primitive.points
                chain = (addition[:-1] if addition[-1].distance_to(chain[0]) <= exact else addition) + chain
                head = (index, not at_start)
                source_types.insert(0, primitive.source_type)
                handles.insert(0, primitive.handle)
            gap = chain[0].distance_to(chain[-1])
            span = max(max(p.x for p in chain)-min(p.x for p in chain),
                       max(p.y for p in chain)-min(p.y for p in chain))
            exact_closed = len(chain) >= 4 and gap <= exact
            tolerant_cycle = links.get(tail) == head and gap <= tolerance
            is_closed = exact_closed or (tolerant_cycle and span > 4*tolerance)
            if exact_closed:
                chain.pop()
            metadata = {"dxf_types": source_types, "dxf_handles": handles}
            notes = [dict(point={"x": endpoints[key].x, "y": endpoints[key].y}, reason=reason)
                     for key,reason in reviews.items() if key[0] in consumed]
            if not is_closed and gap <= tolerance and len(chain) > 2:
                notes.append(dict(point={"x": chain[0].x, "y": chain[0].y},
                                  reason="Nearby ends retained open: tiny or unproven DXF closure; review before repair."))
            if notes:
                metadata["dxf_join_review"] = notes
            if len(chain) == 2 and len(source_types) == 1 and source_types[0] == "LINE":
                output.append(Line(layer=layer, start=chain[0], end=chain[1], metadata=metadata))
            else:
                output.append(Polyline(layer=layer, points=chain, closed=is_closed, metadata=metadata))
    return output


def _point_key(point: Point, tolerance=JOIN_TOLERANCE) -> tuple[int, int]:
    return math.floor(point.x / tolerance), math.floor(point.y / tolerance)
