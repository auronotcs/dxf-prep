"""Integrated inside/outside profiling with a retained skin and smaller cutter.

These operations never release the part or slug.  This is programmed retention,
not a physical fixture simulation: flat stock, correct Z and hold-down still matter.
"""
import copy
import math
from dataclasses import replace

from .model import RouterOffsetOperation, ProfileRestMachiningOperation, OffsetSide, ToolKind
from .operation_links import capture_profile_rest_dependency
from .object_identity import object_identity_label
from .layout import entity_bounds


def validate_settings(project, source, settings):
    if settings.get('reviewed') is not True:
        raise ValueError('Review and confirm the second-tool feeds, plunge, reach and retained skin first.')
    main = project.tool_library.by_station(source.tool_station)
    tool = project.tool_library.by_station(settings['station'])
    if (main is None or tool is None or tool.kind != ToolKind.END_MILL
            or tool.spindle_mode == 'disabled' or tool.station == 8
            or not 0 < tool.diameter < main.diameter):
        raise ValueError('Choose a loaded smaller rotary flat end mill for the second tool.')
    for key in ('feed_rate', 'plunge_rate', 'spindle_rpm', 'safe_z', 'stepdown', 'skin'):
        if not math.isfinite(float(settings[key])) or float(settings[key]) <= 0:
            raise ValueError('Second-tool feeds, RPM, clearance, stepdown and retained skin must be finite and positive.')
    threshold = float(settings['ignore_stock_less_than'])
    if not math.isfinite(threshold) or not 0 <= threshold < (main.diameter-tool.diameter)/2:
        raise ValueError('Ignore-stock threshold must be nonnegative and smaller than the difference in tool radii.')
    rpm = settings['spindle_rpm']
    if not float(rpm).is_integer() or (tool.min_rpm is not None and rpm < tool.min_rpm) or (tool.max_rpm is not None and rpm > tool.max_rpm):
        raise ValueError('Second-tool RPM must be a whole number within its tool limits.')
    if tool.max_stepdown is not None and settings['stepdown'] > tool.max_stepdown + 1e-9:
        raise ValueError('Second-tool stepdown exceeds the loaded cutter maximum.')
    thickness = float(project.metadata.get('job_setup', {}).get('material_thickness', 0))
    if not math.isfinite(thickness) or not 0 < settings['skin'] < thickness:
        raise ValueError('Set the actual stock thickness in Job Setup; retained skin must be less than it.')
    depth = min(source.target_depth, thickness-settings['skin'])
    if not math.isfinite(depth) or depth <= 0:
        raise ValueError('Profile depth must be positive and finite.')
    if tool.cutting_edge_length is not None and depth > tool.cutting_edge_length + 1e-9:
        raise ValueError('Second-tool cutting-edge length is shorter than the held profile depth.')
    if source.side not in (OffsetSide.INSIDE, OffsetSide.OUTSIDE):
        raise ValueError('Second-tool profiling is for closed Inside and Outside offsets only.')
    if source.common_line_mode != 'off':
        raise ValueError('Disable shared-edge cutting for the retained-skin two-tool workflow.')
    return tool, thickness, depth


def profile_loops(project, source, identity, diameter, allowance):
    from .toolpath import _closed_entity_points, _offset_closed_polygons
    entity = next(e for e in project.entities if e.id == identity)
    side = OffsetSide(source.entity_sides.get(identity, source.side.value))
    if side not in (OffsetSide.INSIDE, OffsetSide.OUTSIDE):
        raise ValueError('Mixed on-contour/open compensation is not supported by two-tool profiling.')
    points = _closed_entity_points(entity)
    # Mirror the existing closed-profile numerical cleanup, never edit artwork.
    from .micro_backtracks import remove_micro_backtracks, MICRO_BACKTRACK_INCHES
    from .units import unit_scale
    points, _ = remove_micro_backtracks(points, closed=True,
        tolerance=MICRO_BACKTRACK_INCHES*unit_scale('in', project.units))
    try:
        return _offset_closed_polygons(points, diameter/2+allowance, side)
    except ValueError as exc:
        # Only a proven collapsed offset is a cutter-size fallback. Invalid
        # contours must still fail rather than be silently reassigned/omitted.
        if 'collapse' in str(exc).lower():
            return []
        raise


