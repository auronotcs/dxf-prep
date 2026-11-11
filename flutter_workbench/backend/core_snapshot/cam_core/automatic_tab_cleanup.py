"""Automatic opposed-tab retention, recomputed from the actual final profiles.

This is a placement policy, not a manual support/connectivity review. It never
changes the profile or creates replacement tabs, and preserves original retracts.
"""
from dataclasses import replace
import math

from .model import effective_tool
from .tab_release import CleanupReview, _hash, _sweep, _clip, _area, cleanup_spans
from ._vendor import pyclipper as pc
from .units import unit_scale

MODE = 'automatic_opposed'
VERSION = 2
SELECTION_REVISION = 6


def policy(operation, count):
    return {'version': VERSION, 'mode': MODE, 'operation_id': operation.id,
            'retain_per_part': CleanupReview.count_value(count)}


def validate_policy(operation):
    plan = operation.tab_release_plan
    if plan != policy(operation, plan.get('retain_per_part')):
        raise ValueError('Invalid automatic retained-tab settings; save the cut again.')
    if not operation.remove_tabs_after or operation.tab_cleanup_order != 'end_of_operation':
        raise ValueError('Automatic retained tabs require end-of-operation cleanup.')
    return plan['retain_per_part']


def signature(project, operation):
    # Includes actual geometry, plates, tool and settings. No reused manual IDs.
    payload = operation.to_dict()
    payload['recipe_metadata'].pop('_computed_aligned_tab_bands', None)
    project_payload = project.to_dict()
    metadata = dict(project_payload.get('metadata', {}))
    # Output may add its default coordinate permission only at publication.
    # These settings do not change retained tabs or prepared motion. Coordinate
    # bounds are still independently checked by the postprocessor on every post.
    from .output_workflow import _OUTPUT_ONLY_JOB_SETUP_KEYS
    if isinstance(metadata.get('job_setup'), dict):
        setup = {key: value for key, value in metadata['job_setup'].items()
                 if key not in _OUTPUT_ONLY_JOB_SETUP_KEYS}
        if setup:
            metadata['job_setup'] = setup
        else:
            metadata.pop('job_setup')
    project_payload['metadata'] = metadata
    return _hash({'selection_revision': SELECTION_REVISION,
                  'project': project_payload, 'operation': payload})


def straight_tab(group, tolerance):
    """True only when the entire physical tab follows one straight line.

    Inspect the actual cutter span, including tessellated corners and both
    halves at a start seam, rather than merely testing its endpoint chord.
    """
    segments = []
    points = []
    for span in group['spans']:
        motions = span['motions'][1:-1]  # Exclude retract/plunge travel.
        for a, b in zip(motions, motions[1:]):
            if getattr(b, 'center', None) is not None:
                return False
            p, q = (a.end.x, a.end.y), (b.end.x, b.end.y)
            points.extend((p, q))
            if math.dist(p, q) > tolerance:
                segments.append((p, q))
    if not segments:
        return False
    a, b = max(segments, key=lambda pair: math.dist(*pair))
    length = math.dist(a, b)
    ux, uy = (b[0]-a[0])/length, (b[1]-a[1])/length
    return (all(abs((p[0]-a[0])*uy-(p[1]-a[1])*ux) <= tolerance for p in points)
            and all((q[0]-p[0])*ux+(q[1]-p[1])*uy > 0 for p,q in segments))


def tab_groups(spans, tolerance):
    """Join halves of one physical tab split at the profile start seam."""
    groups = []
    positions = {id(span): index for index, span in enumerate(spans)}
    for span in spans:
        p, q = span['motions'][1].end, span['motions'][-2].end
        span['ends'] = ((p.x, p.y), (q.x, q.y))
        key = (span['owner'], span['block'].metadata.get('path_index', 0))
        touching = [g for g in groups if g['key'] == key and any(
            math.dist(a, b) <= tolerance for old in g['spans'] for a in old['ends'] for b in span['ends'])]
        group = {'key': key, 'spans': [span]}
        for other in touching:
            group['spans'].extend(other['spans']); groups.remove(other)
        groups.append(group)
    for group in groups:
        points = [p for s in group['spans'] for p in s['ends']]
        group['mid'] = ((min(p[0] for p in points)+max(p[0] for p in points))/2,
                        (min(p[1] for p in points)+max(p[1] for p in points))/2)
        group['order'] = min(positions[id(span)] for span in group['spans'])
        group['straight'] = straight_tab(group, tolerance)
    # cleanup_spans follows the final profile's traversal, including arcs.
    # Merging a start/end seam tab appends it above; restore its cyclic position.
    groups.sort(key=lambda group: group['order'])
    return groups


