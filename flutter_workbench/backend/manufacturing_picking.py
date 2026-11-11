"""Core-derived graphical Manufacturing edits; all changes use draft preflight."""
import copy,math,hashlib,json
from dataclasses import replace,asdict
from cam_core.model import RouterOffsetOperation,IslandFillOperation,Rectangle,Circle,Polyline,Point,OffsetSide
from cam_core.automation import _automation_layer_settings
from cam_core import toolpath as tp

KINDS={'start','tabs','boundaries','islands','order'}
def fingerprint(project):
    return hashlib.sha256(json.dumps(project.to_dict(),sort_keys=True,allow_nan=False).encode()).hexdigest()

def eligible(project):
    return [e for e in project.entities if isinstance(e,(Rectangle,Circle)) or isinstance(e,Polyline) and e.closed]

def available(project,e):
    layer=_automation_layer_settings(project).get(e.layer,{})
    return layer.get('visible') is not False and not layer.get('locked') and not e.metadata.get('annotation_hidden')

def operation(project,p):
    from manufacturing_support import supported
    if not supported(project):raise ValueError('Unsupported machining remains protected.')
    kind=p.get('kind');op=next((o for o in project.operations if o.id==p.get('id')),None)
    if kind not in KINDS or op is None:raise ValueError('Choose a graphical tool and an existing operation.')
    expected=IslandFillOperation if kind in {'boundaries','islands'} else RouterOffsetOperation
    if type(op) is not expected or op.profile_retention:raise ValueError('Choose an independent profile or cleanout; linked groups retain their complete workflow.')
    if not op.enabled:raise ValueError('Enable the operation before graphical editing.')
    if any(op.id in o.depends_on_ids or getattr(o,'source_operation_id',None)==op.id for o in project.operations if o.id!=op.id):
        raise ValueError('Dependent workflows must be revised together; this source operation is protected.')
    if op.geometry_selector:raise ValueError('Remove the associative geometry rule before replacing its targets graphically.')
    source={e.id:e for e in eligible(project)}
    if any(i not in source or not available(project,source[i]) for i in [*op.entity_ids,*getattr(op,'island_entity_ids',[])]):
        raise ValueError('Use visible, unlocked closed source contours.')
    if kind in {'start','tabs'}:
        if len(op.entity_ids)!=1:raise ValueError('Pick starts/tabs on a single-contour profile; multi-contour profiles share one setting.')
        if op.tab_release_plan or op.remove_tabs_after:raise ValueError('Keep all original tabs and clear staged cleanup before repositioning starts or tabs.')
        if kind=='tabs' and (op.entry_mode!='plunge' or op.finishing_pass):raise ValueError('Graphical tabs currently require plunge entry without a separate finishing pass; retain other workflows in the holding editor.')
    return op,source

