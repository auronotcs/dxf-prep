"""Reviewed complete-group revisions; saved provenance never authorizes old motion."""
import copy
from pathlib import Path
from cam_core.automation import AutomationSession
from cam_core.model import Rectangle,Circle,Polyline,OPERATION_SEQUENCE_ROLES
from cam_core.profile_group_edit import (resolve_profile_group_edit,record_profile_group_inputs,
    _role,_validate_dependencies)
from cam_core.profile_second_tool import build_profile_group,retention_order_issues
from cam_core.workflow_features import selector_from_entities,resolve_geometry_selector
from manufacturing_picking import fingerprint,available

ACTIONS={'group_targets','group_rule','group_contours','group_sequence'}

def contexts(project):
    result=[];seen=set()
    for op in project.operations:
        gid=op.profile_retention.get('group_id')
        if gid and gid not in seen:
            seen.add(gid);result.append(resolve_profile_group_edit(project,op))
    return result

def targets(project,source):
    by_id={e.id:e for e in project.entities}
    ids=source.entity_ids
    if not ids or len(ids)!=len(set(ids)):raise ValueError('Choose at least one unique closed source contour.')
    for i in ids:
        e=by_id.get(i)
        if not isinstance(e,(Rectangle,Circle,Polyline)) or isinstance(e,Polyline) and not e.closed:
            raise ValueError('Linked profiles require closed rectangles, circles or polylines.')
        if not available(project,e):raise ValueError('Choose visible, unlocked source contours.')
        if any(e.metadata.get(k) for k in ('mounting_generated','bent_tab_generated','tab_slot_generated','layer_prep_generated')):
            raise ValueError('Generated mounting/repair geometry needs its owning workflow review.')
    if set(source.entity_sides)-set(ids):raise ValueError('Compensation references a removed contour; explicitly reset contour controls.')
    if any(v not in ('inside','outside') for v in source.entity_sides.values()):raise ValueError('Linked contours require inside or outside compensation.')
    order=source.manual_contour_order
    if order:
        if len(order)!=len(ids) or set(order)!=set(ids):raise ValueError('Cutting order must contain every source contour; explicitly reset it when membership changes.')
        from cam_core.toolpath import profile_containers
        containers=profile_containers([by_id[i] for i in ids]);positions={v:i for i,v in enumerate(order)}
        if source.cut_intent!='partial' and any(positions[c]>positions[p] for c,parents in containers.items() for p in parents):
            raise ValueError('Cut nested contours before their containing part.')

def info(project,p):
    from manufacturing_support import supported
    if set(p)!={'id'} or not supported(project):raise ValueError('Choose a verified complete linked workflow.')
    c=resolve_profile_group_edit(project,p['id']);members=[o for o in project.operations if o.id in c.member_ids]
    source=c.source.to_dict()
    source['depends_on_ids']=list(dict.fromkeys(i for o in members for i in o.depends_on_ids if i not in c.member_ids))
    return dict(status='linked_info',fingerprint=fingerprint(project),source=source,member_ids=list(c.member_ids),
        workflow=c.workflow,eligible_ids=[e.id for e in project.entities if available(project,e) and (isinstance(e,(Rectangle,Circle)) or isinstance(e,Polyline) and e.closed)])

