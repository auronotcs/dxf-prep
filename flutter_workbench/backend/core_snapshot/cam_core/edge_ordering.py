"""Soft small-footprint/plate-edge preference for dependency-safe routing."""
import math
from .layout import entity_bounds
from .topology import profile_containers


def small_edge_priorities(project, groups):
    entities={e.id:e for e in project.entities}
    selected=[entities[i] for i in dict.fromkeys(i for g in groups for i in g.entity_ids) if i in entities]
    try:containers=profile_containers(selected)
    except ValueError:return {}
    bounds={e.id:entity_bounds(e) for e in selected}
    plates=[]
    for p in project.metadata.get('plates', ()):
        try:plates.append((float(p['x']),float(p['y']),float(p['x'])+float(p['width']),float(p['y'])+float(p['height'])))
        except (KeyError,TypeError,ValueError):continue
    if not plates:
        p=project.metadata.get('sheet_layout',{})
        try:
            w,h=float(p['width']),float(p['height'])
            x,y=float(p.get('origin_x',0)),float(p.get('origin_y',0))
            for row in range(int(p.get('rows',1))):
                for col in range(int(p.get('columns',1))):
                    plates.append((x+col*w,y+row*h,x+(col+1)*w,y+(row+1)*h))
        except (KeyError,ValueError,TypeError):pass
    plates=[p for p in plates if all(math.isfinite(v) for v in p) and p[2]>p[0] and p[3]>p[1]]
    scores={}
    for e in selected:
        # Nested openings inherit their containing part's rank, while the
        # route planner still enforces their before-parent dependency.
        root=min([e.id,*containers.get(e.id,())],key=lambda i:len(containers.get(i,())))
        b=bounds[root]
        if b is None:continue
        plate=next((p for p in plates if p[0]-1e-7<=b[0] and p[1]-1e-7<=b[1] and p[2]+1e-7>=b[2] and p[3]+1e-7>=b[3]),None)
        if plate is None:continue  # Missing/crossing stock keeps generated rank.
        short=min(plate[2]-plate[0],plate[3]-plate[1])
        distances=(max(0,plate[2]-b[2]),max(0,b[0]-plate[0]),max(0,b[1]-plate[1]),max(0,plate[3]-b[3]))
        # Distance and sqrt(bounding footprint) share dimensionless units;
        # edge biases are deliberately small rather than absolute side phases.
        edge=min(2*d/short+side*.01 for side,d in enumerate(distances))
        scores[e.id]=math.sqrt(max(0,(b[2]-b[0])*(b[3]-b[1])))/short+edge
    return {g.token:min((scores[i] for i in g.entity_ids if i in scores),default=math.inf) for g in groups}
