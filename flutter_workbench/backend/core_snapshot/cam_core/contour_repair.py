"""Bounded endpoint stitching. Never guess at branches or reshape curves."""
import copy
import math
from dataclasses import dataclass, field

from .model import Circle, Line, Point, Polyline, Rectangle
from .shape_conversion import ShapeConversion, apply_shape_conversion
from .units import unit_scale
from .geometry_diagnostics import diagnose_geometry
from .micro_backtracks import remove_micro_backtracks, backtrack_indices, MICRO_BACKTRACK_INCHES
from .endpoint_repair import endpoint_links, ordinary_open_paths


@dataclass
class ContourRepair(ShapeConversion):
    joins: list[Point] = field(default_factory=list)
    removed_points: int = 0
    remaining_issues: int = 0
    backtracks_removed: int = 0
    backtrack_locations: list[Point] = field(default_factory=list)
    near_point_locations: list[Point] = field(default_factory=list)


def propose_near_point_repair(entities, selected_ids, *, tolerance=.0001):
    """Collapse bounded vertex clusters, not arbitrary short-edge chains.

    Every removed vertex must be within tolerance of its final retained
    anchor. Open endpoints are always preserved; contours are never joined.
    """
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError('Point tolerance must be a positive finite number.')
    selected = [e for e in entities if e.id in selected_ids]
    if not selected or len(selected) != len(selected_ids):
        raise ValueError('Select the contours to repair first.')
    if len(selected) > 10000:
        raise ValueError('Select fewer than 10,000 objects per repair.')
    result = []
    for source in selected:
        if source.metadata.get('reference_only') or source.metadata.get('mounting_generated'):
            raise ValueError('Select ordinary drawing contours, not mounting-generated or reference geometry.')
        if isinstance(source, (Circle, Rectangle, Line)):
            continue
        if not isinstance(source, Polyline):
            raise ValueError('Point cleanup supports polylines. Convert text to curves first.')
        raw = source.points
        if len(raw) < (3 if source.closed else 2) or not all(math.isfinite(v) for p in raw for v in (p.x,p.y)):
            raise ValueError('A selected contour has missing or invalid points.')
        clusters = []
        for index, point in enumerate(raw):
            endpoint = not source.closed and index == len(raw)-1
            if clusters and not endpoint and point.distance_to(clusters[-1][0]) <= tolerance:
                clusters[-1].append(point)
            else:
                clusters.append([point])
        # Only merge the cyclic seam when the ENTIRE last cluster fits the
        # first anchor; do not compound multiple tolerance-sized changes.
        if source.closed and len(clusters)>1 and all(p.distance_to(clusters[0][0])<=tolerance for p in clusters[-1]):
            clusters[0].extend(clusters.pop())
        points = [cluster[0] for cluster in clusters]
        removed = [p for cluster in clusters for p in cluster[1:]]
        if not removed:
            continue
        if len(points) < (3 if source.closed else 2):
            raise ValueError('Point tolerance would collapse an outline. Reduce the tolerance; no repair applied.')
        maximum = max(p.distance_to(cluster[0]) for cluster in clusters for p in cluster)
        entity = copy.deepcopy(source)
        entity.points = points
        entity.metadata['contour_repair_source_ids'] = [source.id]
        issues = sum(i.code in ('geometry.self_intersection','geometry.duplicate_segment') for i in diagnose_geometry([entity]))
        result.append(ContourRepair((source.id,),entity,maximum,removed_points=len(removed),
                                    remaining_issues=issues,near_point_locations=removed))
    return result