def build_profile_group(project, source, settings):
    """Pure planning: return operations, never mutate the project or its tools."""
    if settings.get('strategy') == 'Clear waste + tabbed finish':
        from .hybrid_profile import build_hybrid_group
        return build_hybrid_group(project,source,settings)
    from .toolpath import _profile_rest_distance_groups
    tool, thickness, depth = validate_settings(project, source, settings)
    previous = project.tool_library.by_station(source.tool_station)
    main_ids, small_ids, rest_ids, blocked = [], [], [], []
    if not source.entity_ids or not set(source.entity_ids) <= {e.id for e in project.entities}:
        raise ValueError('Select existing closed contours before creating a two-tool profile.')
    for identity in source.entity_ids:
        primary = profile_loops(project, source, identity, previous.diameter,
                                0 if source.finishing_pass else source.allowance)
        smaller = profile_loops(project, source, identity, tool.diameter, 0)
        if not smaller:
            blocked.append(object_identity_label(project, identity));continue
        if not primary:
            small_ids.append(identity);continue
        main_ids.append(identity)
        threshold = previous.diameter/2-tool.diameter/2+settings['ignore_stock_less_than']
        if any(_profile_rest_distance_groups(loop, primary, threshold, tool.diameter, None) for loop in smaller):
            rest_ids.append(identity)
    if blocked:
        raise ValueError('Still too small for the second cutter: '+', '.join(blocked)+
            '. Choose a smaller cutter or review the artwork. Nothing was omitted.')
    hold = dict(version=1, units=project.units, thickness=thickness, skin=settings['skin'],
                original_depth=source.target_depth)
    # Tabs are unnecessary above a continuous skin. Preserve the requested
    # settings for reference only, never sneak in a full-depth cleanup pass.
    hold['requested_tabs'] = {key: copy.deepcopy(getattr(source,key)) for key in
        ('tab_count','tab_width','tab_height','tab_mode','manual_tab_positions','female_tab_min_perimeter')}
    base = replace(source, target_depth=depth, cut_intent='partial', breakthrough=0,
        onion_skin=0, remove_tabs_after=False, tab_release_plan={},
        tab_count=0, tab_mode='even', tab_height=0, manual_tab_positions=[],
        geometry_selector={}, profile_retention=hold)
    def selection(ids):
        return dict(entity_ids=list(ids), entity_sides={i:source.entity_sides[i] for i in ids if i in source.entity_sides},
                    manual_contour_order=[i for i in source.manual_contour_order if i in ids])
    operations = []
    primary = replace(base, name=source.name+' — skin retained', **selection(main_ids)) if main_ids else None
    if primary:
        operations.append(primary)
    rates = dict(feed_rate_override=settings['feed_rate'], plunge_rate_override=settings['plunge_rate'],
                 spindle_rpm_override=int(settings['spindle_rpm']))
    recipe = dict(recipe_name='Reviewed second-tool profile settings',
                  recipe_metadata={'qualification':'prototype', 'profile_second_tool_reviewed':True})
    if small_ids:
        from .model import new_id
        operations.append(replace(base, id=new_id('operation'), name=f'Small-feature profile T{tool.station} — skin retained',
            tool_station=tool.station, **selection(small_ids), allowance=0, finishing_pass=False,
            finish_axial_stock=0, finish_feed_override=None, finish_spindle_rpm_override=None,
            safe_z=settings['safe_z'], stepdown=settings['stepdown'], pass_depths=[],
            entry_mode='plunge', lead_in=0, lead_out=0, **rates, **recipe))
        operations[-1].profile_retention = {**copy.deepcopy(hold), 'small_tool_stage':True}
    if primary and rest_ids:
        child = ProfileRestMachiningOperation(f'Profile corner rest T{tool.station} — skin retained',
            tool.station, rest_ids, depth, settings['safe_z'], source_operation_id=primary.id,
            stepdown=settings['stepdown'], inherit_pass_schedule=False, preserve_tabs=False,
            ignore_stock_less_than=settings['ignore_stock_less_than'],
            profile_retention={**copy.deepcopy(hold), 'small_tool_stage':True}, **rates, **recipe)
        operations.append(child)
        capture_profile_rest_dependency(replace(project, operations=[*project.operations,*operations]), child)
    # Hard dependencies survive role/tool sorting. No automatic release exists.
    for index, operation in enumerate(operations):
        operation.depends_on_ids = list(dict.fromkeys([*source.depends_on_ids,
            *operation.depends_on_ids, *[o.id for o in operations[:index]]]))
    return operations


