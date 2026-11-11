"""Reviewed core keepout authoring and conservative fixture-envelope checks."""
import math
import copy
from cam_core.automation import _project_plates
from cam_core.patchwork_holes import piece_targets, resolve_zone, bind_zone, resolved_hole_project
from cam_core.hole_exclusion import exclusion_bounds
from cam_core.model import KeepoutZone, Point, Rectangle, Circle, Polyline
from cam_core.automation import _automation_layer_settings
from cam_core.collision import _segment_hits_expanded_box


def finite(value, label):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f'{label} must be a finite number.')
    return float(value)


def validate_points(points):
    from cam_core.nesting import _segments_intersect
    if not 3 <= len(points) <= 2000:
        raise ValueError('Use a closed polygon with 3–2,000 vertices.')
    for p in points:
        finite(p.x, 'X'); finite(p.y, 'Y')
    # The core validates arbitrary polygons during preflight. Authoring also
    # rejects degenerate/self-crossing outlines before publishing a draft.
    ring = points[:-1] if points[0] == points[-1] else points
    origin = ring[0]
    area = abs(sum((a.x-origin.x)*(b.y-origin.y)-(b.x-origin.x)*(a.y-origin.y) for a,b in zip(ring, ring[1:]+ring[:1]))) / 2
    crossed = any(_segments_intersect(ring[i], ring[(i+1)%len(ring)], ring[j], ring[(j+1)%len(ring)])
                  for i in range(len(ring)) for j in range(i+2,len(ring)) if (j+1)%len(ring)!=i)
    if len(set((v.x,v.y) for v in ring)) != len(ring) or not math.isfinite(area) or area <= 1e-12 or crossed:
        raise ValueError('Keepout must be a simple closed outline with nonzero area.')


