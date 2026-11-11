"""Parent-owned small-center selection; no independent or skipped cuts."""
from cam_core.model import RouterOffsetOperation,effective_tool
from cam_core.small_center_cleanout import center_loops,validate_selection
from manufacturing_picking import fingerprint,available

def operation(project,p):
    op=next((o for o in project.operations if o.id==p.get('id')),None)
    if type(op) is not RouterOffsetOperation or op.profile_retention or not op.enabled:
        raise ValueError('Choose an enabled independent profile for small-center cleanout.')
    return op

def validate(project,op):
    ids=op.small_center_cleanout_ids
    if not isinstance(ids,list) or not all(isinstance(i,str) for i in ids) or len(set(ids))!=len(ids):
        raise ValueError('Choose unique small-center contours.')
    validate_selection(project,op)

def info(project,p):
    op=operation(project,p);tool=effective_tool(project,op);entities={e.id:e for e in project.entities};rows=[]
    for eid in op.entity_ids:
        e=entities.get(eid);reason='';loops=[]
        try:
            if e is None or not available(project,e):raise ValueError('Contour must be visible and unlocked.')
            loops=center_loops(project,op,eid,tool.diameter)
        except ValueError as exc:reason=str(exc)
        rows.append(dict(id=eid,eligible=not reason,reason=reason,loops=len(loops),kind=getattr(e,'kind','missing')))
    return dict(fingerprint=fingerprint(project),id=op.id,selected=list(op.small_center_cleanout_ids),rows=rows,units=project.units,diameter=tool.diameter)

def prepare(project,p):
    if set(p)-{'id','fingerprint','ids'}:raise ValueError('Unknown small-center setting.')
    if p.get('fingerprint')!=fingerprint(project):raise ValueError('Job changed; reopen small-center review.')
    op=operation(project,p);op.small_center_cleanout_ids=p.get('ids')
    validate(project,op)
    return dict(selected=list(op.small_center_cleanout_ids),name=op.name)
