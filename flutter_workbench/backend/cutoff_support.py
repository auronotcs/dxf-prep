"""Private sheet-cutoff proposals using the immutable Python geometry planner."""
import copy
import math
from cam_core.automation import _project_plates
from cam_core.model import Line, RouterOffsetOperation, new_id
from cam_core.sheet_cutoff import plan_sheet_cutoffs, apply_sheet_cutoffs


def references(value, ids):
    if isinstance(value,str): return value in ids
    if isinstance(value,dict): return any(references(k,ids) or references(v,ids) for k,v in value.items())
    if isinstance(value,(list,tuple)): return any(references(v,ids) for v in value)
    return False


def guard_operations(before,after):
    from manufacturing_support import supported
    if not supported(before) or not supported(after):
        raise ValueError('This job has protected machining workflows. Continue cutoff editing in Tkinter.')
    originals={e.id:e.to_dict() for e in before.entities}
    changed={e.id for e in after.entities if e.id in originals and e.to_dict()!=originals[e.id]}
    changed |= set(originals)-{e.id for e in after.entities}
    entities={e.id:e for e in before.entities};affected=[]
    for op in before.operations:
        if not references(op.to_dict(),changed): continue
        if (type(op) is not RouterOffsetOperation or op.profile_retention or op.depends_on_ids
                or op.tab_release_plan or op.manual_contour_order or op.geometry_selector
                or any(not isinstance(entities.get(i),Line) for i in op.entity_ids)):
            raise ValueError(f'{op.name}: this cutoff has a dependent or protected workflow; requalify it in Tkinter.')
        affected.append(op.id)
    for op in before.operations:
        if references(op.to_dict(),set(affected)-{op.id}):
            raise ValueError(f'{op.name}: a dependent operation uses this cutoff; requalify it in Tkinter.')
    return [dict(id=o.id,name=o.name) for o in before.operations if o.id in affected]


class CutoffDraft:
    def __init__(self): self.cached=None

    def preview(self,project,p,revision,digest):
        self.cached=None
        if set(p)-{'scope','plate_number','orientation','clearance'}: raise ValueError('Unknown cutoff option.')
        if p.get('scope') not in ('selected','all'): raise ValueError('Choose selected sheet or all sheets.')
        clearance=p.get('clearance')
        if isinstance(clearance,bool) or not isinstance(clearance,(int,float)) or not math.isfinite(clearance) or clearance<0:
            raise ValueError('Clearance must be a finite nonnegative number in drawing units.')
        if p.get('orientation') not in ('vertical','horizontal'): raise ValueError('Choose vertical or horizontal placement.')
        plates=_project_plates(project)
        if p['scope']=='selected':
            if type(p.get('plate_number')) is not int: raise ValueError('Choose a sheet.')
            plates=[plate for plate in plates if plate.number==p['plate_number']]
        if not plates: raise ValueError('Choose a defined sheet.')
        candidate=copy.deepcopy(project)
        proposals=plan_sheet_cutoffs(candidate,plates,clearance,p['orientation'])
        rows=[]
        for proposal in proposals:
            if proposal.line is None:
                rows.append(dict(plate=proposal.plate.to_dict(),action='Skipped',message=proposal.message,line=None))
            else:
                lines,replaced=apply_sheet_cutoffs(candidate,[proposal])
                rows.append(dict(plate=proposal.plate.to_dict(),action='Replace' if replaced else 'Add',message=proposal.message,line=lines[0].to_dict(),replaced=replaced))
        affected=guard_operations(project,candidate)
        ids=[row['line']['id'] for row in rows if row['line'] is not None]
        token=None
        if ids:
            # Existing locked layers are skipped by the core planner; never unlock them.
            candidate.metadata.setdefault('layer_settings',{}).setdefault('Cutoff',{})['visible']=True
            token=new_id('cutoff-review')
            self.cached=dict(token=token,revision=revision,digest=digest,project=candidate,ids=ids)
        return dict(token=token,rows=rows,units=project.units,affected_operations=affected,revision=revision)

    def apply(self,session,p,revision,digest):
        d=self.cached
        if set(p)!={'token'} or not d or p.get('token')!=d['token'] or revision!=d['revision'] or digest!=d['digest']:
            raise ValueError('The drawing or cutoff settings changed. Preview Sheet Cutoff Lines again.')
        guard_operations(session.project,d['project'])
        session.project=copy.deepcopy(d['project']);session.selected_ids=set(d['ids'])
        session._record_history();self.cached=None
