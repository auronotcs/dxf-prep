from __future__ import annotations

import json
from pathlib import Path
import sys


path = Path(r"C:\Users\auron\Documents\Shop Jobs\Jobs\entry-visibility-current.reference.camjob.json")
data = json.loads(path.read_text(encoding="utf-8"))
print(tuple(data))
print(
    "name", data.get("name"), "units", data.get("units"),
    "entities", len(data.get("entities", ())),
    "operations", len(data.get("operations", ())),
    "plates", len(data.get("plates", ())),
)
print("metadata keys", sorted(data.get("metadata", {})))
for key in ("plates", "plate_definitions", "sheet_plates", "active_plate"):
    if key in data.get("metadata", {}):
        print(key, data["metadata"][key])
for index, operation in enumerate(data.get("operations", ())):
    print(
        index, operation.get("id"), operation.get("name"), operation.get("kind"),
        "enabled", operation.get("enabled"),
        "entities", len(operation.get("entity_ids", ())),
        "entry", operation.get("entry_mode"),
        "tool", operation.get("tool_station"),
        "depth", operation.get("target_depth"),
    )
    for key in (
        "recipe_name", "cut_side", "cut_intent", "lead_in", "lead_out",
        "ramp_length", "helix_radius", "helix_pitch", "stepdown", "safe_z",
    ):
        if key in operation:
            print("   ", key, operation[key])

sys.path.insert(0, r"C:\Users\auron\Documents\CAM Workbench")
from cam_core import CamProject, MotionKind, Plate, entity_bounds, generate_operation_toolpath, run_preflight
from cam_core.toolpath import _profile_entry_interference

project = CamProject.load(path)
entities = {entity.id: entity for entity in project.entities}
plate2 = (120.0, 0.0, 240.0, 60.0)

plates = [Plate.from_dict(item) for item in project.metadata.get("plates", [])]
generated_paths = {}
job_setup = project.metadata.get("job_setup", {})
issues = run_preflight(
    project,
    plates,
    material=str(job_setup.get("material", "")),
    material_thickness=float(job_setup.get("material_thickness", 0.0) or 0.0) or None,
    generated_paths_out=generated_paths,
)
print("\nPREFLIGHT", len(issues), "issue(s)")
for issue in issues:
    print(issue.severity, issue.code, issue.message, "ids", issue.entity_ids, "point", issue.point)


def in_plate2(bounds):
    return bounds is not None and not (
        bounds[2] < plate2[0] or bounds[0] > plate2[2]
        or bounds[3] < plate2[1] or bounds[1] > plate2[3]
    )


def entry_description(block):
    motions = block.motions
    actual = str(block.metadata.get("entry_mode_actual", block.metadata.get("entry_mode", "")))
    if actual == "helical":
        for index, motion in enumerate(motions):
            if (
                index > 0
                and motion.kind in {MotionKind.ARC_CW, MotionKind.ARC_CCW}
                and motion.center is not None
                and min(motions[index - 1].end.z, motion.end.z) < 0
            ):
                start = motions[index - 1].end
                radius = ((start.x - motion.center.x) ** 2 + (start.y - motion.center.y) ** 2) ** 0.5
                return actual, (motion.center.x, motion.center.y), radius, (start.x, start.y)
    location = block.metadata.get("entry_plunge_location") or block.metadata.get("entry_effective_start")
    if location is None and motions:
        location = (motions[0].end.x, motions[0].end.y)
    return actual, tuple(location) if location is not None else None, 0.0, None


print("\nGENERATED PLATE 2 ENTRIES")
for operation in project.operations:
    generated = generate_operation_toolpath(project, operation)
    plate_blocks = [
        block for block in generated.blocks
        if in_plate2(entity_bounds(entities.get(str(block.metadata.get("entity_id", "")))))
    ]
    if not plate_blocks:
        continue
    print("\n", operation.id, operation.name, "blocks", len(plate_blocks))
    for block in plate_blocks:
        entity_id = str(block.metadata.get("entity_id", ""))
        bounds = entity_bounds(entities.get(entity_id))
        actual, location, radius, start = entry_description(block)
        print(
            " ", entity_id, "side", block.metadata.get("side"),
            "role", block.metadata.get("role"), "bounds", bounds,
            "actual", actual, "location", location, "radius", round(radius, 6),
            "start", start, "adjust", block.metadata.get("entry_adjustment_kind"),
            "fallback", block.metadata.get("entry_fallback_reason_code"),
        )
        if entity_id == "entity_dc92ea7dd3e8461d9c2092b8fa7a6961" and block.metadata.get("role") == "rough":
            print("     CORE INTERFERENCE", _profile_entry_interference(
                block.motions, actual, entity_id, project.entities, 0.125,
            ))
            for motion_index, motion in enumerate(block.motions[:10]):
                print("     MOTION", motion_index, motion.kind, motion.end, "center", motion.center)
