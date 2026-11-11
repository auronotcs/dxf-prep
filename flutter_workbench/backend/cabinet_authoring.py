"""Revision-bound Cabinet Tabs review plans, never fabrication or CAM permission."""
import copy
import json
from dataclasses import asdict
from cam_core.model import Polyline, Point, new_id
from cam_core.mounting import discover_mounting_parts
from cam_core.cabinet_tab_mounting import (KEY, CabinetTabPlan, LetterPlane, source_fingerprint,
    with_applied_review, reregister_plan)
from manufacturing_picking import available


def editable(project):
    if project.operations or 'cabinet_fabrication_snapshot' in project.metadata or any(
        any(k.startswith('cabinet_fabrication') for k in e.metadata) for e in project.entities):
        return False
    try:
        plans=project.metadata.get(KEY,{})
        if not isinstance(plans,dict):return False
        for identity,value in plans.items():
            if CabinetTabPlan.from_dict(value).plan_id!=identity:return False
    except (KeyError,TypeError,ValueError,AttributeError):
        return False
    return True


def guard(project):
    if not editable(project):raise ValueError('Unrecognized plans, fabrication snapshots and machining jobs remain protected; edit their source assembly plan.')


class CabinetDraft:
    def __init__(self):self.context=None;self.cached=None;self.registered={}

    def begin(self,project,revision,digest,selection):
        guard(project);self.cached=None;self.registered={}
        token=new_id('cabinet-context');identity=new_id('cabinet-plan');candidates=[];warning=None
        try:
            parts=discover_mounting_parts([e for e in project.entities if not e.metadata.get('reference_only')],set(selection)) if selection else []
            byid={e.id:e for e in project.entities}
            for part in parts:
                source=byid[part.owner_entity_id]
                if not isinstance(source,Polyline):raise ValueError('Cabinet backs must be closed polylines; convert rectangles to contours first.')
                if any(not available(project,byid[i]) or byid[i].metadata.get('machinable') is False for i in part.member_ids):raise ValueError('Select visible unlocked backs and their complete member holes.')
                if any(any(k.startswith(('dr_pan','mounting','led_','cabinet_')) for k in byid[i].metadata) for i in part.member_ids):
                    raise ValueError('Use original back contours, not generated or registered copies.')
                x,y,_,_=part.bounds
                candidates.append(asdict(LetterPlane(part.owner_entity_id,part.member_ids,source_fingerprint(project,part.member_ids),x,y,Point(0,0),0,0)))
        except (ValueError,KeyError) as exc:warning=str(exc);candidates=[]
        self.context=dict(token=token,revision=revision,digest=digest,new_id=identity,candidates=candidates)
        return dict(token=token,new_id=identity,units=project.units,candidates=candidates,warning=warning,
            saved=copy.deepcopy(project.metadata.get(KEY,{})),defaults=dict(back_thickness=.25*(25.4 if project.units=='mm' else 1),
                front_skin_thickness=.063*(25.4 if project.units=='mm' else 1),width_clearance_total=0,thickness_clearance_total=0))

    def check(self,p,revision,digest):
        c=self.context
        if not c or p.get('context_token')!=c['token'] or c['revision']!=revision or c['digest']!=digest:
            raise ValueError('Document changed. Reopen Cabinet Tabs authoring.')

    def plan(self,project,p):
        plan=CabinetTabPlan.from_dict(p['plan'])
        saved=project.metadata.get(KEY,{}).get(plan.plan_id)
        if saved is None and plan.plan_id!=self.context['new_id']:raise ValueError('Choose a plan from this authoring session.')
        allowed={v['owner_entity_id']:v for v in self.context['candidates']}
        if saved:allowed.update({v['owner_entity_id']:v for v in saved['planes']})
        allowed.update(self.registered.get(plan.plan_id,{}))
        for plane in plan.planes:
            record=allowed.get(plane.owner_entity_id)
            if not record or tuple(record['member_ids'])!=plane.member_ids or record['source_sha256']!=plane.source_sha256:
                raise ValueError('Source ownership/fingerprint changed. Use explicit re-registration or reopen source discovery.')
            byid={e.id:e for e in project.entities}
            if any(i not in byid or not available(project,byid[i]) for i in plane.member_ids):
                raise ValueError('Every registered source must remain visible and unlocked.')
        return plan

    def reregister(self,project,p,revision,digest):
        self.cached=None;guard(project);self.check(p,revision,digest)
        if set(p)!={'context_token','plan','confirmed'} or p['confirmed'] is not True:
            raise ValueError('Explicitly confirm accepting current sources and reviewing every datum and plane.')
        plan=reregister_plan(project,self.plan(project,p))
        payload=plan.to_dict();self.registered[plan.plan_id]={v['owner_entity_id']:v for v in payload['planes']}
        return payload

    def preview(self,project,p,revision,digest):
        self.cached=None;guard(project);self.check(p,revision,digest)
        if set(p)!={'context_token','plan'}:raise ValueError('Unknown Cabinet Tabs preview setting.')
        plan=self.plan(project,p)
        candidate=with_applied_review(project,plan)
        candidate.metadata=json.loads(json.dumps(candidate.metadata,allow_nan=False))
        token=new_id('cabinet-review');self.cached=dict(token=token,revision=revision,digest=digest,project=candidate,plan=plan)
        return dict(token=token,units=project.units,plan=plan.to_dict(),
            geometry=[e.to_dict() for e in candidate.entities if e.metadata.get('cabinet_plan_id')==plan.plan_id],
            originals=[e.to_dict() for e in project.entities if e.id in {i for plane in plan.planes for i in plane.member_ids}],
            message='Review references only. Cabinet origins and inward direction are explicit physical registration choices; no fabrication or toolpath approval is implied.')

    def apply(self,session,p,revision,digest):
        guard(session.project);d=self.cached
        if set(p)!={'token','reviewed'} or p['reviewed'] is not True or not d or d['token']!=p['token'] or revision!=d['revision'] or digest!=d['digest']:
            raise ValueError('Review a current paired Cabinet Tabs proposal before applying.')
        session.project=copy.deepcopy(d['project']);session._record_history();self.cached=None