def prepare_keepout(project, params):
    p = dict(params)
    change = p.pop('change', None)
    identity = p.pop('id', None)
    old = next((z for z in project.keepouts if z.id == identity), None)
    before = old.to_dict() if old else None
    if change not in ('add', 'edit', 'delete', 'toggle', 'link'):
        raise ValueError('Choose add, edit, delete or toggle keepout.')
    if change == 'add':
        if identity is not None: raise ValueError('New keepouts receive a new identity.')
    elif old is None: raise ValueError('This keepout no longer exists.')
    if change == 'delete':
        if p: raise ValueError('Delete accepts only the keepout identity.')
        project.keepouts.remove(old)
        return dict(change=change, before=before, after=None, protection_removed=True)
    if change == 'toggle':
        if set(p) != {'enabled'} or type(p['enabled']) is not bool:
            raise ValueError('Choose whether this keepout is enabled.')
        old.enabled = p['enabled']
        if old.to_dict() == before: raise ValueError('No keepout changes to review.')
        return dict(change=change, before=before, after=old.to_dict(), protection_removed=not old.enabled)
    if change == 'link':
        if set(p) != {'patchwork_id'}: raise ValueError('Choose a Patchwork piece or detach.')
        targets = piece_targets(project, _project_plates(project))
        try: current = resolve_zone(old, targets)
        except ValueError:
            current = old  # Explicit relink/detach starts at the last known position; bind validates the new overlap.
        zone = bind_zone(current, p['patchwork_id'], targets)
        if before == zone.to_dict(): raise ValueError('No link changes to review.')
        project.keepouts[project.keepouts.index(old)] = zone
        return dict(change=change, before=before, after=zone.to_dict(), protection_removed=False)
    if set(p) - {'kind', 'extra_margin', 'plate_number', 'name', 'clearance_z', 'shape', 'entity_id', 'x', 'y', 'width', 'height', 'dx', 'dy'}:
        raise ValueError('Unknown keepout setting.')
    kind = p.get('kind', old.kind if old else 'fixture')
    if kind not in ('fixture','hole_exclusion') or old and kind != old.kind:
        raise ValueError('Keepout type cannot be changed. Create a separate zone.')
    margin = finite(p.get('extra_margin', old.extra_margin if old else 0), 'Extra margin')
    if margin < 0 or kind == 'fixture' and margin != 0: raise ValueError('Use a nonnegative hole margin; fixture margin must be zero.')
    current = resolve_zone(old, piece_targets(project, _project_plates(project))) if old else None
    name = p.get('name', old.name if old else '')
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
        raise ValueError('Enter a keepout name of 1–120 characters.')
    height = finite(p.get('clearance_z', old.clearance_z if old else None), 'Required clearance Z')
    if height <= 0: raise ValueError('Required clearance Z must be above the stock top.')
    shape = p.get('shape', 'existing' if old else None)
    if shape == 'rectangle':
        if 'entity_id' in p: raise ValueError('Choose rectangle dimensions or a source contour.')
        x, y, w, h = [finite(p.get(k), k) for k in ('x', 'y', 'width', 'height')]
        if min(w, h) <= 0: raise ValueError('Rectangle width and height must be positive.')
        points = [Point(x, y), Point(x+w, y), Point(x+w, y+h), Point(x, y+h)]
    elif shape == 'selection':
        if any(k in p for k in ('x', 'y', 'width', 'height')): raise ValueError('Selection uses its saved outline.')
        entity = next((e for e in project.entities if e.id == p.get('entity_id')), None)
        if entity is None: raise ValueError('Select one closed drawing contour.')
        layer = _automation_layer_settings(project).get(entity.layer, {})
        if layer.get('visible') is False or layer.get('locked') or entity.metadata.get('annotation_hidden'):
            raise ValueError('Use visible, unlocked source geometry.')
        if isinstance(entity, Rectangle):
            a, b = entity.corner_a, entity.corner_b
            points = [a, Point(b.x, a.y), b, Point(a.x, b.y)]
        elif isinstance(entity, Circle):
            # Circumscribed vertices include the whole circular fixture.
            r = entity.radius / math.cos(math.pi / 96)
            points = [Point(entity.center.x+r*math.cos(i*math.tau/96), entity.center.y+r*math.sin(i*math.tau/96)) for i in range(96)]
        elif isinstance(entity, Polyline) and entity.closed:
            points = list(entity.points)
        else: raise ValueError('Use a rectangle, circle or closed polyline.')
    elif shape == 'existing' and old:
        if any(k in p for k in ('entity_id', 'x', 'y', 'width', 'height')): raise ValueError('Choose a replacement shape explicitly.')
        points = list(current.points)
    else: raise ValueError('Choose rectangle dimensions or one selected closed contour.')
    dx, dy = finite(p.get('dx', 0), 'Move X'), finite(p.get('dy', 0), 'Move Y')
    points = [Point(v.x+dx, v.y+dy) for v in points]
    validate_points(points)
    zone = KeepoutZone(name.strip(), points, height, old.enabled if old else True,
                       kind=kind, extra_margin=margin, **({'id': old.id} if old else {}))
    if kind == 'hole_exclusion':
        exclusion_bounds(zone)
        if 'plate_number' in p:
            if type(p['plate_number']) is not int: raise ValueError('Choose a whole plate number.')
            plate = next((v for v in _project_plates(project) if v.number == p['plate_number']), None)
            if plate is None: raise ValueError('Choose an existing plate.')
            # Input is explicitly drawing/world coordinates, including on offset plates.
            b = exclusion_bounds(zone); m = zone.extra_margin
            if b[2]-m <= plate.x or b[0]+m >= plate.x+plate.width or b[3]-m <= plate.y or b[1]+m >= plate.y+plate.height:
                raise ValueError('Physical hole must overlap the chosen plate.')
        elif not old: raise ValueError('Choose a plate for the hole.')
    if current and current.patchwork_id is not None:
        zone = bind_zone(zone, current.patchwork_id, piece_targets(project, _project_plates(project)))
    if before == zone.to_dict(): raise ValueError('No keepout changes to review.')
    if old: project.keepouts[project.keepouts.index(old)] = zone
    else: project.keepouts.append(zone)
    return dict(change=change, before=before, after=zone.to_dict(), protection_removed=False)