def replan(job,context,source,progress,sequence=None,validate_support=True):
    """Build fresh stages, map only unambiguous roles, then re-seal actual inputs.

    Unlike core rebuild_profile_group (which intentionally keeps membership),
    this explicit revision allows new targets. No original edit record is
    rewritten to evade that core guard. Geometry-derived hybrid roles that
    disappear cannot take a referenced old ID.
    """
    from manufacturing_support import seal_group
    targets(job,source)
    old=[o for o in job.operations if o.id in context.member_ids];old_ids=set(context.member_ids)
    if len(old)!=len(old_ids):raise ValueError('Restore every linked stage before revision.')
    old_by_role={_role(o):o for o in old}
    if len(old_by_role)!=len(old):raise ValueError('Ambiguous linked stage roles.')
    # Independent rest/release contracts require their own qualification;
    # ordinary ordering prerequisites may keep references to surviving IDs.
    for o in job.operations:
        if o.id not in old_ids and getattr(o,'source_operation_id',None) in old_ids:
            raise ValueError(f'{o.name} uses this group as a machining source; revise its dependent workflow first.')
    if source.tab_release_plan:raise ValueError('A staged cleanup plan must be reviewed separately after changing sources.')
    if set(source.depends_on_ids)&old_ids:raise ValueError('A group cannot depend on one of its own stages.')
    insertion=min(i for i,o in enumerate(job.operations) if o.id in old_ids)
    others=[o for o in job.operations if o.id not in old_ids]
    if source.id in {o.id for o in others}:raise ValueError('Original source identity collides with another operation.')
    plan=copy.deepcopy(job);plan.operations=others
    settings=dict(context.settings,reviewed=True)
    progress('Regenerating complete linked workflow')
    group=build_profile_group(plan,copy.deepcopy(source),settings)
    mapping={o.id:old_by_role[_role(o)].id for o in group if _role(o) in old_by_role}
    planned_ids={mapping.get(o.id,o.id) for o in group}
    for member in group:
        prior=old_by_role.get(_role(member));member.id=mapping.get(member.id,member.id)
        member.depends_on_ids=[mapping.get(i,i) for i in member.depends_on_ids]
        if prior:
            member.depends_on_ids=list(dict.fromkeys([*member.depends_on_ids,*(i for i in prior.depends_on_ids if i not in old_ids)]))
            for key in ('sequence_role','sequence_priority','sequence_locked'):setattr(member,key,getattr(prior,key))
        if sequence is not None:
            member.depends_on_ids=list(dict.fromkeys([*(i for i in member.depends_on_ids if i in planned_ids),*source.depends_on_ids]))
            for key in ('sequence_role','sequence_priority','sequence_locked'):setattr(member,key,getattr(source,key))
        if hasattr(member,'source_operation_id'):member.source_operation_id=mapping.get(member.source_operation_id,member.source_operation_id)
        for key in ('main_template','small_template'):
            if key in member.profile_retention:
                template=member.profile_retention[key];template['id']=mapping.get(template['id'],template['id'])
                template['depends_on_ids']=[mapping.get(i,i) for i in template.get('depends_on_ids',[])]
    new_ids={o.id for o in group};removed=old_ids-new_ids
    if len(new_ids)!=len(group) or new_ids&{o.id for o in others}:raise ValueError('Duplicate replacement stage identities.')
    if any(i not in old_ids and i not in source.depends_on_ids for o in old if o.id in removed for i in o.depends_on_ids):
        raise ValueError('A disappearing stage has a prerequisite; review that dependency first.')
    if any((set(o.depends_on_ids)|{getattr(o,'source_operation_id',None)})&removed for o in others):
        raise ValueError('Another operation depends on a disappearing stage; its prerequisite must be reviewed first.')
    source.id=mapping.get(source.id,source.id)
    # Stable logical group identity, with all generated fingerprints recaptured.
    gid=old[0].profile_retention['group_id']
    for o in group:o.profile_retention['group_id']=gid
    record_profile_group_inputs(plan,source,settings,group)
    plan.operations[insertion:insertion]=group
    _validate_dependencies(plan)
    issues=retention_order_issues(plan) if validate_support else []
    if issues:raise ValueError(' '.join(i.message for i in issues))
    seal_group(plan,group)
    detail=dict(group_id=gid,workflow=context.workflow,name=source.name,before=context.source.entity_ids,after=source.entity_ids,
        kept_stage_ids=sorted(old_ids&new_ids),added_stage_ids=sorted(new_ids-old_ids),removed_stage_ids=sorted(removed),
        selector=source.geometry_selector,entity_sides=source.entity_sides,manual_contour_order=source.manual_contour_order,
        prerequisites=source.depends_on_ids,sequence_role=source.sequence_role,sequence_priority=source.sequence_priority,sequence_locked=source.sequence_locked)
    return plan,detail

