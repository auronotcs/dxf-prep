"""Explicit closed-outline stock overrun review; no geometry clipping."""
from cam_core.automation import _project_plates
from cam_core.closed_plate_overrun import KEY, eligible, approve, approved_plate, edge_overruns, MAX_OVERRUN_IN
from cam_core.units import unit_scale
from cam_core.layout import entity_bounds
from manufacturing_ownership import token
from manufacturing_picking import available


def review(project):
    plates = _project_plates(project)
    rows = []
    for e in project.entities:
        choices = [dict(number=p.number, edges=list(edge_overruns(e,p))) for p in plates if eligible(e,p,project.units)]
        if not choices and KEY not in e.metadata: continue
        owner = approved_plate(e,plates,project.units)
        rows.append(dict(id=e.id,layer=e.layer,bounds=entity_bounds(e),choices=choices,
            owner=owner.number if owner else None,saved=e.metadata.get(KEY),
            stale=KEY in e.metadata and owner is None,editable=available(project,e),
            operations=[o.name for o in project.operations if o.enabled and e.id in o.entity_ids]))
    return dict(token=token(project),units=project.units,limit=MAX_OVERRUN_IN*unit_scale('in',project.units),rows=rows)


def prepare(project,p):
    if set(p)!={'id','plate_number','token','reviewed'} or p['reviewed'] is not True:
        raise ValueError('Review the complete outline, edge overruns and machine clearance first.')
    info=review(project)
    if p['token']!=info['token']: raise ValueError('Drawing or stock changed. Refresh overrun review.')
    row=next((r for r in info['rows'] if r['id']==p['id']),None)
    if row is None or not row['editable']: raise ValueError('Choose a visible unlocked outline.')
    entity=next(e for e in project.entities if e.id==p['id'])
    if p['plate_number'] is None:
        if KEY not in entity.metadata: raise ValueError('No approval to revoke.')
        entity.metadata.pop(KEY)
    else:
        if type(p['plate_number']) is not int: raise ValueError('Choose a plate number.')
        plate=next((v for v in _project_plates(project) if v.number==p['plate_number']),None)
        if plate is None: raise ValueError('Plate no longer exists.')
        approve(entity,plate,project.units)
    return dict(before=row,after=next((r for r in review(project)['rows'] if r['id']==p['id']),None))


def overtravel(project,numbers):
    return [dict(r,kind='closed',overtravel=True) for r in review(project)['rows'] if r['owner'] in numbers and r['operations']]
