"""Scoped Manufacturing authoring and isolated, review-bound motion output."""
import copy
import hashlib
import json
import math
from dataclasses import asdict
import multiprocessing as mp
import sys
from pathlib import Path
from queue import Empty
import re
from uuid import uuid4
from cam_core.automation import AutomationSession, _project_plates, _automation_layer_settings
from cam_core.model import RouterOffsetOperation, OffsetSide, Tool, ToolKind, Circle, Rectangle, Polyline, Line, DrillCirclesOperation, IslandFillOperation, ProfileRestMachiningOperation, RestMachiningOperation
from cam_core.depth_intent import through_depth
from manufacturing_keepouts import display as keepout_display, link_choices as hole_link_choices
from cam_core.preflight import run_preflight
from cam_core.workflow import ordered_operations
from cam_core.optimization import (cycle_optimization_settings, production_cycle_optimization_settings,
    validate_cycle_optimization_settings, estimate_cycle_metrics, CycleComparison, optimize_generated_toolpath)
from cam_core.output_workflow import (output_generation_fingerprint, prepare_plate_outputs,
    execute_plate_outputs, repository_output_paths)

PROFILE_FIELDS = {'name','tool_station','entity_ids','target_depth','safe_z','stepdown','side','cut_direction'}
from profile_helix import FIELDS as PROFILE_HELIX_FIELDS, validate as validate_profile_helix

PROFILE_ADVANCED = PROFILE_HELIX_FIELDS | {'entity_sides','manual_contour_order','start_index','manual_tab_positions','remove_tabs_after','tab_cleanup_order','tab_cleanup_min_perimeter','cut_intent','breakthrough','onion_skin','tab_mode','tab_count','tab_width','tab_height','corner_tab_offset','tab_spacing','female_tab_min_perimeter','contour_order','curve_feed_mode','curve_feed_min_percent','curve_feed_transition_distance','common_line_mode','common_line_tolerance','pass_depths','entry_mode','entry_side','lead_in','lead_out','helix_diameter','helix_pitch','helix_fallback',
    'small_center_cleanout_ids','helix_feed_override','finishing_pass','allowance','finish_axial_stock','finish_feed_override','finish_spindle_rpm_override'}
GROUP_SOURCE_FIELDS = PROFILE_FIELDS | {'cut_intent','breakthrough','tab_count','tab_width','tab_height','female_tab_min_perimeter'}
PROFILE_OPEN_FIELDS = PROFILE_FIELDS | {'cut_intent','breakthrough','pass_depths','entry_mode','lead_in','lead_out','allowance'}
TAB_MODES = {'even','minimum_and_spacing','minimum_avoid_corners','support_aware','corners_and_spacing','manual','aligned'}
OPTIMIZATION_FIELDS = {'enabled','depth_linking','profile_ordering','profile_travel_strategy','profile_start_optimization','drill_ordering','motion_compaction','line_merge_tolerance','rapid_xy_ipm','rapid_z_ipm','tool_change_seconds','spindle_start_seconds'}
STRATEGIES = {'retained_skin':'retained_skin_profile','Tabbed profile + rest / leave X tabs':'tabbed_profile_rest',
    'Clear waste + tabbed finish':'waste_then_tabbed_finish'}
PARAM_ALIASES = {'surface_feed_override':'surface_feed','retract_feed_override':'retract_feed','plunge_rate_override':'plunge_rate','spindle_rpm_override':'spindle_rpm','bottom_finish_feed_override':'bottom_finish_feed','finish_feed_override':'finish_feed','finish_spindle_rpm_override':'finish_spindle_rpm','helix_feed_override':'helix_feed'}
TOOL_FIELDS = {'station','name','diameter','spindle_rpm','feed_rate','plunge_rate','max_stepdown','cutting_edge_length','kind'}

BASE_FIELDS = {'name','tool_station','entity_ids','target_depth','safe_z'}
DRILL_FIELDS = BASE_FIELDS | {'peck_depth','peck_mode','retract_height','reentry_clearance','dwell_seconds'}
DRILL_ADVANCED = {'peck_definition','peck_count','surface_feed_override','retract_feed_override','controlled_retract','return_to_surface','top_dwell_seconds','plunge_rate_override','spindle_rpm_override'}
FILL_FIELDS = BASE_FIELDS | {'stepdown','stepover','island_entity_ids','cut_direction'}
FILL_ADVANCED = {'cut_intent','breakthrough','corner_tags','sliver_cleanup','sliver_max_path_length','sliver_link_distance','pass_mode','pass_count','pass_depths','bottom_finish_pass','bottom_finish_depth','bottom_finish_feed_override',
    'entry_mode','ramp_length','helix_diameter','helix_min_diameter','helix_pitch','helix_ramp_angle','helix_clearance','helix_finish_revolutions','helix_feed_override','helix_fallback','dwell_seconds',
    'keep_tool_down','stay_down_max_distance','stay_down_max_engagement','stay_down_feed_percent','strategy','speed_max_full_depth_engagement','speed_center_feed_percent','speed_link_distance','speed_fallback'}

PRESERVED_FIELDS = {'sequence_role','sequence_priority','sequence_locked','geometry_selector','depends_on_ids'}

def fields_match(op, fields):
    from operation_recipe import attached, FIELDS
    fields = fields | PRESERVED_FIELDS | (FIELDS if attached(op) else set())
    return type(op)(**{k:getattr(op,k) for k in fields},id=op.id,enabled=op.enabled).to_dict() == op.to_dict()

def stock_verification(project):
    from cam_core.units import unit_scale
    f = unit_scale('in',project.units)
    setup = project.metadata.get('job_setup',{})
    plates = _project_plates(project)
    fingerprint = dict(machine_profile=setup.get('machine','Shop Router / EnRoute'),
        material=str(setup.get('material','Anything')).strip().casefold(),
        thickness_in=round(float(setup.get('material_thickness',0))/f,6),
        breakthrough_in=round(float(setup.get('through_cut_breakthrough',.015*f))/f,6),
        plates_in=[[round(p.width/f,5),round(p.height/f,5)] for p in plates])
    binding = dict(fingerprint=fingerprint,plate_positions=[[p.number,p.x/f,p.y/f] for p in plates])
    token = hashlib.sha256(json.dumps(binding,sort_keys=True).encode()).hexdigest()
    valid = fingerprint['thickness_in'] > 0 and bool(plates) and bool(fingerprint['material']) and bool(fingerprint['machine_profile'])
    return dict(verified=valid and setup.get('stock_verification') == fingerprint and setup.get('flutter_stock_verification') == token,
        required=setup.get('flutter_require_verified_stock',False),token=token,eligible=valid,
        fingerprint=fingerprint,plates=[p.to_dict() for p in plates],units=project.units)


