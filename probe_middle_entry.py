from dataclasses import replace
import sys
sys.path.insert(0, r"C:\Users\auron\Documents\CAM Workbench")
from cam_core import CamProject, MotionKind, generate_closed_router_offset
from cam_core.toolpath import _profile_entry_interference

p = CamProject.load(r"C:\Users\auron\Documents\Boat stuff\entry-visibility-current.reference.camjob.json")
source = "entity_dc92ea7dd3e8461d9c2092b8fa7a6961"
op = p.operations[4]
for start_index in range(8):
    candidate = replace(op, start_index=start_index)
    path = generate_closed_router_offset(p, candidate)
    block = next(b for b in path.blocks if b.metadata.get("entity_id") == source and b.metadata.get("role") == "rough")
    actual = str(block.metadata.get("entry_mode_actual", block.metadata.get("entry_mode")))
    first_arc = next((m for m in block.motions if m.kind in {MotionKind.ARC_CW, MotionKind.ARC_CCW} and m.center is not None and m.end.z < 0), None)
    print(start_index, actual, first_arc.center if first_arc else block.motions[0].end, block.metadata.get("entry_adjustment_kind"), _profile_entry_interference(block.motions, actual, source, p.entities, .125))
