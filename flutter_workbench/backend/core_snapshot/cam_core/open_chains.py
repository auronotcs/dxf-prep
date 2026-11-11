"""Explicit connected Cut Line policy for new operations; no source mutation."""
import math
from .model import Line,Polyline
from .drafting import segment_intersection


def assemble_open_chains(entities,entity_ids,tolerance=1e-7):
    chunks=[];ignored=[];simplified=[];seen=set()
    for key in entity_ids:
        if key in seen:raise ValueError(f'Cut Line repeats source {key}.')
        seen.add(key)
        entity=entities.get(key)
        if isinstance(entity,Line):points=[entity.start,entity.end]
        elif isinstance(entity,Polyline) and not entity.closed:points=list(entity.points)
        else:raise ValueError(f'Cut Line source {key} is closed or unsupported. Use Inside/Outside for closed contours.')
        if not all(math.isfinite(value) for point in points for value in (point.x,point.y)):
            raise ValueError(f'Cut Line source {key} contains non-finite coordinates; repair it before cutting.')
        clean=[]
        for point in points:
            if not clean or clean[-1].distance_to(point)>tolerance:clean.append(point)
        if len(clean)!=len(points):simplified.append(key)
        if len(clean)<2:ignored.append(key);continue
        if clean[0].distance_to(clean[-1])<=tolerance:
            raise ValueError(f'Cut Line source {key} forms a closed loop. Use Inside/Outside for closed contours.')
        chunks.append((key,clean))
    if not chunks:raise ValueError('Cut Line selection contains only zero-length geometry.')
    # Detect branches at interior vertices/segments as well as ordinary shared
    # endpoints. Do not silently split, bridge, or double-cut overlapping edges.
    segments=[(key,index,a,b) for key,points in chunks for index,(a,b) in enumerate(zip(points,points[1:]))]
    for index,(key,edge,a,b) in enumerate(segments):
        for other,other_edge,c,d in segments[index+1:]:
            if (max(a.x,b.x)+tolerance<min(c.x,d.x) or max(c.x,d.x)+tolerance<min(a.x,b.x) or
                max(a.y,b.y)+tolerance<min(c.y,d.y) or max(c.y,d.y)+tolerance<min(a.y,b.y)):
                continue
            adjacent=key==other and abs(edge-other_edge)==1
            def interior(p,u,v):
                length=u.distance_to(v)
                return (p.distance_to(u)>tolerance and p.distance_to(v)>tolerance and
                        abs((v.x-u.x)*(p.y-u.y)-(v.y-u.y)*(p.x-u.x))<=tolerance*length and
                        min(u.x,v.x)-tolerance<=p.x<=max(u.x,v.x)+tolerance and
                        min(u.y,v.y)-tolerance<=p.y<=max(u.y,v.y)+tolerance)
            overlap=any(interior(p,u,v) for p,u,v in ((a,c,d),(b,c,d),(c,a,b),(d,a,b)))
            duplicate=(a.distance_to(c)<=tolerance and b.distance_to(d)<=tolerance or
                       a.distance_to(d)<=tolerance and b.distance_to(c)<=tolerance)
            hit=segment_intersection(a,b,c,d,tolerance)
            endpoints=(hit is not None and min(hit.distance_to(a),hit.distance_to(b))<=tolerance and
                       min(hit.distance_to(c),hit.distance_to(d))<=tolerance)
            if overlap or duplicate or hit is not None and not endpoints:
                raise ValueError(f'Cut Line branch, crossing or overlap: {key} / {other}. Split or deselect the conflicting geometry.')
            if key==other and endpoints and not adjacent:
                raise ValueError(f'Cut Line source {key} touches itself; repair it before cutting.')
    nodes=[];ends=[]
    for key,points in chunks:
        pair=[]
        for point in (points[0],points[-1]):
            matches=[i for i,node in enumerate(nodes) if point.distance_to(node)<=tolerance]
            if len(matches)>1:raise ValueError(f'Cut Line source {key} has ambiguous near-coincident endpoints.')
            if not matches:nodes.append(point);matches=[len(nodes)-1]
            pair.append(matches[0])
        ends.append(pair)
    links={i:[] for i in range(len(nodes))}
    for index,(a,b) in enumerate(ends):links[a].append(index);links[b].append(index)
    for node,attached in links.items():
        if len(attached)>2:
            raise ValueError(f'Cut Line branch near X {nodes[node].x:g}, Y {nodes[node].y:g}: '+', '.join(chunks[i][0] for i in attached))
    remaining=set(range(len(chunks)));chains=[]
    while remaining:
        seed=min(remaining,key=lambda i:chunks[i][0]);component={seed};pending=[seed]
        while pending:
            current=pending.pop()
            for node in ends[current]:
                for candidate in links[node]:
                    if candidate not in component:component.add(candidate);pending.append(candidate)
        terminals=[node for node,attached in links.items() if len(attached)==1 and attached[0] in component]
        if len(terminals)!=2:
            raise ValueError('Cut Line connected sources form a closed loop. Use Inside/Outside for closed contours.')
        # Preserve an already consistent source direction. For mixed sources,
        # choose the lexicographically lower endpoint, independent of click order.
        directed=[node for node in terminals if ends[links[node][0]][0]==node]
        start=directed[0] if len(directed)==1 else min(terminals,key=lambda n:(nodes[n].x,nodes[n].y))
        points=[];ids=[];current=start;used=set()
        while True:
            choices=[i for i in links[current] if i in component and i not in used]
            if not choices:break
            index=choices[0];used.add(index);key,part=chunks[index]
            forward=ends[index][0]==current
            oriented=part if forward else list(reversed(part))
            points.extend(oriented if not points else oriented[1:]);ids.append(key)
            current=ends[index][1] if forward else ends[index][0]
        if used!=component:raise ValueError('Cut Line chain could not be resolved without guessing.')
        chains.append((points,ids));remaining-=component
    chains.sort(key=lambda chain:(chain[0][0].x,chain[0][0].y,chain[1]))
    return chains,ignored,simplified