def group_signature(operations):
    return hashlib.sha256(json.dumps([dict(o.to_dict(),enabled=True) for o in operations],sort_keys=True).encode()).hexdigest()

def seal_group(project, members):
    gid = members[0].profile_retention['group_id']
    project.metadata.setdefault('flutter_manufacturing_groups',{})[gid] = group_signature(members)

def basic_operation(op):
    cls = type(op)
    from manufacturing_special import TYPES, COMMON, EXTRA
    from cam_core.model import TabReleaseOperation
    if cls is TabReleaseOperation:
        if op.small_center_cleanout_ids: return False
        return bool(op.release_plan) and fields_match(op, PROFILE_FIELDS | PROFILE_ADVANCED | {'tab_release_plan','source_operation_id','release_plan'})
    if cls in TYPES.values():
        return fields_match(op, COMMON | EXTRA[op.kind])
    if cls is RouterOffsetOperation:
        if op.side not in {OffsetSide.INSIDE, OffsetSide.OUTSIDE, OffsetSide.ON, OffsetSide.LEFT, OffsetSide.RIGHT}: return False
        if op.tab_mode not in TAB_MODES: return False
        fields = PROFILE_FIELDS | PROFILE_ADVANCED
        from operation_recipe import attached, FIELDS
        if attached(op): fields |= FIELDS
        if op.tab_release_plan:
            plan=op.tab_release_plan
            if not isinstance(plan,dict) or plan.get('version')!=1 or plan.get('operation_id')!=op.id or not {'plate','fingerprint','retain_per_part','remove','totals','retained'} <= set(plan): return False
            fields = fields | {'tab_release_plan'}
    elif cls is DrillCirclesOperation: fields = DRILL_FIELDS | DRILL_ADVANCED
    elif cls is IslandFillOperation:
        fields = FILL_FIELDS | FILL_ADVANCED
        from operation_recipe import attached,FIELDS
        if attached(op):fields |= FIELDS
    else: return False
    default = cls(**{k:getattr(op,k) for k in fields | PRESERVED_FIELDS},id=op.id,enabled=op.enabled)
    return default.to_dict() == op.to_dict()

def has_cabinet_review(project):
    return bool(project.metadata.get('cabinet_tab_plans')) or any(
        e.metadata.get('mounting_workflow')=='cabinet_tabs' for e in project.entities)


def supported(project):
    if has_cabinet_review(project): return False
    # A geometry-only document may carry preferences from Tkinter without using them.
    # Preserve its editing/recovery path; validate settings before any machining exists.
    if not project.operations: return True
    settings = cycle_optimization_settings(project)
    if settings.enabled:
        # Low retracts need fixture qualification; arc fitting needs controller qualification.
        if settings.minimum_retract or settings.arc_fitting: return False
        try: validate_cycle_optimization_settings(settings)
        except (ValueError,TypeError): return False
    groups = {}
    for op in project.operations:
        hold = op.profile_retention
        if not hold:
            if not basic_operation(op): return False
            if type(op) is RouterOffsetOperation:
                if op.small_center_cleanout_ids:
                    from manufacturing_centers import validate
                    # Core error labels may populate object-number metadata; inspection is read-only.
                    try: validate(copy.deepcopy(project),op)
                    except (ValueError,TypeError,KeyError): return False
                shapes = [e for e in project.entities if e.id in op.entity_ids]
                opened = [isinstance(e,Line) or isinstance(e,Polyline) and not e.closed for e in shapes]
                if any(opened) and (not all(opened) or op.side not in {OffsetSide.LEFT,OffsetSide.RIGHT,OffsetSide.ON} or not fields_match(op,PROFILE_OPEN_FIELDS) or op.entry_mode not in {'plunge','ramp'}): return False
                if not any(opened) and op.side in {OffsetSide.LEFT,OffsetSide.RIGHT}: return False
        else:
            if getattr(op,'small_center_cleanout_ids',[]): return False
            if type(op) not in (RouterOffsetOperation, ProfileRestMachiningOperation, IslandFillOperation, RestMachiningOperation): return False
            if hold.get('workflow','retained_skin_profile') not in STRATEGIES.values(): return False
            groups.setdefault(hold.get('group_id'),[]).append(op)
    for gid, members in groups.items():
        if not gid or project.metadata.get('flutter_manufacturing_groups',{}).get(gid) != group_signature(members): return False
        if set(members[0].profile_retention.get('member_ids',[])) != {o.id for o in members}: return False
    return True


def inventory(project):
    from manufacturing_ownership import capable
    zones, link_issues = keepout_display(project)
    return dict(stockVerification=stock_verification(project),optimization=asdict(cycle_optimization_settings(project)),editable=supported(project), operations=[o.to_dict() for o in project.operations],
        openPathOwnership=capable(),knifeWorkflows=True,knifeClearance=True,smallCenters=True,
        tools=[t.to_dict() for t in project.tool_library.tools], linkedRevisions=True, graphicalPicking=True, keepoutsEditable=True, holeExclusions=True, closedOverruns=True, keepouts=zones, holeLinkIssues=link_issues, holeLinkChoices=hole_link_choices(project),
        scope='Profiles with entry/finishing, reviewed second-tool groups, drilling and Concentric/Speed Cleanout. Stock verification and reviewed plate output.')

def execute(session, command, params):
    result = session.execute(command, params)
    if not result['ok']: raise ValueError(result['error']['message'])
    return result.get('data', {})

