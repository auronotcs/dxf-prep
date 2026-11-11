"""Exact-path travel reductions; no cutting feeds, geometry or release reordering."""
from dataclasses import replace
import math


def link_closed_profile_depths(project, operation, blocks):
    """Retrace a previously cut straight lead-in, then plunge the next step.

    Do not infer a cleared chord from proximity. The old contour must close on
    its exact lead endpoint and the next pass must reuse that exact lead. A
    known stock thickness prevents linking after a potentially released part.
    Tab cleanup, finish passes, ramps/helices and common lines are excluded.
    """
    from .toolpath import Motion, MotionKind, ToolPoint, TOLERANCE
    thickness = project.metadata.get('job_setup', {}).get('material_thickness', 0)
    if (not isinstance(thickness, (int, float)) or not math.isfinite(thickness)
            or thickness <= 0 or operation.entry_mode != 'plunge'
            or operation.common_line_mode != 'off'):
        return blocks
    result = []
    previous_raw = None
    for block in blocks:
        previous = previous_raw
        previous_raw = block
        eligible = previous is not None and len(previous.motions) >= 5 and len(block.motions) >= 5
        if eligible:
            a, b = previous.metadata, block.metadata
            old_depth = a.get('depth', 0)
            depth = b.get('depth', 0)
            eligible = (a.get('role') == b.get('role') == 'rough'
                and a.get('entry_mode_actual') == b.get('entry_mode_actual') == 'plunge'
                and all(a.get(k) == b.get(k) for k in
                        ('entity_id','path_index','side','offset_distance','spindle_rpm','cut_direction'))
                and a.get('entity_id') is not None
                and previous.tool_station == block.tool_station
                and 0 < old_depth < thickness - TOLERANCE
                and depth > old_depth + TOLERANCE)
        if eligible:
            old, new = previous.motions, block.motions
            entry, seam, end = old[1], old[2], old[-2]
            def xy(p, q):
                return math.hypot(p.x-q.x, p.y-q.y) <= TOLERANCE
            eligible = (old[0].kind is new[0].kind is old[-1].kind is MotionKind.RAPID
                and entry.kind is seam.kind is new[1].kind is new[2].kind is MotionKind.LINEAR
                and xy(old[0].end, entry.end) and xy(new[0].end, new[1].end)
                and xy(entry.end, new[1].end) and xy(seam.end, new[2].end)
                and xy(end.end, seam.end)
                and all(abs(m.end.z + old_depth) <= TOLERANCE for m in (entry, seam, end))
                and abs(new[1].end.z + depth) <= TOLERANCE
                and abs(new[2].end.z + depth) <= TOLERANCE
                and seam.feed is not None and seam.feed > 0)
        if not eligible:
            result.append(block)
            continue
        joined = result[-1]
        reverse = Motion(MotionKind.LINEAR, ToolPoint(entry.end.x, entry.end.y, -old_depth), seam.feed)
        metadata = dict(joined.metadata)
        markers = list(metadata.get('cleared_lead_returns', ()))
        source_index = markers[-1]['return_index'] + 2 if markers else 2
        markers.append({'return_index': len(joined.motions)-1, 'source_index': source_index, 'previous_depth': old_depth,
                        'next_depth': depth})
        metadata.update(cleared_lead_returns=markers, depth=depth,
                        linked_depths=[*metadata.get('linked_depths', [old_depth]), depth],
                        depth_links_saved=metadata.get('depth_links_saved', 0)+1)
        result[-1] = replace(joined, motions=[*joined.motions[:-1], reverse, *block.motions[1:]],
                             metadata=metadata, label=f'{joined.label} through {depth:g}')
    return result


def verify_cleared_lead_returns(project, operation, generated):
    """Independently compare every return to its earlier actual forward cut."""
    from .toolpath import MotionKind
    from .motion_verifier import MotionVerificationIssue
    from .model import Point
    issues = []
    thickness = project.metadata.get('job_setup', {}).get('material_thickness', 0)
    def same(a, b):
        return math.dist((a.x,a.y,a.z), (b.x,b.y,b.z)) <= 1e-7
    for block in generated.blocks:
        for marker in block.metadata.get('cleared_lead_returns', ()):
            valid = False
            try:
                i, j = marker['return_index'], marker['source_index']
                if not 1 <= j < i < len(block.motions)-1:
                    raise ValueError('indices')
                forward, reverse, plunge = block.motions[j], block.motions[i], block.motions[i+1]
                before, start = block.motions[j-1].end, block.motions[i-1].end
                valid = (forward.kind is reverse.kind is plunge.kind is MotionKind.LINEAR
                    and same(start, forward.end) and same(reverse.end, before)
                    and abs(before.z-forward.end.z) <= 1e-7
                    and -before.z < thickness - 1e-7
                    and forward.feed is not None and reverse.feed is not None
                    and 0 < reverse.feed <= forward.feed + 1e-7
                    and math.hypot(plunge.end.x-before.x, plunge.end.y-before.y) <= 1e-7
                    and plunge.end.z < before.z - 1e-7
                    and abs(before.z + marker['previous_depth']) <= 1e-7
                    and abs(plunge.end.z + marker['next_depth']) <= 1e-7)
            except (ValueError, TypeError, KeyError, IndexError):
                pass
            if not valid:
                issues.append(MotionVerificationIssue('error', 'motion.cleared_lead_return',
                    f'{operation.name} has a depth return not proven by its earlier lead-in.',
                    tuple(operation.entity_ids)))
    return tuple(issues)
