"""Passive drag-knife pivot paths. No controller-specific spindle commands."""
import math
from .model import Circle, Line, Polyline, Rectangle, effective_tool
from .toolpath import GeneratedToolpath, ToolpathBlock, Motion, MotionKind, ToolPoint
from .topology import profile_containers


def validate_knife_setup(project, operation):
    tool = effective_tool(project, operation)
    if tool is None or tool.kind.value != 'drag_knife':
        raise ValueError('Select a Drag Knife tool in the tool library; do not reuse a router or engraver definition.')
    if tool.spindle_mode != 'disabled' or tool.spindle_rpm is not None:
        raise ValueError('Drag Knife requires a spindle-disabled tool with no RPM.')
    values = (operation.target_depth, operation.safe_z, operation.blade_offset,
              operation.swivel_depth, operation.approach_height,
              operation.alignment_length, operation.curve_tolerance,
              tool.feed_rate, tool.plunge_rate)
    if any(v is None or not math.isfinite(v) or v <= 0 for v in values):
        raise ValueError('Knife depths, offset, alignment, tolerance, clearance and feeds must be positive finite numbers.')
    if not math.isfinite(operation.swivel_angle) or not 0 <= operation.swivel_angle <= 180:
        raise ValueError('Swivel threshold must be between 0 and 180 degrees.')
    if not isinstance(operation.passes,int) or not 1 <= operation.passes <= 100:
        raise ValueError('Knife pass count must be between 1 and 100.')
    if operation.swivel_depth > operation.target_depth / operation.passes:
        raise ValueError('Swivel penetration must not exceed the first pass depth.')
    if operation.safe_z <= operation.approach_height:
        raise ValueError('Safe Z must be above the approach height.')
    if operation.alignment_length < 2 * operation.blade_offset:
        raise ValueError('Provide at least two blade offsets of sacrificial alignment travel; verify the lead is in waste.')
    if tool.cutting_edge_length is not None and operation.target_depth > tool.cutting_edge_length:
        raise ValueError('Knife depth exceeds the configured blade clearance/cutting length.')
    return tool


def contour_points(entity, tolerance):
    if isinstance(entity, Circle):
        if not math.isfinite(entity.radius) or entity.radius <= tolerance:
            raise ValueError('Circle radius must exceed the knife curve tolerance.')
        angle = 2*math.acos(max(-1, min(1, 1-tolerance/entity.radius)))
        count = max(24, math.ceil(2*math.pi/angle))
        if count > 20000:raise ValueError('Knife curve tolerance is too fine for this circle.')
        points = [(entity.center.x+entity.radius*math.cos(2*math.pi*i/count),
                   entity.center.y+entity.radius*math.sin(2*math.pi*i/count)) for i in range(count)]
        closed = True
    elif isinstance(entity, Rectangle):
        a,b=entity.corner_a,entity.corner_b
        points=[(a.x,a.y),(b.x,a.y),(b.x,b.y),(a.x,b.y)];closed=True
    elif isinstance(entity, Line):
        points=[(entity.start.x,entity.start.y),(entity.end.x,entity.end.y)];closed=False
    elif isinstance(entity, Polyline):
        points=[(p.x,p.y) for p in entity.points];closed=entity.closed
    else:raise ValueError('Drag Knife supports lines, circles, rectangles and polylines. Convert text to curves first.')
    clean=[]
    for point in points:
        if not all(math.isfinite(v) for v in point):raise ValueError('Knife source has non-finite coordinates.')
        if not clean or math.dist(clean[-1],point)>1e-10:clean.append(point)
    if closed and len(clean)>1 and math.dist(clean[0],clean[-1])<=1e-10:clean.pop()
    if len(clean)<(3 if closed else 2):raise ValueError('Knife contour has too few distinct points.')
    return clean,closed


