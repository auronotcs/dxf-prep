"""Explicit, geometry-preserving resolutions for the quick profile review."""
import copy

from .model import RouterOffsetOperation, TabReleaseOperation
from .contour_repair import propose_near_point_repair
from .fragment_repair import apply_fragment_repair, fragment_source_ids
from .units import unit_scale


def repairable_fragment_ids(project, findings):
    result = set()
    for identity in fragment_source_ids(findings):
        try:
            proposals = propose_near_point_repair(project.entities, {identity},
                tolerance=.0001 * unit_scale('in', project.units))
            apply_fragment_repair(project, proposals, {identity})
        except (ValueError, TypeError):
            continue
        result.add(identity)
    return result


def exclusion_plan(project, operations, findings):
    """Validate the entire selection before returning any operation changes."""
    by_id = {op.id: op for op in operations}
    removals = {}
    for finding in findings:
        op = by_id.get(finding.get('operation_id'))
        ids = set(finding.get('entity_ids', ()))
        if (not isinstance(op, RouterOffsetOperation) or isinstance(op, TabReleaseOperation)
                or not ids or not ids <= set(op.entity_ids)):
            raise ValueError('Select findings belonging to an existing closed profile contour.')
        removals.setdefault(op.id, set()).update(ids)
    if not removals:
        raise ValueError('Select the contour findings to exclude from this profile.')
    dependents = {op.id: op for op in [*project.operations, *operations]}
    for op in dependents.values():
        if (getattr(op, 'source_operation_id', '') in removals
                or set(getattr(op, 'depends_on_ids', ())) & removals.keys()):
            raise ValueError('This profile has a dependent operation. Edit its selection and dependent operations together first.')
    changes = []
    for op_id, ids in removals.items():
        op = by_id[op_id]
        remaining = [identity for identity in op.entity_ids if identity not in ids]
        if not remaining:
            raise ValueError('This would leave an empty profile. Cancel and disable or revise that operation instead.')
        changes.append((op, dict(entity_ids=remaining,
            entity_sides={k:v for k,v in op.entity_sides.items() if k not in ids},
            manual_contour_order=[k for k in op.manual_contour_order if k not in ids],
            geometry_selector={})))
    return changes


def commit_profile_exclusion(app, target, operations, findings):
    """Update selected profiles and matching live/staged selections atomically.

    New pending operations stay pending. Existing live selections get one Undo
    record; pending feeds/tabs/other settings are never copied onto live ops.
    """
    changes = exclusion_plan(target, operations, findings)
    mirrored = []
    seen = set()
    live_changed = False
    for op, fields in changes:
        removed = set(op.entity_ids) - set(fields['entity_ids'])
        mirrors = [op, *[o for o in target.operations if o.id == op.id],
                   *[o for o in app.project.operations if o.id == op.id]]
        for mirror in mirrors:
            if id(mirror) in seen:
                continue
            seen.add(id(mirror))
            # A staged-only source cannot silently change a different live selection.
            if not removed <= set(mirror.entity_ids):
                raise ValueError('Pending and live profile selections differ. Cancel and edit the profile first.')
            local_findings = [dict(operation_id=mirror.id, entity_ids=tuple(removed))]
            owner = app.project if any(mirror is o for o in app.project.operations) else target
            update = exclusion_plan(owner, [mirror], local_findings)[0][1]
            mirrored.append((mirror, update))
            live_changed |= any(mirror is o for o in app.project.operations)
    for op, fields in mirrored:
        for name, value in fields.items():
            setattr(op, name, copy.deepcopy(value))
    app._output_generation_cache.clear()
    app.clear_geometry_diagnostics()
    if live_changed:
        app._defer_toolpath_regeneration()
        app._set_dirty(True)
        app._record_history('Exclude selected contours from profile', 'OP')
        app._redraw()
    return live_changed
