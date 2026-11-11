"""Explicit, separately reviewed final tab-release stage.

No restraint is inferred from a drawing group, vacuum, sheet-edge connectivity or
part size. Executable plans require an operator's explicit independent-restraint
contract for EVERY physical body. Unknown restraint still permits an audit, not
an executable automatic recommendation. Existing retained-X planning is separate.
"""
from dataclasses import replace
import copy
import math

from .model import (CamProject, RouterOffsetOperation, TabReleaseOperation,
                    DrillCirclesOperation, EngraveOperation, IslandFillOperation,
                    OffsetSide, effective_tool, new_id)
from .plates import Plate, make_plate_project
from .layout import entity_bounds
from .workflow import ordered_operations
from .tab_release import CleanupReview, _hash, _canonical, _clip, _area, _sweep, pc, SCALE

VERSION=2
RESTRAINT='independently_fixed_in_original_footprint_below_stock_top'


def motion_digest(generated,origin=(0,0)):
    return _hash(_canonical([[b.operation_id,b.tool_station,b.metadata.get('spindle_rpm'),
        [[m.kind.value,m.end.x-origin[0],m.end.y-origin[1],m.end.z,m.feed,
          None if m.center is None else [m.center.x-origin[0],m.center.y-origin[1]],m.dwell_seconds]
         for m in b.motions]] for b in generated.blocks]))


def release_scope(project, plate_data):
    plate=Plate.from_dict(plate_data)
    source=project.metadata.get('source_plate')
    if source:
        if _canonical(source)!=_canonical(plate_data):
            raise ValueError('Release stage belongs to a different source plate.')
        plate=Plate(plate.number,0,0,plate.width,plate.height,plate.name)
        declared=project.metadata.get('plates',[])
        if declared and (len(declared)!=1 or Plate.from_dict(declared[0]).bounds!=plate.bounds):
            raise ValueError('Local release plate changed; review again.')
    else:
        declared=project.metadata.get('plates',[])
        if declared:
            current=next((Plate.from_dict(p) for p in declared if p['number']==plate.number),None)
            if current is None or current.bounds!=plate.bounds:
                raise ValueError('Release plate changed; review again.')
        elif project.metadata.get('sheet_layout'):
            from .layout import SheetLayout,sheet_rectangles
            s=project.metadata['sheet_layout']
            boxes=sheet_rectangles(SheetLayout(s.get('label',''),s['width'],s['height'],s['columns'],s['rows'],s.get('origin_x',0),s.get('origin_y',0)))
            if plate.number>len(boxes) or boxes[plate.number-1]!=plate.bounds:
                raise ValueError('Release sheet layout changed; review again.')
    result=make_plate_project(project,plate)
    if result.crossing_entity_ids:
        raise ValueError('Release review requires unambiguous plate ownership; geometry crosses the plate.')
    if project.metadata.get('patchwork_areas'):
        raise ValueError('Full release on patchwork stock is not supported by this review.')
    return result.project,plate


def scope_fingerprint(project,plate_data):
    local,plate=release_scope(project,plate_data)
    data=local.to_dict()
    data['operations']=[o for o in data['operations'] if o['kind']!='tab_release']
    metadata=data['metadata']
    from .output_workflow import _OUTPUT_ONLY_JOB_SETUP_KEYS
    for key in ('source_plate','plates','sheet_layout','active_plate_number','active_layer','cycle_optimization'):
        metadata.pop(key,None)
    if isinstance(metadata.get('job_setup'),dict):
        metadata['job_setup']={k:v for k,v in metadata['job_setup'].items() if k not in _OUTPUT_ONLY_JOB_SETUP_KEYS}
    return _hash(_canonical({'version':VERSION,'entities':data['entities'],'operations':data['operations'],
        'tools':data['tool_library'],'metadata':metadata,'keepouts':data.get('keepouts',[]),
        'units':local.units,'plate':plate_data}))


def normalize_source(project,source_id):
    clone=CamProject.from_dict(project.to_dict())
    source=next((o for o in clone.operations if o.id==source_id),None)
    if type(source) is not RouterOffsetOperation or not source.enabled:
        raise ValueError('Choose an enabled ordinary outside profile as the release source.')
    # All original tab definitions remain. The separate final stage replaces
    # any automatic/retained cleanup attached to the source profile operation.
    source.tab_release_plan={};source.remove_tabs_after=False
    clone.operations=[o for o in clone.operations if not (isinstance(o,TabReleaseOperation) and o.source_operation_id==source_id)]
    return clone,source


