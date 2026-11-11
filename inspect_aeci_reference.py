from pathlib import Path
import sys

REPO = Path(r"C:\Users\auron\Documents\CAM Workbench")
sys.path.insert(0, str(REPO))

from cam_core import import_dxf, entity_bounds, is_closed_entity, discover_mounting_parts

path = Path(r"C:\Users\auron\Documents\Boat stuff\AECI half inch rail mounted letters.reference.dxf")
result = import_dxf(path)
project = result.project
closed = [entity for entity in project.entities if is_closed_entity(entity)]
print("units", project.units, "entities", len(project.entities), "closed", len(closed), "warnings", result.warnings)
print("bounds", entity_bounds(project.entities[0]) if len(project.entities) == 1 else None)
selected = {entity.id for entity in closed}
parts = discover_mounting_parts(project.entities, selected)
print("parts", len(parts))
for index, part in enumerate(parts, 1):
    print(index, part.owner_entity_id, part.label, tuple(round(value, 4) for value in part.bounds), "members", len(part.member_ids))
