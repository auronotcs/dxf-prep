"""Read-only Guided Production readiness; never qualifies or approves output."""
import copy
import hashlib
import json
import math
from dataclasses import asdict
from cam_core.model import RouterOffsetOperation, IslandFillOperation, OffsetSide, ToolKind, Line, Polyline, effective_tool, inferred_sequence_role
from cam_core.router_parameters import RouterValidationContext, validate_router_parameters
from cam_core.workflow import recipe_matches, normalize_material
from cam_core.units import unit_scale
from cam_core.layout import drawing_bounds
from cam_core.depth_intent import through_depth
from manufacturing_support import stock_verification, supported
from manufacturing_tools import validate_turret
from catalog_matching import list_matches


def catalog_snapshot(library, project):
    a=library.command('catalog_list',{},project)
    b=library.command('catalog_list',{},project)
    if a!=b: raise ValueError('Tool library changed while reading. Refresh readiness.')
    return a


def signature(project, catalog, revision):
    return hashlib.sha256(json.dumps([project.to_dict(),catalog,revision],sort_keys=True,allow_nan=False).encode()).hexdigest()


def parameters(op, tool):
    p=op.to_dict()
    feed=tool.feed_rate;rpm=tool.spindle_rpm;plunge=tool.plunge_rate
    if type(op) is IslandFillOperation:
        p.update(station=tool.station,tool_name=tool.name,diameter=tool.diameter,feed_rate=feed,plunge_rate=plunge,spindle_rpm=rpm,stepdown=op.stepdown if op.stepdown is not None else tool.max_stepdown)
        return p
    p.update(station=tool.station,tool_name=tool.name,diameter=tool.diameter,feed_rate=feed,plunge_rate=plunge,spindle_rpm=rpm,
        stepdown=op.stepdown if op.stepdown is not None else tool.max_stepdown,
        finish_feed=op.finish_feed_override if op.finish_feed_override is not None else feed,
        finish_spindle_rpm=op.finish_spindle_rpm_override if op.finish_spindle_rpm_override is not None else rpm,
        helix_feed=op.helix_feed_override if op.helix_feed_override is not None else plunge,
        tabs_enabled=op.tab_count>0 or op.tab_mode!='even')
    return p