def _components(material):
    tree=_clip(material,kind=pc.CT_UNION,tree=True)
    result=[]
    def visit(node):
        if node.Contour and not node.IsHole:
            result.append([node.Contour]+[c.Contour for c in node.Childs if c.IsHole])
        for c in node.Childs:visit(c)
    visit(tree)
    return result


class FullReleaseReview:
    def __init__(self,project,source_id,plate):
        from .toolpath import generate_closed_router_offset,_closed_entity_points,_region_clipper_path
        from .stock import generate_operation_toolpath
        # Match native reload's numeric types so a reviewed acknowledgment is
        # stable across apply/reopen (e.g. width 40 versus serialized 40.0).
        self.plate_data=Plate.from_dict(plate.to_dict()).to_dict()
        self.input_fingerprint=scope_fingerprint(project,self.plate_data)
        self.prepared,self.source=normalize_source(project,source_id)
        self.fingerprint=scope_fingerprint(self.prepared,self.plate_data)
        self.local,self.plate=release_scope(self.prepared,self.plate_data)
        source=next(o for o in self.local.operations if o.id==source_id)
        if source.entity_ids!=self.source.entity_ids:
            raise ValueError('The complete source operation must belong to one plate.')
        self.required_ids=[o.id for o in ordered_operations([o for o in self.local.operations if o.enabled])]
        if len(set(self.required_ids))!=len(self.required_ids):raise ValueError('Duplicate operation identity.')
        self.later_ids=self.required_ids[self.required_ids.index(source_id)+1:]
        self.prerequisites=[o for o in self.local.operations if o.enabled and o.id!=source_id]
        allowed=(DrillCirclesOperation,EngraveOperation,IslandFillOperation,RouterOffsetOperation)
        for op in self.prerequisites:
            if type(op) not in allowed:
                raise ValueError(f'{op.name}: dependent/rest/thread release analysis is not yet supported; review it separately.')
            if isinstance(op,RouterOffsetOperation):
                sides={op.entity_sides.get(k,op.side.value) for k in op.entity_ids}
                if sides!={'inside'} or op.remove_tabs_after or op.tab_count or op.tab_mode!='even':
                    raise ValueError(f'{op.name}: only tab-free inside profiles may precede this final release; another outside/release scope requires separate review.')
        isolated=CamProject.from_dict(self.prepared.to_dict())
        isolated.keepouts=[]  # Checked against actual prior/stage motions below.
        for o in isolated.operations:
            if o.id!=source_id:o.enabled=False
        iso_source=next(o for o in isolated.operations if o.id==source_id)
        self.tabs=CleanupReview(isolated,iso_source,plate)
        self.parts=copy.deepcopy(self.tabs.parts)
        self.spans=self.tabs.spans;self.by_id=self.tabs.by_id
        self.tolerance=self.tabs.tolerance;self.radius=self.tabs.radius
        entities={e.id:e for e in self.local.entities}
        self.ownership={}
        for owner in self.tabs.owners:
            e=entities[owner]
            group=str(e.metadata.get('mounting_part_id') or e.metadata.get('compound_id') or e.metadata.get('group_id') or '')
            self.ownership[owner]={'outer_id':owner,'assembly_id':group or owner,'feature_ids':[],
                                   'operation_ids':[source_id]}
        # An assembly groups review rows, not physical restraint. Distinct outer
        # bodies remain distinct and need individual confirmation.
        polygons={o:[_region_clipper_path(_closed_entity_points(entities[o]))] for o in self.parts}
        for i,first in enumerate(self.tabs.owners):
            for second in self.tabs.owners[i+1:]:
                if _area(_clip(polygons[first],polygons[second],pc.CT_INTERSECTION))>self.tolerance**2*10:
                    raise ValueError('Overlapping/nested outside bodies have ambiguous physical ownership; separate the release parts explicitly.')
        relevant={k for o in self.local.operations if o.enabled for k in o.entity_ids}
        for key in relevant-set(self.parts):
            e=entities[key]
            try:polygon=[_region_clipper_path(_closed_entity_points(e))]
            except ValueError:
                raise ValueError(f'Feature {key}: open/annotation ownership requires manual release review.') from None
            area=_area(polygon)
            owners=[owner for owner,outer in polygons.items()
                    if _area(_clip(polygon,outer,pc.CT_INTERSECTION))>=area-self.tolerance**2*10]
            if len(owners)!=1:
                raise ValueError(f'Feature {key} has {len(owners)} candidate finished-part owners; assign/review it before automatic release.')
            owner=owners[0];record=self.ownership[owner]
            record['feature_ids'].append(key)
            uses=[o for o in self.local.operations if o.enabled and key in o.entity_ids]
            record['operation_ids']=list(dict.fromkeys(record['operation_ids']+[o.id for o in uses]))
            thickness=self.local.metadata['job_setup']['material_thickness']
            if any(o.target_depth>=thickness-self.tolerance and
                   (isinstance(o,DrillCirclesOperation) or isinstance(o,RouterOffsetOperation)
                    and o.entity_sides.get(key,o.side.value)=='inside') for o in uses):
                self.parts[owner]=_clip(self.parts[owner],polygon)
        # Ensure the source's actual multi-operation entry/profile geometry is
        # the same one from which cleanup spans were extracted.
        real=generate_closed_router_offset(self.local,source)
        generated_prior={source.id:real}
        iso_local=CamProject.from_dict(self.local.to_dict())
        for o in iso_local.operations:
            if o.id!=source_id:o.enabled=False
        baseline=generate_closed_router_offset(iso_local,next(o for o in iso_local.operations if o.id==source_id))
        if [b.motions for b in real.blocks]!=[b.motions for b in baseline.blocks]:
            raise ValueError('Other operations change the source entry/path geometry; this release needs manual dependency review.')
        plane=-source.tab_floor_depth
        sweeps=list(self.tabs.profile_sweep)
        for op in self.prerequisites:
            generated=generate_operation_toolpath(self.local,op,optimize=False)
            generated_prior[op.id]=generated
            tool=effective_tool(self.local,op)
            sweeps+=_sweep([b.motions for b in generated.blocks],tool.diameter/2,plane,self.tolerance)
        self.base=_clip(self.tabs.stock,sweeps)
        from .workflow_features import keepout_violations
        fixture_issues=keepout_violations(self.local,set(self.required_ids),generated_prior)
        if fixture_issues:raise ValueError(fixture_issues[0].message)
        self.prerequisite_motion_digests={key:motion_digest(value) for key,value in generated_prior.items()}
        self.tabs.parts=self.parts
        self.tabs._assert_connected(_clip(self.base,kind=pc.CT_UNION,tree=True))
        # Other machining must not erode a bridge silently.
        for span in self.spans:
            remaining=_clip(span['bridge'],sweeps)
            if _area(remaining)<_area(span['bridge'])-self.tolerance**2*10:
                raise ValueError(f'Later/other machining cuts tab {span["id"]}; restore original support before release planning.')
        self.restrained_ids=set()
        self.order=self.propose_order()

    def propose_order(self):
        if self.tabs.locked:return [s['id'] for s in self.spans]
        owners=self.tabs.owners
        entities={e.id:e for e in self.local.entities}
        # Edge-first deterministic proposal; never advertised as global optimum.
        def rank(owner):
            x0,y0,x1,y1=entity_bounds(entities[owner])
            return (min(x0,y0,self.plate.width-x1,self.plate.height-y1),y0,x0,owner)
        return self.order_for_parts(sorted(owners,key=rank))

    def order_for_parts(self,owners):
        if len(owners)!=len(self.parts) or set(owners)!=set(self.parts):
            raise ValueError('Part order must name every physical body exactly once.')
        if self.tabs.locked:
            if owners!=self.tabs.owners:raise ValueError('Existing locked/manual order cannot be rearranged here.')
            return [s['id'] for s in self.spans]
        result=[];seen=set()
        for owner in owners:
            for span in self.spans:
                if span['owner']==owner and span['id'] not in seen:
                    group=self.tabs.group(span['id'])
                    for member in self.spans:
                        if member['id'] in group and member['id'] not in seen:
                            result.append(member['id']);seen.add(member['id'])
        return result

    def _detached(self,material):
        w,h=self.plate.width*SCALE,self.plate.height*SCALE
        result=set()
        for rings in _components(material):
            if any(x<=1 or y<=1 or x>=w-1 or y>=h-1 for x,y in rings[0]):continue
            for owner,polygon in self.parts.items():
                if _area(_clip(rings,polygon,pc.CT_INTERSECTION))>self.tolerance**2*10:result.add(owner)
        return result

    def _loose_waste(self,material):
        w,h=self.plate.width*SCALE,self.plate.height*SCALE
        result=[]
        for rings in _components(material):
            if any(x<=1 or y<=1 or x>=w-1 or y>=h-1 for x,y in rings[0]):continue
            if _area(rings)<=self.tolerance**2*10:continue
            if not any(_area(_clip(rings,polygon,pc.CT_INTERSECTION))>self.tolerance**2*10
                       for polygon in self.parts.values()):
                result.append(rings)
        return result

    def audit(self,order):
        from .toolpath import Motion,MotionKind,_offset_region_paths
        if len(order)!=len(self.spans) or len(set(order))!=len(order) or set(order)!=set(self.by_id):
            raise ValueError('Full release must contain every original cleanup span exactly once.')
        if self.tabs.locked and order!=[s['id'] for s in self.spans]:
            raise ValueError('Existing locked/manual contour order must be preserved.')
        material=self.base;released=set();events=[];travel=[];hazards=[];previous=None
        warnings=[];seen_waste=[]
        def record_waste(number):
            for rings in self._loose_waste(material):
                # Later cuts can shrink/split a previously reported scrap. Its
                # original highlighted footprint already covers those remnants.
                if any(_area(_clip(rings,prior,pc.CT_INTERSECTION))>self.tolerance**2*10 for prior in seen_waste):
                    continue
                seen_waste.append(rings)
                points=[(x/SCALE,y/SCALE) for ring in rings for x,y in ring]
                bounds=[min(x for x,y in points),min(y for x,y in points),max(x for x,y in points),max(y for x,y in points)]
                warnings.append({'code':'detached_waste','after_span':number,'bounds':bounds,
                    'area':_area(rings),'paths':[[[x/SCALE,y/SCALE] for x,y in ring] for ring in rings],
                    'message':f'Loose waste after cleanup {number}' if number else 'Loose waste from prerequisite machining'})
        record_waste(0)
        # Shrink by three sampling tolerances to avoid treating intended tangent
        # edge contact as an interior collision. This is a stated numeric bound.
        interiors={owner:_offset_region_paths(paths,-3*self.tolerance) for owner,paths in self.parts.items()}
        for number,key in enumerate(order,1):
            span=self.by_id[key]
            motions=span['motions']
            check=([Motion(MotionKind.RAPID,previous)] if previous else [])+motions
            check=[replace(m,kind=MotionKind.LINEAR) if m.kind==MotionKind.RAPID else m for m in check]
            swept=_sweep([check],self.radius,0,self.tolerance)
            for owner in released:
                if _area(_clip(swept,interiors[owner],pc.CT_INTERSECTION))>self.tolerance**2*10:
                    hazards.append(f'Cleanup {number} enters already-released body {owner} below its confirmed top surface.')
            # Known fixtures are checked at their declared top + clearance Z.
            for zone in self.local.keepouts:
                if not zone.enabled:continue
                from .toolpath import _region_clipper_path
                footprint=[_region_clipper_path(zone.points)]
                zone_sweep=_sweep([check],self.radius,zone.clearance_z,self.tolerance)
                if _area(_clip(zone_sweep,footprint,pc.CT_INTERSECTION))>self.tolerance**2*10:
                    hazards.append(f'Cleanup {number} violates fixture keepout {zone.name}; do not change Safe Z automatically.')
            if previous:
                travel.append(math.hypot(motions[0].end.x-previous.x,motions[0].end.y-previous.y))
            material=_clip(material,span['sweep'])
            record_waste(number)
            detached=self._detached(material)
            new=detached-released
            if new:events.append({'after_span':number,'span_id':key,'part_ids':sorted(new)})
            released|=new;previous=motions[-1].end
        if released!=set(self.parts):
            hazards.append('Some bodies remain connected after every tab span; full-release ownership cannot be established.')
        return {'events':events,'hazards':list(dict.fromkeys(hazards)),'warnings':warnings,
                'travel_distance':sum(travel),'cleanup_spans':len(order)}

    def warning_review_key(self,order,audit):
        return _hash({'fingerprint':self.fingerprint,'order':order,'warnings':audit['warnings']})

    def warning_issues(self,order,audit,contract):
        if audit['warnings'] and contract.get('waste_warning_review')!=self.warning_review_key(order,audit):
            return ['Review the highlighted loose-waste warnings, then acknowledge them to proceed. This does not certify that scraps remain stationary.']
        return []

    def contract_issues(self,contract):
        issues=[]
        if contract.get('restraint')!=RESTRAINT or set(contract.get('confirmed_parts',[]))!=set(self.parts):
            issues.append('Required: confirm independent restraint for EVERY listed physical body, including each member of a drawing group. It must remain in its original XY footprint and at/below the stock top throughout cutting and later travel.')
        if not str(contract.get('evidence_note','')).strip():
            issues.append('Required: document the reviewed fixture/restraint method. Unknown or unverified vacuum hold-down is not an automatic-release qualification.')
        return issues

    def plan(self,order,contract,require_qualified=True):
        from .toolpath import GeneratedToolpath
        audit=self.audit(order)
        issues=self.contract_issues(contract)+audit['hazards']+self.warning_issues(order,audit,contract)
        if require_qualified and issues:raise ValueError('\n'.join(issues))
        cleanup=GeneratedToolpath([replace(self.by_id[order[0]]['block'],operation_id=self.source.id,
                    motions=[m for key in order for m in self.by_id[key]['motions']])])
        return {'version':VERSION,'source_operation_id':self.source.id,'plate':self.plate_data,
                'fingerprint':self.fingerprint,'input_fingerprint':self.input_fingerprint,
                'required_operation_ids':self.required_ids,'ownership':copy.deepcopy(self.ownership),
                'prerequisite_motion_digests':self.prerequisite_motion_digests,
                'cleanup_motion_digest':motion_digest(cleanup),
                'order':list(order),'contract':copy.deepcopy(contract),'audit':audit,'review_issues':issues}