def fixture_issues(project, paths):
    """Conservative bounds checks in addition to the core polygon checks.

    All arc directions use the full circle envelope. This can flag clear paths,
    but never substitutes an unsafe endpoint chord for curved motion.
    """
    try: project = resolved_hole_project(project, _project_plates(project))
    except ValueError as exc:
        return [dict(severity='error',code='hole.patchwork_link',message=str(exc),operation_id=None,entity_ids=[])]
    zones = [z for z in project.keepouts if z.enabled]
    if not zones: return []
    issues = []
    def issue(severity, code, message, op=None):
        issues.append(dict(severity=severity, code=code, message=message,
                           operation_id=op.id if op else None, entity_ids=[]))
    valid = []
    for zone in zones:
        try:
            validate_points(zone.points)
            if finite(zone.clearance_z, 'Required clearance Z') <= 0: raise ValueError('clearance must be positive')
            if zone.kind not in ('fixture','hole_exclusion'): raise ValueError('Unknown keepout type.')
            bounds = exclusion_bounds(zone) if zone.kind == 'hole_exclusion' else (min(p.x for p in zone.points), min(p.y for p in zone.points), max(p.x for p in zone.points), max(p.y for p in zone.points))
            valid.append((zone, bounds))
        except ValueError as exc: issue('error', 'fixture.invalid', f'{zone.name}: {exc}')
    missing = set()
    from cam_core.workflow import ordered_operations
    previous = None
    for op in ordered_operations(project.operations):
        if not op.enabled or op.id not in paths: continue
        tool = project.tool_library.by_station(op.tool_station)
        if tool is None: continue
        holder = all(type(v) in (int, float) and math.isfinite(v) and v > 0 for v in (tool.holder_diameter, tool.stickout))
        if not holder and tool.station not in missing:
            missing.add(tool.station)
            issue('warning', 'fixture.holder_missing', f'T{tool.station}: holder diameter/stickout missing; only the cutter envelope was checked.', op)
        hit = set()
        for block in paths[op.id].blocks:
            for motion in block.motions:
                a, b = previous or motion.end, motion.end
                previous = b
                for zone, bounds in valid:
                    for label, radius, bottom in [('cutter', tool.diameter/2, min(a.z, b.z)), *([('holder', tool.holder_diameter/2, min(a.z, b.z)+tool.stickout)] if holder else [])]:
                        key = (zone.id, label)
                        if key in hit or bottom > zone.clearance_z + 1e-9: continue
                        if motion.center is not None:
                            c = motion.center
                            r = max(math.hypot(a.x-c.x, a.y-c.y), math.hypot(b.x-c.x, b.y-c.y)) + radius
                            contact = not (c.x+r < bounds[0] or c.x-r > bounds[2] or c.y+r < bounds[1] or c.y-r > bounds[3])
                        else: contact = _segment_hits_expanded_box(a, b, bounds, radius)
                        if contact:
                            hit.add(key)
                            issue('error', 'fixture.envelope_collision', f'{op.name}: T{tool.station} {label} envelope may contact {zone.name} below required clearance Z {zone.clearance_z:g}. Checks use conservative polygon bounds and full-circle arc bounds.', op)
    return issues


def localize_all_keepouts(source, prepared):
    # Core plate extraction drops zones outside the sheet. A holder/cutter can
    # reach beyond that edge, so retain every authored zone in local coordinates.
    source = resolved_hole_project(source, _project_plates(source))
    for item in prepared:
        item.result.project.keepouts = [KeepoutZone(z.name, [Point(p.x-item.plate.x, p.y-item.plate.y) for p in z.points], z.clearance_z, z.enabled, z.id, kind=z.kind, extra_margin=z.extra_margin) for z in source.keepouts]


def display(project):
    """Private resolved view; malformed links remain visible for explicit repair."""
    from cam_core.patchwork_holes import refresh_linked_holes
    view = copy.copy(project)
    view.keepouts = copy.deepcopy(project.keepouts)
    problems = refresh_linked_holes(view, _project_plates(view))
    return [z.to_dict() for z in view.keepouts], list(problems)

def link_choices(project):
    try:
        return [dict(id=i,name=a.name or i,plate=a.plate_number) for i,(a,origin) in piece_targets(project,_project_plates(project)).items() if origin is not None]
    except ValueError: return []