def mutate(session, command, p, reviewed_draft=False):
    if not supported(session.project): raise ValueError('This job contains advanced operations. Its machining data is protected.')
    candidate = AutomationSession(project=copy.deepcopy(session.project))
    candidate.selected_ids = set(session.selected_ids)
    project = candidate.project
    from cam_core.patchwork_holes import refresh_linked_holes
    if command == 'setup': refresh_linked_holes(project, _project_plates(project))
    if command == 'closed_overrun_assign':
        from manufacturing_overruns import prepare
        prepare(project,p)
    elif command == 'open_path_assign':
        from manufacturing_ownership import assign
        assign(project,p)
    elif command == 'setup':
        if set(p) - {'material','material_detail','material_thickness','safe_z','width','height','machine','through_cut_breakthrough','require_verified_stock'}: raise ValueError('Unsupported stock field.')
        execute(candidate, 'job.setup', {k:v for k,v in p.items() if k not in {'width','height','require_verified_stock'}})
        if 'require_verified_stock' in p:
            if type(p['require_verified_stock']) is not bool: raise ValueError('Choose whether stock verification is required.')
            project.metadata['job_setup']['flutter_require_verified_stock'] = p['require_verified_stock']
        if 'width' in p or 'height' in p:
            plates = _project_plates(project)
            if not plates:
                if not {'width','height'} <= set(p): raise ValueError('Set both stock width and height for this imported drawing.')
                execute(candidate,'plate.define',dict(plates=[dict(number=1,name='Plate 1',x=0,y=0,width=p['width'],height=p['height'])]))
            else:
                if len(plates)!=1: raise ValueError('Edit multi-plate dimensions in Design before adding operations.')
                execute(candidate, 'plate.update',dict(number=plates[0].number,reflow=False,
                    **{k:p[k] for k in ('width','height') if k in p}))
            if project.metadata.get('patchwork_areas'):
                from cam_core.plates import PatchworkArea, validate_patchwork_areas
                validate_patchwork_areas([PatchworkArea.from_dict(a) for a in project.metadata['patchwork_areas']],_project_plates(project))
    elif command == 'verify_stock':
        review = stock_verification(project)
        if not review['eligible']: raise ValueError('Set material, positive thickness, machine and stock plates before verification.')
        if p.get('token') != review['token']: raise ValueError('Stock settings changed. Review the current physical stock again.')
        if any(p.get(k) is not True for k in ('material_checked','thickness_checked','machine_size_checked')):
            raise ValueError('Confirm the physical material, measured thickness and sheet/machine setup.')
        setup = project.metadata.setdefault('job_setup',{})
        setup['stock_verification'] = review['fingerprint']
        setup['flutter_stock_verification'] = review['token']
    elif command == 'tool_save':
        if set(p) - TOOL_FIELDS: raise ValueError('Unsupported cutter field.')
        station = p.get('station')
        if type(station) is not int or not 1 <= station <= 12: raise ValueError('Choose a station from 1 to 12.')
        old = project.tool_library.by_station(station)
        tool = copy.deepcopy(old) if old else Tool(station, '', 0)
        if tool.kind not in (ToolKind.END_MILL,ToolKind.DRILL): raise ValueError('Only end mills and drills can be edited here.')
        for key in TOOL_FIELDS:
            if key in p: setattr(tool, key, ToolKind(p[key]) if key == 'kind' else p[key])
        if tool.kind not in (ToolKind.END_MILL,ToolKind.DRILL): raise ValueError('Choose end_mill or drill.')
        if not tool.name.strip(): raise ValueError('Enter a cutter name.')
        for key in ('diameter','feed_rate','plunge_rate','max_stepdown','cutting_edge_length','spindle_rpm'):
            if getattr(tool,key) is None or getattr(tool,key) <= 0: raise ValueError(f'{key} must be positive.')
        if type(tool.spindle_rpm) is not int: raise ValueError('RPM must be a whole number.')
        from manufacturing_tools import validate_turret
        setup=project.metadata.get('job_setup',{})
        validate_turret([tool],setup.get('machine_min_rpm',8000),setup.get('machine_max_rpm',24000))
        project.tool_library.tools = [t for t in project.tool_library.tools if t.station != station] + [tool]
        issues = project.tool_library.validate()
        errors = [i.message for i in issues if i.severity == 'error']
        if errors: raise ValueError('; '.join(errors))
    elif command == 'tool_delete':
        station = p['station']
        if any(o.tool_station == station for o in project.operations): raise ValueError('This cutter is used by an operation.')
        if not project.tool_library.by_station(station): raise ValueError('Cutter no longer exists.')
        project.tool_library.tools = [t for t in project.tool_library.tools if t.station != station]
    elif command == 'special_save':
        if not reviewed_draft: raise ValueError('Specialized machining requires a reviewed motion draft.')
        from manufacturing_special import prepare_operation
        prepare_operation(project,p)
    elif command in {'profile_save','drill_save','fill_save'}:
        fields = {'profile_save':PROFILE_FIELDS | (PROFILE_ADVANCED if reviewed_draft else set()),'drill_save':DRILL_FIELDS | (DRILL_ADVANCED if reviewed_draft else set()),'fill_save':FILL_FIELDS | (FILL_ADVANCED if reviewed_draft else set())}[command]
        if set(p) - fields - {'id'}: raise ValueError('Unsupported profile field.')
        opid = p.get('id')
        old = next((o for o in project.operations if o.id == opid), None)
        if opid and not old: raise ValueError('Operation no longer exists.')
        expected = {'profile_save':RouterOffsetOperation,'drill_save':DrillCirclesOperation,'fill_save':IslandFillOperation}[command]
        if old and (type(old) is not expected or old.profile_retention): raise ValueError('Edit a matching operation or the complete linked group.')
        if old and reviewed_draft and not old.enabled: raise ValueError('Enable this operation before reviewing an edited motion draft.')
        from operation_recipe import attached
        if old and attached(old) and not reviewed_draft: raise ValueError('Recipe-bearing operations require a reviewed motion draft.')
        if old and not reviewed_draft and not fields_match(old, fields): raise ValueError('Edit these settings with a reviewed operation draft.')
        ids = p.get('entity_ids', [])
        entities = {e.id:e for e in project.entities}
        layers = _automation_layer_settings(project)
        if not ids or len(set(ids)) != len(ids): raise ValueError('Choose contours without duplicates.')
        opened = []
        for eid in ids + p.get('island_entity_ids',[]):
            e = entities.get(eid)
            is_open = isinstance(e,Line) or isinstance(e,Polyline) and not e.closed
            opened.append(is_open)
            if not isinstance(e,(Circle,Rectangle,Polyline,Line)) or is_open and command != 'profile_save':
                raise ValueError('Choose a supported closed contour, or use Cut Line for lines and open polylines.')
            setting = layers.get(e.layer,{})
            if setting.get('locked') or setting.get('visible') is False or e.metadata.get('annotation_hidden'):
                raise ValueError('Choose visible, unlocked contours.')
        tool = project.tool_library.by_station(p.get('tool_station'))
        kinds = (ToolKind.END_MILL,ToolKind.DRILL) if command == 'drill_save' else (ToolKind.END_MILL,)
        if tool is None or tool.kind not in kinds: raise ValueError('Choose a configured compatible cutter.')
        params = {PARAM_ALIASES.get(k,k):v for k,v in p.items() if k in fields}
        if command == 'profile_save':
            if any(opened) and (not all(opened) or params.get('side') not in {'left','right','on'}):
                raise ValueError('Cut Line requires only open lines/polylines and left, right or on compensation.')
            if not any(opened) and params.get('side') not in {'inside','outside','on'}:
                raise ValueError('Closed profiles require inside, outside or on-contour cutting.')
            intent = params.get('cut_intent','custom')
            thickness = project.metadata.get('job_setup',{}).get('material_thickness',0)
            if intent == 'through': params['target_depth'] = through_depth(thickness,params.get('breakthrough',0))
            if intent == 'partial' and (thickness <= 0 or params.get('target_depth',0) >= thickness):
                raise ValueError('Partial depth must be less than a positive stock thickness.')
            for key in ('onion_skin','tab_count','tab_width','tab_height','corner_tab_offset','tab_spacing','female_tab_min_perimeter'):
                if key in params and params[key] < 0: raise ValueError(f'{key} cannot be negative.')
            if params.get('tab_mode','even') not in TAB_MODES:
                raise ValueError('Choose a supported tab mode.')
            positions = params.get('manual_tab_positions',[])
            if not isinstance(positions,list) or any(type(v) not in (int,float) or not math.isfinite(v) or not 0 <= v < 1 for v in positions) or len(set(positions)) != len(positions):
                raise ValueError('Manual tab positions must be unique fractions from 0 up to, but not including, 1.')
            if params.get('tab_mode') == 'manual' and not positions: raise ValueError('Enter at least one manual tab center.')
            if params.get('start_index',0) < 0: raise ValueError('Start index must be nonnegative.')
        if command == 'drill_save' and p.get('peck_depth') is not None and p['peck_depth'] < 0: raise ValueError('Peck depth cannot be negative.')
        if command == 'fill_save':
            params['boundary_ids'] = params.pop('entity_ids')
            params['island_ids'] = params.pop('island_entity_ids',[])
            if set(params['boundary_ids']) & set(params['island_ids']): raise ValueError('A contour cannot be both boundary and island.')
            if len(set(params['island_ids'])) != len(params['island_ids']): raise ValueError('Duplicate islands.')
            params['corner_tags'] = params.get('corner_tags',getattr(old,'corner_tags',False))
            fill_intent={k:params.pop(k,getattr(old,k,default)) for k,default in [('cut_intent','custom'),('breakthrough',0)]}
            thickness=project.metadata.get('job_setup',{}).get('material_thickness',0)
            if fill_intent['cut_intent']=='through':
                params['target_depth']=through_depth(thickness,fill_intent['breakthrough'])
            if fill_intent['cut_intent']=='partial' and (thickness<=0 or params.get('target_depth',0)>=thickness):
                raise ValueError('Partial cleanout depth must be less than a positive stock thickness.')
        for key in ('remove_tabs_after','finishing_pass','bottom_finish_pass','keep_tool_down','sliver_cleanup','controlled_retract','return_to_surface','corner_tags'):
            if key in params and type(params[key]) is not bool: raise ValueError('Choose a valid on/off setting.')
        if 'pass_depths' in params and (not isinstance(params['pass_depths'],list) or any(type(v) not in (int,float) or v <= 0 for v in params['pass_depths'])): raise ValueError('Pass increments must be a list of positive numbers.')
        if 'small_center_cleanout_ids' in params:
            ids=params['small_center_cleanout_ids']
            if not isinstance(ids,list) or not all(isinstance(v,str) for v in ids) or len(set(ids))!=len(ids):
                raise ValueError('Choose unique small-center contours; use an empty list to turn off.')
        for key in ('start_index','pass_count','helix_finish_revolutions','peck_count','tab_count','spindle_rpm','finish_spindle_rpm'):
            if params.get(key) is not None and type(params[key]) is not int: raise ValueError(f'{key} must be a whole number.')
        if command=='profile_save':
            # The pinned router CLI predates these model fields. Preserve omitted
            # values on edits and assign them before any motion is generated.
            helix={k:params.pop(k,getattr(old,k,0) if old else 0) for k in PROFILE_HELIX_FIELDS}
        centers=params.pop('small_center_cleanout_ids',None) if command=='profile_save' else None
        execute(candidate, {'profile_save':'operation.add_router_offset','drill_save':'operation.add_drill_circles','fill_save':'operation.add_island_fill'}[command],params)
        added = project.operations.pop()
        # The existing Tkinter editor assigns this model field directly too.
        if command == 'profile_save':
            added.start_index = params.get('start_index',0)
            for key,value in helix.items():setattr(added,key,value)
            validate_profile_helix(added)
        if command == 'profile_save' and any(opened) and (not fields_match(added,PROFILE_OPEN_FIELDS) or added.entry_mode not in {'plunge','ramp'}):
            raise ValueError('Cut Line supports plunge/ramp, leads, allowance and depth passes; closed-profile holding/finishing settings cannot be applied.')
        if command=='fill_save':
            for key,value in fill_intent.items():setattr(added,key,value)
            from cam_core.depth_intent import resolve_fill_depth
            added=resolve_fill_depth(project,added)
        if old:
            for key in PRESERVED_FIELDS:
                setattr(added,key,copy.deepcopy(getattr(old,key)))
            if type(added) is RouterOffsetOperation:
                for key in ('entity_sides','manual_contour_order','tab_release_plan','small_center_cleanout_ids'):
                    if key not in p: setattr(added,key,copy.deepcopy(getattr(old,key)))
            from operation_recipe import attached, FIELDS
            if attached(old):
                for key in FIELDS: setattr(added,key,copy.deepcopy(getattr(old,key)))
                if any(getattr(added,k)!=getattr(old,k) for k in (FILL_FIELDS | FILL_ADVANCED if type(old) is IslandFillOperation else PROFILE_FIELDS | PROFILE_ADVANCED)):
                    added.recipe_metadata['qualification']='prototype'
                    added.recipe_metadata['_flutter_attachment']['invalidated']='Operation settings changed; attach a current recipe again.'
            added.id = old.id
            added.enabled = old.enabled
            project.operations[project.operations.index(old)] = added
        else: project.operations.append(added)
        if command=='profile_save' and centers is not None: added.small_center_cleanout_ids=centers
        if not supported(project): raise ValueError('Operation uses settings outside the supported editor.')
        from manufacturing_management import validate_targets
        validate_targets(project)
    elif command in {'operation_delete','operation_toggle'}:
        op = next((o for o in project.operations if o.id == p.get('id')),None)
        if op is None: raise ValueError('Operation no longer exists.')
        members = [o for o in project.operations if o.profile_retention.get('group_id') == op.profile_retention.get('group_id')] if op.profile_retention else [op]
        ids = {o.id for o in members}
        if command == 'operation_delete':
            if any(set(o.depends_on_ids) & ids for o in project.operations if o.id not in ids): raise ValueError('Another operation depends on this group.')
            project.operations = [o for o in project.operations if o.id not in ids]
            project.metadata.get('flutter_manufacturing_groups',{}).pop(op.profile_retention.get('group_id'),None)
        else:
            enabled = not op.enabled
            for member in members: member.enabled = enabled
    else: raise ValueError('Unknown Manufacturing edit.')
    from cam_core.patchwork_holes import refresh_linked_holes
    refresh_linked_holes(project, _project_plates(project))
    changed = project.to_dict() != session.project.to_dict()
    if command == 'open_path_assign':
        # Python equates True with 1. Repairing malformed imported ownership must
        # still replace its representation and create an undoable transaction.
        changed = json.dumps(project.to_dict(),sort_keys=True) != json.dumps(session.project.to_dict(),sort_keys=True)
    if changed:
        session.project = project
        session._record_history()

