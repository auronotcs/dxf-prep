"""Bounded waste clearing followed by a deliberately tabbed small-tool finish.

The whole bottom skin is protected during clearing, so final bridge locations
cannot be undercut by either cutter. Exterior sheet waste is NOT an implicit
pocket: outside contours are finished last, with their surrounding stock intact.
"""
import copy
import hashlib
import json
import math
from dataclasses import replace

from .model import IslandFillOperation, RestMachiningOperation, RouterOffsetOperation, FillStrategy, OffsetSide, new_id
from .profile_second_tool import validate_settings, profile_loops
from .cleanout_recovery import build_recovery_operations


def is_hybrid(operation):
    return operation.profile_retention.get('workflow') == 'waste_then_tabbed_finish'


def _canonical(value):
    if isinstance(value, bool) or value is None:return value
    if isinstance(value, (int,float)):return float(value)
    if isinstance(value, dict):return {k:_canonical(v) for k,v in value.items()}
    if isinstance(value, (tuple,list)):return [_canonical(v) for v in value]
    return value


def _fingerprint(project, members):
    ids=set()
    operations=[]
    for operation in members:
        values=copy.deepcopy(operation.to_dict())
        values['profile_retention'].pop('snapshot',None)
        operations.append(values)
        ids.update(operation.entity_ids)
        ids.update(getattr(operation,'island_entity_ids',()))
    entities={e.id:e for e in project.entities}
    if not ids <= entities.keys():raise ValueError('Hybrid group has missing artwork; recreate the group.')
    geometry=[]
    for identity in sorted(ids):
        values=entities[identity].to_dict()
        geometry.append(values)
    payload=dict(operations=operations,geometry=geometry,units=project.units,
        thickness=project.metadata.get('job_setup',{}).get('material_thickness'),
        tools=[project.tool_library.by_station(s).to_dict() if project.tool_library.by_station(s) else None
               for s in sorted({o.tool_station for o in members})])
    raw=json.dumps(_canonical(payload),sort_keys=True,separators=(',',':'),allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def capture_group(project,members):
    """Only at creation, or after validated plate localization; never on edits."""
    ids=[o.id for o in members]
    for operation in members:operation.profile_retention['member_ids']=ids
    fingerprint=_fingerprint(project,members)
    for operation in members:operation.profile_retention['snapshot']=fingerprint


def validate_group(project,operation):
    hold=operation.profile_retention
    try:
        if hold['version']!=2 or hold['units']!=project.units:raise ValueError('Hybrid retention version/units changed.')
        thickness=float(project.metadata.get('job_setup',{}).get('material_thickness',0))
        skin=float(hold['skin'])
        if not math.isfinite(thickness) or thickness!=hold['thickness'] or not 0<skin<thickness:
            raise ValueError('Hybrid stock thickness or skin changed.')
        by_id={o.id:o for o in project.operations}
        ids=hold['member_ids']
        if not ids or len(set(ids))!=len(ids) or operation.id not in ids or any(i not in by_id for i in ids):
            raise ValueError('Hybrid group is incomplete.')
        members=[by_id[i] for i in ids]
        if _canonical(operation.to_dict()) != _canonical(by_id[operation.id].to_dict()):
            raise ValueError('A hybrid stage was changed outside its saved group.')
        for index,member in enumerate(members):
            contract=member.profile_retention
            if (not member.enabled or contract.get('group_id')!=hold['group_id']
                    or contract.get('member_ids')!=ids or contract.get('snapshot')!=hold['snapshot']):
                raise ValueError('Hybrid stages were removed, disabled or reassigned.')
            if not set(ids[:index])<=set(member.depends_on_ids):
                raise ValueError('Hybrid clearing must precede the tabbed finish.')
            if contract['stage']=='clear':
                if not 0<member.target_depth<=thickness-skin+1e-9:
                    raise ValueError('A clearing stage would cut the protected bottom skin.')
            elif contract['stage']=='finish' and isinstance(member,RouterOffsetOperation):
                if (member.tab_count<2 or not 0<member.tab_height<=skin+1e-9 or member.tab_width<=0
                        or member.remove_tabs_after or member.tab_release_plan or member.onion_skin
                        or member.cut_intent!='through' or member.female_tab_min_perimeter!=0
                        or member.contour_order!='inside_first' or member.manual_contour_order):
                    raise ValueError('Hybrid finish must retain tabs on every contour, inside first, with no automatic tab cleanup.')
            else:raise ValueError('Unknown hybrid stage.')
        if _fingerprint(project,members)!=hold['snapshot']:
            raise ValueError('Hybrid geometry, tools or machining settings changed.')
    except (KeyError,TypeError,OverflowError) as exc:
        raise ValueError('Invalid hybrid group; recreate Clear waste + tabbed finish.') from exc
    except ValueError as exc:
        raise ValueError(str(exc)+' Recreate Clear waste + tabbed finish so both tools and tab supports are planned together.') from exc


def build_hybrid_group(project,source,settings):
    from .toolpath import classify_island_fill_regions
    tool,thickness,depth=validate_settings(project,source,settings)
    main=project.tool_library.by_station(source.tool_station)
    skin=settings['skin']
    if source.cut_intent!='through' or abs(source.target_depth-thickness-source.breakthrough)>1e-8:
        raise ValueError('Clear waste + tabbed finish requires Through stock depth intent and the actual stock thickness.')
    if source.tab_count<2 or not 0<source.tab_height<=skin or source.tab_width<=0:
        raise ValueError('Enable at least two bridge tabs with positive width and height no greater than the retained skin. '
                         'For taller tabs, increase the retained skin; clearing cannot remove their supports.')
    if tool.cutting_edge_length is not None and source.target_depth>tool.cutting_edge_length+1e-9:
        raise ValueError('The small cutter must reach the complete through depth, including breakthrough.')
    for key,diameter in (('main_stepover',main.diameter),('stepover',tool.diameter)):
        value=float(settings[key])
        if not math.isfinite(value) or not 0<value<=diameter/2:
            raise ValueError('Clearing stepovers must be positive and at most 50% of each cutter diameter.')
    if source.allowance or source.finish_axial_stock:
        raise ValueError('This hybrid mode finishes the walls to size (zero wall allowance). Set radial/axial finish stock to zero; bottom skin is separate.')
    if source.pass_depths:
        raise ValueError('Use maximum stepdown for hybrid clearing; custom through-profile increments cannot be reused above the skin.')
    if source.stepdown is None or not math.isfinite(source.stepdown) or source.stepdown<=0:
        raise ValueError('Set a positive main-tool maximum stepdown for waste clearing.')
    by_id={e.id:e for e in project.entities}
    if not source.entity_ids or not set(source.entity_ids)<=by_id.keys():
        raise ValueError('Select existing closed contours for hybrid clearing.')
    sides={i:OffsetSide(source.entity_sides.get(i,source.side.value)) for i in source.entity_ids}
    if any(s not in (OffsetSide.INSIDE,OffsetSide.OUTSIDE) for s in sides.values()):
        raise ValueError('Hybrid clearing requires closed inside/outside profiles.')
    inside=[i for i in source.entity_ids if sides[i]==OffsetSide.INSIDE]
    if not inside:
        raise ValueError('Select bounded INSIDE waste contours as well as any outside outlines. '
                         'Exterior sheet waste is not automatically pocketed; use contour rest for outside-only work.')
    # An inside contour defines waste. Include contained selected artwork as
    # islands, but not its enclosing outside part. Parity must agree with the
    # explicit compensation sides, never infer that a letter stroke is waste.
    from .toolpath import _closed_entity_points,_region_clipper_path,_clipper_difference
    paths={i:_region_clipper_path(_closed_entity_points(by_id[i])) for i in source.entity_ids}
    from .toolpath import pyclipper
    def contained(child,parent):
        return abs(pyclipper.Area(paths[child]))<abs(pyclipper.Area(paths[parent])) and not _clipper_difference([paths[child]],[paths[parent]])
    relevant=[i for i in source.entity_ids if i in inside or any(contained(i,p) for p in inside)]
    regions=classify_island_fill_regions([by_id[i] for i in relevant])
    outers=[e.id for e,_ in regions];islands=[e.id for _,holes in regions for e in holes]
    if set(outers)!=set(inside) or any(sides[i]!=OffsetSide.OUTSIDE for i in islands):
        raise ValueError('Inside waste/outside island roles conflict with nesting. Review compensation sides before clearing; artwork was not changed.')
    # All selected finish boundaries must fit the small cutter, including
    # outside-only outlines. Split loops remain review findings, not deletion.
    from .object_identity import object_identity_label
    blocked=[i for i in source.entity_ids if not profile_loops(project,source,i,tool.diameter,0)]
    if blocked:raise ValueError('Still too small for the finish cutter: '+', '.join(object_identity_label(project,i) for i in blocked))
    clearing=IslandFillOperation(source.name+' — waste clearing / skin retained',source.tool_station,outers,depth,source.safe_z,
        island_entity_ids=islands,stepover=settings['main_stepover'],stepdown=source.stepdown,
        cut_intent='partial',strategy=FillStrategy.CONCENTRIC,
        entry_mode='plunge',cut_direction=source.cut_direction,feed_rate_override=source.feed_rate_override,
        plunge_rate_override=source.plunge_rate_override,spindle_rpm_override=source.spindle_rpm_override,
        recipe_name=source.recipe_name,recipe_metadata=copy.deepcopy(source.recipe_metadata))
    operations=build_recovery_operations(project,clearing,{**settings,'include_rest':True})
    # A global rest group with no-work pockets would become an empty operation
    # when output is split by plate. Partition those pockets at planning time;
    # each rest source owns only regions with actual reachable rest motion.
    # Use the same final stock filter as generation, not merely nominal radii.
    from .toolpath import (_rest_region_loops,_generate_single_island_fill,_required_tool)
    from .rest_path_filter import source_clearance
    for rest in [o for o in operations if isinstance(o,RestMachiningOperation)]:
        primary=next(o for o in operations if o.id==rest.source_operation_id)
        needed=[];plain=[]
        for outer,holes in regions:
            if outer.id not in primary.entity_ids:continue
            region_source=replace(primary,entity_ids=[outer.id],island_entity_ids=[e.id for e in holes])
            # Use precisely the eventual hybrid geometry engine and loop order,
            # including for rectangles. This is an internal region calculation,
            # not a publishable retained-skin operation.
            region_source.profile_retention={'workflow':'waste_then_tabbed_finish'}
            previous_tool=_required_tool(project,primary.tool_station,operation=primary)
            def cleared_factory():
                path=_generate_single_island_fill(project,region_source,previous_tool,primary.stepover)
                return source_clearance(path,depth,main.diameter/2)
            loops=_rest_region_loops(_closed_entity_points(outer),[_closed_entity_points(e) for e in holes],
                main.diameter/2,tool.diameter/2,tool.diameter/2,settings['stepover'],
                settings['ignore_stock_less_than'],cleared_factory=cleared_factory)
            (needed if loops else plain).append((outer,holes))
        def geometry(records):
            return dict(entity_ids=[e.id for e,_ in records],island_entity_ids=[e.id for _,holes in records for e in holes])
        if not needed:
            operations.remove(rest)
        elif plain:
            standalone=replace(primary,id=new_id('operation'),name=primary.name+' / no rest needed',**geometry(plain))
            operations.insert(0,standalone)
            selection=geometry(needed)
            primary.entity_ids=selection['entity_ids'];primary.island_entity_ids=selection['island_entity_ids']
            rest.entity_ids=list(primary.entity_ids);rest.island_entity_ids=list(primary.island_entity_ids)
    operations.sort(key=lambda o:o.tool_station!=source.tool_station)
    finish=replace(source,id=new_id('operation'),name=f'Tabbed finish T{tool.station} — release tabs separately',
        tool_station=tool.station,allowance=0,finishing_pass=False,finish_axial_stock=0,
        finish_feed_override=None,finish_spindle_rpm_override=None,
        stepdown=settings['stepdown'],pass_depths=[],safe_z=settings['safe_z'],entry_mode='plunge',
        lead_in=0,lead_out=0,onion_skin=0,remove_tabs_after=False,tab_release_plan={},
        female_tab_min_perimeter=0,contour_order='inside_first',manual_contour_order=[],geometry_selector={},
        feed_rate_override=settings['feed_rate'],plunge_rate_override=settings['plunge_rate'],
        spindle_rpm_override=int(settings['spindle_rpm']),recipe_name='Reviewed hybrid small-tool finish',
        recipe_metadata={'qualification':'prototype','profile_second_tool_reviewed':True})
    operations.append(finish)
    group_id=new_id('hybrid')
    for index,operation in enumerate(operations):
        operation.profile_retention=dict(version=2,workflow='waste_then_tabbed_finish',group_id=group_id,
            stage='finish' if operation is finish else 'clear',units=project.units,thickness=thickness,skin=skin)
        operation.depends_on_ids=list(dict.fromkeys([*source.depends_on_ids,*operation.depends_on_ids,*[o.id for o in operations[:index]]]))
    planned=replace(project,operations=[*project.operations,*operations])
    capture_group(planned,operations)
    validate_group(planned,finish)
    return operations


def verify_finish_tabs(operation,path):
    """Do not silently pass a split fragment that failed to receive a bridge."""
    if not is_hybrid(operation) or operation.profile_retention['stage']!='finish':return
    from .toolpath import _raised_profile_tab_groups
    final=[b for b in path.blocks if abs(float(b.metadata.get('depth',0))-operation.target_depth)<1e-8]
    if not final:raise ValueError('Hybrid finish has no verifiable final-depth contours.')
    for block in final:
        if not _raised_profile_tab_groups(block.motions,operation):
            identity=block.metadata.get('entity_id','unknown contour')
            raise ValueError(f'Hybrid finish could not retain a bridge on {identity}. Review tab width/placement or split offsets; no unheld fragment is released.')


def rebase_plate_groups(original,output):
    """Translation/pruning is trusted only after the unmodified group verifies."""
    seen=set()
    original_ops={o.id:o for o in original.operations}
    for operation in output.operations:
        if not is_hybrid(operation):continue
        group_id=operation.profile_retention['group_id']
        if group_id in seen:continue
        validate_group(original,original_ops[operation.id]);seen.add(group_id)
        members=[o for o in output.operations if o.profile_retention.get('group_id')==group_id]
        capture_group(output,members)