def propose_contour_repair(entities, selected_ids, *, tolerance=.001, units='in', cleanup=True,
                           backtracks=True, join_endpoints=True, endpoint_context=None):
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError('Join tolerance must be a positive finite number.')
    selected=[e for e in entities if e.id in selected_ids]
    if not selected or len(selected)!=len(selected_ids):
        raise ValueError('Select the contour pieces to repair first.')
    if len(selected)>10000:
        raise ValueError('Select fewer than 10,000 objects per repair.')
    tiny=min(tolerance,.00001*unit_scale('in',units)) if cleanup else 0.
    paths=[]
    for e in selected:
        if e.metadata.get('reference_only') or e.metadata.get('mounting_generated'):
            raise ValueError('Select ordinary drawing contours, not mounting-generated or reference geometry.')
        if isinstance(e,(Circle,Rectangle)):
            continue  # Already intact native primitives, not joining candidates.
        if not isinstance(e,(Line,Polyline)):
            raise ValueError('Join / Repair supports lines and polylines. Convert text to curves first.')
        raw=[e.start,e.end] if isinstance(e,Line) else list(e.points)
        if len(raw)<2 or not all(math.isfinite(v) for p in raw for v in (p.x,p.y)):
            raise ValueError('A selected contour has missing or invalid points.')
        closed=bool(getattr(e,'closed',False));points=[]
        for p in raw:
            if not points or not cleanup or p.distance_to(points[-1])>tiny:
                points.append(p)
        if cleanup and closed and len(points)>1 and points[0].distance_to(points[-1])<=tiny:
            points.pop()
        if len(points)<(3 if closed else 2):
            raise ValueError('A selected contour collapses to negligible geometry. Remove or inspect it separately.')
        paths.append((e,points,closed,len(raw)-len(points)))
    # Hash only open endpoints. Exact geometry vertices are never searched or
    # paired, so large tessellated curves do not create quadratic join work.
    links={}
    if join_endpoints and endpoint_context is not None:
        # Opening selects flagged objects, not necessarily every nearby branch.
        # Keep the full drawing in the matching context; never make a formerly
        # ambiguous join look unique by omitting an unselected third contour.
        others=ordinary_open_paths(e for e in endpoint_context if e.id not in selected_ids)
        links=endpoint_links(paths+others,tolerance,strict=False,
                             blocked_indices=range(len(paths),len(paths)+len(others)))
    elif join_endpoints:
        links=endpoint_links(paths,tolerance)
    remaining=set(range(len(paths)));result=[]
    while remaining:
        first=min(remaining);component={first};pending=[first]
        while pending:
            i=pending.pop()
            for end in (0,1):
                other=links.get((i,end))
                if other is not None and other[0] not in component:
                    component.add(other[0]);pending.append(other[0])
        remaining-=component
        source,source_points,already_closed,_=paths[first]
        ids=tuple(paths[i][0].id for i in sorted(component))
        removed=sum(paths[i][3] for i in component)
        joins=[];maximum=0.
        if already_closed:
            points=list(source_points);closed=True
        else:
            free=sorted((i,end) for i in component for end in (0,1) if (i,end) not in links)
            start=free[0] if free else (first,0)
            current=start;visited=set();points=[];closed=False
            while True:
                i,end=current
                if i in visited:raise ValueError('The selected pieces do not form a simple chain.')
                visited.add(i)
                part=list(paths[i][1]);part=part if end==0 else part[::-1]
                if points:
                    gap=points[-1].distance_to(part[0]);maximum=max(maximum,gap)
                    midpoint=Point((points[-1].x+part[0].x)/2,(points[-1].y+part[0].y)/2)
                    points[-1]=midpoint;joins.append(midpoint);points.extend(part[1:])
                else:points.extend(part)
                following=links.get((i,1-end))
                if following is None:break
                if following==start:
                    gap=points[-1].distance_to(points[0]);maximum=max(maximum,gap)
                    midpoint=Point((points[-1].x+points[0].x)/2,(points[-1].y+points[0].y)/2)
                    points[0]=midpoint;points.pop();joins.append(midpoint);closed=True;break
                current=following
            if visited!=component:raise ValueError('Could not connect every selected piece in the contour.')
        # Joining can itself create a zero-length edge or reverse sliver at a
        # midpoint. Inspect the RESULT, including a newly closed cyclic seam.
        if cleanup:
            cleaned=[]
            for p in points:
                if not cleaned or p.distance_to(cleaned[-1])>tiny:cleaned.append(p)
            if closed and len(cleaned)>1 and cleaned[0].distance_to(cleaned[-1])<=tiny:cleaned.pop()
            removed+=len(points)-len(cleaned);points=cleaned
        count=0;locations=[]
        if backtracks:
            limit=min(tolerance,MICRO_BACKTRACK_INCHES*unit_scale('in',units))
            locations=[points[i] for i in backtrack_indices(points,closed=closed,tolerance=limit)]
            points,count=remove_micro_backtracks(points,closed=closed,tolerance=limit)
            removed+=count
        if not joins and not removed:continue
        if len(points)<(3 if closed else 2):
            raise ValueError('Join tolerance would collapse an outline. Reduce the tolerance.')
        metadata=copy.deepcopy(source.metadata)
        metadata['contour_repair_source_ids']=list(ids)
        entity=Polyline(id=source.id,layer=source.layer,appearance=copy.deepcopy(source.appearance),
                        metadata=metadata,points=points,closed=closed)
        issues=sum(issue.code in ('geometry.self_intersection','geometry.duplicate_segment')
                   for issue in diagnose_geometry([entity]))
        result.append(ContourRepair(ids,entity,maximum,joins,removed,issues,count,locations))
    return result


