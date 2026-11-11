"""Review cutter-width intrusions into other contours over the complete cut.

Broad phase uses object bounds and indexed contour edges. Repeated depth-pass
segments are checked once per source/operation. This is a boundary check, not
a fixture, stock-support, or complete solid-removal simulation.
"""
import math
from .contour_index import ContourIndex
from .geometry_relationships import coincident_process_copies, noncutting_bent_source_ids
from .layout import entity_bounds
from .model import Circle, Rectangle, Polyline, Point, RouterOffsetOperation, EngraveOperation, ToolKind, effective_tool, ValidationIssue
from .toolpath import MotionKind, _entry_segment_distance, _circle_path_segment_distance, partial_entry_through_pairs
from .units import unit_scale

APPROVAL_CODE = 'toolpath.neighbor_cut'


class _CandidateBoundsIndex:
    """Read-only per-check AABB tree; only provably distant boxes are pruned.

    Leaves retain drawing order so the exact edge checks and first-hit
    diagnostics are identical to a full candidate scan. This is not a
    geometry simplifier or a persistent clearance-result cache.
    """
    def __init__(self, candidates):
        entries = [(order, target, value) for order,(target,value) in enumerate(candidates.items())]
        def build(items):
            if not items:return None
            boxes = [value[1] for _,_,value in items]
            box = (min(b[0] for b in boxes),min(b[1] for b in boxes),
                   max(b[2] for b in boxes),max(b[3] for b in boxes))
            if len(items)<=8:return box,items,None,None
            axis = 0 if box[2]-box[0]>=box[3]-box[1] else 1
            items.sort(key=lambda item:(item[2][1][axis]+item[2][1][axis+2])/2)
            middle = len(items)//2
            return box,None,build(items[:middle]),build(items[middle:])
        self.root = build(entries)

    def query(self, bounds, reach):
        x0,y0,x1,y1=bounds[0]-reach,bounds[1]-reach,bounds[2]+reach,bounds[3]+reach
        found=[];pending=[self.root] if self.root else []
        while pending:
            box,items,left,right=pending.pop()
            if box[0]>x1 or box[2]<x0 or box[1]>y1 or box[3]<y0:continue
            if items is not None:
                found.extend(items)
            else:
                pending.extend((left,right))
        found.sort(key=lambda item:item[0])
        return ((target,value) for _,target,value in found)


def _segments(block, chord_error):
    previous = None
    for motion in block.motions:
        end=motion.end
        if not all(math.isfinite(v) for v in (end.x,end.y,end.z)):
            previous=None
            continue  # The motion verifier reports invalid coordinates.
        if previous is not None and motion.kind != MotionKind.RAPID and min(previous.z,end.z)<-1e-8:
            a,b=Point(previous.x,previous.y),Point(end.x,end.y)
            if motion.center is not None and motion.kind in {MotionKind.ARC_CW,MotionKind.ARC_CCW}:
                c=motion.center; radius=a.distance_to(c)
                if radius>1e-9:
                    angle=math.atan2(a.y-c.y,a.x-c.x)
                    last=math.atan2(b.y-c.y,b.x-c.x)
                    sign=1 if motion.kind==MotionKind.ARC_CCW else -1
                    sweep=((last-angle)*sign)%(2*math.pi)
                    if sweep<1e-9:sweep=2*math.pi
                    step=2*math.acos(max(-1,min(1,1-chord_error/radius)))
                    count=max(1,math.ceil(sweep/max(step,1e-6)))
                    for i in range(1,count+1):
                        p=b if i==count else Point(c.x+radius*math.cos(angle+sign*sweep*i/count),c.y+radius*math.sin(angle+sign*sweep*i/count))
                        yield a,p,chord_error
                        a=p
                else:yield a,b,0
            else:yield a,b,0
        previous=end