def install_release_stage(project,plan):
    if scope_fingerprint(project,plan['plate'])!=plan['input_fingerprint']:
        raise ValueError('Document/machining inputs changed during release review; review again.')
    review=FullReleaseReview(project,plan['source_operation_id'],Plate.from_dict(plan['plate']))
    verified=review.plan(plan['order'],plan['contract'])
    output,source=normalize_source(project,review.source.id)
    prior=[o for o in project.operations if isinstance(o,TabReleaseOperation) and o.source_operation_id==review.source.id]
    if len(prior)>1:raise ValueError('Only one final release stage per review is supported.')
    values=source.to_dict();values.pop('kind');values['side']=source.side
    values.update(id=prior[0].id if prior else new_id('operation'),name='Reviewed final tab release',
                  entity_ids=list(review.parts),entity_sides={},side=OffsetSide.OUTSIDE,
                  manual_contour_order=[],sequence_role='finish_cutoff',sequence_locked=True,
                  depends_on_ids=list(review.required_ids),source_operation_id=source.id,
                  release_plan=verified,tab_release_plan={},remove_tabs_after=False)
    stage=TabReleaseOperation(**values)
    output.operations.append(stage)
    before=[o.id for o in ordered_operations([o for o in output.operations if o.enabled and o.id!=stage.id])]
    after=[o.id for o in ordered_operations([o for o in output.operations if o.enabled])]
    if [i for i in after if i!=stage.id]!=before or any(after.index(i)>after.index(stage.id) for i in stage.depends_on_ids):
        raise ValueError('Release dependencies conflict with existing sequence locks; resolve Cut Order first.')
    return output,stage


