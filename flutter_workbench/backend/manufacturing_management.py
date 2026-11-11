"""Reviewed whole-document Manufacturing workflows using the isolated core."""
import copy
from pathlib import Path
from cam_core.automation import AutomationSession, _automation_layer_settings
from cam_core.model import RouterOffsetOperation, DrillCirclesOperation, IslandFillOperation, Circle, Line, Polyline, OPERATION_SEQUENCE_ROLES
from cam_core.workflow import ordered_operations


def validate_targets(project):
    entities={e.id:e for e in project.entities};layers=_automation_layer_settings(project)
    for op in project.operations:
        if not op.entity_ids or len(set(op.entity_ids))!=len(op.entity_ids): raise ValueError(f'{op.name}: choose unique source contours.')
        for eid in [*op.entity_ids,*getattr(op,'island_entity_ids',[])]:
            e=entities.get(eid)
            if e is None: raise ValueError(f'{op.name}: source geometry is missing.')
            layer=layers.get(e.layer,{})
            if layer.get('visible') is False or layer.get('locked') or e.metadata.get('annotation_hidden'):
                raise ValueError(f'{op.name}: source geometry must be visible and unlocked.')
            if isinstance(op,DrillCirclesOperation) and not isinstance(e,Circle): raise ValueError('Drilling rules must resolve to circles.')
            if isinstance(op,IslandFillOperation) and (isinstance(e,Line) or isinstance(e,Polyline) and not e.closed): raise ValueError('Cleanout rules require closed contours.')
        if isinstance(op,RouterOffsetOperation):
            if set(op.entity_sides)-set(op.entity_ids): raise ValueError('Compensation refers to a contour outside this operation.')
            if any(v not in ('inside','outside','on') for v in op.entity_sides.values()): raise ValueError('Choose inside, outside or on-contour compensation.')
            from manufacturing_centers import validate
            validate(project,op)
            order=op.manual_contour_order
            if order and (len(order)!=len(op.entity_ids) or set(order)!=set(op.entity_ids)):
                raise ValueError('Manual order must contain every source contour exactly once. Review it after geometry changes.')


