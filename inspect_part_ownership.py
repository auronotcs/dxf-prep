from collections import Counter, defaultdict
from pathlib import Path
import sys

sys.path.insert(0, r"C:\Users\auron\Documents\CAM Workbench")
from cam_core import CamProject, entity_bounds, profile_containers

p = CamProject.load(Path(r"C:\Users\auron\Documents\Boat stuff\entry-visibility-current.reference.camjob.json"))
op = next(item for item in p.operations if item.id == "operation_c06fb45cf9ca40f2a6deaddc28addc29")
by_id = {item.id: item for item in p.entities}
members = [by_id[item] for item in op.entity_ids]
print("operation members", len(members))
for key in ("group_id", "compound_id", "mounting_part_id", "mounting_owner_entity_id"):
    counts = Counter(str(item.metadata.get(key, "")) for item in members)
    print(key, counts.most_common(8))
containers = profile_containers(members)
roots = defaultdict(list)
for item in members:
    top = [value for value in containers[item.id] if not containers[value]]
    root = top[0] if top else item.id
    roots[root].append(item.id)
print("containment roots", [(root, len(ids), entity_bounds(by_id[root])) for root, ids in roots.items()])
