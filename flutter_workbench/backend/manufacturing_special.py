"""Reviewed specialized operation drafts; generation remains core-owned."""
import copy
import math
from cam_core.model import (EngraveOperation, DragKnifeOperation, ThreadMillOperation,
    RestMachiningOperation, ProfileRestMachiningOperation, RouterOffsetOperation,
    IslandFillOperation, ToolKind, Circle, Rectangle, Polyline, Line)

TYPES = {c.kind:c for c in (EngraveOperation,DragKnifeOperation,ThreadMillOperation,RestMachiningOperation,ProfileRestMachiningOperation)}
COMMON = {'name','tool_station','entity_ids','target_depth','safe_z'}
EXTRA = {
 'engrave': {'approach_height','reverse_open_paths','require_stay_on_material'},
 'drag_knife': {'blade_offset','swivel_depth','swivel_angle','approach_height','alignment_length','curve_tolerance','passes','reverse_paths','setup_verified','outside_corner_loops','loop_radius','loop_feed_percent','edge_aware_corners','corner_feed_ipm','lead_in_feed_ipm','feed_rate_override','plunge_rate_override'},
 'thread_mill': {'thread_designation','pitch_diameter_adjustment','radial_passes','spring_passes','handedness','cut_direction','cutter_verified','pilot_hole_depth','bottom_clearance'},
 'rest_machining': {'source_operation_id','island_entity_ids','stepover','stepdown','stock_to_leave','ignore_stock_less_than'},
 'profile_rest_machining': {'source_operation_id','stepdown','stock_to_leave','ignore_stock_less_than','inherit_pass_schedule','preserve_tabs','remove_tabs_after','source_snapshot'},
}
BOOLS={'reverse_open_paths','require_stay_on_material','reverse_paths','setup_verified','cutter_verified','inherit_pass_schedule','preserve_tabs','remove_tabs_after','outside_corner_loops','edge_aware_corners'}
INTS={'tool_station','passes','radial_passes','spring_passes'}

def prepare_operation(project,p):
 from manufacturing_support import PRESERVED_FIELDS
 from manufacturing_management import validate_targets
 kind=p.get('kind');cls=TYPES.get(kind)
 if cls is None: raise ValueError('Choose a supported specialized operation.')
 allowed=COMMON|EXTRA[kind]-{'source_snapshot'}
 if set(p)-allowed-{'kind','id'}: raise ValueError('Unsupported specialized operation field.')
 for key,value in p.items():
  if key in BOOLS and type(value) is not bool: raise ValueError(f'{key} must be on or off.')
  if key in INTS and type(value) is not int: raise ValueError(f'{key} must be a whole number.')
  if type(value) in (int,float) and not math.isfinite(value): raise ValueError('Machining numbers must be finite.')
 old=next((o for o in project.operations if o.id==p.get('id')),None)
 if p.get('id') and (old is None or type(old) is not cls or old.profile_retention or not old.enabled): raise ValueError('Choose an enabled independent matching operation.')
 values={k:copy.deepcopy(v) for k,v in p.items() if k in allowed}
 source=None
 if kind in {'rest_machining','profile_rest_machining'}:
  source=next((o for o in project.operations if o.id==p.get('source_operation_id')),None)
  expected=IslandFillOperation if kind=='rest_machining' else RouterOffsetOperation
  if type(source) is not expected or not source.enabled or source.profile_retention: raise ValueError('Choose an enabled independent matching source operation.')
  if isinstance(source,RouterOffsetOperation) and source.remove_tabs_after: raise ValueError('Keep source tabs before adding dependent rest machining.')
  values.update(entity_ids=list(source.entity_ids),target_depth=source.target_depth)
  if kind=='rest_machining': values['island_entity_ids']=list(source.island_entity_ids)
  values['depends_on_ids']=[source.id]
 op=cls(**values)
 if not op.name.strip(): raise ValueError('Enter an operation name.')
 if old:
  op.id=old.id
  for key in PRESERVED_FIELDS:
   if key not in values: setattr(op,key,copy.deepcopy(getattr(old,key)))
 if source: op.depends_on_ids=list(dict.fromkeys([*op.depends_on_ids,source.id]))
 tool=project.tool_library.by_station(op.tool_station)
 expected={'engrave':ToolKind.ENGRAVER,'drag_knife':ToolKind.DRAG_KNIFE,'thread_mill':ToolKind.THREAD_MILL}.get(kind,ToolKind.END_MILL)
 if tool is None or tool.kind is not expected: raise ValueError(f'Choose a configured {expected.value} cutter in Turret Setup.')
 if source:
  source_tool=project.tool_library.by_station(source.tool_station)
  if source_tool is None or tool.diameter>=source_tool.diameter: raise ValueError('The rest cutter must be smaller than the source cutter.')
 if kind=='profile_rest_machining':
  from cam_core.operation_links import capture_profile_rest_dependency
  capture_profile_rest_dependency(project,op)
 entities={e.id:e for e in project.entities}
 for eid in op.entity_ids:
  entity=entities.get(eid)
  if not isinstance(entity,(Circle,Rectangle,Polyline,Line)): raise ValueError('Select machinable contours; convert text to strokes first.')
  if kind=='thread_mill' and not isinstance(entity,Circle): raise ValueError('Thread milling requires pilot-hole circles.')
 if old: project.operations[project.operations.index(old)]=op
 else: project.operations.append(op)
 validate_targets(project)
 return op

def release(project,p,qualified):
 from cam_core.patchwork_holes import resolved_hole_project
 from cam_core.automation import _project_plates
 project = resolved_hole_project(project, _project_plates(project))
 from cam_core.full_release import FullReleaseReview,install_release_stage,RESTRAINT
 from cam_core.automation import _project_plates
 if set(p)-{'id','plate_number','fingerprint','confirmed_parts','evidence_note','waste_warning_review','part_order','audit_key'}: raise ValueError('Unsupported final-release setting.')
 plate=next((v for v in _project_plates(project) if v.number==p.get('plate_number')),None)
 if plate is None: raise ValueError('Choose a source plate.')
 review=FullReleaseReview(project,p.get('id'),plate)
 if 'fingerprint' in p and p['fingerprint']!=review.fingerprint: raise ValueError('Release inputs changed. Audit again.')
 if 'part_order' in p:
  owners=p['part_order']
  if not isinstance(owners,list) or any(not isinstance(v,str) for v in owners): raise ValueError('Part order must name every physical body exactly once.')
  order=review.order_for_parts(owners)
 else:
  order=review.order
  owners=list(dict.fromkeys(review.by_id[key]['owner'] for key in order))
 audit=review.audit(order)
 key=review.warning_review_key(order,audit)
 details=dict(source_id=p.get('id'),plate_number=plate.number,fingerprint=review.fingerprint,parts=list(review.parts),part_order=owners,order_locked=bool(review.tabs.locked),ownership=copy.deepcopy(review.ownership),audit=audit,warning_key=key)
 if qualified and 'part_order' in p and p.get('audit_key')!=key: raise ValueError('Release order or audit changed. Review the current order before confirming restraint.')
 if not qualified: return project,details
 if p.get('fingerprint')!=review.fingerprint: raise ValueError('Release inputs changed. Audit again.')
 contract=dict(restraint=RESTRAINT,confirmed_parts=p.get('confirmed_parts',[]),evidence_note=p.get('evidence_note',''),waste_warning_review=p.get('waste_warning_review',''))
 plan=review.plan(order,contract)
 job,stage=install_release_stage(project,plan)
 details['stage_id']=stage.id
 return job,details