def prepare(project,params,progress):
    from manufacturing_support import supported, execute, seal_group
    from cam_core.profile_group_edit import resolve_profile_group_edit, rebuild_profile_group
    action=params.get('action');p=params.get('params',{})
    candidate=AutomationSession(project=copy.deepcopy(project));job=candidate.project
    before={o.id:list(o.entity_ids) for o in project.operations}
    details={}
    if action=='import_groups':
        if p: raise ValueError('Group import uses saved source inputs, not replacement settings.')
        selected=[];seen=set()
        for op in job.operations:
            gid=op.profile_retention.get('group_id')
            if gid and gid not in seen: seen.add(gid);selected.append(op.id)
        if not selected: raise ValueError('This job has no linked profile groups to verify.')
        for identity in selected:
            progress('Rebuilding saved linked workflow')
            context=resolve_profile_group_edit(job,identity)
            settings=dict(context.settings,reviewed=True)
            result=rebuild_profile_group(job,identity,context.source,settings)
            job=result.project;candidate.project=job
            seal_group(job,result.operations)
        details['rebuilt_groups']=len(selected)
    else:
        if not supported(job): raise ValueError('Verify imported linked groups first; other unsupported operations remain protected.')
        op=next((o for o in job.operations if o.id==p.get('id')),None)
        if action in {'profile_controls','sequence','associate','retained_cleanup','keep_tabs'}:
            if op is None or op.profile_retention: raise ValueError('Choose an independent operation; linked groups retain their complete workflow contract.')
        if action.startswith('group_') or action=='linked_revision':
            from manufacturing_linked import prepare as prepare_linked
            job,linked_details=prepare_linked(job,action,p,progress)
            candidate.project=job;details.update(linked_details)
        elif action in {'release_audit','final_release'}:
            from manufacturing_special import release
            job,release_details=release(job,p,action=='final_release')
            candidate.project=job
            details['final_release']=release_details
        elif action=='small_centers':
            from manufacturing_centers import prepare as prepare_centers
            details['small_centers']=prepare_centers(job,p)
        elif action=='graphical':
            from manufacturing_picking import prepare as prepare_picking
            details['graphical']=prepare_picking(job,p)
        elif action=='keepouts':
            from manufacturing_keepouts import prepare_keepout
            details['keepout_change']=prepare_keepout(job,p)
        elif action=='turret':
            from manufacturing_tools import prepare_turret
            details.update(prepare_turret(job,p))
        elif action in {'retained_cleanup','keep_tabs'}:
            if type(op) is not RouterOffsetOperation: raise ValueError('Choose an outside tabbed profile.')
            if action=='keep_tabs':
                if set(p)!={'id'}: raise ValueError('Choose the profile whose original tabs should remain.')
                op.tab_release_plan={};op.remove_tabs_after=False
                details['retained_cleanup']='All original tabs retained; cleanup removed.'
            else:
                if set(p)!={'id','count','plate'} or type(p['count']) is not int or p['count']<1: raise ValueError('Retain at least one whole tab per part; full release requires its own review.')
                from cam_core.automation import _project_plates
                from cam_core.tab_release import CleanupReview
                plate=next((v for v in _project_plates(job) if v.number==p['plate']),None)
                if plate is None: raise ValueError('Choose the stock plate containing the complete operation.')
                op.tab_release_plan={};op.remove_tabs_after=True;op.tab_cleanup_order='end_of_operation'
                progress('Checking physical tab bridges and remaining support')
                review=CleanupReview(job,op,plate)
                plan=review.plan(review.propose(p['count']),p['count']);op.tab_release_plan=plan
                details['retained_cleanup']=dict(requested=p['count'],totals=plan['totals'],retained=plan['retained'],removed_spans=len(plan['remove']))
        elif action=='profile_controls':
            if set(p)-{'id','entity_sides','manual_contour_order'}: raise ValueError('Unknown contour control.')
            if type(op) is not RouterOffsetOperation: raise ValueError('Choose a closed profile.')
            entities={e.id:e for e in job.entities}
            if any(isinstance(entities[i],Line) or isinstance(entities[i],Polyline) and not entities[i].closed for i in op.entity_ids): raise ValueError('Cut Line keeps its source direction and compensation.')
            sides=p.get('entity_sides',{});order=p.get('manual_contour_order',[])
            if not isinstance(sides,dict) or not isinstance(order,list): raise ValueError('Invalid contour controls.')
            op.entity_sides=copy.deepcopy(sides);op.manual_contour_order=list(order)
            op.contour_order='inside_first'
            validate_targets(job)
            from cam_core.toolpath import profile_containers
            containers=profile_containers([entities[i] for i in op.entity_ids])
            if order and op.cut_intent!='partial':
                indices={v:i for i,v in enumerate(order)}
                if any(indices[child]>indices[parent] for child,parents in containers.items() for parent in parents):
                    raise ValueError('Cut nested contours before their containing part; reorder the requested cuts.')
            details.update(entity_sides=op.entity_sides,manual_contour_order=op.manual_contour_order)
        elif action=='sequence':
            if set(p)-{'id','sequence_role','sequence_priority','sequence_locked','depends_on_ids'}: raise ValueError('Unknown sequence setting.')
            priority=p.get('sequence_priority',0);role=p.get('sequence_role','automatic');locked=p.get('sequence_locked',False)
            deps=p.get('depends_on_ids',[])
            if type(priority) is not int or not -999<=priority<=999 or role not in OPERATION_SEQUENCE_ROLES or type(locked) is not bool:
                raise ValueError('Choose a valid role, whole priority (-999 to 999), and lock setting.')
            if not isinstance(deps,list) or not all(isinstance(v,str) for v in deps) or len(set(deps))!=len(deps): raise ValueError('Choose unique prerequisite operations.')
            execute(candidate,'operation.set_dependencies',dict(operation_id=op.id,depends_on_ids=deps))
            execute(candidate,'operation.set_sequence',dict(operation_ids=[op.id],sequence_role=role,sequence_priority=priority,sequence_locked=locked))
            details.update(role=role,priority=priority,locked=locked,prerequisites=deps)
        elif action=='associate':
            if set(p)-{'id','enabled'} or type(p.get('enabled')) is not bool: raise ValueError('Choose whether to bind a geometry rule.')
            if p['enabled']:
                if any(o.profile_retention for o in job.operations): raise ValueError('Associative refresh of linked groups requires a separate complete-group revision review.')
                execute(candidate,'operation.bind_selector',dict(operation_id=op.id))
            else: op.geometry_selector={}
            details['selector']=copy.deepcopy(op.geometry_selector)
        elif action=='revision':
            if set(p)!={'path'}: raise ValueError('Choose one revised DXF file.')
            path=Path(p['path'])
            if path.suffix.lower()!='.dxf' or not path.is_file() or path.stat().st_size>64*1024*1024: raise ValueError('Choose an existing DXF of at most 64 MiB.')
            if any(o.profile_retention for o in job.operations):
                from manufacturing_linked import prepare as prepare_linked
                job,linked_details=prepare_linked(job,'linked_revision',p,progress)
                candidate.project=job;details.update(linked_details)
            else:details['revision']=execute(candidate,'project.reimport_dxf',dict(path=str(path)))
        else: raise ValueError('Unknown Manufacturing workflow.')
    job=candidate.project
    if not supported(job): raise ValueError('The proposed job still contains unsupported machining settings.')
    validate_targets(job)
    details['geometry_changes']=[dict(id=o.id,name=o.name,before=before.get(o.id,[]),after=o.entity_ids,selector=o.geometry_selector) for o in job.operations if before.get(o.id)!=o.entity_ids or action=='associate' and o.id==p.get('id')]
    details['operation_order']=[dict(id=o.id,name=o.name,enabled=o.enabled,prerequisites=o.depends_on_ids) for o in ordered_operations(job.operations)]
    return job,details


def management_worker(project,params,request_id,queue,event):
    from manufacturing_support import preflight_bundle,serialize_motion,requires_release_review
    from manufacturing_keepouts import display
    def progress(stage):
        if event.is_set(): raise InterruptedError('Workflow canceled.')
        queue.put(('progress',request_id,stage))
    try:
        progress('Preparing Manufacturing workflow')
        candidate,details=prepare(project,params,progress)
        paths,validation=preflight_bundle(candidate,progress)
        if validation['blocking']: raise ValueError(' '.join(i['message'] for i in validation['issues'] if i['severity']=='error'))
        motions=serialize_motion(candidate,paths)
        if sum(map(len,motions))>150000: raise ValueError('Workflow exceeds the 150,000-motion preview limit.')
        progress('Review source geometry, ordering and regenerated motion')
        queue.put(('management_done',request_id,candidate,dict(action=params['action'],details=details,
            motions=motions,geometry=[e.to_dict() for e in candidate.entities],keepouts=display(candidate)[0],motionCount=sum(map(len,motions)),
            validation=validation,requiresReleaseReview=requires_release_review(candidate))))
    except InterruptedError: queue.put(('canceled',request_id))
    except Exception as exc: queue.put(('failed',request_id,str(exc)))
