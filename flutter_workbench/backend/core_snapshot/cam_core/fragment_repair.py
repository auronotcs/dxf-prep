"""Strict same-contour repair transaction for output fragment warnings."""
from .contour_repair import apply_contour_repair
from .model import Polyline

FRAGMENT_CODES=frozenset(('toolpath.tab_free_fragment','toolpath.split_geometry'))


def fragment_source_ids(problems):
    return {identity for problem in problems if problem.get('code') in FRAGMENT_CODES
            for identity in problem.get('entity_ids',())}


def apply_fragment_repair(project, proposals, allowed_ids):
    proposals=list(proposals);allowed_ids=set(allowed_ids)
    originals={e.id:e for e in project.entities}
    if not proposals:raise ValueError('No safe near-duplicate point repair was found. No geometry changed.')
    seen=set()
    for p in proposals:
        source=originals.get(p.entity.id)
        if (p.entity.id not in allowed_ids or p.entity.id in seen or p.source_ids!=(p.entity.id,)
                or not isinstance(source,Polyline) or not source.closed
                or not isinstance(p.entity,Polyline) or not p.entity.closed
                or p.entity.layer!=source.layer or p.joins or p.remaining_issues or p.removed_points<=0):
            raise ValueError('This repair requires manual review: only safe, same-ID closed-contour point cleanup is allowed here.')
        seen.add(p.entity.id)
    candidate,disabled,removed=apply_contour_repair(project,proposals)
    if (disabled or removed or {e.id for e in candidate.entities}!=set(originals)
            or [o.to_dict() for o in candidate.operations]!=[o.to_dict() for o in project.operations]):
        raise ValueError('Repair would remove dependent geometry or change machining associations/settings. Nothing applied; use manual Repair review.')
    changed={e.id for e in candidate.entities if e.to_dict()!=originals[e.id].to_dict()}
    return candidate,changed-seen


def context_is_current(app, context):
    project,document_id,revision,payload=context
    return (app.project is project and app.document_id==document_id and app.document_revision==revision
            and app.project.to_dict()==payload)
