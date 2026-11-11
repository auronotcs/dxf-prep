"""Black-box benchmark tools for characterizing external nesting software.

The benchmark exchanges ordinary DXF files and deliberately remains separate
from production CAM state.  Unique DXF layers identify rigid test parts across
an external nest/export round trip.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from typing import Iterable

from .dxf import export_dxf, import_dxf
from .layout import drawing_bounds, entity_bounds
from .model import CamProject, Circle, Entity, Line, Point, Polyline, Rectangle
from .transform import transform_entity


BENCHMARK_SCHEMA = "cam_workbench.flexi_nest_fingerprint"
BENCHMARK_VERSION = 1
_FIT_SAMPLE_SPACING = 0.3
_ANGLE_SEARCH_STEP = 1.0
_POSITION_TOLERANCE = 0.02
_ROTATION_TOLERANCE = 1.1


@dataclass(frozen=True)
class BenchmarkPack:
    root: Path
    manifest_path: Path
    worksheet_path: Path
    source_paths: tuple[Path, ...]


def generate_flexi_benchmark_pack(root: Path) -> BenchmarkPack:
    """Create the deterministic DXF corpus and operator worksheet."""
    root = Path(root)
    source_directory = root / "source"
    result_directory = root / "flexi_results"
    report_directory = root / "reports"
    source_directory.mkdir(parents=True, exist_ok=True)
    result_directory.mkdir(parents=True, exist_ok=True)
    report_directory.mkdir(parents=True, exist_ok=True)

    manifest_cases: list[dict] = []
    source_paths: list[Path] = []
    for case in _benchmark_cases():
        source_path = root / case["source_file"]
        project = CamProject(
            name=case["title"],
            units="in",
            entities=case.pop("entities"),
            source={"application": "CAM Workbench Nest Fingerprint Lab"},
        )
        export_dxf(project, source_path, dialect="enroute_r12")
        imported = import_dxf(source_path).project
        parts = []
        for order, (layer, entities) in enumerate(_group_by_layer(imported.entities).items(), start=1):
            bounds = drawing_bounds(entities)
            if bounds is None:
                continue
            parts.append({
                "id": layer,
                "layer": layer,
                "source_order": order,
                "bounds": list(bounds),
                "bounding_area": _bounds_area(bounds),
                "entity_count": len(entities),
            })
        case["parts"] = parts
        manifest_cases.append(case)
        source_paths.append(source_path)

    manifest = {
        "schema": BENCHMARK_SCHEMA,
        "version": BENCHMARK_VERSION,
        "units": "in",
        "instructions": "See OPERATOR_WORKSHEET.md. Do not hand-adjust nested results.",
        "cases": manifest_cases,
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    worksheet_path = root / "OPERATOR_WORKSHEET.md"
    worksheet_path.write_text(_operator_worksheet(manifest), encoding="utf-8")
    return BenchmarkPack(root, manifest_path, worksheet_path, tuple(source_paths))


def analyze_flexi_benchmark_pack(root: Path) -> dict:
    """Analyze every completed external result and write JSON/Markdown reports."""
    root = Path(root)
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        raise ValueError("Benchmark manifest.json was not found. Generate the pack first.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != BENCHMARK_SCHEMA:
        raise ValueError("This directory is not a CAM Workbench Flexi Nest Fingerprint pack.")
    if int(manifest.get("version", 0)) != BENCHMARK_VERSION:
        raise ValueError("The benchmark pack version is not supported by this analyzer.")

    reports: list[dict] = []
    missing_files: list[str] = []
    for case in manifest.get("cases", []):
        source_path = root / case["source_file"]
        if not source_path.exists():
            raise ValueError(f"Benchmark source file is missing: {case['source_file']}")
        source = import_dxf(source_path).project
        for run_index, result_name in enumerate(case.get("result_files", []), start=1):
            result_path = root / "flexi_results" / result_name
            if not result_path.exists():
                missing_files.append(str(result_path.relative_to(root)))
                continue
            result = import_dxf(result_path).project
            reports.append(_analyze_case_run(case, run_index, source.entities, result.entities, result_path))

    comparisons = _cross_case_comparisons(reports)
    report = {
        "schema": BENCHMARK_SCHEMA + ".report",
        "version": BENCHMARK_VERSION,
        "completed_runs": len(reports),
        "expected_runs": sum(len(case.get("result_files", [])) for case in manifest.get("cases", [])),
        "missing_result_files": missing_files,
        "runs": reports,
        "comparisons": comparisons,
    }
    report_directory = root / "reports"
    report_directory.mkdir(parents=True, exist_ok=True)
    (report_directory / "flexi_nest_report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8",
    )
    (report_directory / "flexi_nest_report.md").write_text(
        _markdown_report(report), encoding="utf-8",
    )
    return report


def _benchmark_cases() -> list[dict]:
    order_parts = _layout_parts([
        _part("P01_BLOCK_L", [_rectangle_contour(6.0, 4.0)]),
        _part("P02_T", [_letter_t()]),
        _part("P03_C", [_letter_c()]),
        _part("P04_L", [_letter_l()]),
        _part("P05_BLOCK_S", [_rectangle_contour(2.3, 1.7)]),
    ])
    order_forward = _flatten_parts(order_parts)
    order_reverse = _flatten_parts(list(reversed(order_parts)))

    complementary_parts = _layout_parts([
        _part("P01_E_A", [_letter_e()]),
        _part("P02_E_B", [_letter_e()]),
        _part("P03_F", [_letter_f()]),
        _part("P04_T_A", [_letter_t()]),
        _part("P05_T_B", [_letter_t()]),
        _part("P06_K", [_letter_k()]),
        _part("P07_C", [_letter_c()]),
        _part("P08_A", [_letter_a_outer(), _letter_a_hole()]),
        _part("P09_O", _ring_contours(2.5, 1.25)),
        _part("P10_L", [_letter_l()]),
        _part("P11_M", [_letter_m()]),
        _part("P12_I", [_rectangle_contour(0.8, 5.0)]),
    ], wrap_width=55.0)

    hole_parts = _layout_parts([
        _part("P01_O_HOST", _ring_contours(4.0, 2.25)),
        _part("P02_INSERT", [_asymmetric_insert()]),
        _part("P03_CONTROL_T", [_letter_t(scale=0.8)]),
        _part("P04_CONTROL_BOX", [_rectangle_contour(3.0, 2.0)]),
    ])

    rotation_parts = _layout_parts([
        _part("P01_WEDGE", [_wedge()]),
        _part("P02_BAR", [_slanted_bar()]),
        _part("P03_L", [_letter_l(scale=0.8)]),
        _part("P04_T", [_letter_t(scale=0.75)]),
        _part("P05_NOTCH", [_notched_block()]),
    ])

    global_base_parts = _layout_parts([
        _part("P01_C", [_letter_c(scale=1.1)]),
        _part("P02_K", [_letter_k(scale=1.1)]),
        _part("P03_A", [_letter_a_outer(scale=1.1), _letter_a_hole(scale=1.1)]),
        _part("P04_T", [_letter_t(scale=1.1)]),
        _part("P05_L", [_letter_l(scale=1.1)]),
    ])
    extra_part = _layout_parts([_part("P06_TINY", [_asymmetric_insert(scale=0.55)])], start_x=45.0)

    def case(
        case_id: str,
        title: str,
        hypothesis: str,
        entities: list[Entity],
        *,
        panel: tuple[float, float],
        rotation: float,
        use_holes: bool = False,
        result_files: list[str] | None = None,
    ) -> dict:
        source_name = f"{case_id}_{_file_slug(title)}.dxf"
        default_result = f"{Path(source_name).stem}_flexi.dxf"
        return {
            "id": case_id,
            "title": title,
            "hypothesis": hypothesis,
            "source_file": f"source/{source_name}",
            "result_files": result_files or [default_result],
            "panel": {"width": panel[0], "height": panel[1]},
            "settings": {
                "spacing": 0.25,
                "rotation_increment_degrees": rotation,
                "use_holes": use_holes,
                "break_text": False,
            },
            "entities": entities,
        }

    return [
        case("01", "order_forward", "Tests geometric sorting versus forward input order.", order_forward, panel=(24, 16), rotation=90),
        case("02", "order_reverse", "Repeats case 01 with reverse entity order.", order_reverse, panel=(24, 16), rotation=90),
        case("03", "complementary_letters", "Tests sign-letter silhouette pairing.", _flatten_parts(complementary_parts), panel=(36, 24), rotation=90),
        case("04", "use_holes", "Tests insertion into a true compound-part void.", _flatten_parts(hole_parts), panel=(20, 16), rotation=90, use_holes=True),
        case("05", "rotation_90", "Measures the 90 degree rotation grid.", _flatten_parts(rotation_parts), panel=(20, 13), rotation=90),
        case("06", "rotation_45", "Measures the 45 degree rotation grid.", _flatten_parts(rotation_parts), panel=(20, 13), rotation=45),
        case("07", "rotation_22_5", "Measures the 22.5 degree rotation grid.", _flatten_parts(rotation_parts), panel=(20, 13), rotation=22.5),
        case("08", "global_base", "Provides the common-part baseline for the extra-part test.", _flatten_parts(global_base_parts), panel=(22, 16), rotation=90),
        case("09", "global_with_small_part", "Shows whether one extra part rearranges the common layout.", _flatten_parts(global_base_parts + extra_part), panel=(22, 16), rotation=90),
        case(
            "10", "determinism", "Runs identical input twice to test deterministic tie-breaking.",
            _flatten_parts(rotation_parts), panel=(20, 13), rotation=45,
            result_files=["10_determinism_flexi_run_1.dxf", "10_determinism_flexi_run_2.dxf"],
        ),
    ]


def _analyze_case_run(
    case: dict,
    run_index: int,
    source_entities: list[Entity],
    result_entities: list[Entity],
    result_path: Path,
) -> dict:
    source_groups = _group_by_layer(source_entities)
    result_groups = _group_by_layer(result_entities)
    result_keys = {_layer_key(layer): layer for layer in result_groups}
    transforms = []
    missing = []
    matched_result_layers: set[str] = set()
    rotation_increment = float(case["settings"]["rotation_increment_degrees"])

    for source_layer, source_group in source_groups.items():
        result_layer = result_keys.get(_layer_key(source_layer))
        if result_layer is None:
            missing.append(source_layer)
            continue
        matched_result_layers.add(result_layer)
        transforms.append(_fit_part_transform(
            source_layer,
            source_group,
            result_groups[result_layer],
            rotation_increment,
        ))

    unexpected = sorted(layer for layer in result_groups if layer not in matched_result_layers)
    bounds = drawing_bounds(result_entities)
    panel = case["panel"]
    if bounds is None:
        span_x = span_y = packed_area = occupied_span_ratio = 0.0
    else:
        span_x = bounds[2] - bounds[0]
        span_y = bounds[3] - bounds[1]
        packed_area = span_x * span_y
        panel_area = float(panel["width"]) * float(panel["height"])
        occupied_span_ratio = packed_area / panel_area if panel_area > 0 else 0.0
        for transform in transforms:
            transform["normalized_center"] = [
                transform["center"][0] - bounds[0],
                transform["center"][1] - bounds[1],
            ]
            transform["corner_distance"] = (
                transform["bounds"][0] - bounds[0]
                + transform["bounds"][1] - bounds[1]
            )

    transforms.sort(key=lambda item: (item.get("corner_distance", math.inf), item["part_layer"]))
    hole_insertion = None
    if case["id"] == "04":
        host_layer = result_keys.get(_layer_key("P01_O_HOST"))
        insert_layer = result_keys.get(_layer_key("P02_INSERT"))
        if host_layer and insert_layer:
            hole_insertion = _group_inside_smallest_closed_contour(
                result_groups[insert_layer], result_groups[host_layer],
            )

    return {
        "case_id": case["id"],
        "title": case["title"],
        "run": run_index,
        "result_file": result_path.name,
        "matched_parts": len(transforms),
        "missing_part_layers": sorted(missing),
        "unexpected_result_layers": unexpected,
        "packed_bounds": list(bounds) if bounds else None,
        "packed_span_x": span_x,
        "packed_span_y": span_y,
        "packed_bounding_area": packed_area,
        "occupied_span_ratio": occupied_span_ratio,
        "hole_insertion_detected": hole_insertion,
        "placements": transforms,
    }


def _fit_part_transform(
    part_layer: str,
    source_entities: list[Entity],
    result_entities: list[Entity],
    requested_increment: float,
) -> dict:
    source_points = _sample_entities(source_entities)
    result_points = _sample_entities(result_entities)
    if not source_points or not result_points:
        raise ValueError(f"Part layer {part_layer} has no measurable contour geometry.")
    source_center = _mean_point(source_points)
    result_center = _mean_point(result_points)
    source_normalized = [Point(point.x - source_center.x, point.y - source_center.y) for point in source_points]
    result_normalized = [Point(point.x - result_center.x, point.y - result_center.y) for point in result_points]
    diagonal = max(_point_cloud_diagonal(result_normalized), 1e-9)

    def score(angle: float) -> tuple[float, float]:
        normalized_angle = angle % 360.0
        rotated = [_rotate_point(point, normalized_angle) for point in source_normalized]
        error = _symmetric_cloud_error(rotated, result_normalized) / diagonal
        return error, normalized_angle

    # A coarse-to-fine search keeps the analyzer responsive even when a part
    # has several long, densely sampled contours. The final one-degree grid is
    # already tighter than the reporting tolerance and can expose an operator
    # accidentally using the wrong Flexi rotation increment.
    coarse_scores = [score(float(angle)) for angle in range(0, 360, 5)]
    coarse_scores.sort()
    fine_angles: set[float] = set()
    for _error, coarse_angle in coarse_scores[:8]:
        offset = -5.0
        while offset <= 5.0 + 1e-9:
            fine_angles.add(round((coarse_angle + offset) % 360.0, 9))
            offset += _ANGLE_SEARCH_STEP
    scores = [score(angle) for angle in sorted(fine_angles)]
    scores.sort()
    best_error, best_angle = scores[0]
    distinct = [item for item in scores[1:] if _angular_distance(item[1], best_angle) > _ANGLE_SEARCH_STEP + 1e-9]
    second_error = distinct[0][0] if distinct else best_error
    ambiguous = second_error <= max(best_error * 1.08, best_error + 1e-5)
    bounds = drawing_bounds(result_entities)
    grid_error = _distance_to_rotation_grid(best_angle, requested_increment)
    return {
        "part_layer": part_layer,
        "rotation_degrees": best_angle,
        "rotation_ambiguous": ambiguous,
        "rotation_grid_error_degrees": grid_error,
        "rotation_matches_requested_grid": grid_error <= _ROTATION_TOLERANCE,
        "translation": [result_center.x - source_center.x, result_center.y - source_center.y],
        "fit_error_fraction": best_error,
        "center": [result_center.x, result_center.y],
        "bounds": list(bounds) if bounds else None,
    }


def _cross_case_comparisons(reports: list[dict]) -> dict:
    by_case: dict[str, list[dict]] = defaultdict(list)
    for report in reports:
        by_case[report["case_id"]].append(report)
    comparisons: dict[str, object] = {}

    order = _compare_first_runs(by_case, "01", "02")
    if order:
        comparisons["input_order"] = {
            **order,
            "interpretation": (
                "The reversed input produced a materially different arrangement; Flexi is input-order sensitive under these settings."
                if order["materially_different"] else
                "Forward and reverse inputs produced equivalent arrangements; geometric sorting or stable tie-breaking is likely."
            ),
        }

    global_change = _compare_first_runs(by_case, "08", "09", ignore_layers={"P06_TINY"})
    if global_change:
        comparisons["extra_part"] = {
            **global_change,
            "interpretation": (
                "Adding one small part moved common parts, supporting a global rebuild or improvement pass."
                if global_change["materially_different"] else
                "Common parts stayed fixed when the small part was added, supporting incremental insertion or stable first-fit behavior."
            ),
        }

    if len(by_case.get("10", [])) >= 2:
        determinism = _compare_layouts(by_case["10"][0], by_case["10"][1])
        comparisons["determinism"] = {
            **determinism,
            "interpretation": (
                "Repeated runs differed; a random seed, time limit, concurrency, or unstable tie-break may be involved."
                if determinism["materially_different"] else
                "Repeated runs matched within tolerance, supporting deterministic behavior for this case."
            ),
        }

    rotation_cases = []
    for case_id in ("05", "06", "07"):
        if by_case.get(case_id):
            run = by_case[case_id][0]
            rotation_cases.append({
                "case_id": case_id,
                "packed_bounding_area": run["packed_bounding_area"],
                "occupied_span_ratio": run["occupied_span_ratio"],
                "off_grid_parts": [
                    item["part_layer"] for item in run["placements"]
                    if not item["rotation_matches_requested_grid"] and not item["rotation_ambiguous"]
                ],
            })
    if rotation_cases:
        comparisons["rotation_resolution"] = rotation_cases

    if by_case.get("04"):
        comparisons["use_holes"] = {
            "insert_inside_host_hole": by_case["04"][0]["hole_insertion_detected"],
            "interpretation": (
                "The asymmetric insert was placed fully inside the O void."
                if by_case["04"][0]["hole_insertion_detected"] else
                "The controlled insert was not detected inside the O void."
            ),
        }

    corner_evidence = []
    for case_id in ("01", "02"):
        if not by_case.get(case_id):
            continue
        run = by_case[case_id][0]
        if run["placements"]:
            corner_evidence.append({
                "case_id": case_id,
                "nearest_corner_part": run["placements"][0]["part_layer"],
                "placement_order": [item["part_layer"] for item in run["placements"]],
            })
    if corner_evidence:
        comparisons["corner_bias"] = corner_evidence
    return comparisons


def _compare_first_runs(
    by_case: dict[str, list[dict]],
    left: str,
    right: str,
    *,
    ignore_layers: set[str] | None = None,
) -> dict | None:
    if not by_case.get(left) or not by_case.get(right):
        return None
    return _compare_layouts(by_case[left][0], by_case[right][0], ignore_layers=ignore_layers)


def _compare_layouts(left: dict, right: dict, *, ignore_layers: set[str] | None = None) -> dict:
    ignored = {_layer_key(layer) for layer in (ignore_layers or set())}
    left_parts = {_layer_key(item["part_layer"]): item for item in left["placements"]}
    right_parts = {_layer_key(item["part_layer"]): item for item in right["placements"]}
    common = sorted((set(left_parts) & set(right_parts)) - ignored)
    movements = []
    for key in common:
        first = left_parts[key]
        second = right_parts[key]
        first_center = first.get("normalized_center", first["center"])
        second_center = second.get("normalized_center", second["center"])
        position_delta = math.hypot(
            second_center[0] - first_center[0], second_center[1] - first_center[1],
        )
        rotation_delta = _angular_distance(first["rotation_degrees"], second["rotation_degrees"])
        movements.append({
            "part_layer": first["part_layer"],
            "position_delta": position_delta,
            "rotation_delta_degrees": rotation_delta,
        })
    max_position = max((item["position_delta"] for item in movements), default=0.0)
    max_rotation = max((item["rotation_delta_degrees"] for item in movements), default=0.0)
    return {
        "common_parts": len(common),
        "maximum_position_delta": max_position,
        "maximum_rotation_delta_degrees": max_rotation,
        "materially_different": max_position > _POSITION_TOLERANCE or max_rotation > _ROTATION_TOLERANCE,
        "part_differences": movements,
    }


def _group_by_layer(entities: Iterable[Entity]) -> dict[str, list[Entity]]:
    grouped: dict[str, list[Entity]] = {}
    for entity in entities:
        grouped.setdefault(entity.layer or "0", []).append(entity)
    return grouped


def _sample_entities(entities: Iterable[Entity]) -> list[Point]:
    points: list[Point] = []
    for entity in entities:
        if isinstance(entity, Circle):
            count = max(24, int(math.ceil(2 * math.pi * entity.radius / _FIT_SAMPLE_SPACING)))
            points.extend(Point(
                entity.center.x + entity.radius * math.cos(2 * math.pi * index / count),
                entity.center.y + entity.radius * math.sin(2 * math.pi * index / count),
            ) for index in range(count))
        elif isinstance(entity, Line):
            points.extend(_sample_segment(entity.start, entity.end))
        elif isinstance(entity, Rectangle):
            low_x, high_x = sorted((entity.corner_a.x, entity.corner_b.x))
            low_y, high_y = sorted((entity.corner_a.y, entity.corner_b.y))
            corners = [Point(low_x, low_y), Point(high_x, low_y), Point(high_x, high_y), Point(low_x, high_y)]
            points.extend(_sample_contour(corners, True))
        elif isinstance(entity, Polyline):
            points.extend(_sample_contour(entity.points, entity.closed))
    return points


def _sample_contour(points: list[Point], closed: bool) -> list[Point]:
    if not points:
        return []
    pairs = list(zip(points, points[1:]))
    if closed and len(points) > 1:
        pairs.append((points[-1], points[0]))
    sampled: list[Point] = []
    for start, end in pairs:
        sampled.extend(_sample_segment(start, end)[:-1])
    sampled.append(points[-1])
    return sampled


def _sample_segment(start: Point, end: Point) -> list[Point]:
    length = start.distance_to(end)
    count = max(1, int(math.ceil(length / _FIT_SAMPLE_SPACING)))
    return [Point(
        start.x + (end.x - start.x) * index / count,
        start.y + (end.y - start.y) * index / count,
    ) for index in range(count + 1)]


def _symmetric_cloud_error(first: list[Point], second: list[Point]) -> float:
    def directed(source: list[Point], target: list[Point]) -> float:
        total = 0.0
        for point in source:
            total += min((point.x - other.x) ** 2 + (point.y - other.y) ** 2 for other in target)
        return total / len(source)
    return math.sqrt((directed(first, second) + directed(second, first)) / 2.0)


def _mean_point(points: list[Point]) -> Point:
    return Point(
        sum(point.x for point in points) / len(points),
        sum(point.y for point in points) / len(points),
    )


def _point_cloud_diagonal(points: list[Point]) -> float:
    return math.hypot(
        max(point.x for point in points) - min(point.x for point in points),
        max(point.y for point in points) - min(point.y for point in points),
    )


def _rotate_point(point: Point, angle_degrees: float) -> Point:
    angle = math.radians(angle_degrees)
    cosine, sine = math.cos(angle), math.sin(angle)
    return Point(point.x * cosine - point.y * sine, point.x * sine + point.y * cosine)


def _distance_to_rotation_grid(angle: float, increment: float) -> float:
    if increment <= 0:
        return 0.0
    quotient = round(angle / increment)
    return _angular_distance(angle, (quotient * increment) % 360.0)


def _angular_distance(first: float, second: float) -> float:
    return abs((first - second + 180.0) % 360.0 - 180.0)


def _group_inside_smallest_closed_contour(candidate: list[Entity], host: list[Entity]) -> bool:
    closed = [entity for entity in host if isinstance(entity, Circle) or isinstance(entity, Rectangle) or (isinstance(entity, Polyline) and entity.closed)]
    if len(closed) < 2:
        return False
    inner = min(closed, key=lambda entity: _bounds_area(entity_bounds(entity)))
    candidate_points = _sample_entities(candidate)
    return bool(candidate_points) and all(_point_inside_entity(point, inner) for point in candidate_points)


def _point_inside_entity(point: Point, entity: Entity) -> bool:
    if isinstance(entity, Circle):
        return point.distance_to(entity.center) <= entity.radius + 1e-6
    if isinstance(entity, Rectangle):
        low_x, high_x = sorted((entity.corner_a.x, entity.corner_b.x))
        low_y, high_y = sorted((entity.corner_a.y, entity.corner_b.y))
        return low_x - 1e-6 <= point.x <= high_x + 1e-6 and low_y - 1e-6 <= point.y <= high_y + 1e-6
    if not isinstance(entity, Polyline) or not entity.closed or len(entity.points) < 3:
        return False
    inside = False
    previous = entity.points[-1]
    for current in entity.points:
        if ((current.y > point.y) != (previous.y > point.y)):
            crossing_x = (previous.x - current.x) * (point.y - current.y) / (previous.y - current.y) + current.x
            if point.x < crossing_x:
                inside = not inside
        previous = current
    return inside


def _operator_worksheet(manifest: dict) -> str:
    lines = [
        "# Flexi Nest Fingerprint — Operator Worksheet",
        "",
        "For each row: import the source DXF into a fresh document, select all geometry, run True Shape Nest with the exact settings, apply it, and export the untouched result to `flexi_results` using the listed name.",
        "",
        "Do not regroup, combine layers, hand-adjust, or reuse a previous nest. Panel dimensions and spacing are inches.",
        "",
        "| Case | Source | Panel | Spacing | Rotation | Use holes | Result file(s) |",
        "|---|---|---:|---:|---:|:---:|---|",
    ]
    for case in manifest["cases"]:
        settings = case["settings"]
        panel = case["panel"]
        results = "<br>".join(f"`{name}`" for name in case["result_files"])
        lines.append(
            f"| {case['id']} — {case['title']} | `{Path(case['source_file']).name}` | "
            f"{panel['width']} × {panel['height']} | {settings['spacing']} | "
            f"{settings['rotation_increment_degrees']}° | {'Yes' if settings['use_holes'] else 'No'} | {results} |"
        )
    lines.extend([
        "",
        "## Notes",
        "",
        "- `Break Text` stays off because every test part is already geometry.",
        "- Case 10 must be run twice from a fresh import each time.",
        "- Preserve the unique `P##_...` layers; they are how the analyzer identifies rigid parts.",
        "- If Flexi reports a failed nest, record the message here and leave that result file absent.",
        "",
    ])
    return "\n".join(lines)


def _markdown_report(report: dict) -> str:
    lines = [
        "# Flexi Nest Fingerprint Report",
        "",
        f"Completed {report['completed_runs']} of {report['expected_runs']} requested runs.",
        "",
    ]
    if report["missing_result_files"]:
        lines.extend(["## Still needed", ""])
        lines.extend(f"- `{name}`" for name in report["missing_result_files"])
        lines.append("")
    if report["runs"]:
        lines.extend([
            "## Completed runs",
            "",
            "| Case | Run | Matched | Missing | Span X × Y | Span ratio |",
            "|---|---:|---:|---:|---:|---:|",
        ])
        for run in report["runs"]:
            lines.append(
                f"| {run['case_id']} — {run['title']} | {run['run']} | {run['matched_parts']} | "
                f"{len(run['missing_part_layers'])} | {run['packed_span_x']:.3f} × {run['packed_span_y']:.3f} | "
                f"{run['occupied_span_ratio']:.3f} |"
            )
        lines.append("")
    if report["comparisons"]:
        lines.extend(["## Evidence", ""])
        for name, comparison in report["comparisons"].items():
            lines.append(f"### {name.replace('_', ' ').title()}")
            lines.append("")
            if isinstance(comparison, dict) and comparison.get("interpretation"):
                lines.append(str(comparison["interpretation"]))
            else:
                lines.append("See the JSON report for the measured values.")
            lines.append("")
    lines.extend([
        "## Caution",
        "",
        "These results characterize observed behavior for the tested settings. They do not prove Flexi's internal source code or scoring function.",
        "",
    ])
    return "\n".join(lines)


def _part(layer: str, contours: list[list[Point]]) -> list[Entity]:
    entities = []
    for index, points in enumerate(contours, start=1):
        entities.append(Polyline(
            id=f"{layer}_C{index}", layer=layer, points=points, closed=True,
            metadata={"benchmark_part": layer},
        ))
    return entities


def _layout_parts(
    parts: list[list[Entity]],
    *,
    start_x: float = 2.0,
    start_y: float = 2.0,
    gap: float = 2.5,
    wrap_width: float = 48.0,
) -> list[list[Entity]]:
    laid_out: list[list[Entity]] = []
    cursor_x, cursor_y, row_height = start_x, start_y, 0.0
    for part in parts:
        bounds = drawing_bounds(part)
        if bounds is None:
            continue
        width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
        if cursor_x > start_x and cursor_x + width > wrap_width:
            cursor_x = start_x
            cursor_y += row_height + gap
            row_height = 0.0
        delta_x, delta_y = cursor_x - bounds[0], cursor_y - bounds[1]
        laid_out.append([
            transform_entity(entity, delta_x=delta_x, delta_y=delta_y)
            for entity in part
        ])
        cursor_x += width + gap
        row_height = max(row_height, height)
    return laid_out


def _flatten_parts(parts: list[list[Entity]]) -> list[Entity]:
    return [entity for part in parts for entity in part]


def _scale_points(points: list[Point], scale: float) -> list[Point]:
    return [Point(point.x * scale, point.y * scale) for point in points]


def _rectangle_contour(width: float, height: float) -> list[Point]:
    return [Point(0, 0), Point(width, 0), Point(width, height), Point(0, height)]


def _circle_contour(radius: float, count: int = 48) -> list[Point]:
    return [Point(
        radius + radius * math.cos(2 * math.pi * index / count),
        radius + radius * math.sin(2 * math.pi * index / count),
    ) for index in range(count)]


def _ring_contours(outer_radius: float, inner_radius: float) -> list[list[Point]]:
    if not 0 < inner_radius < outer_radius:
        raise ValueError("Ring radii require 0 < inner radius < outer radius.")
    inner_offset = outer_radius - inner_radius
    inner = [
        Point(point.x + inner_offset, point.y + inner_offset)
        for point in _circle_contour(inner_radius)
    ]
    return [_circle_contour(outer_radius), inner]


def _letter_e(scale: float = 1.0) -> list[Point]:
    return _scale_points([
        Point(0, 0), Point(4, 0), Point(4, 1), Point(1.2, 1),
        Point(1.2, 2.2), Point(3.4, 2.2), Point(3.4, 3.2),
        Point(1.2, 3.2), Point(1.2, 4.4), Point(4, 4.4),
        Point(4, 5.4), Point(0, 5.4),
    ], scale)


def _letter_f(scale: float = 1.0) -> list[Point]:
    return _scale_points([
        Point(0, 0), Point(1.2, 0), Point(1.2, 2.2), Point(3.4, 2.2),
        Point(3.4, 3.2), Point(1.2, 3.2), Point(1.2, 4.4),
        Point(4, 4.4), Point(4, 5.4), Point(0, 5.4),
    ], scale)


def _letter_t(scale: float = 1.0) -> list[Point]:
    return _scale_points([
        Point(1.5, 0), Point(2.5, 0), Point(2.5, 4), Point(4, 4),
        Point(4, 5), Point(0, 5), Point(0, 4), Point(1.5, 4),
    ], scale)


def _letter_k(scale: float = 1.0) -> list[Point]:
    return _scale_points([
        Point(0, 0), Point(1, 0), Point(1, 2.0), Point(2.9, 0),
        Point(4.1, 0), Point(2.0, 2.65), Point(4.1, 5.4),
        Point(2.9, 5.4), Point(1, 3.25), Point(1, 5.4), Point(0, 5.4),
    ], scale)


def _letter_c(scale: float = 1.0) -> list[Point]:
    return _scale_points([
        Point(4, 0), Point(1.2, 0), Point(0, 1.2), Point(0, 3.8),
        Point(1.2, 5), Point(4, 5), Point(4, 4), Point(1.7, 4),
        Point(1, 3.3), Point(1, 1.7), Point(1.7, 1), Point(4, 1),
    ], scale)


def _letter_a_outer(scale: float = 1.0) -> list[Point]:
    return _scale_points([
        Point(0, 0), Point(1.35, 5.2), Point(2.65, 5.2), Point(4, 0),
        Point(2.9, 0), Point(2.58, 1.3), Point(1.42, 1.3), Point(1.1, 0),
    ], scale)


def _letter_a_hole(scale: float = 1.0) -> list[Point]:
    return _scale_points([Point(1.68, 2.15), Point(2, 3.55), Point(2.32, 2.15)], scale)


def _letter_l(scale: float = 1.0) -> list[Point]:
    return _scale_points([
        Point(0, 0), Point(4, 0), Point(4, 1), Point(1, 1), Point(1, 5), Point(0, 5),
    ], scale)


def _letter_m(scale: float = 1.0) -> list[Point]:
    return _scale_points([
        Point(0, 0), Point(0, 5), Point(1, 5), Point(1.8, 3.1),
        Point(2.6, 5), Point(3.6, 5), Point(3.6, 0), Point(2.6, 0),
        Point(2.6, 2.7), Point(1.8, 0.8), Point(1, 2.7), Point(1, 0),
    ], scale)


def _asymmetric_insert(scale: float = 1.0) -> list[Point]:
    return _scale_points([
        Point(0, 0), Point(2.2, 0), Point(2.2, 0.7), Point(1.4, 0.7),
        Point(1.4, 1.6), Point(0.5, 1.6), Point(0.5, 0.7), Point(0, 0.7),
    ], scale)


def _wedge(scale: float = 1.0) -> list[Point]:
    return _scale_points([Point(0, 0), Point(4.5, 0), Point(3.3, 1.4), Point(0.7, 2.6)], scale)


def _slanted_bar(scale: float = 1.0) -> list[Point]:
    return _scale_points([Point(0, 0.5), Point(4.4, 0), Point(4.6, 0.9), Point(0.2, 1.4)], scale)


def _notched_block(scale: float = 1.0) -> list[Point]:
    return _scale_points([
        Point(0, 0), Point(4, 0), Point(4, 3), Point(2.6, 3),
        Point(2.6, 1.5), Point(1.4, 1.5), Point(1.4, 3), Point(0, 3),
    ], scale)


def _bounds_area(bounds: tuple[float, float, float, float] | None) -> float:
    if bounds is None:
        return 0.0
    return max(0.0, bounds[2] - bounds[0]) * max(0.0, bounds[3] - bounds[1])


def _layer_key(layer: str) -> str:
    return "".join(character.casefold() for character in layer if character.isalnum())


def _file_slug(value: str) -> str:
    return "_".join(part for part in value.casefold().replace(".", "_").split() if part)


__all__ = [
    "BENCHMARK_SCHEMA",
    "BENCHMARK_VERSION",
    "BenchmarkPack",
    "generate_flexi_benchmark_pack",
    "analyze_flexi_benchmark_pack",
]