def preflight_bundle(project, progress):
    setup = project.metadata.get('job_setup',{})
    for op in project.operations:
        if type(op) is not RouterOffsetOperation or not op.enabled or op.profile_retention: continue
        thickness = setup.get('material_thickness',0)
        if op.cut_intent == 'through' and not math.isclose(op.target_depth,through_depth(thickness,op.breakthrough),rel_tol=1e-9,abs_tol=1e-9):
            raise ValueError(f'{op.name}: stock thickness changed. Reopen and review the through-depth profile draft.')
        if op.cut_intent == 'partial' and (thickness <= 0 or op.target_depth >= thickness):
            raise ValueError(f'{op.name}: partial depth must remain below stock thickness. Review the profile draft.')
    paths = {}
    issues = run_preflight(project, _project_plates(project),
        material=setup.get('material',''), material_thickness=setup.get('material_thickness'),
        small_part_max_area=setup.get('small_part_max_area'), small_part_max_span=setup.get('small_part_max_span'),
        generated_paths_out=paths,progress_callback=progress)
    validation = dict(issues=[asdict(i) for i in issues],blocking=any(i.severity=='error' for i in issues))
    expected = {o.id for o in project.operations if o.enabled}
    if not validation['blocking'] and set(paths) != expected:
        raise ValueError('Preflight did not generate every enabled operation.')
    # Preflight audits optimized clearance but deliberately returns original paths.
    # Preview/export must use the same core transformation as stock.generate_operation_toolpath.
    if not validation['blocking'] and cycle_optimization_settings(project).enabled:
        paths = {o.id:optimize_generated_toolpath(project,o,paths[o.id]) for o in project.operations if o.enabled}
    from manufacturing_keepouts import fixture_issues
    validation['issues'].extend(fixture_issues(project, paths))
    validation['blocking'] = any(i['severity']=='error' for i in validation['issues'])
    from manufacturing_clearance import findings
    validation['knifeFindings']=findings(project,paths,progress)
    return paths,validation