def apply_contour_repair(project,proposal):
    """Preserve machining associations for point-only, same-contour repairs.

    Motion is NOT retained: the UI invalidates previews and normal generation
    and preflight run again. Joins/topology changes retain conversion's
    conservative reassociation behavior. Never re-enable an already disabled op.
    """
    proposal = list(proposal)
    originals = {e.id: e for e in project.entities}
    safe = {}
    for item in proposal:
        source = originals.get(item.entity.id)
        if (item.source_ids == (item.entity.id,) and isinstance(source, Polyline)
                and isinstance(item.entity, Polyline) and source.closed == item.entity.closed
                and source.layer == item.entity.layer and not item.joins
                and item.removed_points > 0 and not item.remaining_issues):
            safe[source.id] = item.entity
    # Layer preparation makes exact outline copies for cleanout. Keep their
    # IDs/settings and apply the same repair, but do not reshape edited copies,
    # mounting features, pattern geometry, or manually overridden dependents.
    selected = {identity for item in proposal for identity in item.source_ids}
    pending = list(safe)
    while pending:
        source_id = pending.pop()
        source = originals[source_id]
        for entity in project.entities:
            if (entity.id in selected or entity.id in safe
                    or not isinstance(entity, Polyline)
                    or not entity.metadata.get('layer_prep_generated')
                    or entity.metadata.get('layer_prep_source_entity_id') != source_id
                    or entity.metadata.get('layer_prep_role') != 'cleanout_outline'
                    or entity.metadata.get('layer_prep_manual_override')
                    or entity.metadata.get('reference_only') or entity.metadata.get('mounting_generated')
                    or entity.closed != source.closed or entity.points != source.points):
                continue
            repaired = copy.deepcopy(entity)
            repaired.points = copy.deepcopy(safe[source_id].points)
            safe[entity.id] = repaired
            proposal.append(ContourRepair((entity.id,), repaired))
            pending.append(entity.id)
    candidate,disabled,removed=apply_shape_conversion(project,proposal)
    # Only ambiguous/deleted geometry invalidates an operation's association.
    # An operation may contain both repaired and unsafe sources; propagate that
    # invalidation through rest/dependency chains exactly as conversion does.
    unsafe = {identity for item in proposal for identity in item.source_ids} - set(safe)
    disabled = {op.id for op in project.operations if
                (set(op.entity_ids) | set(getattr(op,'island_entity_ids',[]))) & (unsafe | removed)}
    while True:
        previous = set(disabled)
        disabled.update(op.id for op in project.operations if
                        set(getattr(op,'depends_on_ids',[])) & disabled
                        or getattr(op,'source_operation_id','') in disabled)
        if previous == disabled:
            break
    saved_operations = {op.id: op for op in project.operations}
    candidate.operations = [op if op.id in disabled else copy.deepcopy(saved_operations[op.id])
                            for op in candidate.operations]
    for op in candidate.operations:
        if op.id in disabled:
            op.recipe_metadata['geometry_invalidated']='Join / Repair changed source geometry. Recreate or reassociate this operation.'
    return candidate,disabled,removed
