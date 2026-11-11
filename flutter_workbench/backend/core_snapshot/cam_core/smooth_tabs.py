"""Bounded, soft corner avoidance on compensated closed cutter contours.

No placement grid or toolpath trials: build forbidden center intervals once,
then project nominal centers into neighboring legal spans in two sweeps.
"""
from bisect import bisect_left, bisect_right
from functools import lru_cache
import math

MODE = 'minimum_avoid_corners'
SHARP_TURN = math.radians(30)
MAX_TAB_BEND = math.radians(60)


def turn(a, b):
    return abs(math.atan2(a[0]*b[1]-a[1]*b[0], a[0]*b[0]+a[1]*b[1]))


def legal_spans(points, width):
    lengths = [math.dist(a,b) for a,b in zip(points,points[1:])]
    total = sum(lengths)
    if not lengths or total <= 0:
        return total, []
    directions = [((b[0]-a[0])/length,(b[1]-a[1])/length)
                  for a,b,length in zip(points,points[1:],lengths)]
    blocked=[];position=0.0
    # Width already includes cutter compensation. A small additional clearance
    # keeps the raised span from ending exactly at the turn.
    margin=width*.55
    for i,length in enumerate(lengths):
        angle=turn(directions[i-1],directions[i])
        local_radius_turn=angle*width/((lengths[i-1]+length)*.5)
        if angle >= SHARP_TURN or local_radius_turn > MAX_TAB_BEND:
            for shift in (-total,0,total):
                a,b=max(0.,position-margin+shift),min(total,position+margin+shift)
                if b>a:blocked.append((a,b))
        position+=length
    merged=[]
    for a,b in sorted(blocked):
        if merged and a<=merged[-1][1]:merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
        else:merged.append((a,b))
    legal=[];position=0.
    for a,b in merged:
        if a>position:legal.append((position,a))
        position=b
    if position<total:legal.append((position,total))
    return total,legal


def nearest_legal(value, low, high, spans, starts, ends):
    """Only the predecessor/successor intervals can contain the nearest point."""
    if low>high:return None
    value=max(low,min(high,value))
    first=bisect_left(ends,low);last=bisect_right(starts,high)-1
    if first>last:return None
    index=bisect_right(starts,value)-1
    candidates=[]
    for i in {max(first,min(last,index)),max(first,min(last,index+1))}:
        a,b=max(low,spans[i][0]),min(high,spans[i][1])
        if a<=b:candidates.append(max(a,min(b,value)))
    scale=max(high-low,1e-20)
    return min(candidates,key=lambda v:(round(abs(v-value)/scale,12),v)) if candidates else None


def intervals(centers, width, total):
    result=[]
    for center in centers:
        a,b=center-width/2,center+width/2
        if a<0:result.extend(((0.,b),(total+a,total)))
        elif b>total:result.extend(((a,total),(0.,b-total)))
        else:result.append((a,b))
    return tuple(sorted(result))


@lru_cache(maxsize=128)
def planned_intervals(coordinates, width, count, max_spacing):
    points=[]
    for p in coordinates:
        if not points or math.dist(p,points[-1])>1e-10:points.append(p)
    if len(points)<3:return ()
    if points[0]!=points[-1]:points.append(points[0])
    total,legal=legal_spans(points,width)
    if width<=0 or total<=width or max_spacing<=0:return ()
    requested=max(4,count,math.ceil(total/max_spacing))
    capacity=max(1,int(total//(2*width)))
    count=min(requested,capacity)
    spacing=total/count
    centers=[(i+.5)*spacing for i in range(count)]
    if legal and count>1:
        # When a tiny part cannot fit the requested count, retain the same
        # non-overlap fallback as Minimum + Max Spacing.
        limit=max(max_spacing,spacing)
        expanded=[(a+shift,b+shift) for shift in (-total,0,total) for a,b in legal]
        starts=[a for a,b in expanded];ends=[b for a,b in expanded]
        for indices in (range(count),range(count-1,-1,-1)):
            for i in indices:
                previous=centers[i-1] if i else centers[-1]-total
                following=centers[i+1] if i+1<count else centers[0]+total
                low=max(previous+2*width,following-limit)
                high=min(following-2*width,previous+limit)
                moved=nearest_legal(centers[i],low,high,expanded,starts,ends)
                if moved is not None:centers[i]=moved
    elif legal:
        starts=[a for a,b in legal];ends=[b for a,b in legal]
        moved=nearest_legal(centers[0],0,total,legal,starts,ends)
        if moved is not None:centers[0]=moved
    return intervals(sorted(c%total for c in centers),width,total)


def smooth_tab(group):
    """Test actual cleanup spans, accepting broad arcs but not sharp turns."""
    pieces=[];bend=0.
    for span in group['spans']:
        first=last=None;points=span['motions'][1:-1]
        for a,b in zip(points,points[1:]):
            dx,dy=b.end.x-a.end.x,b.end.y-a.end.y
            if math.hypot(dx,dy)<1e-10:continue
            center=getattr(b,'center',None)
            if center is None:
                length=math.hypot(dx,dy);start=end=(dx/length,dy/length)
            else:
                clockwise=getattr(b.kind,'value',b.kind)=='arc_cw'
                sign=-1 if clockwise else 1
                def tangent(p):
                    x,y=p.x-center.x,p.y-center.y;r=math.hypot(x,y)
                    return (-sign*y/r,sign*x/r)
                start,end=tangent(a.end),tangent(b.end)
                sa=math.atan2(a.end.y-center.y,a.end.x-center.x)
                ea=math.atan2(b.end.y-center.y,b.end.x-center.x)
                bend+=(sign*(ea-sa))%(2*math.pi)
            if last is not None:
                angle=turn(last,start)
                if angle>=SHARP_TURN:return False
                bend+=angle
            first=first or start;last=end
        if first is not None:pieces.append((points[0].end,points[-1].end,first,last))
    # Physical seam fragments may be stored first/last rather than connected.
    for i,(_,end,_,direction) in enumerate(pieces):
        for j,(start,_,tangent,_) in enumerate(pieces):
            if i!=j and math.hypot(end.x-start.x,end.y-start.y)<1e-7:
                angle=turn(direction,tangent)
                if angle>=SHARP_TURN:return False
                bend+=angle
    return bool(pieces) and bend<=MAX_TAB_BEND


def retained_groups(groups, count):
    """Smoothness first; bounded spreading within the eligible contour slots."""
    if len(groups)<=count:return list(groups)
    smooth=[i for i,g in enumerate(groups) if smooth_tab(g)]
    eligible=smooth if len(smooth)>=count else list(range(len(groups)))
    preferred=set(smooth)
    # A deterministic anchor plus farthest-gap insertion takes O(n * count),
    # not a search through subsets. Uneven placement is intentional here.
    first=min(eligible,key=lambda i:(i not in preferred,groups[i]['mid']))
    chosen=[first];n=len(groups)
    while len(chosen)<count:
        remaining=[i for i in eligible if i not in chosen]
        def score(i):
            gap=min(min(abs(i-j),n-abs(i-j)) for j in chosen)
            distance=min(math.dist(groups[i]['mid'],groups[j]['mid']) for j in chosen)
            return i in preferred,gap,round(distance,10)
        chosen.append(max(remaining,key=score))
    return [groups[i] for i in chosen]
