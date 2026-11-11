"""Bounded geometry-only repeats with full-document stale protection."""
import copy
from cam_core.automation import _project_plates, _automation_layer_settings
from cam_core.layout import entity_bounds
from cam_core.model import new_id
from cam_core.step_repeat import RepeatOptions, plan_repeat, copy_repeat_geometry
from manufacturing_picking import available


class RepeatDraft:
    def __init__(self): self.cached=None

    def preview(self,project,p,revision,digest,selected):
        self.cached=None
        if project.operations: raise ValueError('Use a source drawing before repeating geometry.')
        if set(p)-{'columns','rows','spacing','x','y','direction_x','direction_y','target_layer','plate_number'}:
            raise ValueError('Unknown repeat option.')
        entities=[e for e in project.entities if e.id in selected]
        if len(entities)!=len(selected) or not entities: raise ValueError('Select current source geometry.')
        for e in entities:
            if not available(project,e) or e.metadata.get('reference_only') or e.metadata.get('machinable') is False:
                raise ValueError('Repeat visible unlocked source artwork only.')
            if any(k.startswith(('dr_pan','mounting','cabinet','led_')) or 'owner' in k or 'generated' in k for k in e.metadata):
                raise ValueError('Use the owning workflow to duplicate generated or registered assemblies.')
        # Do not silently split linked groups/compounds.
        for key in ('group_id','compound_id'):
            groups={e.metadata.get(key) for e in entities}-{None}
            if any(e.id not in selected and e.metadata.get(key) in groups for e in project.entities):
                raise ValueError('Select the complete group or compound before repeating.')
        layer=p.get('target_layer')
        if layer is not None:
            layers=_automation_layer_settings(project)
            if layer not in layers or layers[layer].get('locked') or layers[layer].get('visible') is False:
                raise ValueError('Choose an existing visible unlocked target layer.')
        for key in ('x','y'):
            if type(p.get(key)) not in (int,float): raise ValueError('Enter numeric spacing.')
        if any(type(p.get(key,1)) is not int for key in ('direction_x','direction_y')):
            raise ValueError('Choose a direction.')
        plate=next((v for v in _project_plates(project) if v.number==p.get('plate_number')),None)
        if plate is None: raise ValueError('Choose a current plate for the boundary review.')
        options=RepeatOptions(**{k:v for k,v in p.items() if k!='plate_number'})
        plan=plan_repeat(entities,options,plate_bounds=plate.bounds,other_bounds=[entity_bounds(e) for e in project.entities if e.id not in selected])
        additions=copy_repeat_geometry(entities,plan)
        token=new_id('repeat-review')
        self.cached=dict(token=token,revision=revision,digest=digest,additions=additions)
        return dict(token=token,boxes=plan.boxes,overlap=list(plan.overlap_cells),outside=list(plan.outside_cells),
                    warnings=list(plan.warnings),plate=plate.to_dict(),geometry=[e.to_dict() for e in additions],count=len(additions),units=project.units)

    def apply(self,session,p,revision,digest):
        d=self.cached
        if set(p)!={'token','reviewed'} or p['reviewed'] is not True or not d or p['token']!=d['token'] or revision!=d['revision'] or digest!=d['digest']:
            raise ValueError('Review a current Step and Repeat preview before applying.')
        session.project.entities.extend(copy.deepcopy(d['additions']))
        session.selected_ids={e.id for e in d['additions']}
        session._record_history();self.cached=None
