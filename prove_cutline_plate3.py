from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0, r"C:\Users\auron\Documents\CAM Workbench")

from cam_core import CamProject, Line, Plate, Polyline, generate_open_router_offset, run_preflight
from cam_core.layout import entity_bounds


snapshot = Path(r"C:\Users\auron\Documents\Boat stuff\cut-line-chain-error.reference.camjob.json")
project = CamProject.load(snapshot)
selected = []
for entity in project.entities:
    bounds = entity_bounds(entity)
    if (
        bounds is not None
        and bounds[0] >= 240.0
        and bounds[2] <= 360.0
        and bounds[1] >= 0.0
        and bounds[3] <= 60.0
        and (isinstance(entity, Line) or isinstance(entity, Polyline) and not entity.closed)
    ):
        selected.append(entity)

operation = deepcopy(project.operations[0])
operation.id = "plate3_cut_line_proof"
operation.name = "Plate 3 separate cutoffs"
operation.entity_ids = [entity.id for entity in selected]
path = generate_open_router_offset(project, operation)

assert len(selected) == 9
assert len(path.blocks) == 6
assert all(block.metadata["chain_count"] == 6 for block in path.blocks)
assert all(len(block.metadata["entity_ids"]) == 1 for block in path.blocks)
assert len(set(path.blocks[0].metadata["ignored_entity_ids"])) == 3
assert all(block.motions[0].end.z == operation.safe_z for block in path.blocks)
assert all(block.motions[-1].end.z == operation.safe_z for block in path.blocks)
assert all(
    max(motion.end.x for motion in block.motions) - min(motion.end.x for motion in block.motions) <= 1e-7
    for block in path.blocks
)

project.operations = [operation]
plates = [Plate.from_dict(payload) for payload in project.metadata.get("plates", ())]
issues = run_preflight(
    project, plates, material="Aluminum", material_thickness=0.125,
    machine_min_rpm=8000, machine_max_rpm=24000,
)
blocking = [issue for issue in issues if issue.severity == "error"]
assert not blocking, blocking
assert len([issue for issue in issues if issue.code == "cut_line.degenerate_ignored"]) == 3

print(f"sources={len(selected)} valid_paths={len(path.blocks)} ignored_remnants=3")
for block in path.blocks:
    x = block.motions[0].end.x
    start_y = block.motions[0].end.y
    end_y = block.motions[-1].end.y
    print(
        f"path={block.metadata['chain_index']}/{block.metadata['chain_count']} "
        f"source={block.metadata['entity_ids'][0]} x={x:.6f} y={start_y:.6f}->{end_y:.6f}"
    )
print(f"preflight_errors={len(blocking)} warnings={len([issue for issue in issues if issue.severity == 'warning'])}")