def contour(project,op,tabs=False):
    e=next(e for e in project.entities if e.id==op.entity_ids[0])
    side=OffsetSide(op.entity_sides.get(e.id,op.side.value));local=replace(op,side=side)
    if isinstance(e,Polyline) and side!=OffsetSide.ON:
        from cam_core.micro_backtracks import remove_micro_backtracks,MICRO_BACKTRACK_INCHES
        from cam_core.units import unit_scale
        points,_=remove_micro_backtracks(e.points,closed=True,tolerance=MICRO_BACKTRACK_INCHES*unit_scale('in',project.units))
        e=replace(e,points=points)
    tool=project.tool_library.by_station(op.tool_station)
    if tool is None:raise ValueError('Configure the operation cutter first.')
    distance=0 if side==OffsetSide.ON else tool.diameter/2+op.allowance
    if distance<0:raise ValueError('Cutter radius plus allowance must be nonnegative.')
    has_tabs=(op.tab_count>0 or op.tab_mode!='even') and (side!=OffsetSide.INSIDE or tp.entity_perimeter(e)>=op.female_tab_min_perimeter)
    if isinstance(e,Circle):
        c,r=tp._offset_circle(e,distance,side)
        angle=-math.pi/2+(op.start_index%4)*math.pi/2
        clockwise=(side in {OffsetSide.OUTSIDE,OffsetSide.ON} and op.cut_direction=='climb') or (side==OffsetSide.INSIDE and op.cut_direction=='conventional')
        return dict(circle=dict(center=asdict(c),radius=r,angle=angle,direction=-1 if clockwise else 1),starts=[dict(x=c.x+r*math.cos(-math.pi/2+i*math.pi/2),y=c.y+r*math.sin(-math.pi/2+i*math.pi/2)) for i in range(4)])
    source=tp._rectangle_points(e) if isinstance(e,Rectangle) else tp._clean_polygon_points(e.points)
    if len(source)>512:raise ValueError('Graphical profile picking supports up to 512 contour vertices.')
    if not tabs and not has_tabs and side==OffsetSide.OUTSIDE and distance>tp.TOLERANCE:
        rect=e if isinstance(e,Rectangle) else tp._axis_aligned_rectangle(source)
        starts=[]
        # Derive indexed starts from the exact core rounded contour builder,
        # with entry leads suppressed only for the marker calculation.
        for index in range(4 if rect else len(source)):
            seed=replace(local,start_index=index,entry_mode='plunge',lead_in=0,lead_out=0,tab_count=0,tab_mode='even',onion_skin=0,remove_tabs_after=False)
            fn=tp._rounded_rectangle_cut_motions if rect else tp._rounded_polygon_cut_motions
            motions=fn(rect or source,distance,.01,op.safe_z,1,1,seed)
            starts.append(dict(x=motions[0].end.x,y=motions[0].end.y))
        return dict(starts=starts)
    paths=tp._offset_closed_polygons(source,distance,side)
    if len(paths)!=1:raise ValueError('Split compensated contours need separate profile operations for graphical picking.')
    base=tp._prepare_router_polygon(paths[0],replace(local,start_index=0))
    path=tp._prepare_router_polygon(paths[0],local)
    return dict(starts=[asdict(v) for v in base],path=[asdict(v) for v in path])

def point_at(shape,fraction):
    if 'circle' in shape:
        s=shape['circle'];a=s['angle']+s['direction']*fraction*math.tau;c=s['center'];r=s['radius']
        return dict(x=c['x']+r*math.cos(a),y=c['y']+r*math.sin(a))
    points=[Point(**p) for p in shape['path']];pairs=list(zip(points,points[1:]+points[:1]))
    lengths=[a.distance_to(b) for a,b in pairs];remaining=(fraction%1)*sum(lengths)
    for (a,b),length in zip(pairs,lengths):
        if remaining<=length:return dict(x=a.x+(b.x-a.x)*remaining/length,y=a.y+(b.y-a.y)*remaining/length)
        remaining-=length
    return asdict(points[0])

def fraction_at(shape,p):
    if not isinstance(p,dict) or set(p)!={'x','y'} or any(type(v) not in (int,float) or not math.isfinite(v) for v in p.values()):raise ValueError('Pick a finite XY location.')
    if 'circle' in shape:
        s=shape['circle'];c=s['center']
        return ((math.atan2(p['y']-c['y'],p['x']-c['x'])-s['angle'])*s['direction']/math.tau)%1
    points=[Point(**v) for v in shape['path']];pairs=list(zip(points,points[1:]+points[:1]));total=sum(a.distance_to(b) for a,b in pairs)
    best=(float('inf'),0);traveled=0
    for a,b in pairs:
        dx,dy=b.x-a.x,b.y-a.y;length=a.distance_to(b)
        if length<=1e-12:continue
        t=max(0,min(1,((p['x']-a.x)*dx+(p['y']-a.y)*dy)/(length*length)))
        d=math.hypot(a.x+t*dx-p['x'],a.y+t*dy-p['y'])
        if d<best[0]:best=(d,(traveled+t*length)/total)
        traveled+=length
    return best[1]%1