def operation_review(project, op, catalog, stock):
    row=dict(id=op.id,name=op.name,kind=op.kind,station=op.tool_station,enabled=op.enabled,role=inferred_sequence_role(op),
        status='blocked',findings=[],checks=[],candidates=[],recipe=copy.deepcopy(op.recipe_metadata),bounds=None)
    def check(key,passed,detail,action):
        row['checks'].append(dict(key=key,passed=bool(passed),detail=detail,action=action))
        if not passed:row['findings'].append(detail)
    if not op.enabled:
        row.update(status='disabled',findings=['Disabled operation; excluded from readiness totals.']);return row
    if type(op) not in (RouterOffsetOperation,IslandFillOperation):
        row.update(status='unsupported',findings=['This review covers ordinary profiles, Cut Lines and cleanouts. Specialized and dependent operations require their own review.']);return row
    if op.profile_retention or op.depends_on_ids or getattr(op,'tab_release_plan',None) or any(op.id in o.depends_on_ids for o in project.operations):
        row.update(status='unsupported',findings=['Linked retention/release or dependent operations require complete-group qualification.']);return row
    ids=op.entity_ids+getattr(op,'island_entity_ids',[])
    entities=[e for e in project.entities if e.id in ids]
    b=drawing_bounds(entities)
    if b:row['bounds']=list(b)
    check('stock',stock['verified'],'Physical stock is current.' if stock['verified'] else 'Verify the physical stock, material, thickness and plate setup.','stock')
    check('geometry',bool(entities) and len({e.id for e in entities})==len(set(ids)), 'Geometry references are complete.' if entities and len({e.id for e in entities})==len(set(ids)) else 'Operation geometry is missing or empty.','operation')
    tool=effective_tool(project,op)
    if tool is None:
        check('cutter',False,f'T{op.tool_station} has no loaded cutter.','turret');return row
    f=unit_scale('in',project.units);setup=project.metadata.get('job_setup',{})
    try:
        if tool.kind!=ToolKind.END_MILL or tool.spindle_mode=='disabled':raise ValueError('Use a spindle-enabled router end mill.')
        validate_turret([tool],setup.get('machine_min_rpm',8000),setup.get('machine_max_rpm',24000))
        check('cutter',True,f'T{tool.station}: {tool.name}; effective operation overrides included.','turret')
    except (ValueError,TypeError) as e:check('cutter',False,str(e),'turret')
    row['effective']=dict(rpm=tool.spindle_rpm,feed=None if tool.feed_rate is None else tool.feed_rate*60/f,
        plunge=None if tool.plunge_rate is None else tool.plunge_rate*60/f,stepdown=None if op.stepdown is None and tool.max_stepdown is None else (op.stepdown if op.stepdown is not None else tool.max_stepdown)/f)
    recipe=op.recipe_metadata if isinstance(op.recipe_metadata,dict) else {}
    try:
        scope=recipe.get('qualification')=='production_approved' and recipe_matches(recipe,str(setup.get('material','Anything')),float(setup.get('material_thickness',0))/f,row['role'])
        check('qualification',scope,'Saved operation recipe matches the core qualification scope.' if scope else 'The operation needs an exact production-approved recipe for this stock and process role. Loading a station recipe alone does not attach it to the operation.','library')
        matches=list_matches(project,catalog['catalog'],catalog['version'],dict(station=tool.station,process_role=row['role']),operation=op)
        row['candidates']=matches['matches']
        identity=tool.metadata.get('catalog_tool_id')
        current=[]
        for entry in catalog['catalog']:
            if entry['id']!=identity:continue
            for index,r in enumerate(entry.get('recipes',[])):
                if r and all(recipe.get(k)==v for k,v in r.items()):
                    current.extend(c for c in row['candidates'] if c['id']==identity and c['recipe']==index and c['eligible'])
        check('catalog',bool(current),'Saved recipe matches a current compatible catalog record.' if current else 'Saved recipe or cutter identity does not match a current compatible library record; compare recipe scope and values.','library')
        differences=[]
        for key,value in [('rpm',tool.spindle_rpm),('feed',None if tool.feed_rate is None else tool.feed_rate*60/f),('plunge',None if tool.plunge_rate is None else tool.plunge_rate*60/f),('stepdown',(op.stepdown if op.stepdown is not None else tool.max_stepdown))]:
            if key=='stepdown' and value is not None:value/=f
            expected=recipe.get(key)
            if type(expected) not in (int,float) or type(value) not in (int,float) or not math.isclose(expected,value,rel_tol=1e-9,abs_tol=1e-9):differences.append(key)
        from router_recipe_presets import differences as preset_differences
        preset_changes=preset_differences(project,op,recipe)
        if recipe.get('cut_parameters') or recipe.get('fill_parameters'):check('preset',not preset_changes,'Stored cut settings match the operation.' if not preset_changes else 'Stored cut settings differ: '+', '.join(preset_changes)+'.','operation')
        check('values',not differences,'Effective feed, plunge, RPM and stepdown match the saved recipe.' if not differences else 'Effective settings differ from the saved recipe: '+', '.join(differences)+'.','operation')
        if type(op) is IslandFillOperation:
            from fill_recipe_presets import validate
            validate(project,op,tool)
            check('parameters',True,'Cleanout input/depth checks pass; entry and swept-area proofs require generated motion review.','operation')
            row['status']='ready' if all(c['passed'] for c in row['checks']) else 'blocked'
            return row
        through=op.cut_intent=='through' and math.isclose(op.target_depth,through_depth(float(setup.get('material_thickness',0)),op.breakthrough),rel_tol=1e-9,abs_tol=1e-9)
        check('intent',through,'Through-cut depth matches stock plus breakthrough.' if through else 'Guided through-cut depth must match stock thickness plus breakthrough.','operation')
        p=parameters(op,tool)
        context=RouterValidationContext(stock_thickness=float(setup.get('material_thickness',0)),project_units=project.units,
            open_contour=any(isinstance(e,Line) or isinstance(e,Polyline) and not e.closed for e in entities),outside_cut=op.side==OffsetSide.OUTSIDE,
            guided_mode=False,stock_verified=stock['verified'],job_material=str(setup.get('material','Anything')),
            job_thickness_in=float(setup.get('material_thickness',0))/f,process_role=row['role'],
            machine_min_rpm=setup.get('machine_min_rpm',8000),machine_max_rpm=setup.get('machine_max_rpm',24000),
            station_max_rpm=12000 if tool.station==6 else None,tool_min_rpm=tool.min_rpm,tool_max_rpm=tool.max_rpm)
        issue=validate_router_parameters(p,context)
        check('parameters',issue is None,issue.message if issue else 'Router parameter rules pass.','operation')
        guided=validate_router_parameters(p,RouterValidationContext(**dict(asdict(context),guided_mode=True)))
        if guided and guided.message not in row['findings']:check('guided',False,guided.message,'operation')
    except (ValueError,TypeError,KeyError,OverflowError) as e:check('record',False,f'Incomplete or invalid stock/cutter/recipe record: {e}','library')
    row['status']='ready' if row['checks'] and all(c['passed'] for c in row['checks']) else 'blocked'
    return row


def review(project,library,revision,p):
    if p:raise ValueError('Refresh readiness without extra inputs.')
    catalog=catalog_snapshot(library,project)
    try:stock=stock_verification(project)
    except (ValueError,TypeError,OverflowError):stock=dict(verified=False,eligible=False,units=project.units,plates=[],fingerprint={})
    rows=[operation_review(project,o,catalog,stock) for o in project.operations]
    counts={key:sum(r['status']==key for r in rows) for key in ('ready','blocked','unsupported','disabled')}
    return dict(token=signature(project,catalog,revision),revision=revision,units=project.units,stock=stock,operations=rows,counts=counts,
        editable=supported(project),catalog_version=catalog['version'],
        note='Readiness only. No operation, qualification, stock verification or output approval is created. Review generated motion, hold-down and output separately.')


def action(project,library,revision,p):
    if set(p)!={'token','operation_id','action'}:raise ValueError('Choose a current readiness action.')
    catalog=catalog_snapshot(library,project)
    if p['token']!=signature(project,catalog,revision):raise ValueError('Job, cutter, stock or library changed. Refresh readiness.')
    verb=p['action'];op=next((o for o in project.operations if o.id==p['operation_id']),None)
    if verb not in {'stock','verify','turret','library','operation','locate','compare','attach'}:raise ValueError('Unknown readiness action.')
    if verb in {'operation','locate','compare','attach'} and op is None:raise ValueError('Choose an existing operation.')
    if verb in {'stock','verify','turret','operation'} and not supported(project):raise ValueError('This job is protected; use its owning revision workflow.')
    if verb=='attach':
        from operation_recipe import source
        source(project,op.id)
    result=dict(action=verb,operation_id=op.id if op else None)
    if op:
        result.update(station=op.tool_station,role=inferred_sequence_role(op))
        if verb=='locate':
            b=drawing_bounds([e for e in project.entities if e.id in op.entity_ids]);result['bounds']=list(b) if b else None
        if verb=='compare':
            stock=stock_verification(project);result['operation']=operation_review(project,op,catalog,stock)
    return result