def requires_knife_review(project):
    return any(o.enabled and o.kind=='drag_knife' and o.edge_aware_corners for o in project.operations)

def requires_release_review(project):
    from cam_core.model import TabReleaseOperation
    return any(o.enabled and (isinstance(o,TabReleaseOperation) or getattr(o,'remove_tabs_after',False)) for o in project.operations)

def optimization_worker(project, params, request_id, queue, cancel_event):
    def progress(stage):
        if cancel_event.is_set(): raise InterruptedError('Optimization canceled.')
        queue.put(('progress',request_id,stage))
    try:
        if not supported(project): raise ValueError('Unsupported machining is protected.')
        if not any(o.enabled for o in project.operations): raise ValueError('Add and enable an operation first.')
        if set(params) - OPTIMIZATION_FIELDS: raise ValueError('Unsupported optimization setting.')
        candidate = AutomationSession(project=copy.deepcopy(project))
        current = asdict(cycle_optimization_settings(project)) if project.metadata.get('cycle_optimization') else production_cycle_optimization_settings(project.units)
        values = {**current,**params,'minimum_retract':False,'arc_fitting':False}
        execute(candidate,'cycle.settings',values)
        if not supported(candidate.project): raise ValueError('Unsupported optimization settings.')
        baseline = copy.deepcopy(candidate.project)
        baseline.metadata['cycle_optimization']['enabled'] = False
        progress('Checking original motion')
        original,original_validation = preflight_bundle(baseline,progress)
        progress('Checking requested motion')
        optimized,validation = preflight_bundle(candidate.project,progress)
        errors = [i['message'] for v in (original_validation,validation) for i in v['issues'] if i['severity']=='error']
        if errors: raise ValueError(' '.join(errors))
        ops = ordered_operations([o for o in candidate.project.operations if o.enabled])
        settings = cycle_optimization_settings(candidate.project)
        comparison = CycleComparison(estimate_cycle_metrics(baseline,[(o,original[o.id]) for o in ops],settings),
            estimate_cycle_metrics(candidate.project,[(o,optimized[o.id]) for o in ops],settings)).to_dict()
        motions = serialize_motion(candidate.project,optimized)
        baseline_motions = serialize_motion(baseline,original)
        if sum(map(len,motions))+sum(map(len,baseline_motions)) > 150000: raise ValueError('Comparison exceeds the 150,000-motion preview limit.')
        queue.put(('optimization_done',request_id,candidate.project,dict(settings=asdict(settings),comparison=comparison,
            motions=motions,baselineMotions=baseline_motions,geometry=[e.to_dict() for e in project.entities],keepouts=keepout_display(project)[0],
            motionCount=sum(map(len,motions)),validation=validation,baselineValidation=original_validation,
            requiresKnifeReview=requires_knife_review(candidate.project),requiresReleaseReview=requires_release_review(candidate.project))))
    except InterruptedError: queue.put(('canceled',request_id))
    except Exception as exc: queue.put(('failed',request_id,str(exc)))

def serialize_motion(project, bundle):
    motions = []
    for op in ordered_operations([o for o in project.operations if o.enabled]):
        for block in bundle[op.id].blocks:
            motions.append([dict(kind=m.kind.value,x=m.end.x,y=m.end.y,z=m.end.z,
                center=asdict(m.center) if m.center else None,feed=m.feed) for m in block.motions])
    return motions

def generate_worker(prepared, request_id, queue, cancel_event):
    """Every selected plate is checked in one immutable, killable worker."""
    def progress(stage):
        if cancel_event.is_set(): raise InterruptedError('Generation canceled.')
        queue.put(('progress',request_id,stage))
    try:
        bundles, issues, previews = {}, [], []
        for item in prepared:
            progress(f'Plate {item.plate.number}')
            project = item.result.project
            paths,validation = preflight_bundle(project,progress)
            bundles[item.plate.number] = paths
            issues.extend(dict(i,plate=item.plate.number) for i in validation['issues'])
            motions = [] if validation['blocking'] else serialize_motion(project,paths)
            previews.append(dict(plate=item.plate.to_dict(),motions=motions,
                knifeFindings=[dict(f,plate_number=item.plate.number,frame='Plate-local') for f in validation.get('knifeFindings',[])],
                geometry=[e.to_dict() for e in project.entities],keepouts=keepout_display(project)[0],motionCount=sum(map(len,motions))))
        if sum(p['motionCount'] for p in previews)>150000: raise ValueError('Preview exceeds the current 150,000-motion limit.')
        progress('Complete')
        queue.put(('done',request_id,bundles,dict(issues=issues,blocking=any(i['severity']=='error' for i in issues)),previews))
    except InterruptedError: queue.put(('canceled',request_id))
    except Exception as exc: queue.put(('failed',request_id,str(exc)))

