import sys, json, time, hashlib, ast
from pathlib import Path
root = Path('C:/Users/auron/Documents/CAM Workbench')
sys.path.insert(0, str(root))
from tests.test_entry_inspection import EntryInspectionTests
from cam_core import CamProject, Rectangle, Point, create_bottom_edge_mounting_plan, create_bottom_edge_hole, apply_bottom_edge_mounting_plan, load_bottom_edge_mounting_plans, validate_bottom_edge_registration
from cam_core.units import convert_project_units
from cam_core import current_bottom_edge_hole_center
from cam_core.layout import entity_bounds

out = {}
host, _ = EntryInspectionTests.make_host()
host.project.metadata['plates'].append(dict(number=2,x=200,y=0,width=120,height=60))
host.project.metadata['active_plate_number'] = 2
records = host._scoped_toolpath_entry_records(update_status=False)
out['empty_active_plate'] = dict(active_plate=2, returned_entries=len(records), returned_plates=sorted({r.plate_number for r in records}))
letter = Rectangle(corner_a=Point(10,4),corner_b=Point(20,14))
p = CamProject('synthetic audit', entities=[letter])
plan = create_bottom_edge_mounting_plan(p.entities,{letter.id},units='in',stock_thickness=.5)
plan.holes.append(create_bottom_edge_hole(plan,plan.parts[0].part_id,2))
apply_bottom_edge_mounting_plan(p,plan)
mm = convert_project_units(p,'mm')
loaded = load_bottom_edge_mounting_plans(mm)[0]
try:
    validation = str(validate_bottom_edge_registration(mm,loaded))
except Exception as exc:
    validation = type(exc).__name__ + ': ' + str(exc)
out['edge_mounting_units'] = dict(project_units=mm.units,plan_units=loaded.units,letter_bounds=entity_bounds(mm.entities[0]),stored_hole=[loaded.holes[0].assembly_center.x,loaded.holes[0].assembly_center.y],registration=validation)
center=current_bottom_edge_hole_center(mm,loaded,loaded.holes[0])
out['edge_mounting_units']['current_center_reported']=[center.x,center.y]
out['edge_mounting_units']['expected_project_center']=[12*25.4,4*25.4]
snapshot = Path(r'C:\Users\auron\Documents\Shop Jobs\Jobs\entry-visibility-current.reference.camjob.json')
from cam_core import generate_operation_toolpath
p = CamProject.from_dict(json.loads(snapshot.read_text()))
times=[]
for op in p.operations:
    if not op.enabled: continue
    start=time.perf_counter()
    try:
        path=generate_operation_toolpath(p,op)
        result={'blocks':len(path.blocks)}
    except Exception as exc:
        result={'error':str(exc)}
    times.append(dict(name=op.name,seconds=round(time.perf_counter()-start,4),**result))
out['synchronous_generation_single_run']=times
source=(root/'cam_editor_app.py').read_bytes()
tree=ast.parse(source)
out['source']={'sha256':hashlib.sha256(source).hexdigest(),'lines':len(source.splitlines()),'bytes':len(source),'largest_functions':sorted([(n.end_lineno-n.lineno+1,n.name,n.lineno) for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))],reverse=True)[:8]}
Path('WORKBENCH_AUDIT_2026-09-15_EVIDENCE.json').write_text(json.dumps(out,indent=2))
print(json.dumps(out,indent=2))
