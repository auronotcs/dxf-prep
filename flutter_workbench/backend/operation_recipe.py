"""Explicit operation-only recipe choices; current catalog and motion review required."""
import copy
from cam_core.model import RouterOffsetOperation, IslandFillOperation, effective_tool, inferred_sequence_role
from cam_core.units import unit_scale
from guided_readiness import catalog_snapshot, signature, parameters, operation_review
from catalog_matching import list_matches
from manufacturing_support import supported, stock_verification
from router_recipe_presets import apply_preset, HOLDING, FIELDS as PRESET_FIELDS

FIELDS={'recipe_name','recipe_metadata','feed_rate_override','plunge_rate_override','spindle_rpm_override'}
SCHEMA='flutter-operation-recipe-v1'

def attached(op):
    return (type(op) in (RouterOffsetOperation,IslandFillOperation) and not op.depends_on_ids and not op.profile_retention and not getattr(op,'tab_release_plan',None)
        and isinstance(op.recipe_metadata,dict) and isinstance(op.recipe_metadata.get('_flutter_attachment'),dict)
        and op.recipe_metadata['_flutter_attachment'].get('schema')==SCHEMA)

def source(project,opid):
    op=next((o for o in project.operations if o.id==opid),None)
    if type(op) not in (RouterOffsetOperation,IslandFillOperation) or not op.enabled: raise ValueError('Choose an enabled ordinary profile, Cut Line or cleanout.')
    if op.depends_on_ids or op.profile_retention or getattr(op,'tab_release_plan',None) or getattr(op,'small_center_cleanout_ids',[]) or any(op.id in o.depends_on_ids for o in project.operations):
        raise ValueError('Linked, dependent and small-center operations require complete-group recipe review.')
    if not isinstance(op.recipe_metadata,dict):raise ValueError('Invalid saved recipe record.')
    probe=copy.deepcopy(project);target=next(o for o in probe.operations if o.id==op.id)
    target.recipe_name='';target.recipe_metadata={};target.feed_rate_override=target.plunge_rate_override=target.spindle_rpm_override=None
    if not supported(probe):raise ValueError('Other advanced job settings remain protected.')
    return op

def listing(project,library,revision,p):
    if set(p)!={'operation_id'}:raise ValueError('Choose one existing operation.')
    op=source(project,p['operation_id']);catalog=catalog_snapshot(library,project)
    # Matching is operation-scoped: the shared cutter and other operations never change.
    scope=copy.deepcopy(project);scope.operations=[copy.deepcopy(op)]
    matches=list_matches(scope,catalog['catalog'],catalog['version'],dict(station=op.tool_station,process_role=inferred_sequence_role(op)),operation=op)
    tool=project.tool_library.by_station(op.tool_station)
    for row in matches['matches']:
        row['warnings']=[('Existing operation overrides and its saved recipe will be replaced only after review; the shared station remains unchanged.' if w.startswith('Operation overrides or recipe records remain unchanged') else w) for w in row['warnings']]
        if row['id']!=tool.metadata.get('catalog_tool_id'):
            row['eligible']=False;row['reasons'].append('Load this exact catalog cutter identity in Turret Setup first.')
    return dict(token=signature(project,catalog,revision),operation_id=op.id,name=op.name,units=project.units,
        matches=matches['matches'],stock=stock_verification(project),saved_recipe=copy.deepcopy(op.recipe_metadata),
        note='Only this operation receives the saved speeds and any supported profile or cleanout settings. Review every change; holding changes need separate acknowledgment. The shared cutter stays unchanged. Qualification is never upgraded.')

