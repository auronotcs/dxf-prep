from collections import Counter
from pathlib import Path
import sys

sys.path.insert(0, r"C:\Users\auron\Documents\CAM Workbench")
from cam_core import CamProject, Line, Polyline, entity_bounds

p = CamProject.load(Path(r"C:\Users\auron\Documents\Boat stuff\cut-line-chain-error.reference.camjob.json"))
items = []
for index, entity in enumerate(p.entities, 1):
    bounds = entity_bounds(entity)
    if bounds is None or bounds[2] < 240 or bounds[0] > 360 or bounds[3] < 0 or bounds[1] > 60:
        continue
    if isinstance(entity, Line) or isinstance(entity, Polyline) and not entity.closed:
        items.append((index, entity, bounds))
print("project", p.name, "entities", len(p.entities), "plate3 open", len(items))
print("layers", Counter(entity.layer for _, entity, _ in items))
for index, entity, bounds in items:
    if isinstance(entity, Line):
        print(index, entity.id, entity.layer, "LINE", entity.start, entity.end, bounds)
    else:
        print(index, entity.id, entity.layer, "OPEN", len(entity.points), entity.points[0], entity.points[-1], bounds)
