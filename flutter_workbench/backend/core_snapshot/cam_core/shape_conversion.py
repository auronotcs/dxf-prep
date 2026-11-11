"""Explicit, previewable shape replacement and connected-contour circle repair."""
from dataclasses import dataclass
import copy
import math

from .feature_recognition import fit_circular_polyline
from .layout import entity_bounds
from .model import Circle, Line, Point, Polyline, Rectangle
from .units import unit_scale


@dataclass
class ShapeConversion:
    source_ids: tuple[str, ...]
    entity: object
    maximum_error: float = 0.0


def path_points(entity, tolerance):
    if isinstance(entity, Polyline):
        return list(entity.points), entity.closed
    if isinstance(entity, Line):
        return [entity.start, entity.end], False
    if isinstance(entity, Rectangle):
        x1,y1,x2,y2=entity_bounds(entity)
        return [Point(x1,y1),Point(x2,y1),Point(x2,y2),Point(x1,y2)], True
    if isinstance(entity, Circle):
        if not math.isfinite(entity.radius) or entity.radius <= 0:
            raise ValueError('Circle radius must be positive and finite.')
        angle=2*math.acos(max(-1.0,1-min(tolerance/entity.radius,1.0)))
        count=max(32,math.ceil(math.tau/max(angle,1e-12)))
        count=4*math.ceil(count/4)
        if count>100000:
            raise ValueError('This tolerance would create more than 100,000 outline points. Increase it.')
        return [Point(entity.center.x+entity.radius*math.cos(math.tau*i/count),
                      entity.center.y+entity.radius*math.sin(math.tau*i/count)) for i in range(count)],True
    raise ValueError('Select drawing contours. Use Text to Curves for text first.')


def _groups(entities, scope, tolerance):
    if scope=='whole':return [entities]
    if scope=='each':return [[entity] for entity in entities]
    if scope!='connected':raise ValueError('Unknown conversion scope.')
    paths=[path_points(entity,tolerance) for entity in entities]
    neighbors=[set() for _ in entities]
    for i,(points,closed) in enumerate(paths):
        if closed:continue
        for j in range(i+1,len(paths)):
            other,other_closed=paths[j]
            if not other_closed and any(a.distance_to(b)<=tolerance
                    for a in (points[0],points[-1]) for b in (other[0],other[-1])):
                neighbors[i].add(j);neighbors[j].add(i)
    remaining=set(range(len(entities)));groups=[]
    while remaining:
        component=set();todo=[min(remaining)]
        while todo:
            index=todo.pop()
            if index in component:continue
            component.add(index);todo.extend(neighbors[index]-component)
        remaining-=component;groups.append([entities[i] for i in sorted(component)])
    return groups


def _joined_closed_path(group,tolerance):
    paths=[path_points(entity,tolerance) for entity in group]
    if len(paths)==1:
        points,closed=paths[0]
        if closed:return points
    if any(closed for _,closed in paths):
        raise ValueError('Repair each closed contour separately; do not combine separate closed shapes.')
    points=list(paths[0][0]);remaining=list(range(1,len(paths)))
    while remaining:
        matches=[]
        for index in remaining:
            for reverse in (False,True):
                candidate=paths[index][0]
                if points[-1].distance_to(candidate[-1] if reverse else candidate[0])<=tolerance:
                    matches.append((index,reverse))
        if len(matches)!=1:
            raise ValueError('Pieces have a gap or ambiguous branch. Select just one complete contour or adjust join tolerance.')
        index,reverse=matches[0];remaining.remove(index)
        candidate=paths[index][0][::-1] if reverse else paths[index][0]
        points.extend(candidate[1:])
    if points[-1].distance_to(points[0])>tolerance:
        raise ValueError('The selected pieces do not close. Select the remaining arc(s); an incomplete arc is not a circle.')
    return points[:-1]