def generate_drag_knife(project, operation):
    tool=validate_knife_setup(project,operation)
    by_id={e.id:e for e in project.entities}
    if not operation.entity_ids:raise ValueError('Select geometry for Drag Knife.')
    if any(i not in by_id for i in operation.entity_ids):raise ValueError('Knife source geometry is missing.')
    selected=[by_id[i] for i in dict.fromkeys(operation.entity_ids)]
    if any(e.metadata.get('reference_only') or e.metadata.get('machinable') is False for e in selected):
        raise ValueError('Reference geometry cannot be knife cut.')
    contours={e.id:contour_points(e,operation.curve_tolerance) for e in selected}
    closed=[e for e in selected if contours[e.id][1]]
    containers=profile_containers(closed)
    # Open scores precede closed cutouts; nested closed shapes cut inside first.
    selected.sort(key=lambda e:(int(contours[e.id][1]),-len(containers.get(e.id,()))))
    blocks=[];offset=operation.blade_offset
    for entity in selected:
        points,closed=contours[entity.id];points=list(reversed(points)) if operation.reverse_paths else points
        pairs=list(zip(points,points[1:]+([points[0]] if closed else [])))
        tangents=[((b[0]-a[0])/math.dist(a,b),(b[1]-a[1])/math.dist(a,b)) for a,b in pairs]
        def pivot(point,tangent):return (point[0]+offset*tangent[0],point[1]+offset*tangent[1])
        start=pivot(points[0],tangents[0])
        lead=(start[0]-operation.alignment_length*tangents[0][0],start[1]-operation.alignment_length*tangents[0][1])
        for pass_index in range(operation.passes):
            depth=operation.target_depth*(pass_index+1)/operation.passes
            motions=[]
            def move(kind,xy,z,feed=None,center=None):
                from .model import Point
                motions.append(Motion(kind,ToolPoint(*xy,z),feed,Point(*center) if center else None))
            move(MotionKind.RAPID,lead,operation.safe_z)
            move(MotionKind.RAPID,lead,operation.approach_height)
            move(MotionKind.LINEAR,lead,-operation.swivel_depth,tool.plunge_rate)
            move(MotionKind.LINEAR,start,-operation.swivel_depth,tool.feed_rate)
            move(MotionKind.LINEAR,start,-depth,tool.plunge_rate)
            for index,(_,end) in enumerate(pairs):
                incoming=tangents[index];end_pivot=pivot(end,incoming)
                move(MotionKind.LINEAR,end_pivot,-depth,tool.feed_rate)
                if index+1==len(pairs):continue
                outgoing=tangents[index+1]
                turn=math.atan2(incoming[0]*outgoing[1]-incoming[1]*outgoing[0],
                                incoming[0]*outgoing[0]+incoming[1]*outgoing[1])
                if abs(turn)<1e-10:continue
                if abs(abs(turn)-math.pi)<1e-8:raise ValueError('Knife contour reverses 180 degrees; repair the backtrack first.')
                swivel=abs(math.degrees(turn))>=operation.swivel_angle
                z=-operation.swivel_depth if swivel else -depth
                if swivel:move(MotionKind.LINEAR,end_pivot,z,tool.plunge_rate)
                next_pivot=pivot(end,outgoing)
                move(MotionKind.ARC_CCW if turn>0 else MotionKind.ARC_CW,next_pivot,z,tool.feed_rate,end)
                if swivel:move(MotionKind.LINEAR,next_pivot,-depth,tool.plunge_rate)
            final=(motions[-1].end.x,motions[-1].end.y)
            move(MotionKind.LINEAR,final,operation.approach_height,tool.plunge_rate)
            move(MotionKind.RAPID,final,operation.safe_z)
            blocks.append(ToolpathBlock(operation.id,tool.station,motions,f'Drag knife {entity.id}',{
                'entity_id':entity.id,'side':'drag_knife','spindle':'off','closed':closed,
                'pass_index':pass_index,'blade_offset':offset,'role':'knife_cut'}))
    return GeneratedToolpath(blocks)
