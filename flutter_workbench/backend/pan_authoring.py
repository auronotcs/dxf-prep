"""Private DR-pan geometry proposals using the pinned manufacturing engine."""
import copy
import math
from dataclasses import asdict
from cam_core.automation import _automation_layer_settings
from cam_core.model import Line, new_id
from cam_core.dr_pans import (DrPanSettings, DrPanHoleSettings, generate_dr_pan,
    dr_pan_bend_marks, DR_PAN_HOLE_LAYER, DR_PAN_MARK_LAYER)
from manufacturing_picking import available


def numeric(value,name):
    if type(value) not in (int,float) or not math.isfinite(value):
        raise ValueError(f'{name} must be a finite number in drawing units.')
    return float(value)


def require_source(project):
    if project.operations or project.metadata.get('cabinet_tab_plans'):
        raise ValueError('Use a source drawing; machining and Cabinet registration require their owning revision workflows.')


def guard_layer(project,name):
    setting=_automation_layer_settings(project).get(name,{})
    if setting.get('locked') or setting.get('visible') is False:
        raise ValueError(f'Layer {name} must be visible and unlocked; it will not be changed automatically.')


class PanDraft:
    def __init__(self): self.cached=None

    def preview(self,project,p,revision,digest,selection):
        self.cached=None;require_source(project)
        if set(p)-{'mode','settings','holes','marks_enabled','mark_offset'}: raise ValueError('Unknown pan setting.')
        mode=p.get('mode','create')
        if mode not in ('create','marks'):raise ValueError('Choose new pans or existing bend marks.')
        if type(p.get('marks_enabled',False)) is not bool:raise ValueError('Choose whether to create bend marks.')
        offset=numeric(p.get('mark_offset',0),'Mark offset')
        selected=[e for e in project.entities if e.id in selection]
        if not selected or len(selected)!=len(selection):raise ValueError('Select current face rectangles or applied pans.')
        candidate=copy.deepcopy(project);additions=[];replacements={};rows=[];context_ids=set()
        if mode=='create':
            if len(selected)>20:raise ValueError('Create at most 20 pans per reviewed batch.')
            settings=dict(p.get('settings',{}));holes=dict(p.get('holes',{}))
            if set(settings)!=set(asdict(DrPanSettings())):raise ValueError('Provide all six physical pan/bend settings.')
            settings=DrPanSettings(**{k:numeric(v,k) for k,v in settings.items()});settings.validate()
            if set(holes)!=set(asdict(DrPanHoleSettings())):raise ValueError('Provide hole dimensions and side choices.')
            for key in ('enabled','top','bottom','left','right'):
                if type(holes[key]) is not bool:raise ValueError('Hole side choices must be enabled or disabled.')
            for key in ('diameter','max_spacing','end_offset'):holes[key]=numeric(holes[key],key)
            hs=DrPanHoleSettings(**holes);hs.validate()
            for e in selected:
                if not available(project,e) or e.metadata.get('reference_only') or e.metadata.get('machinable') is False:raise ValueError('Use visible unlocked face rectangles.')
                if any(k.startswith(('dr_pan','mounting','cabinet','led_')) or 'owner' in k or 'generated' in k for k in e.metadata):
                    raise ValueError('Use original face artwork, not generated or registered geometry.')
                for key in ('group_id','compound_id'):
                    group=e.metadata.get(key)
                    if group and any(x.id not in selection and x.metadata.get(key)==group for x in project.entities):
                        raise ValueError('Select the complete source group or compound.')
                result=generate_dr_pan(e,settings,hs)
                marks=dr_pan_bend_marks(result.outline,result.references,offset) if p.get('marks_enabled') else []
                replacements[e.id]=result.outline;additions.extend([*result.references,*result.holes,*marks])
                rows.append(dict(id=e.id,face=[result.face_width,result.face_height],blank=[result.blank_width,result.blank_height],
                    allowance=settings.allowance,deduction=settings.deduction,holes=len(result.holes),marks=len(marks)))
        else:
            if p.get('marks_enabled') is not True:raise ValueError('Enable marks to create or update them; disabling does not delete existing marks.')
            groups={e.metadata.get('group_id') for e in selected}-{None}
            owners={e.id for e in selected}|{e.metadata.get('dr_pan_owner') for e in selected}
            pans=[e for e in project.entities if e.metadata.get('dr_pan') and not e.metadata.get('reference_only') and (e.id in owners or e.metadata.get('group_id') in groups)]
            if not pans or len(pans)>20:raise ValueError('Select one to twenty applied pans or their members.')
            for pan in pans:
                owner_ids={pan.id,pan.metadata['dr_pan'].get('source',{}).get('id')}-{None}
                members=[e for e in project.entities if e.metadata.get('dr_pan_owner') in owner_ids and e.metadata.get('group_id')==pan.metadata.get('group_id')]
                context_ids.update(e.id for e in [pan,*members])
                guides=[e for e in members if e.metadata.get('reference_only')]
                oldmarks=[e for e in members if e.metadata.get('dr_pan_bend_mark')]
                if any(not available(project,e) for e in [pan,*members]):raise ValueError('Unlock and show the complete pan assembly before editing marks.')
                proposed=dr_pan_bend_marks(pan,guides,offset)
                matched=set()
                for i,line in enumerate(proposed):
                    matches=[]
                    for old in oldmarks:
                        if not isinstance(old,Line):raise ValueError('Bend-mark geometry is invalid.')
                        original=dr_pan_bend_marks(pan,guides,numeric(old.metadata.get('dr_pan_mark_offset',0),'Saved mark offset'))[i]
                        if any(original.start.distance_to(a)<=2e-6 and original.end.distance_to(b)<=2e-6 for a,b in ((old.start,old.end),(old.end,old.start))):matches.append(old)
                    if len(matches)>1:raise ValueError('Multiple marks match a bend guide; resolve duplicate marks first.')
                    if matches:
                        old=matches[0];matched.add(old.id);line.id=old.id;line.layer=old.layer
                        line.appearance=copy.deepcopy(old.appearance);line.metadata={**old.metadata,**line.metadata}
                        replacements[old.id]=line
                    else:additions.append(line)
                if matched!={e.id for e in oldmarks}:raise ValueError('Existing marks no longer match the pan guides; review altered geometry before updating.')
                rows.append(dict(id=pan.id,marks=4,updated=len(matched),created=4-len(matched)))
        if len(additions)>10000:raise ValueError('This proposal exceeds 10,000 generated objects. Use fewer pans or wider hole spacing.')
        for e in [*additions,*replacements.values()]:guard_layer(project,e.layer)
        candidate.entities=[replacements.get(e.id,e) for e in candidate.entities]+additions
        layers=candidate.metadata.setdefault('layer_settings',{})
        for e in additions:layers.setdefault(e.layer,dict(visible=True,locked=False,color='#9c36b5' if e.metadata.get('reference_only') else '#1971c2'))
        if candidate.to_dict()==project.to_dict():raise ValueError('No pan changes to review.')
        selected_ids=list(replacements) if mode=='create' else [e.id for e in [*additions,*replacements.values()]]
        token=new_id('pan-review');self.cached=dict(token=token,revision=revision,digest=digest,project=candidate,ids=selected_ids)
        return dict(token=token,units=project.units,mode=mode,rows=rows,
            geometry=[e.to_dict() for e in candidate.entities if e.id in context_ids|set(replacements)|{a.id for a in additions}],
            originals=[e.to_dict() for e in selected],message='Bend compensation is an uncalibrated estimate. Verify material and brake settings on a physical sample.')

    def apply(self,session,p,revision,digest):
        d=self.cached;require_source(session.project)
        if set(p)!={'token','reviewed'} or p['reviewed'] is not True or not d or p['token']!=d['token'] or revision!=d['revision'] or digest!=d['digest']:
            raise ValueError('Review a current pan proposal before applying.')
        session.project=copy.deepcopy(d['project']);session.selected_ids=set(d['ids']);session._record_history();self.cached=None