def propose_shape_conversion(entities, selected_ids, *, mode='repair', scope='connected',
                             units='in', tolerance=None, diameter=None, width=None, height=None):
    tolerance=.001*unit_scale('in',units) if tolerance is None else float(tolerance)
    if not math.isfinite(tolerance) or tolerance<=0:raise ValueError('Tolerance must be positive and finite.')
    if mode not in ('repair','circle','rectangle','outline'):raise ValueError('Unknown target shape.')
    sizes={key:None if value is None else float(value) for key,value in
           (('diameter',diameter),('width',width),('height',height))}
    if any(value is not None and (not math.isfinite(value) or value<=0) for value in sizes.values()):
        raise ValueError('Explicit sizes must be positive and finite; leave blank for automatic size.')
    selected=[entity for entity in entities if entity.id in set(selected_ids)]
    if not selected:raise ValueError('Select geometry first.')
    for entity in selected:
        if entity.metadata.get('reference_only'):
            raise ValueError('Reference-only geometry cannot be replaced. Select the production outline.')
        points,_=path_points(entity,tolerance)
        if len(points)<2 or any(not math.isfinite(v) for p in points for v in (p.x,p.y)):
            raise ValueError('A selected contour has missing or non-finite coordinates.')
    output=[]
    for group in _groups(selected,'each' if mode=='outline' else scope,tolerance):
        source=group[0];error=0.0
        bounds=[entity_bounds(entity) for entity in group]
        x1=min(b[0] for b in bounds);y1=min(b[1] for b in bounds)
        x2=max(b[2] for b in bounds);y2=max(b[3] for b in bounds)
        center=Point((x1+x2)/2,(y1+y2)/2)
        common=dict(id=source.id,layer=source.layer,metadata=copy.deepcopy(source.metadata),
                    appearance=copy.deepcopy(source.appearance))
        if mode=='repair':
            if len(group)==1 and isinstance(source,Circle):
                continue
            points=_joined_closed_path(group,tolerance)
            if points and points[0].distance_to(points[-1])<1e-12:points=points[:-1]
            fit=fit_circular_polyline(Polyline(points=points,closed=True),absolute_tolerance=tolerance)
            if fit is None:
                raise ValueError('Selected contour does not fit a complete circle. Use Circle from bounds for deliberate reshaping.')
            angles=[math.atan2(p.y-fit.center.y,p.x-fit.center.x) for p in points]
            turns=[(b-a+math.pi)%math.tau-math.pi for a,b in zip(angles,angles[1:]+angles[:1])]
            allowed=max(tolerance,.005*fit.radius)
            mid_error=max(abs(Point((a.x+b.x)/2,(a.y+b.y)/2).distance_to(fit.center)-fit.radius)
                          for a,b in zip(points,points[1:]+points[:1]))
            if abs(abs(sum(turns))-math.tau)>.05 or abs(sum(abs(t) for t in turns)-math.tau)>.05 or mid_error>allowed:
                raise ValueError('Contour doubles back, misses a sector, or has straight chords inconsistent with a circle.')
            error=max(fit.maximum_error,mid_error)
            target=Circle(**common,center=fit.center,radius=fit.radius)
        elif mode=='circle':
            diameter=sizes['diameter'] if sizes['diameter'] is not None else min(x2-x1,y2-y1)
            if diameter<=0:raise ValueError('Circle bounds have no area. Enter an explicit diameter.')
            target=Circle(**common,center=center,radius=diameter/2)
        elif mode=='rectangle':
            width=sizes['width'] if sizes['width'] is not None else x2-x1
            height=sizes['height'] if sizes['height'] is not None else y2-y1
            if min(width,height)<=0:raise ValueError('Rectangle needs positive width and height.')
            target=Rectangle(**common,corner_a=Point(center.x-width/2,center.y-height/2),
                             corner_b=Point(center.x+width/2,center.y+height/2))
        else:
            if isinstance(source,Polyline):continue
            points,closed=path_points(source,tolerance)
            target=Polyline(**common,points=points,closed=closed)
        target.metadata['shape_conversion_source_ids']=[entity.id for entity in group]
        output.append(ShapeConversion(tuple(entity.id for entity in group),target,error))
    return output


def apply_shape_conversion(project, proposal):
    """Return an isolated transaction; source objects remain untouched on error."""
    ids={identity for item in proposal for identity in item.source_ids}
    if not ids.issubset({e.id for e in project.entities}):raise ValueError('Conversion source changed. Preview again.')
    settings=project.metadata.get('layer_settings',{})
    if any(settings.get(e.layer or '0',{}).get('locked',False) for e in project.entities if e.id in ids):
        raise ValueError('Unlock selected layers before converting shapes.')
    candidate=copy.deepcopy(project)
    dependents={e.id for e in candidate.entities if e.id not in ids and (
        str(e.metadata.get('mounting_owner_entity_id','')) in ids or
        str(e.metadata.get('owner_entity_id','')) in ids or
        str(e.metadata.get('layer_prep_source_entity_id','')) in ids or
        bool(ids.intersection(e.metadata.get('pattern_source_entity_ids',[]))))}
    replacements={item.source_ids[0]:copy.deepcopy(item.entity) for item in proposal}
    candidate.entities=[replacements[e.id] if e.id in replacements else e for e in candidate.entities
                        if e.id in replacements or e.id not in ids|dependents]
    disabled=set()
    for operation in candidate.operations:
        if (set(operation.entity_ids)|set(getattr(operation,'island_entity_ids',[]))) & (ids|dependents):
            disabled.add(operation.id)
    while True:
        previous=set(disabled)
        disabled.update(op.id for op in candidate.operations if
                        set(getattr(op,'depends_on_ids',[]))&disabled or getattr(op,'source_operation_id','') in disabled)
        if previous==disabled:break
    for operation in candidate.operations:
        if operation.id in disabled:
            operation.enabled=False
            operation.recipe_metadata['geometry_invalidated']='Convert to Shape changed source geometry. Recreate or reassociate this operation.'
    for plan in candidate.metadata.get('led_plans',[]):
        if isinstance(plan,dict) and ids.intersection(plan.get('linked_entity_ids',[])):
            plan['geometry_review_required']=True
            plan['geometry_review_reason']='Convert to Shape changed linked artwork.'
    return candidate,disabled,dependents