def info(project,p):
    if set(p)!={'id','kind'}:raise ValueError('Choose one operation and picking mode.')
    op,source=operation(project,p);kind=p['kind'];shape=contour(project,op,kind=='tabs') if kind in {'tabs','start'} else {}
    return dict(status='picking',fingerprint=fingerprint(project),operation=op.to_dict(),kind=kind,
        eligible_ids=[i for i,e in source.items() if available(project,e) and (kind in {'boundaries','islands'} or i in op.entity_ids)],
        shape=shape,tab_points=[point_at(shape,f) for f in op.manual_tab_positions] if kind=='tabs' and op.tab_mode=='manual' else [])

def prepare(project,p):
    if p.get('fingerprint')!=fingerprint(project):raise ValueError('The drawing or setup changed. Reopen the graphical picker.')
    if set(p)-{'id','kind','fingerprint','index','points','tab_width','tab_height','ids','automatic'}:raise ValueError('Unknown graphical setting.')
    op,source=operation(project,p);kind=p['kind'];before=op.to_dict()
    if kind=='start':
        shape=contour(project,op);index=p.get('index')
        if type(index) is not int or not 0<=index<len(shape['starts']):raise ValueError('Choose a marked preferred start.')
        old_tabs=[point_at(contour(project,op,True),f) for f in op.manual_tab_positions] if op.tab_mode=='manual' else []
        op.start_index=index
        if old_tabs:op.manual_tab_positions=sorted(fraction_at(contour(project,op,True),v) for v in old_tabs)
    elif kind=='tabs':
        points=p.get('points');w=p.get('tab_width');h=p.get('tab_height')
        if not isinstance(points,list) or not 1<=len(points)<=100:raise ValueError('Place 1–100 manual tab centers. Use the holding editor to remove all tabs.')
        if any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in (w,h)):raise ValueError('Tab width and retained height must be positive finite distances.')
        if h>=op.target_depth:raise ValueError('Tab height must be less than the cutting depth.')
        side=OffsetSide(op.entity_sides.get(op.entity_ids[0],op.side.value))
        if side==OffsetSide.INSIDE and tp.entity_perimeter(source[op.entity_ids[0]])<op.female_tab_min_perimeter:raise ValueError('The female-perimeter threshold suppresses tabs here; review it in the holding editor.')
        shape=contour(project,op,True);values=sorted(fraction_at(shape,v) for v in points)
        if len({round(v,9) for v in values})!=len(values):raise ValueError('Choose distinct tab centers.')
        op.tab_mode='manual';op.manual_tab_positions=values;op.tab_count=len(values);op.tab_width=w;op.tab_height=h
    else:
        ids=p.get('ids',[])
        if not isinstance(ids,list) or not all(isinstance(i,str) for i in ids) or len(ids)!=len(set(ids)) or any(i not in source or not available(project,source[i]) for i in ids):raise ValueError('Choose unique visible, unlocked contours.')
        if kind=='order':
            if p.get('automatic') is True:ids=[]
            elif set(ids)!=set(op.entity_ids):raise ValueError('Pick every profile contour once.')
            from manufacturing_management import prepare as management_prepare
            revised,details=management_prepare(project,dict(action='profile_controls',params=dict(id=op.id,entity_sides=op.entity_sides,manual_contour_order=ids)),lambda _:None)
            project.operations=revised.operations;op=next(o for o in project.operations if o.id==op.id)
        else:
            if kind=='boundaries':
                if not ids:raise ValueError('Pick at least one outer boundary.')
                op.entity_ids=ids
            else:op.island_entity_ids=ids
            if set(op.entity_ids)&set(op.island_entity_ids):raise ValueError('A contour cannot be both boundary and island.')
            regions=tp.classify_island_fill_regions([source[i] for i in [*op.entity_ids,*op.island_entity_ids]])
            if {o.id for o,_ in regions}!=set(op.entity_ids) or {e.id for _,islands in regions for e in islands}!=set(op.island_entity_ids):raise ValueError('Every island must be inside a chosen outer boundary; nested outlines cannot both be outer boundaries.')
    if before==op.to_dict():raise ValueError('No graphical changes to review.')
    return dict(kind=kind,before=before,after=op.to_dict(),manual_tabs_preserved=kind=='start' and bool(before.get('manual_tab_positions')))