def resolve(project,library,revision,p):
    if set(p)!={'operation_id','token','id','recipe'} or type(p['recipe']) is not int:raise ValueError('Choose an exact current recipe.')
    current=listing(project,library,revision,{'operation_id':p['operation_id']})
    if p['token']!=current['token']:raise ValueError('Job, cutter, stock or library changed. Refresh recipe choices.')
    if not current['stock']['verified']:raise ValueError('Verify current physical stock before attaching a recipe.')
    match=next((r for r in current['matches'] if r['id']==p['id'] and r['recipe']==p['recipe']),None)
    if not match or not match['eligible']:raise ValueError('This recipe is excluded for the current operation.')
    catalog=catalog_snapshot(library,project)
    if signature(project,catalog,revision)!=p['token']:raise ValueError('Library changed during recipe review. Refresh choices.')
    raw=next(r for r in catalog['catalog'] if r['id']==p['id'])['recipes'][p['recipe']]
    candidate=copy.deepcopy(project);old=copy.deepcopy(source(candidate,p['operation_id']));op=apply_preset(candidate,old,raw);candidate.operations[candidate.operations.index(next(o for o in candidate.operations if o.id==old.id))]=op;f=unit_scale('in',project.units)
    recipe=copy.deepcopy(raw)
    for key in ('thickness_min','thickness_max'):
        if recipe.get(key+'_in') is not None:recipe[key]=recipe[key+'_in']*f
        else:recipe.pop(key,None)
    prior=copy.deepcopy(old.recipe_metadata);prior.pop('_flutter_attachment',None)
    # Archive the immediate old record, without recursively accumulating attachment history.
    recipe['_flutter_attachment']=dict(schema=SCHEMA,catalog_id=p['id'],catalog_version=catalog['version'],recipe_index=p['recipe'],
        previous_recipe=prior,previous_recipe_name=old.recipe_name,previous_overrides={k:getattr(old,k) for k in FIELDS if k not in {'recipe_metadata','recipe_name'}})
    op.recipe_name=str(raw.get('material',''));op.recipe_metadata=recipe;op.spindle_rpm_override=raw['rpm'];op.feed_rate_override=raw['feed']*f/60
    op.plunge_rate_override=raw['plunge']*f/60;op.stepdown=raw['stepdown']*f
    from manufacturing_management import validate_targets
    validate_targets(candidate)
    if not supported(candidate):raise ValueError('This operation requires a specialized recipe editor.')
    details=operation_review(candidate,op,catalog,current['stock'])
    failed=[c['detail'] for c in details['checks'] if not c['passed'] and c['key'] in {'geometry','cutter','parameters','record'}]
    if failed:raise ValueError('; '.join(failed))
    before=parameters(old,effective_tool(project,old));after=parameters(op,effective_tool(candidate,op))
    keys=['feed_rate','plunge_rate','spindle_rpm','stepdown','entry_mode','entry_side','lead_in','lead_out','helix_diameter','helix_pitch','target_depth','cut_intent','breakthrough','safe_z','pass_depths','tab_mode','tab_count','tab_width','tab_height','remove_tabs_after','finishing_pass','finish_feed','finish_spindle_rpm','helix_feed']
    keys=list(dict.fromkeys(keys+sorted(PRESET_FIELDS-{'second_tool','tab_cleanup_retain'})))
    holding=HOLDING
    if type(op) is IslandFillOperation:
        from fill_recipe_presets import MODEL_FIELDS,HOLDING as FILL_HOLDING
        keys=['feed_rate','plunge_rate','spindle_rpm','stepdown']+sorted(MODEL_FIELDS)
        holding=FILL_HOLDING
    diff=[dict(field=k,before=before.get(k),after=after.get(k),changed=before.get(k)!=after.get(k)) for k in keys]
    review=dict(choice=copy.deepcopy(p),operation_id=op.id,operation_kind=op.kind,name=op.name,units=project.units,match=match,changes=diff,
        saved_recipe=copy.deepcopy(old.recipe_metadata),recipe=copy.deepcopy(recipe),readiness=details,
        requiresHoldingReview=any(c['changed'] and c['field'] in holding for c in diff),
        preset_fields=sorted(raw.get('fill_parameters' if type(op) is IslandFillOperation else 'cut_parameters',{})),
        note='Feeds are drawing units per second; dimensions use drawing units. Saved profile or cleanout settings are applied; absent fields retain current values. Through depth follows current verified stock plus breakthrough. Review holding and finish/helix overrides. This draft is not output approval.')
    return candidate,review

def worker(project,params,request_id,queue,cancel_event):
    from manufacturing_support import preflight_bundle,serialize_motion,keepout_display,requires_release_review,operation_block_review
    def progress(stage):
        if cancel_event.is_set():raise InterruptedError('Recipe motion review canceled.')
        queue.put(('progress',request_id,stage))
    try:
        candidate=params['candidate'];review=params['review'];progress('Generating operation-recipe motion')
        paths,validation=preflight_bundle(candidate,progress)
        if validation['blocking']:raise ValueError(' '.join(i['message'] for i in validation['issues'] if i['severity']=='error'))
        motions=serialize_motion(candidate,paths)
        if sum(map(len,motions))>150000:raise ValueError('Draft exceeds the 150,000-motion preview limit.')
        op=next(o for o in candidate.operations if o.id==review['operation_id'])
        progress('Review recipe changes and generated motion')
        queue.put(('operation_done',request_id,candidate,dict(operation=op.to_dict(),attachment=review,motions=motions,
            blockReview=operation_block_review(paths[op.id].blocks),blockCount=len(paths[op.id].blocks),
            geometry=[e.to_dict() for e in candidate.entities],keepouts=keepout_display(candidate)[0],motionCount=sum(map(len,motions)),
            validation=validation,requiresReleaseReview=requires_release_review(candidate))))
    except InterruptedError:queue.put(('canceled',request_id))
    except Exception as exc:queue.put(('failed',request_id,str(exc)))