def prepare(project,action,p,progress):
    from manufacturing_support import supported,execute
    if not supported(project):raise ValueError('Verify imported linked workflows first.')
    if action=='linked_revision':
        if set(p)!={'path'}:raise ValueError('Choose one revised DXF.')
        path=Path(p['path'])
        if path.suffix.lower()!='.dxf' or not path.is_file() or path.stat().st_size>64*1024*1024:raise ValueError('Choose an existing DXF of at most 64 MiB.')
        before=path.read_bytes();cs=contexts(project)
        if not cs:raise ValueError('This drawing has no linked groups.')
        group_ids={i for c in cs for i in c.member_ids}
        revision=AutomationSession(project=copy.deepcopy(project))
        revision.project.operations=[o for o in revision.project.operations if o.id not in group_ids]+[copy.deepcopy(c.source) for c in cs]
        report=execute(revision,'project.reimport_dxf',dict(path=str(path)))
        if path.read_bytes()!=before:raise ValueError('The DXF changed during capture. Retry after saving finishes.')
        changed={o.id:o for o in revision.project.operations}
        job=revision.project
        job.operations=[copy.deepcopy(o) if o.id in group_ids else changed[o.id] for o in project.operations]
        rows=[]
        for c in cs:
            source=changed[c.source.id]
            if not source.geometry_selector and set(source.entity_ids)!=set(c.source.entity_ids):raise ValueError('A linked source disappeared. Bind a geometry rule or explicitly replace targets before importing this revision.')
            # Never silently drop an explicit cutting sequence on revision.
            if c.source.manual_contour_order and source.manual_contour_order!=c.source.manual_contour_order:raise ValueError('Revision changed the explicit contour order. Reset/review group contour controls first.')
            job,row=replan(job,c,source,progress,validate_support=False);rows.append(row)
        issues=retention_order_issues(job)
        if issues:raise ValueError(' '.join(i.message for i in issues))
        return job,dict(linked_changes=rows,revision=report)
    if action not in ACTIONS:raise ValueError('Unknown linked workflow action.')
    if p.get('fingerprint')!=fingerprint(project):raise ValueError('The drawing or setup changed. Reopen the group editor.')
    c=resolve_profile_group_edit(project,p.get('id'));source=copy.deepcopy(c.source)
    common={'id','fingerprint'};sequence=None
    if action=='group_targets':
        if set(p)-common-{'entity_ids','reset_controls'}:raise ValueError('Unknown target setting.')
        ids=p.get('entity_ids')
        if not isinstance(ids,list) or not all(isinstance(i,str) for i in ids):raise ValueError('Choose source contours.')
        source.entity_ids=list(ids);source.geometry_selector={}
        if p.get('reset_controls') is True:source.entity_sides={};source.manual_contour_order=[]
    elif action=='group_rule':
        if set(p)-common-{'mode','reset_controls'}:raise ValueError('Unknown rule setting.')
        mode=p.get('mode')
        if mode=='bind':source.geometry_selector=selector_from_entities([e for e in project.entities if e.id in source.entity_ids])
        elif mode=='clear':source.geometry_selector={}
        elif mode!='refresh':raise ValueError('Choose bind, refresh or clear.')
        elif not source.geometry_selector:raise ValueError('Bind a group geometry rule first.')
        if source.geometry_selector:source.entity_ids=list(resolve_geometry_selector(project,source.geometry_selector).entity_ids)
        if p.get('reset_controls') is True:source.entity_sides={};source.manual_contour_order=[]
    elif action=='group_contours':
        if set(p)-common-{'entity_sides','manual_contour_order'}:raise ValueError('Unknown contour control.')
        if not isinstance(p.get('entity_sides'),dict) or not isinstance(p.get('manual_contour_order'),list):raise ValueError('Choose contour compensation and order.')
        source.entity_sides=copy.deepcopy(p['entity_sides']);source.manual_contour_order=list(p['manual_contour_order'])
    else:
        if set(p)-common-{'depends_on_ids','sequence_role','sequence_priority','sequence_locked'}:raise ValueError('Unknown sequence setting.')
        deps=p.get('depends_on_ids');priority=p.get('sequence_priority');role=p.get('sequence_role');locked=p.get('sequence_locked')
        if not isinstance(deps,list) or not all(isinstance(i,str) for i in deps) or len(deps)!=len(set(deps)) or set(deps)&set(c.member_ids):raise ValueError('Choose unique prerequisites outside this group.')
        if type(priority) is not int or not -999<=priority<=999 or role not in OPERATION_SEQUENCE_ROLES or type(locked) is not bool:raise ValueError('Choose a valid whole-group role, priority and lock.')
        source.depends_on_ids=list(deps);source.sequence_role=role;source.sequence_priority=priority;source.sequence_locked=locked;sequence=True
    if source.to_dict()==c.source.to_dict() and action!='group_sequence':raise ValueError('No group changes to review.')
    job,row=replan(copy.deepcopy(project),c,source,progress,sequence)
    return job,dict(linked_changes=[row])
