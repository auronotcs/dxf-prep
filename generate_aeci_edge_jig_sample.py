from __future__ import annotations

from pathlib import Path
import shutil
import sys

REPO = Path(r"C:\Users\auron\Documents\CAM Workbench")
SOURCE = Path(r"C:\Users\auron\Documents\Boat stuff\AECI half inch rail mounted letters.reference.dxf")
DESTINATION = Path(r"C:\Users\auron\Documents\Boat stuff\AECI bottom-edge jig PREVIEW - NOT PRODUCTION")
sys.path.insert(0, str(REPO))

from cam_core import (  # noqa: E402
    BottomEdgeJigSettings,
    Point,
    Polyline,
    build_bottom_edge_jig_projects,
    create_bottom_edge_hole,
    create_bottom_edge_mounting_plan,
    drawing_bounds,
    entity_bounds,
    export_dxf,
    import_dxf,
    is_closed_entity,
)


def bottom_candidates(part):
    min_x, min_y, max_x, _max_y = part.bounds
    width = max_x - min_x
    values = {width * fraction / 200.0 for fraction in range(5, 196)}
    tolerance = max(1e-7, width * 1e-5)
    for loop in part.assembly_loops:
        for first, second in zip(loop, loop[1:] + loop[:1]):
            if abs(first.y - min_y) <= tolerance:
                values.add(first.x - min_x)
            if abs(first.y - min_y) <= tolerance and abs(second.y - min_y) <= tolerance:
                values.add((first.x + second.x) / 2.0 - min_x)
    return sorted(values, key=lambda value: (abs(value - width / 2.0), value))


def main() -> None:
    imported = import_dxf(SOURCE)
    project = imported.project
    selected = set()
    for entity in project.entities:
        bounds = entity_bounds(entity)
        if bounds is not None and is_closed_entity(entity) and bounds[3] - bounds[1] > 2.0:
            selected.add(entity.id)
    plan = create_bottom_edge_mounting_plan(
        project.entities, selected, units="in", stock_thickness=0.5,
        thread_designation="1/4-20", rail_group="AECI rail",
    )
    failures = []
    for part in plan.parts:
        for along in bottom_candidates(part):
            try:
                plan.holes.append(create_bottom_edge_hole(plan, part.part_id, along))
                break
            except ValueError:
                continue
        else:
            failures.append(part.label)
    if failures:
        raise RuntimeError("No rail-supported bottom candidate for: " + ", ".join(failures))

    settings = BottomEdgeJigSettings(
        tap_drill_diameter=0.201,
        printed_guide_fit_allowance=None,
        rail_clearance_diameter=None,
        rail_thickness=0.25,
        drill_depth=None,
        jig_style="u_channel_saddle",
        cradle_clearance=0.012,
        jig_wall_thickness=0.18,
        jig_engagement_depth=0.45,
        jig_span_along_edge=1.25,
    )
    if DESTINATION.exists():
        raise RuntimeError(f"Refusing to replace existing sample folder: {DESTINATION}")
    DESTINATION.mkdir(parents=True)
    jig_projects = build_bottom_edge_jig_projects(plan, settings)
    manifest = [
        "AECI BOTTOM-EDGE JIG CONSTRUCTION PREVIEW — NOT FOR PRODUCTION",
        "Hole locations are one-per-letter automatic demonstrations and have NOT been operator reviewed.",
        "#7 / 0.201 in is tentative. Printed guide fit allowance is unverified and exported at nominal 0.201 in.",
        "Letter thickness 0.500 in and 1/4-20 thread are confirmed inputs. Rail thickness is 0.250 in.",
        "Rail pattern intentionally omitted until its clearance diameter is verified.",
        "DXF units are explicitly inches. Import 1:1; do not scale.",
        f"Source: {SOURCE}",
        f"Source import warning: {'; '.join(imported.warnings) or 'none'}",
        f"Source bounds: {drawing_bounds(project.entities, include_annotations=False)} in",
        f"Letter parts: {len(plan.parts)}; sample anchors: {len(plan.holes)}",
        "",
    ]
    for index, (part, jig_project) in enumerate(jig_projects, start=1):
        hole = next(value for value in plan.holes if value.owner_part_id == part.part_id)
        filename = f"AECI JIG {index:02d} {part.label} {part.part_id[-8:]}.dxf"
        result = export_dxf(jig_project, DESTINATION / filename, dialect="enroute_r12")
        round_trip = import_dxf(result.path)
        if round_trip.project.units != "in" or round_trip.warnings:
            raise RuntimeError(f"{filename}: unit round-trip failed: {round_trip.warnings}")
        reference = next(
            entity for entity in round_trip.project.entities
            if entity.layer == "JIG_LETTER_REFERENCE" and isinstance(entity, Polyline)
        )
        source_width = part.bounds[2] - part.bounds[0]
        source_height = part.bounds[3] - part.bounds[1]
        imported_bounds = entity_bounds(reference)
        if imported_bounds is None:
            raise RuntimeError(f"{filename}: missing reference bounds")
        if abs((imported_bounds[2] - imported_bounds[0]) - source_width) > 1e-6:
            raise RuntimeError(f"{filename}: width changed in DXF round-trip")
        if abs((imported_bounds[3] - imported_bounds[1]) - source_height) > 1e-6:
            raise RuntimeError(f"{filename}: height changed in DXF round-trip")
        if any(entity.layer.startswith("_") for entity in round_trip.project.entities):
            raise RuntimeError(f"{filename}: private/internal layer leaked")
        circles = [entity for entity in round_trip.project.entities if entity.layer == "JIG_GUIDE_BORE_PROFILE"]
        if len(circles) != 1 or abs(circles[0].radius * 2.0 - 0.201) > 1e-8:
            raise RuntimeError(f"{filename}: drill-normal guide bore did not round-trip")
        manifest.append(
            f"{index:02d} | {filename} | {part.part_id} | "
            f"size {source_width:.6f} x {source_height:.6f} in | "
            f"local edge X {hole.along_from_left:.6f} | assembly {hole.assembly_center.x:.6f},{hole.assembly_center.y:.6f}"
        )
    (DESTINATION / "AECI JIG MANIFEST - READ FIRST.txt").write_text("\n".join(manifest) + "\n", encoding="utf-8")
    print(f"verified_parts={len(plan.parts)} verified_files={len(jig_projects)} destination={DESTINATION}")


if __name__ == "__main__":
    main()