def group_worker(project, params, request_id, queue, cancel_event):
    from cam_core.calculation_cache import CalculationCache, calculation_session
    with calculation_session(CalculationCache()):
        _group_worker(project,params,request_id,queue,cancel_event)

def _group_worker(project, params, request_id, queue, cancel_event):
    from cam_core.profile_group_edit import resolve_profile_group_edit, rebuild_profile_group
    from cam_core.profile_second_tool import build_profile_group
    def progress(stage):
        if cancel_event.is_set(): raise InterruptedError('Group planning canceled.')
        queue.put(('progress',request_id,stage))
    try:
        if not supported(project): raise ValueError('Unsupported machining is protected.')
        selected = next((o for o in project.operations if o.id == params.get('id')),None)
        if selected is None: raise ValueError('Select an existing basic profile or retained-skin group.')
        from operation_recipe import attached
        if attached(selected): raise ValueError('Recipe-bearing profiles require complete-group requalification before adding a second tool.')
        settings = params.get('settings',{})
        strategy = settings.get('strategy','retained_skin')
        if strategy not in STRATEGIES: raise ValueError('Choose a supported second-tool workflow.')
        allowed = {'station','feed_rate','plunge_rate','spindle_rpm','stepdown','safe_z','skin','ignore_stock_less_than'}
        extra = {'strategy'} | ({'retain_tabs'} if strategy == 'Tabbed profile + rest / leave X tabs' else {'main_stepover','stepover'} if strategy == 'Clear waste + tabbed finish' else set())
        if not allowed <= set(settings) or set(settings)-allowed-extra: raise ValueError('Complete the settings for this second-tool strategy.')
        settings = dict(settings,reviewed=True) # Private planning only; Apply separately requires actual user review.
        source = resolve_profile_group_edit(project,selected).source if selected.profile_retention else copy.deepcopy(selected)
        if not fields_match(source,GROUP_SOURCE_FIELDS): raise ValueError('Group conversion requires plunge entry and no separate finishing pass. Edit those settings first.')
        if source.side not in {OffsetSide.INSIDE,OffsetSide.OUTSIDE}: raise ValueError('Second-tool groups require closed inside/outside profiles.')
        if params.get('recipe_tool') is not None:
            from cam_core.second_tool_recipe import matches_tool
            from cam_core.units import unit_scale
            tool = project.tool_library.by_station(settings['station'])
            if tool is None or not matches_tool(tool,params['recipe_tool'],unit_scale('in',project.units)):
                raise ValueError('Recipe cutter changed. Load the recipe and choose its cutter again.')
        source_changes = params.get('source',{})
        if set(source_changes) - (GROUP_SOURCE_FIELDS - {'entity_ids'}): raise ValueError('Unsupported primary profile input.')
        if source_changes:
            trial = AutomationSession(project=copy.deepcopy(project))
            execute(trial,'operation.add_router_offset',dict({k:source.to_dict()[k] for k in GROUP_SOURCE_FIELDS},**source_changes))
            changed = trial.project.operations[-1]
            for key in PRESERVED_FIELDS: setattr(changed,key,copy.deepcopy(getattr(source,key)))
            changed.id = source.id
            if not fields_match(changed,GROUP_SOURCE_FIELDS): raise ValueError('Use supported closed primary profile settings.')
            tool = project.tool_library.by_station(changed.tool_station)
            if tool is None or tool.kind != ToolKind.END_MILL: raise ValueError('Choose a loaded primary end mill.')
            source = changed
        if not fields_match(source,GROUP_SOURCE_FIELDS): raise ValueError('Group conversion requires plunge entry and no separate finishing pass. Edit those settings first.')
        if source.side not in {OffsetSide.INSIDE,OffsetSide.OUTSIDE}: raise ValueError('Second-tool groups require closed inside/outside profiles.')
        if strategy != 'retained_skin':
            if source.cut_intent != 'through': raise ValueError('Tabbed strategies require Through stock intent.')
            source.target_depth = project.metadata.get('job_setup',{}).get('material_thickness',0)+source.breakthrough
        if selected.profile_retention:
            rebuilt = rebuild_profile_group(project,selected,source,settings)
            candidate,members = rebuilt.project,rebuilt.operations
        else:
            if not basic_operation(selected) or type(selected) is not RouterOffsetOperation or not selected.enabled:
                raise ValueError('Choose an enabled basic inside/outside profile.')
            candidate = copy.deepcopy(project)
            index = candidate.operations.index(next(o for o in candidate.operations if o.id == source.id))
            candidate.operations.pop(index)
            members = build_profile_group(candidate,source,settings)
            candidate.operations[index:index] = members
        seal_group(candidate,members)
        if not supported(candidate): raise ValueError('Planned group is outside the supported editor.')
        paths,validation = preflight_bundle(candidate,progress)
        if validation['blocking']: raise ValueError(' '.join(i['message'] for i in validation['issues'] if i['severity']=='error'))
        motions = serialize_motion(candidate,paths)
        if sum(map(len,motions))>150000: raise ValueError('Draft exceeds the 150,000-motion preview limit.')
        progress('Review retained material and linked stages')
        queue.put(('group_done',request_id,candidate,dict(workflow=STRATEGIES[strategy],motions=motions,
            geometry=[e.to_dict() for e in candidate.entities],keepouts=keepout_display(candidate)[0],motionCount=sum(map(len,motions)),
            validation=validation, requiresReleaseReview=requires_release_review(candidate),stages=[o.to_dict() for o in members],skin=settings['skin'],
            thickness=candidate.metadata['job_setup']['material_thickness'])))
    except InterruptedError: queue.put(('canceled',request_id))
    except Exception as exc: queue.put(('failed',request_id,str(exc)))

def operation_block_review(blocks):
    review_keys = {'entry_mode','entry_mode_actual','entry_effective_start','entry_original_start','entry_rejection_reason','entry_adjustment_kind','entry_fallback_reason_code','role','entity_id','entity_ids','depth','finished_tab_width','raised_tab_span','offset_distance','side','loop_count','linked_count','plunge_count','common_line_length','common_line_lifts_saved','curve_feed_adjusted_motions','curve_feed_minimum','outside_corner_loops','corner_loops','edge_swivels','loop_radius','corner_feed_ipm','lead_in_feed_ipm'}
    block_review = [dict(label=b.label,details={k:v for k,v in b.metadata.items() if k in review_keys}) for b in blocks[:200]]
    return block_review