def validate_stage(project,stage):
    plan=stage.release_plan
    if not isinstance(plan,dict) or plan.get('version')!=VERSION or plan.get('source_operation_id')!=stage.source_operation_id:
        raise ValueError('Invalid full-release stage; open the release review.')
    if scope_fingerprint(project,plan['plate'])!=plan.get('fingerprint'):
        raise ValueError('Full-release plan is stale after source/feature/stock/tool/ownership/operation changes; review again.')
    source=next((o for o in project.operations if o.id==stage.source_operation_id),None)
    if type(source) is not RouterOffsetOperation or source.remove_tabs_after or source.tab_release_plan:
        raise ValueError('Release source must keep every original tab until the separate final stage.')
    if (set(stage.entity_ids)!=set(plan['ownership']) or stage.side!=OffsetSide.OUTSIDE
        or stage.entity_sides or stage.manual_contour_order):
        raise ValueError('Release-stage body ownership/side was edited; re-open the release review.')
    # A release stage cannot independently override its source cutting settings.
    allowed={'kind','id','name','entity_ids','entity_sides','side','manual_contour_order','sequence_role',
             'sequence_locked','depends_on_ids','source_operation_id','release_plan'}
    for key,value in source.to_dict().items():
        if key not in allowed and stage.to_dict().get(key)!=value:
            raise ValueError(f'Release-stage {key} differs from its source; re-review instead of editing the stage as a cut.')
    local,_=release_scope(project,plan['plate'])
    stages=[o for o in local.operations if o.enabled and isinstance(o,TabReleaseOperation)]
    if len(stages)!=1 or stages[0].id!=stage.id:
        raise ValueError('Only one enabled final-release stage may own a plate; duplicate release is blocked.')
    required={o.id for o in local.operations if o.enabled and not isinstance(o,TabReleaseOperation)}
    if set(stage.depends_on_ids)!=required or set(plan['required_operation_ids'])!=required:
        raise ValueError('Release stage must follow every enabled machining operation on its plate.')
    actual=ordered_operations([o for o in project.operations if o.enabled])
    indexes={o.id:i for i,o in enumerate(actual)}
    if stage.id not in indexes or any(indexes.get(k,math.inf)>=indexes[stage.id] for k in required):
        raise ValueError('Release stage is not after its prerequisite operations.')
    return plan