def opposed_groups(groups, count):
    """Spread by contour order; prefer straight spans, then XY separation.

    Four physical tabs retaining two must yield 1+3 or 2+4, even on a hook
    where neighboring tabs happen to be farther apart in a straight line.
    """
    if len(groups) <= count:
        return list(groups)
    left = min(g['mid'][0] for g in groups); bottom = min(g['mid'][1] for g in groups)
    scale = max(max(g['mid'][0] for g in groups)-left,
                max(g['mid'][1] for g in groups)-bottom, 1e-12)
    def location(g):
        return (round((g['mid'][0]-left)/scale,10), round((g['mid'][1]-bottom)/scale,10))
    def distance(a,b):return round(math.dist(a['mid'],b['mid'])/scale,10)
    if count == 1:
        return [min(groups, key=lambda g: (not g.get('straight', True), location(g)))]
    n = len(groups)
    candidates = []
    for direction in (1, -1):
        for start in range(n):
            candidates.append([groups[(start + direction * (i*n//count)) % n] for i in range(count)])
    # Candidate gaps around the ring differ by at most one tab. In particular,
    # two of an even count are exactly half a circuit apart in tab order.
    candidates.sort(key=lambda items: tuple(sorted(location(g) for g in items)))
    def score(items):
        distances = [distance(a,b) for i,a in enumerate(items) for b in items[i+1:]]
        return sum(g.get('straight', True) for g in items), min(distances), round(sum(distances),10)
    return max(candidates, key=score)


def apply_automatic_cleanup(project, operation, blocks):
    count = validate_policy(operation)
    spans = cleanup_spans(blocks)
    tolerance = .001 * unit_scale('in', project.units)
    groups = tab_groups(spans, tolerance * .01)
    retained = set()
    for owner in dict.fromkeys(g['key'] for g in groups):
        ring = [g for g in groups if g['key'] == owner]
        if operation.tab_mode in {'minimum_avoid_corners', 'support_aware'}:
            from .smooth_tabs import retained_groups
            selected = retained_groups(ring, count)
        else:
            selected = opposed_groups(ring, count)
        retained.update(s['id'] for g in selected for s in g['spans'])

    # An adjacent contour must not clean through a tab chosen to remain. Bounds
    # reject remote spans before the exact cutter-sweep overlap test. These extra
    # retentions are not a claim of vacuum/clamp or sheet-perimeter support.
    radius = effective_tool(project, operation).diameter / 2
    plane = -(operation.tab_floor_depth)
    sweeps = {}
    def sweep(span):
        if span['id'] not in sweeps:
            sweeps[span['id']] = _sweep([span['motions']], radius, plane, tolerance)
        return sweeps[span['id']]
    for s in spans:
        points = list(s['ends'])
        for m in s['motions'][2:-1]:
            if m.center is not None:
                r = math.hypot(m.end.x-m.center.x, m.end.y-m.center.y)
                points.extend([(m.center.x-r,m.center.y-r),(m.center.x+r,m.center.y+r)])
            points.append((m.end.x,m.end.y))
        s['bounds'] = (min(p[0] for p in points)-radius, min(p[1] for p in points)-radius,
                       max(p[0] for p in points)+radius, max(p[1] for p in points)+radius)
    queue = [s for s in spans if s['id'] in retained]
    pending = {s['id']:s for s in spans if s['id'] not in retained}
    while queue:
        a = queue.pop(); ax, ay, bx, by = a['bounds']
        for key, b in list(pending.items()):
            cx, cy, dx, dy = b['bounds']
            if bx <= cx or dx <= ax or by <= cy or dy <= ay:
                continue
            if _area(_clip(sweep(a), sweep(b), pc.CT_INTERSECTION)) > tolerance**2*10:
                retained.add(key); queue.append(b); del pending[key]
    result = [b for b in blocks if b.metadata.get('role') != 'tab_cleanup']
    removed = [s for s in spans if s['id'] not in retained]
    for i, block in enumerate(result):
        ends = [s['ends'] for s in removed if s['owner'] == block.metadata.get('entity_id')
                and s['block'].metadata.get('path_index') == block.metadata.get('path_index')]
        result[i] = replace(block, metadata=dict(block.metadata, automatic_cleanup_ends=ends))
    for block in blocks:
        chosen = [s for s in removed if s['block'] is block]
        if chosen:
            metadata = dict(block.metadata, tab_release_span_ids=[s['id'] for s in chosen],
                            tab_release_owner_ids=[s['owner'] for s in chosen], retain_per_part=count)
            result.append(replace(block, motions=[m for s in chosen for m in s['motions']],
                                  metadata=metadata, label='Automatic opposed-tab cleanup'))
    if result:
        stamp = {'signature': signature(project, operation), 'removed': [s['id'] for s in removed],
                 'retained': sorted(retained), 'retain_per_part': count}
        result[0] = replace(result[0], metadata=dict(result[0].metadata, automatic_tab_cleanup=stamp))
    return result


def check_prepared(project, operation, generated):
    count = validate_policy(operation)
    stamp = generated.blocks[0].metadata.get('automatic_tab_cleanup', {}) if generated.blocks else {}
    if stamp.get('signature') != signature(project, operation) or stamp.get('retain_per_part') != count:
        raise ValueError('Automatic retained-tab preview is stale; regenerate Output.')
    cleanup = [b for b in generated.blocks if b.metadata.get('role') == 'tab_cleanup']
    if cleanup and generated.blocks[-len(cleanup):] != cleanup:
        raise ValueError('Automatic tab cleanup must follow all profiles; regenerate Output.')
    if [key for b in cleanup for key in b.metadata.get('tab_release_span_ids', [])] != stamp['removed']:
        raise ValueError('Automatic cleanup changed; regenerate Output.')