def operation_worker(project, params, request_id, queue, cancel_event):
    def progress(stage):
        if cancel_event.is_set(): raise InterruptedError('Operation planning canceled.')
        queue.put(('progress',request_id,stage))
    try:
        command = params.get('command')
        if command not in {'profile_save','fill_save','drill_save','special_save'}: raise ValueError('Choose a supported operation draft.')
        candidate = AutomationSession(project=copy.deepcopy(project))
        progress('Validating operation draft')
        mutate(candidate,command,params.get('params',{}),reviewed_draft=True)
        paths,validation = preflight_bundle(candidate.project,progress)
        if validation['blocking']:
            queue.put(('failed',request_id,' '.join(i['message'] for i in validation['issues'] if i['severity']=='error'),dict(knifeFindings=validation.get('knifeFindings',[]),retryOperation=params)))
            return
        motions = serialize_motion(candidate.project,paths)
        if sum(map(len,motions))>150000: raise ValueError('Draft exceeds the 150,000-motion preview limit.')
        operation = next((o for o in candidate.project.operations if o.id == params.get('params',{}).get('id')),candidate.project.operations[-1])
        from cam_core.toolpath import island_fill_depth_schedule, drill_peck_depths
        schedule = island_fill_depth_schedule(operation) if type(operation) is IslandFillOperation else None
        drill_schedule = drill_peck_depths(operation) if type(operation) is DrillCirclesOperation else None
        blocks = paths[operation.id].blocks
        block_review = operation_block_review(blocks)
        progress('Review operation settings and actual motion')
        queue.put(('operation_done',request_id,candidate.project,dict(knifeFindings=validation.get('knifeFindings',[]),operation=operation.to_dict(),schedule=schedule,drillSchedule=drill_schedule,blockReview=block_review,blockCount=len(blocks),requiresKnifeReview=requires_knife_review(candidate.project),requiresReleaseReview=requires_release_review(candidate.project),
            motions=motions,geometry=[e.to_dict() for e in candidate.project.entities],keepouts=keepout_display(candidate.project)[0],motionCount=sum(map(len,motions)),validation=validation)))
    except InterruptedError: queue.put(('canceled',request_id))
    except Exception as exc: queue.put(('failed',request_id,str(exc),dict(retryOperation=params)))