def generate_release_stage(project,stage):
    from .toolpath import GeneratedToolpath,ToolPoint
    plan=validate_stage(project,stage)
    review=FullReleaseReview(project,stage.source_operation_id,Plate.from_dict(plan['plate']))
    verified=review.plan(plan['order'],plan['contract'])
    if (verified['ownership']!=plan['ownership'] or verified['audit']!=plan['audit']
        or verified['prerequisite_motion_digests']!=plan['prerequisite_motion_digests']
        or verified['cleanup_motion_digest']!=plan.get('cleanup_motion_digest')):
        raise ValueError('Release ownership/chronology changed; review again.')
    dx,dy=(0,0) if project.metadata.get('source_plate') else (plan['plate']['x'],plan['plate']['y'])
    selected=[review.by_id[k] for k in plan['order']]
    motions=[]
    for span in selected:
        for m in span['motions']:
            motions.append(replace(m,end=ToolPoint(m.end.x+dx,m.end.y+dy,m.end.z),
                center=None if m.center is None else type(m.center)(m.center.x+dx,m.center.y+dy)))
    metadata=dict(selected[0]['block'].metadata)
    for key in ('entity_id','path_index','contour_order_index','containment_depth'):metadata.pop(key,None)
    metadata.update(entity_ids=list(review.parts),role='tab_full_release',release_plan_digest=_hash(plan),
                    release_events=plan['audit']['events'],release_span_ids=plan['order'])
    return GeneratedToolpath([replace(selected[0]['block'],operation_id=stage.id,motions=motions,
        label='Reviewed final release — independently restrained parts',metadata=metadata)])


def check_prepared_release(project,stage,generated):
    from .toolpath import GeneratedToolpath
    plan=validate_stage(project,stage)
    if len(generated.blocks)!=1 or generated.blocks[0].metadata.get('release_plan_digest')!=_hash(plan):
        raise ValueError('Prepared final-release motion does not match the reviewed contract; regenerate Output.')
    origin=(0,0) if project.metadata.get('source_plate') else (plan['plate']['x'],plan['plate']['y'])
    source_block=replace(generated.blocks[0],operation_id=stage.source_operation_id)
    if motion_digest(GeneratedToolpath([source_block]),origin)!=plan.get('cleanup_motion_digest'):
        raise ValueError('Prepared final-release motion/order changed after review; regenerate Output.')


def check_prepared_prerequisites(project,stage,generated_paths):
    plan=validate_stage(project,stage)
    origin=(0,0) if project.metadata.get('source_plate') else (plan['plate']['x'],plan['plate']['y'])
    for key,digest in plan['prerequisite_motion_digests'].items():
        if key not in generated_paths or motion_digest(generated_paths[key],origin)!=digest:
            raise ValueError('Prepared prerequisite motion changed or contains earlier tab removal; regenerate the whole release review/output.')
