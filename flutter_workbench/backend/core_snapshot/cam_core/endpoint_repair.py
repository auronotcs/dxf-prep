"""Bounded endpoint matching shared by opening recommendations and repair."""
import math

from .model import Line, Polyline, Point
from .units import unit_scale


DEFAULT_JOIN_INCHES = .001


def endpoint_links(paths, tolerance, *, strict=True, blocked_indices=()):
    """Paths are (entity, points, closed, ...); return reciprocal unique links.

    Dense endpoint buckets are ambiguous rather than an all-pairs search.
    Non-strict scanning excludes whole chains touching an ambiguous endpoint.
    """
    ends={};cells={};buckets={};matches={};bad=set(blocked_indices)
    for i,(entity,points,closed,*_) in enumerate(paths):
        if closed:continue
        scope=(entity.layer,str(entity.metadata.get('group_id','')))
        for end,p in enumerate((points[0],points[-1])):
            key=(i,end);ends[key]=p
            cell=(*scope,math.floor(p.x/tolerance),math.floor(p.y/tolerance))
            cells[key]=cell;buckets.setdefault(cell,[]).append(key)
    for key,p in ends.items():
        layer,group,x,y=cells[key]
        nearby=[buckets.get((layer,group,x+dx,y+dy),()) for dx in (-1,0,1) for dy in (-1,0,1)]
        if sum(map(len,nearby))>128:
            bad.add(key[0]);matches[key]=None;continue
        matches[key]=[other for bucket in nearby for other in bucket
                      if other!=key and p.distance_to(ends[other])<=tolerance]
        if len(matches[key])>1:bad.add(key[0])
    if strict and bad:
        raise ValueError('Ambiguous endpoint connections: more than one piece fits. Select one outline at a time or reduce the join tolerance.')
    links={}
    for key,options in matches.items():
        if options is not None and len(options)==1:
            other=options[0]
            if matches.get(other)==[key]:links[key]=other
            else:bad.update((key[0],other[0]))
    # Propagate exclusion along uniquely linked portions of a branched chain.
    neighbors={}
    for key,other in links.items():neighbors.setdefault(key[0],set()).add(other[0])
    pending=list(bad)
    while pending:
        for other in neighbors.get(pending.pop(),()):
            if other not in bad:bad.add(other);pending.append(other)
    return {key:other for key,other in links.items() if key[0] not in bad and other[0] not in bad}


def ordinary_open_paths(entities):
    paths=[]
    for e in entities:
        if not isinstance(e,(Line,Polyline)) or getattr(e,'closed',False):continue
        if e.metadata.get('reference_only') or e.metadata.get('mounting_generated'):continue
        points=[e.start,e.end] if isinstance(e,Line) else e.points
        if len(points)<2 or not all(math.isfinite(v) for p in points for v in (p.x,p.y)):continue
        paths.append((e,points,False))
    return paths


def small_gap_diagnostics(entities, units='in', *, tolerance=None):
    from .geometry_diagnostics import GeometryDiagnostic
    tolerance=DEFAULT_JOIN_INCHES*unit_scale('in',units) if tolerance is None else tolerance
    if not math.isfinite(tolerance) or tolerance<=0:return []
    paths=ordinary_open_paths(entities)
    links=endpoint_links(paths,tolerance,strict=False)
    # Include the complete chain in each marker's identity set so automatic
    # review cannot select half of a chain when one member's layer is locked.
    components={};collapsed=set()
    for i in range(len(paths)):
        if i in components:continue
        members={i};pending=[i]
        while pending:
            j=pending.pop()
            for end in (0,1):
                other=links.get((j,end))
                if other is not None and other[0] not in members:
                    members.add(other[0]);pending.append(other[0])
        ids=tuple(paths[j][0].id for j in sorted(members))
        closed=all((j,end) in links for j in members for end in (0,1))
        joins=sum((j,end) in links for j in members for end in (0,1))//2
        if closed and sum(len(paths[j][1]) for j in members)-joins<3:
            collapsed.update(members)
        for j in members:components[j]=ids
    issues=[]
    for key,other in links.items():
        if key>=other:continue
        i,end=key;j,other_end=other
        if i in collapsed:continue
        a=paths[i][1][0 if end==0 else -1];b=paths[j][1][0 if other_end==0 else -1]
        issues.append(GeometryDiagnostic('warning','geometry.repairable_gap',
            f'Unambiguous endpoint gap ({a.distance_to(b):.6g} {units}); '
            'join first, then check microscopic backtracks.',
            Point((a.x+b.x)/2,(a.y+b.y)/2),components[i]))
    return issues


def opening_repair_diagnostics(entities, units='in'):
    from .micro_backtracks import micro_backtrack_diagnostics
    entities=list(entities)
    return small_gap_diagnostics(entities,units)+micro_backtrack_diagnostics(entities,units)