class Manufacturing:
    def __init__(self, source_id):
        self.source_id = source_id
        self.process = self.queue = self.event = None
        self.key = None
        self.bundle = None
        self.prepared = None
        self.result = dict(status='idle')
        self.token = None
        self.group_candidate = None
        self.bundles = None

    def close(self):
        if self.event: self.event.set()
        if self.process:
            if self.process.pid is not None:
                self.process.join(.15)
                if self.process.is_alive(): self.process.terminate(); self.process.join(1)
            self.process.close()
        if self.queue:
            self.queue.cancel_join_thread()
            self.queue.close()
        self.process = self.queue = self.event = None

    def invalidate(self):
        self.close()
        self.key = self.bundle = self.prepared = self.token = None
        self.result = dict(status='idle')
        self.group_candidate = self.bundles = None

    def apply_group(self, session, p, revision):
        if self.result.get('action') == 'release_audit':
            raise ValueError('This is an audit only. Qualify restraint and prepare the final release draft first.')
        if self.result.get('status') not in {'group_ready','operation_ready','optimization_ready','management_ready'} or self.group_candidate is None or p.get('token') != self.token or self.identity(session.project,revision) != self.key:
            raise ValueError('Prepare a current group draft before applying.')
        if p.get('reviewed') is not True: raise ValueError('Review the draft settings, retained material and motion before applying.')
        if requires_release_review(self.group_candidate) and p.get('release_reviewed') is not True:
            raise ValueError('Explicitly review tab-removal motion and the resulting release of parts.')
        if requires_knife_review(self.group_candidate) and p.get('knife_reviewed') is not True:
            raise ValueError('Review edge-aware knife fallback motion and verify corner separation on scrap before applying.')
        session.project = copy.deepcopy(self.group_candidate)
        session._record_history()

    def identity(self, project, revision):
        return (revision, output_generation_fingerprint(project,_project_plates(project),{'snapshot':self.source_id}))

    def command(self, command, p, project, revision):
        if command == 'closed_overruns':
            from manufacturing_overruns import review
            return review(project)
        if command == 'open_paths':
            from manufacturing_ownership import review
            return review(project)
        if sys.platform == 'win32':
            # Console Python workers can disconnect stdin of a Dart-launched
            # Windows bridge. Workers communicate through Queue, not a console.
            windowless = Path(sys.executable).with_name('pythonw.exe')
            if windowless.is_file(): mp.set_executable(str(windowless))
        if command == 'linked_info':
            from manufacturing_linked import info
            return info(project,p)
        if command == 'small_centers':
            from manufacturing_centers import info
            return info(project,p)
        if command == 'picking_info':
            from manufacturing_picking import info
            return info(project,p)
        if command == 'cancel':
            self.invalidate()
            self.result = dict(status='canceled')
            return self.result
        key = self.identity(project,revision)
        if self.key is not None and key != self.key: self.invalidate()
        if command in {'group_prepare','operation_prepare','optimization_prepare','management_prepare','attachment_prepare'}:
            self.invalidate()
            self.key,self.token = key,uuid4().hex
            self.result = dict(status='running',purpose='group' if command=='group_prepare' else 'operation')
            ctx = mp.get_context('spawn')
            self.queue,self.event = ctx.Queue(),ctx.Event()
            from operation_recipe import worker as attachment_worker
            from manufacturing_management import management_worker
            worker = {'attachment_prepare':attachment_worker,'management_prepare':management_worker,'group_prepare':group_worker,'operation_prepare':operation_worker,'optimization_prepare':optimization_worker}[command]
            self.process = ctx.Process(target=worker,args=(copy.deepcopy(project),p,self.token,self.queue,self.event),daemon=True)
            try: self.process.start()
            except Exception:
                self.invalidate()
                raise
        elif command == 'generate':
            self.invalidate()
            if not supported(project): raise ValueError('Advanced operations are protected.')
            if project.metadata.get('job_setup',{}).get('material_thickness',0)<=0:
                raise ValueError('Set a positive stock thickness before generating output.')
            all_plates = _project_plates(project)
            numbers = p.get('plates',[x.number for x in all_plates])
            if not isinstance(numbers,list) or not numbers or len(set(numbers)) != len(numbers) or any(type(n) is not int for n in numbers):
                raise ValueError('Choose output plates without duplicates.')
            plates = [x for x in all_plates if x.number in numbers]
            if len(plates) != len(numbers): raise ValueError('An output plate no longer exists.')
            if not any(o.enabled for o in project.operations): raise ValueError('Add and enable an operation first.')
            from manufacturing_ownership import capable,review
            if capable() and any(not row['assignmentValid'] for row in review(project)['paths']):
                raise ValueError('A saved open-path assignment is invalid. Review plate assignments before output.')
            # Inspect every plate first: selected output must not hide crossing or unassigned cuts.
            all_prepared = prepare_plate_outputs(copy.deepcopy(project),all_plates,{x.number:Path(f'preview-{x.number}.cnc') for x in all_plates})
            from manufacturing_keepouts import localize_all_keepouts
            localize_all_keepouts(project, all_prepared)
            if any(x.result.crossing_entity_ids for x in all_prepared): raise ValueError('Resolve crossing closed contours or ambiguous open-path plate assignments before output.')
            assigned = set().union(*({o.id for o in x.result.project.operations if o.enabled} for x in all_prepared))
            if assigned != {o.id for o in project.operations if o.enabled}: raise ValueError('Output has unassigned operations.')
            for original in (o for o in project.operations if o.enabled):
                local = [o for item in all_prepared for o in item.result.project.operations if o.id == original.id]
                for field in ('entity_ids','island_entity_ids'):
                    covered = set().union(*(set(getattr(o,field,[])) for o in local))
                    if covered != set(getattr(original,field,[])): raise ValueError('An operation includes unassigned contours or islands. Resolve them before output.')
            prepared = [x for x in all_prepared if x.plate.number in numbers]
            if any(not any(o.enabled for o in x.result.project.operations) for x in prepared): raise ValueError('An output plate has no enabled operations. Deselect empty plates.')
            from manufacturing_ownership import capable,overtravel
            self.result = dict(status='running',openPathOvertravel=(overtravel(project,numbers) if capable() else []) + __import__("manufacturing_overruns").overtravel(project,numbers))
            self.key, self.prepared = key, prepared
            self.token = uuid4().hex
            ctx = mp.get_context('spawn')
            self.queue, self.event = ctx.Queue(), ctx.Event()
            self.process = ctx.Process(target=generate_worker,
                args=(copy.deepcopy(prepared),self.token,self.queue,self.event),daemon=True)
            try: self.process.start()
            except Exception:
                self.invalidate()
                raise
        elif command == 'poll':
            if self.queue:
                while True:
                    try: message = self.queue.get_nowait()
                    except Empty: break
                    if message[1] != self.token: continue
                    if message[0] == 'progress': self.result['progress'] = list(message[2:])
                    elif message[0] in {'group_done','operation_done','optimization_done','management_done'}:
                        self.group_candidate = message[2]
                        self.result = dict(message[3],status={'management_done':'management_ready','group_done':'group_ready','operation_done':'operation_ready','optimization_done':'optimization_ready'}[message[0]],token=self.token)
                        self.result['requiresKnifeReview'] = requires_knife_review(self.group_candidate)
                        self.result['knifeFindings'] = self.result.get('knifeFindings',self.result.get('validation',{}).get('knifeFindings',[]))
                        self.close()
                        break
                    elif message[0] == 'done':
                        bundles,validation,previews = message[2:]
                        self.result['validation'] = validation
                        self.result['previews'] = previews
                        from cam_core.output_coordinates import toolpath_xy_bounds
                        self.result['outputRanges'] = [dict(plate=n, bounds=toolpath_xy_bounds(paths.values())) for n,paths in bundles.items()]
                        self.result['knifeFindings'] = [f for preview in previews for f in preview.get('knifeFindings',[])]
                        if validation['blocking']: self.result.update(status='blocked')
                        else:
                            self.bundles = bundles
                            self.bundle = next(iter(bundles.values())) if len(bundles)==1 else bundles
                            self.result.update({k:v for k,v in previews[0].items() if k!='knifeFindings'})
                            self.result.update(status='ready',token=self.token,plates=[p['plate']['number'] for p in previews],
                                requiresKnifeReview=any(requires_knife_review(item.result.project) for item in self.prepared),
                                requiresReleaseReview=any(requires_release_review(item.result.project) for item in self.prepared),
                                motionCount=sum(p['motionCount'] for p in previews))
                        self.close()
                        break
                    elif message[0] in {'failed','canceled'}:
                        self.result.update(status=message[0],error=message[2] if len(message)>2 else '')
                        if len(message)>3:self.result.update(message[3])
                        self.close()
                        break
                if self.process and not self.process.is_alive() and self.result['status']=='running':
                    # Queue feeder can finish immediately after process exit; poll once more before diagnosing.
                    self.result['exitedPolls'] = self.result.get('exitedPolls',0)+1
                    if self.result['exitedPolls']>3:
                        self.result.update(status='failed',error='Toolpath worker exited without a result.')
                        self.close()
        elif command == 'export':
            verification = stock_verification(project)
            if verification['required'] and not verification['verified']: raise ValueError('Verify the current physical stock before generating and exporting a reviewed package.')
            if not self.bundle or self.result['status']!='ready' or p.get('token')!=self.token or key!=self.key:
                raise ValueError('Generate and review current motion before exporting.')
            if p.get('reviewed') is not True: raise ValueError('Review the motion and preflight before exporting.')
            if self.result.get('openPathOvertravel') and p.get('overtravel_reviewed') is not True:
                raise ValueError('Review the full cuts beyond stock, machine travel and fixture clearance before export.')
            if self.result.get('requiresReleaseReview') and p.get('release_reviewed') is not True:
                raise ValueError('Explicitly review tab-removal motion and the resulting release of parts before export.')
            if self.result.get('requiresKnifeReview') and p.get('knife_reviewed') is not True:
                raise ValueError('Review edge-aware knife fallback motion and scrap corner separation before export.')
            name = p.get('name','')
            if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',name):
                raise ValueError('Use 1-64 letters, numbers, underscores or hyphens for the package name.')
            root = Path(__file__).resolve().parents[1]/'build'/'manufacturing-output'
            for folder in [root,*root.parents]:
                if folder.is_symlink() or getattr(folder,'is_junction',lambda:False)(): raise ValueError('Output cannot use linked directories.')
            destination = root/(name+'-'+uuid4().hex[:12])
            destination.mkdir(parents=True)
            plates = [x for x in _project_plates(project) if x.number in self.bundles]
            prepared = prepare_plate_outputs(copy.deepcopy(project),plates,repository_output_paths(destination,name,[x.number for x in plates]))
            from manufacturing_keepouts import localize_all_keepouts
            localize_all_keepouts(project, prepared)
            for item in prepared:
                groups = {}
                for op in item.result.project.operations:
                    if op.profile_retention: groups.setdefault(op.profile_retention['group_id'],[]).append(op)
                for members in groups.values(): seal_group(item.result.project,members)
            if type(p.get('allow_negative_xy',False)) is not bool: raise ValueError('Explicitly choose negative X/Y permission.')
            result = execute_plate_outputs(prepared,'CNC + CAM project',copy_to_origin=False,
                generated_paths_by_plate=self.bundles,allow_negative_xy=p.get('allow_negative_xy',False))
            return dict(status='exported',paths=[str(v) for v in (*result.controller_paths,*result.project_paths,result.manifest_path) if v])
        else: raise ValueError('Unknown Manufacturing request.')
        return self.result
