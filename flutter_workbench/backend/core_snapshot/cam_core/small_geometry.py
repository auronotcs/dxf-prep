"""Whole-object review; never infer permission to delete from object size."""
import copy
from dataclasses import dataclass
import hashlib
import json
import math

from .model import Circle, Line, Point, Polyline, Rectangle
from .layout import entity_bounds
from .units import unit_scale

DEFAULT_SPECK_IN = .01


def geometry_signature(entity):
    data = entity.to_dict()
    data.pop('metadata', None)
    data.pop('appearance', None)
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class SmallGeometryFinding:
    entity_id: str
    width: float
    height: float
    point: Point
    reason: str
    deletable: bool
    signature: str


def _vertices(entity):
    if isinstance(entity, Line):return [entity.start, entity.end]
    if isinstance(entity, Polyline):return entity.points
    if isinstance(entity, Rectangle):
        a,b=entity.corner_a,entity.corner_b
        return [a,Point(b.x,a.y),b,Point(a.x,b.y)]
    return []


def find_small_geometry(project, maximum_size_in=DEFAULT_SPECK_IN, *, include_kept=False):
    if not math.isfinite(maximum_size_in) or maximum_size_in <= 0:
        raise ValueError('Maximum whole-object size must be a positive finite number.')
    factor=unit_scale('in',project.units);limit=maximum_size_in*factor
    kept=project.metadata.get('small_geometry_kept',{})
    settings=project.metadata.get('layer_settings',{})
    # Exact shared vertices are evidence of an attached detail, not an isolated speck.
    epsilon=1e-7*factor;vertices={}
    for entity in project.entities:
        for point in _vertices(entity):
            if math.isfinite(point.x) and math.isfinite(point.y):
                key=(math.floor(point.x/epsilon),math.floor(point.y/epsilon))
                vertices.setdefault(key,[]).append((entity.id,point))
    findings=[]
    for entity in project.entities:
        if not isinstance(entity,(Line,Polyline,Rectangle,Circle)):continue
        bounds=entity_bounds(entity)
        if bounds is None or not all(math.isfinite(v) for v in bounds):continue
        width,height=bounds[2]-bounds[0],bounds[3]-bounds[1]
        tiny=max(width,height)<=limit
        joining=entity.metadata.get('dxf_join_review',[])
        if not tiny and not joining:continue
        signature=geometry_signature(entity)
        if not include_kept and kept.get(entity.id)==signature:continue
        reason='Small whole object; size alone does not mean junk.' if tiny else 'DXF joining needs review; use local contour repair, not whole-object deletion.'
        deletable=tiny and not joining
        if joining:
            reason=' '.join(dict.fromkeys(str(n.get('reason','DXF joining needs review.')) for n in joining if isinstance(n,dict)))
        attached=False
        for point in _vertices(entity):
            if not (math.isfinite(point.x) and math.isfinite(point.y)):
                continue
            x,y=math.floor(point.x/epsilon),math.floor(point.y/epsilon)
            if any(other_id!=entity.id and point.distance_to(other)<=epsilon
                   for dx in (-1,0,1) for dy in (-1,0,1)
                   for other_id,other in vertices.get((x+dx,y+dy),())):
                attached=True;break
        if attached:
            reason+=' Shares an endpoint with other geometry: inspect/repair locally.'
            deletable=False
        protected=any(entity.metadata.get(k) for k in ('reference_only','mounting_generated','bent_tab_generated',
            'tab_slot_generated','layer_prep_generated','owner_entity_id','mounting_owner_entity_id'))
        if protected:
            reason+=' Generated/reference geometry: use its owning workflow.';deletable=False
        if settings.get(entity.layer or '0',{}).get('locked',False):
            reason+=' Layer is locked.';deletable=False
        findings.append(SmallGeometryFinding(entity.id,width,height,
            Point((bounds[0]+bounds[2])/2,(bounds[1]+bounds[3])/2),reason,deletable,signature))
    return findings


def affected_operations(project, ids):
    affected={op.id for op in project.operations
              if ids & (set(op.entity_ids)|set(getattr(op,'island_entity_ids',[])))}
    while True:
        expanded=affected|{op.id for op in project.operations if
            set(getattr(op,'depends_on_ids',[])) & affected or getattr(op,'source_operation_id','') in affected}
        if expanded==affected:return affected
        affected=expanded


def delete_small_geometry(project, findings, maximum_size_in=DEFAULT_SPECK_IN):
    ids={f.entity_id for f in findings}
    if not ids:raise ValueError('Select objects to delete.')
    current={f.entity_id:f for f in find_small_geometry(project,maximum_size_in,include_kept=True)}
    if any(f.entity_id not in current or current[f.entity_id].signature!=f.signature or
           not current[f.entity_id].deletable for f in findings):
        raise ValueError('Selection changed, is locked, attached, or is not independently deletable. Scan again.')
    # Never cascade a speck deletion into a derived outline or mounting feature.
    for e in project.entities:
        references={str(e.metadata.get(k,'')) for k in ('layer_prep_source_entity_id','owner_entity_id','mounting_owner_entity_id')}
        references.update(e.metadata.get('pattern_source_entity_ids',[]))
        if e.id not in ids and references & ids:
            raise ValueError('Selected geometry owns derived features. Review those dependencies before deletion.')
    candidate=copy.deepcopy(project)
    from .dimensions import refresh_dimension_fallbacks
    candidate.dimensions=refresh_dimension_fallbacks(candidate.dimensions,candidate.entities)
    candidate.entities=[e for e in candidate.entities if e.id not in ids]
    affected=affected_operations(project,ids)
    for op in candidate.operations:
        if op.id in affected:
            op.enabled=False
            op.recipe_metadata['geometry_invalidated']='Small Geometry deletion removed referenced source. Reassociate and regenerate before output.'
    for plan in candidate.metadata.get('led_plans',[]):
        if isinstance(plan,dict) and ids & set(plan.get('linked_entity_ids',[])):
            plan['geometry_review_required']=True
            plan['geometry_review_reason']='Small Geometry deletion removed linked artwork.'
    return candidate,affected