def neighboring_cut_violations(project, generated_paths):
    references=noncutting_bent_source_ids(project)
    copies=coincident_process_copies(project)
    # A shelf and its paired later through-boundary are the same part/process.
    for op in project.operations:
        if isinstance(op,RouterOffsetOperation) and op.enabled:
            tool=effective_tool(project,op)
            if tool:
                for source,targets in partial_entry_through_pairs(project,op,tool.diameter).items():
                    copies.setdefault(source,set()).update(targets)
                    for target in targets:copies.setdefault(target,set()).add(source)
    unit=unit_scale('in',project.units)
    tolerance=0.00005*unit
    arc_error=0.00002*unit
    candidates={}
    for e in project.entities:
        if e.id in references:continue
        index=None
        if isinstance(e,Rectangle):
            x0,y0,x1,y1=entity_bounds(e)
            points=[Point(x0,y0),Point(x1,y0),Point(x1,y1),Point(x0,y1)]
            index=ContourIndex(points,unit*.25)
        elif isinstance(e,Polyline) and e.closed and len(e.points)>=3:
            if not all(math.isfinite(p.x) and math.isfinite(p.y) for p in e.points):continue
            index=ContourIndex(e.points,unit*.25)
        elif not isinstance(e,Circle):continue
        box=entity_bounds(e)
        if box is None or not all(math.isfinite(v) for v in box):continue
        candidates[e.id]=(e,box,index)
    candidate_index=_CandidateBoundsIndex(candidates)
    issues=[];reported=set()
    for op in project.operations:
        if not op.enabled:continue
        tool=effective_tool(project,op)
        generated=generated_paths.get(op.id)
        if not tool or not generated or tool.diameter<=0:continue
        # Drag-knife pivot motions are not a rotating cutter envelope.
        from .model import DragKnifeOperation
        if isinstance(op,DragKnifeOperation):continue
        # This operation traces with a spring-loaded, spindle-off point.
        # Its nominal tool/shank diameter and Z compression do not describe
        # a milled swath. Keep geometry as collision targets for real routers;
        # never exempt a layer, tool name, or spindle-off router operation.
        if (isinstance(op,EngraveOperation)
                and tool.kind in {ToolKind.ENGRAVER, ToolKind.OTHER}
                and tool.spindle_mode == 'disabled' and tool.spindle_rpm is None):
            continue
        radius=tool.diameter/2
        seen=set()
        for block in generated.blocks:
            sources=set(block.metadata.get('entity_ids',()))
            source=block.metadata.get('entity_id')
            if source:sources.add(source)
            if not sources:continue
            excluded=set(sources)|{target for s in sources for target in copies.get(s,())}
            source_key=tuple(sorted(sources))
            for a,b,error in _segments(block,arc_error):
                key=(source_key,a,b)
                if key in seen:continue
                seen.add(key)
                bounds=(min(a.x,b.x),min(a.y,b.y),max(a.x,b.x),max(a.y,b.y))
                limit=radius-tolerance-error
                for target,(e,box,index) in candidate_index.query(bounds,radius):
                    pair=(op.id,source_key,target)
                    if target in excluded or pair in reported:continue
                    if box[0]>bounds[2]+radius or box[2]<bounds[0]-radius or box[1]>bounds[3]+radius or box[3]<bounds[1]-radius:continue
                    hit=None
                    if isinstance(e,Circle):
                        gap,_,p=_circle_path_segment_distance(e.center,e.radius,a,b)
                        if gap<limit:hit=p
                    else:
                        for c,d in index.near_edges(bounds,radius):
                            gap,_,p=_entry_segment_distance(a,b,c,d)
                            if gap<limit:
                                hit=p;break
                    if hit is not None:
                        reported.add(pair)
                        issues.append(ValidationIssue('error',APPROVAL_CODE,
                            f'{op.name}: the cutter-width sweep enters another contour. Increase part spacing or review and explicitly approve this overlap for this output only.',
                            (*source_key,target),hit))
    return issues
