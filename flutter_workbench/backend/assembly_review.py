"""Read-only registered assembly inspection and explicit machining selection."""
import copy
from cam_core.model import Circle
from cam_core.selection import is_machinable_entity
from cam_core.dr_pans import resolve_dr_pan_engraving
from manufacturing_ownership import token
from manufacturing_picking import available


def dr_pans(project):
    rows=[]
    for pan in project.entities:
        if not pan.metadata.get('dr_pan') or pan.metadata.get('reference_only'): continue
        group=pan.metadata.get('group_id')
        owners={pan.id,pan.metadata['dr_pan'].get('source',{}).get('id')}-{None}
        members=[e for e in project.entities if e.metadata.get('dr_pan_owner') in owners and e.metadata.get('group_id')==group]
        guides=[e for e in members if e.metadata.get('reference_only')]
        error=None;marks=[]
        try:
            if len(guides)!=4: raise ValueError('Four original bend guides are required to verify marks.')
            marks=resolve_dr_pan_engraving(project.entities,guides)
        except (ValueError,KeyError,TypeError) as exc: error=str(exc)
        holes=[e for e in members if isinstance(e,Circle) and e.metadata.get('dr_pan_hole') and not e.metadata.get('reference_only')]
        targets=dict(outline=[pan.id],marks=[e.id for e in marks],holes=[e.id for e in holes])
        rows.append(dict(id=pan.id,name=pan.metadata.get('name',pan.id),targets=targets,error=error,
            editable={k:bool(ids) and all(available(project,e) and is_machinable_entity(e) for e in project.entities if e.id in ids) for k,ids in targets.items()}))
    return dict(token=token(project),rows=rows,units=project.units)


def pan_selection(project,p):
    if set(p)!={'token','id','kind'}: raise ValueError('Choose a pan and a machining feature.')
    info=dr_pans(project)
    if p['token']!=info['token']: raise ValueError('Drawing changed. Refresh pan review.')
    row=next((r for r in info['rows'] if r['id']==p['id']),None)
    if row is None or not row['editable'].get(p['kind']):
        raise ValueError('No verified visible unlocked features. Missing marks must be authored in the pan workflow.')
    return set(row['targets'][p['kind']])


def cabinet(project):
    from cam_core.cabinet_tab_mounting import (KEY,STATE_KEY,CabinetTabPlan,validate_sources,source_fingerprint,
        review_entities,reconcile_reviews,_check_review_ownership)
    plans=project.metadata.get(KEY,{})
    if not isinstance(plans,dict): raise ValueError('Saved cabinet plans have an unsupported format.')
    rows=[]
    for identity,payload in plans.items():
        row=dict(id=identity,valid=False,planes=[],tabs=[],geometry=[],error=None)
        try:
            plan=CabinetTabPlan.from_dict(payload)
            if plan.plan_id!=identity: raise ValueError('Plan key and saved identity disagree.')
            row['planes']=[dict(owner=p.owner_entity_id,members=list(p.member_ids),saved=p.source_sha256,
                current=source_fingerprint(project,p.member_ids),origin=dict(x=p.plan_origin.x,y=p.plan_origin.y),angle=p.angle_degrees,setback=p.inward_setback) for p in plan.planes]
            row['tabs']=plan.to_dict()['tabs']
            validate_sources(project,plan);_check_review_ownership(project,identity)
            probe=copy.deepcopy(project)
            stale=reconcile_reviews(probe)
            if identity in stale: raise ValueError(probe.metadata[STATE_KEY][identity])
            row['geometry']=[e.to_dict() for e in review_entities(project,plan)]
            row['valid']=True
        except (ValueError,KeyError,TypeError,AttributeError) as exc:row['error']=str(exc)
        rows.append(row)
    return dict(token=token(project),units=project.units,rows=rows,readOnly=True)
