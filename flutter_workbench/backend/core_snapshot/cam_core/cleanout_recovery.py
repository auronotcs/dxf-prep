"""Non-mutating multi-region cleanout access review and smaller-tool planning."""
from dataclasses import dataclass, replace
import math
from .model import IslandFillOperation, RestMachiningOperation, FillStrategy, ToolKind
from .depth_intent import resolve_fill_depth
from .object_identity import object_identity_label
from .toolpath import (classify_island_fill_regions, _closed_entity_points,
                       _accessible_region_paths, _rest_region_loops)

@dataclass(frozen=True)
class CleanoutAccess:
    outer_id: str
    island_ids: tuple[str, ...]
    reachable_stations: frozenset[int]

def recovery_tools(project, source):
    primary = project.tool_library.by_station(source.tool_station)
    if primary is None:
        raise ValueError(f"Missing primary tool T{source.tool_station}.")
    return sorted((tool for tool in project.tool_library.tools
        if tool.spindle_mode != 'disabled' and tool.station != 8
        and tool.kind == ToolKind.END_MILL
        and 0 < tool.diameter < primary.diameter - 1e-9),
        key=lambda tool: (-tool.diameter, tool.station))

def analyze_access(project, source, stations=()):
    by_id = {e.id:e for e in project.entities}
    ids = list(dict.fromkeys([*source.entity_ids, *source.island_entity_ids]))
    if any(key not in by_id for key in ids):
        raise ValueError('Cleanout selection contains missing geometry.')
    regions = classify_island_fill_regions([by_id[key] for key in ids])
    if ({outer.id for outer, _ in regions} != set(source.entity_ids)
            or {e.id for _, islands in regions for e in islands} != set(source.island_entity_ids)):
        raise ValueError('Cleanout boundary roles no longer match contour nesting; re-select the boundaries.')
    tools = [project.tool_library.by_station(station) for station in dict.fromkeys([source.tool_station,*stations])]
    if any(tool is None for tool in tools):
        raise ValueError('A selected cleanout tool is no longer in the turret.')
    result = []
    for outer, islands in regions:
        points = _closed_entity_points(outer)
        holes = [_closed_entity_points(e) for e in islands]
        result.append(CleanoutAccess(outer.id, tuple(e.id for e in islands), frozenset(
            tool.station for tool in tools if _accessible_region_paths(points, holes, tool.diameter/2))))
    return result

def validate_rest_settings(project, source, settings):
    if settings.get('reviewed') is not True:
        raise ValueError('Review and confirm the smaller-tool cutting settings first.')
    station = int(settings['station'])
    tool = project.tool_library.by_station(station)
    if tool is None or station not in {t.station for t in recovery_tools(project, source)}:
        raise ValueError('Choose a loaded flat end mill smaller than the main tool (for example T9 or T2).')
    for key in ('feed_rate','plunge_rate','spindle_rpm','stepdown','stepover','safe_z'):
        if not math.isfinite(float(settings[key])) or float(settings[key]) <= 0:
            raise ValueError('Rest feeds, RPM, Safe Z, stepdown and stepover must be finite and positive.')
    if settings['stepover'] > tool.diameter:
        raise ValueError('Rest stepover must not exceed its cutter diameter.')
    threshold = float(settings.get('ignore_stock_less_than', 0))
    if not math.isfinite(threshold) or threshold < 0:
        raise ValueError('Ignore residual thickness must be finite and nonnegative.')
    previous = project.tool_library.by_station(source.tool_station)
    if threshold >= (previous.diameter - tool.diameter) / 2:
        raise ValueError('Ignore residual thickness must be less than the difference in cutter radii.')
    if tool.max_stepdown is not None and settings['stepdown'] > tool.max_stepdown + 1e-9:
        raise ValueError('Rest stepdown exceeds the selected tool maximum.')
    if not float(settings['spindle_rpm']).is_integer():
        raise ValueError('Rest RPM must be a whole number.')
    if (tool.min_rpm is not None and settings['spindle_rpm'] < tool.min_rpm
            or tool.max_rpm is not None and settings['spindle_rpm'] > tool.max_rpm):
        raise ValueError('Rest RPM is outside the selected tool range.')
    resolved = resolve_fill_depth(project, source)
    if tool.cutting_edge_length is not None and resolved.target_depth > tool.cutting_edge_length + 1e-9:
        raise ValueError(f'T{station} cutting-edge length is {tool.cutting_edge_length:g} {project.units}; '
                         f'this fill requires {resolved.target_depth:g} {project.units}. Choose a longer cutter.')
    return tool, resolved


def build_recovery_operations(project, source, settings):
    """Plan all stages without changing the job. Empty primary pockets need a
    full small-tool fill; only primary-accessible pockets receive stock rest.
    Each generated operation owns its reviewed feeds and clearance.
    """
    tool, resolved = validate_rest_settings(project, source, settings)
    station = tool.station
    threshold = settings.get('ignore_stock_less_than', 0.0)
    access = analyze_access(project, source, [station])
    main = [r for r in access if source.tool_station in r.reachable_stations]
    small = [r for r in access if source.tool_station not in r.reachable_stations]
    blocked = [r for r in small if station not in r.reachable_stations]
    if blocked:
        raise ValueError('Still unreachable with the selected smaller tool: '+', '.join(object_identity_label(project,r.outer_id) for r in blocked)+'. Use Show Me and a smaller cutter or revise the artwork; no geometry was omitted.')
    def geometry(regions):
        return dict(entity_ids=[r.outer_id for r in regions], island_entity_ids=[k for r in regions for k in r.island_ids])
    rates = dict(feed_rate_override=settings['feed_rate'], plunge_rate_override=settings['plunge_rate'],
                 spindle_rpm_override=int(settings['spindle_rpm']))
    recipe = dict(recipe_name='Reviewed smaller-tool cleanout settings',
                  recipe_metadata={'qualification':'prototype','cleanout_recovery_reviewed':True})
    operations = []
    primary = replace(source, geometry_selector={}, **geometry(main)) if main else None
    if primary is not None:
        operations.append(primary)
    if small:
        operations.append(IslandFillOperation(name=f'Small-pocket cleanout — T{station}',
            tool_station=station, **geometry(small), target_depth=resolved.target_depth,
            cut_intent=source.cut_intent, breakthrough=source.breakthrough, safe_z=settings['safe_z'],
            stepover=settings['stepover'], stepdown=settings['stepdown'], entry_mode='plunge',
            strategy=FillStrategy.CONCENTRIC, corner_tags=False, cut_direction=source.cut_direction, **rates, **recipe))
    if primary is not None and settings.get('include_rest',True):
        by_id={e.id:e for e in project.entities}
        previous_radius=project.tool_library.by_station(source.tool_station).diameter/2
        # Stop at the first reachable remainder; full generation stays in the
        # normal worker. Do not create an empty dependent operation.
        has_rest=any(_rest_region_loops(_closed_entity_points(by_id[r.outer_id]),
            [_closed_entity_points(by_id[k]) for k in r.island_ids],previous_radius,
            tool.diameter/2,tool.diameter/2,settings['stepover'],threshold) for r in main)
        if has_rest:
            operations.append(RestMachiningOperation(name=f'Rest cleanout — T{station}',
                tool_station=station, source_operation_id=primary.id, **geometry(main),
                target_depth=resolved.target_depth, safe_z=settings['safe_z'],
                depends_on_ids=[primary.id],
                ignore_stock_less_than=threshold,
                stepover=settings['stepover'], stepdown=settings['stepdown'], **rates, **recipe))
    return operations
