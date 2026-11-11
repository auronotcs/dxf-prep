"""Explicit open-path plate ownership; geometry and direction remain authoritative."""
import copy,hashlib,json
from cam_core.automation import _project_plates
from cam_core.layout import entity_bounds
from cam_core import plates as core
from manufacturing_picking import available

def capable():
    return hasattr(core,'open_path_plate')

def token(project):
    return hashlib.sha256(json.dumps(project.to_dict(),sort_keys=True,allow_nan=False).encode()).hexdigest()

def review(project):
    if not capable():raise ValueError('Restart with the updated backend to review open-path ownership.')
    plates=_project_plates(project);rows=[]
    for e in project.entities:
        if not core.is_open_plate_path(e):continue
        probe=copy.deepcopy(e)
        probe.metadata.pop('output_plate_number',None)
        if probe.metadata.get('primitive')=='automatic_sheet_cutoff':probe.metadata.pop('plate_number',None)
        choices=[p.number for p in plates if core.open_path_plate(probe,[p]) is not None]
        assigned=e.metadata.get('output_plate_number')
        valid=assigned is None or type(assigned) is int and assigned in choices
        owner=core.open_path_plate(e,plates) if valid else None
        bounds=entity_bounds(e)
        overtravel=bool(owner and bounds and any((bounds[0]<owner.x-1e-7,bounds[1]<owner.y-1e-7,
            bounds[2]>owner.x+owner.width+1e-7,bounds[3]>owner.y+owner.height+1e-7)))
        operations=[o.name for o in project.operations if o.enabled and e.id in o.entity_ids]
        rows.append(dict(id=e.id,layer=e.layer,bounds=bounds,assigned=e.metadata.get('output_plate_number'),
            owner=owner.number if owner else None,assignmentValid=valid,choices=choices,overtravel=overtravel,operations=operations,
            editable=available(project,e) and e.metadata.get('machinable') is not False and not e.metadata.get('reference_only')))
    return dict(token=token(project),units=project.units,plates=[p.to_dict() for p in plates],paths=rows)

def assign(project,p):
    if set(p)!={'id','plate_number','token'}:raise ValueError('Choose an open path and an output plate.')
    details=review(project)
    if p['token']!=details['token']:raise ValueError('Drawing or machining changed. Refresh plate assignments before applying.')
    row=next((r for r in details['paths'] if r['id']==p['id']),None)
    if row is None or not row['editable']:raise ValueError('Choose a visible, unlocked machinable open path.')
    number=p['plate_number']
    if number is not None and (type(number) is not int or number not in row['choices']):
        raise ValueError('Choose a plate touched by the complete open path.')
    e=next(e for e in project.entities if e.id==p['id'])
    if number is None:e.metadata.pop('output_plate_number',None)
    else:e.metadata['output_plate_number']=number
    # Explicit source geometry/direction, other metadata, operations and selection stay intact.

def overtravel(project,numbers):
    return [r for r in review(project)['paths'] if r['overtravel'] and r['owner'] in numbers and r['operations']]