def validate_retention(project, operation):
    """Always recheck saved retention before generation/output, including edits."""
    hold = operation.profile_retention
    if not hold:
        return
    if hold.get('workflow') == 'waste_then_tabbed_finish':
        from .hybrid_profile import validate_group
        validate_group(project,operation)
        return
    try:
        thickness = float(project.metadata.get('job_setup', {}).get('material_thickness',0))
        skin = float(hold['skin'])
        if (hold['version'] != 1 or hold['units'] != project.units
                or not all(math.isfinite(v) for v in (thickness,skin,float(hold['thickness']),operation.target_depth))
                or thickness != hold['thickness'] or not 0 < skin < thickness):
            raise ValueError('Stock thickness/units changed or retained-skin contract is invalid; recreate the two-tool group.')
        if not 0 < operation.target_depth <= thickness-skin+1e-9:
            raise ValueError('This edit would cut the retained skin. Release must be a separate deliberate operation.')
        if getattr(operation,'remove_tabs_after',False) or getattr(operation,'tab_release_plan',{}):
            raise ValueError('Automatic release is not allowed in a retained-skin two-tool stage.')
        if hold.get('small_tool_stage'):
            tool=project.tool_library.by_station(operation.tool_station)
            step=operation.stepdown
            if tool is None or tool.spindle_mode=='disabled' or tool.kind!=ToolKind.END_MILL:
                raise ValueError('The smaller profile stage requires a loaded rotary flat end mill.')
            if (step is None or not math.isfinite(step) or step<=0
                    or tool.max_stepdown is not None and step>tool.max_stepdown+1e-9
                    or getattr(operation,'inherit_pass_schedule',False)
                    or getattr(operation,'pass_depths',[])):
                raise ValueError('Keep the smaller tool on its own positive maximum-stepdown schedule, within its cutter limit.')
            if tool.cutting_edge_length is not None and operation.target_depth>tool.cutting_edge_length+1e-9:
                raise ValueError('The smaller profile depth exceeds the loaded cutter reach.')
    except (KeyError,TypeError,OverflowError) as exc:
        raise ValueError('Invalid retained-skin contract; recreate the two-tool profile.') from exc


def retention_order_issues(project):
    """Conservative prior-cut check; cannot infer that already cut stock is intact.

    Bounding-box broad phase deliberately errs on the side of requiring review.
    No exemptions are made to the normal exact collision checks.
    """
    from .workflow import ordered_operations
    from .model import ValidationIssue
    entities = {e.id:e for e in project.entities}
    ordered = ordered_operations([o for o in project.operations if o.enabled])
    issues=[]
    for index, operation in enumerate(ordered):
        if not operation.profile_retention:continue
        try:validate_retention(project,operation)
        except ValueError as exc:
            issues.append(ValidationIssue('error','profile_rest.retention',str(exc),tuple(operation.entity_ids)));continue
        floor=operation.profile_retention['thickness']-operation.profile_retention['skin']
        for prior in ordered[:index]:
            if prior.target_depth <= floor+1e-9:continue
            tool=project.tool_library.by_station(prior.tool_station)
            radius=tool.diameter/2 if tool else 0
            def overlaps(a,b):
                return a and b and a[0]-radius<=b[2] and a[2]+radius>=b[0] and a[1]-radius<=b[3] and a[3]+radius>=b[1]
            if any(overlaps(entity_bounds(entities[a]),entity_bounds(entities[b]))
                   for a in prior.entity_ids if a in entities for b in operation.entity_ids if b in entities):
                issues.append(ValidationIssue('error','profile_rest.prior_release',
                    f'{prior.name} cuts below the retained-skin floor near {operation.name} before its small-tool work. '
                    'Move the held two-tool work before that cut or revise the earlier depth; intact stock cannot be assumed.',
                    tuple(operation.entity_ids)))
                break
    return issues
