"""Read-only XY profile check. No depth passes, entries, sweeps or collision approval."""
import hashlib,json,math
from dataclasses import replace
from .model import RouterOffsetOperation,TabReleaseOperation,Polyline,Rectangle,Circle,OffsetSide,effective_tool,Point
from .toolpath import _offset_closed_polygons,_rectangle_points,_offset_circle,TOLERANCE
from .micro_backtracks import remove_micro_backtracks,MICRO_BACKTRACK_INCHES
from .units import unit_scale
from .layout import entity_perimeter,entity_bounds


def check_key(project,operations):
    return hashlib.sha256(json.dumps([project.to_dict(),[o.to_dict() for o in operations]],sort_keys=True).encode()).hexdigest()


def quick_offset_check(project,operations):
    from .model import ProfileRestMachiningOperation
    expanded=[]
    for operation in operations:
        if isinstance(operation,ProfileRestMachiningOperation) and operation.profile_retention:
            source=next((o for o in project.operations if o.id==operation.source_operation_id),None)
            if isinstance(source,RouterOffsetOperation):
                expanded.append(replace(source,id=operation.id,name=operation.name,
                    tool_station=operation.tool_station,entity_ids=list(operation.entity_ids),
                    allowance=operation.stock_to_leave,finishing_pass=False))
        else:expanded.append(operation)
    operations=expanded
    entities={e.id:e for e in project.entities};findings=[];checked=0
    for op in operations:
        if not op.enabled or not isinstance(op,RouterOffsetOperation) or isinstance(op,TabReleaseOperation):continue
        # Open cut-line offsets belong to their own planner, not closed-profile repair.
        if op.side not in (OffsetSide.INSIDE,OffsetSide.OUTSIDE,OffsetSide.ON):continue
        try:
            tool=effective_tool(project,op)
            if tool is None:raise ValueError(f'Tool station T{op.tool_station} is not loaded.')
            if not math.isfinite(tool.diameter) or tool.diameter<=0:raise ValueError('Invalid cutter diameter.')
        except (ValueError,KeyError,TypeError) as exc:
            findings.append(_finding(op,None,'quick.offset_error',str(exc)));continue
        for identity in dict.fromkeys(op.entity_ids):
            entity=entities.get(identity)
            if entity is None:
                findings.append(_finding(op,None,'quick.offset_error',f'Missing source {identity}.'));continue
            if not isinstance(entity,(Polyline,Rectangle,Circle)) or isinstance(entity,Polyline) and not entity.closed:continue
            try:
                side=OffsetSide(op.entity_sides.get(identity,op.side.value))
                if side not in (OffsetSide.INSIDE,OffsetSide.OUTSIDE,OffsetSide.ON):raise ValueError('Invalid closed-contour cut side.')
                if isinstance(entity,Polyline) and side!=OffsetSide.ON:
                    points,_=remove_micro_backtracks(entity.points,closed=True,tolerance=MICRO_BACKTRACK_INCHES*unit_scale('in',project.units))
                    entity=replace(entity,points=points)
                distance=0. if side==OffsetSide.ON else tool.diameter/2+op.allowance
                if not math.isfinite(distance) or distance<0:raise ValueError('Tool radius plus allowance must be finite and nonnegative.')
                distances=[('rough',distance)]
                finish=0. if side==OffsetSide.ON else tool.diameter/2
                if op.finishing_pass and finish!=distance:distances.append(('finish',finish))
                tabs=(op.tab_count>0 or op.tab_mode in ('minimum_and_spacing','minimum_avoid_corners','support_aware','corners_and_spacing','manual','aligned'))
                has_tabs=tabs and (side!=OffsetSide.INSIDE or entity_perimeter(entity)+TOLERANCE>=op.female_tab_min_perimeter)
                for phase,offset in distances:
                    checked+=1
                    if isinstance(entity,Circle):_offset_circle(entity,offset,side);continue
                    points=_rectangle_points(entity) if isinstance(entity,Rectangle) else entity.points
                    polygons=_offset_closed_polygons(points,offset,side)
                    if len(polygons)>1:
                        lengths=[sum(a.distance_to(b) for a,b in zip(p,p[1:]+p[:1])) for p in polygons]
                        small=polygons[min(range(len(polygons)),key=lengths.__getitem__)]
                        center=Point(sum(p.x for p in small)/len(small),sum(p.y for p in small)/len(small))
                        focus=min(points,key=lambda p:p.distance_to(center))
                        potential=side==OffsetSide.OUTSIDE and offset>TOLERANCE and not has_tabs
                        message=(f'{phase.title()} {side.value} offset at {offset:g} {project.units}: {len(polygons)} loops; '
                                 f'perimeters '+', '.join(f'{v:.6g}' for v in lengths)+f' {project.units}. ')
                        if potential:message+='Potential split in topology-safe fallback; normal rounded compensation may not split. '
                        message+='Review narrow features versus redundant points. This is not a collision check or permission to delete artwork.'
                        findings.append(_finding(op,entity,'toolpath.split_geometry',message,focus))
            except (ValueError,TypeError,OverflowError) as exc:
                findings.append(_finding(op,entity,'quick.offset_error',str(exc)))
    return findings,checked


def _finding(op,entity,code,message,point=None):
    if point is None and entity is not None:
        b=entity_bounds(entity)
        if b is not None:point=Point((b[0]+b[2])/2,(b[1]+b[3])/2)
    label=f"Object {entity.metadata.get('object_number',entity.id)}" if entity is not None else 'Operation setup'
    return dict(code=code,entity_ids=(entity.id,) if entity is not None else (),operation_id=op.id,
                operation=f'{op.name} — T{op.tool_station}',geometry=label,point=point,
                severity='warning' if code=='toolpath.split_geometry' else 'error',summary=message,details=message)
